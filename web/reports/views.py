import hmac
import json
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import PublishedDocument, Report, ReportRun


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@login_required
def report_list(request):
    reports = Report.objects.annotate(
        run_count=Count("runs"), last_received=Max("runs__created_at")
    ).order_by("flow_name", "report_type")
    published_reports = PublishedDocument.objects.filter(
        kind=PublishedDocument.Kind.REPORT
    )
    query = request.GET.get("q", "").strip()
    report_type = request.GET.get("type", "").strip()
    if query:
        reports = reports.filter(flow_name__icontains=query)
        published_reports = published_reports.filter(
            Q(title__icontains=query) | Q(source_path__icontains=query)
        )
    if report_type:
        reports = reports.filter(report_type=report_type)
    published_page = Paginator(published_reports, 20).get_page(request.GET.get("page"))
    live_page = Paginator(reports, 20).get_page(request.GET.get("live_page"))
    return render(
        request,
        "reports/report_list.html",
        {
            "reports": live_page,
            "published_reports": published_page,
            "query": query,
            "report_type": report_type,
        },
    )


@login_required
def report_detail(request, pk):
    report = get_object_or_404(Report, pk=pk)
    runs = report.runs.all()
    return render(
        request, "reports/report_detail.html", {"report": report, "runs": runs}
    )


@login_required
def run_detail(request, pk):
    run = get_object_or_404(ReportRun.objects.select_related("report"), pk=pk)
    return render(request, "reports/run_detail.html", {"run": run})


@login_required
def guide_list(request):
    guides = PublishedDocument.objects.filter(kind=PublishedDocument.Kind.GUIDE)
    query = request.GET.get("q", "").strip()
    if query:
        guides = guides.filter(title__icontains=query)
    return render(request, "reports/guide_list.html", {"guides": guides})


@login_required
def document_detail(request, kind, slug):
    document = get_object_or_404(PublishedDocument, kind=kind, slug=slug)
    return render(request, "reports/document_detail.html", {"document": document})


@login_required
def document_content(request, kind, slug):
    document = get_object_or_404(PublishedDocument, kind=kind, slug=slug)
    response = HttpResponse(
        document.content, content_type=f"{document.content_type}; charset=utf-8"
    )
    response.headers["Content-Security-Policy"] = (
        "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


def _authorized(request):
    expected = settings.REPORT_INGESTION_TOKEN
    supplied = request.headers.get("Authorization", "")
    if not expected or not supplied.startswith("Bearer "):
        return False
    return hmac.compare_digest(supplied.removeprefix("Bearer "), expected)


@csrf_exempt
@require_POST
def ingest_report_run(request):
    if not _authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "request body must be valid JSON"}, status=400)

    flow_name = str(data.get("flow_name", "")).strip()
    if not flow_name:
        return JsonResponse({"error": "flow_name is required"}, status=400)
    report_type = str(data.get("report_type") or "_default")[:80]
    attributes = data.get("attributes") or {}
    if not isinstance(attributes, dict):
        return JsonResponse({"error": "attributes must be an object"}, status=400)

    key = request.headers.get("Idempotency-Key") or str(data.get("idempotency_key", ""))
    key = key.strip()[:255]
    if not key:
        key = str(uuid.uuid4())

    card_html = str(data.get("card_html") or data.get("html") or "")

    with transaction.atomic():
        report, _ = Report.objects.get_or_create(
            flow_name=flow_name[:255], report_type=report_type
        )
        run, created = ReportRun.objects.get_or_create(
            idempotency_key=key,
            defaults={
                "report": report,
                "attributes": attributes,
                "payload": data.get("payload") if "payload" in data else None,
                "raw_payload": str(data.get("raw_payload") or ""),
                "card_html": card_html,
                "status": str(data.get("status") or "completed")[:40],
                "source_timestamp": parse_datetime(str(data.get("timestamp") or "")),
            },
        )
        if not created and card_html and not run.card_html:
            run.card_html = card_html
            run.save(update_fields=["card_html"])

    return JsonResponse(
        {"id": str(run.id), "created": created, "report_id": report.id},
        status=201 if created else 200,
    )


@csrf_exempt
@require_POST
def publish_document(request):
    if not _authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "request body must be valid JSON"}, status=400)

    kind = str(data.get("kind", "")).strip()
    if kind not in PublishedDocument.Kind.values:
        return JsonResponse({"error": "kind must be guide or report"}, status=400)
    slug = str(data.get("slug", "")).strip()[:255]
    title = str(data.get("title", "")).strip()[:255]
    content = data.get("content")
    if not slug or not title or not isinstance(content, str):
        return JsonResponse(
            {"error": "slug, title, and string content are required"}, status=400
        )
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        return JsonResponse({"error": "metadata must be an object"}, status=400)

    document, created = PublishedDocument.objects.update_or_create(
        kind=kind,
        slug=slug,
        defaults={
            "title": title,
            "source_path": str(data.get("source_path", ""))[:500],
            "content_type": str(data.get("content_type") or "text/plain")[:100],
            "content": content,
            "checksum": str(data.get("checksum", ""))[:64],
            "size_bytes": len(content.encode("utf-8")),
            "metadata": metadata,
            "triggered_at": parse_datetime(str(data.get("triggered_at") or "")),
            "generated_at": parse_datetime(str(data.get("generated_at") or "")),
        },
    )
    return JsonResponse(
        {"id": document.id, "created": created, "slug": document.slug},
        status=201 if created else 200,
    )
