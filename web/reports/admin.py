from django.contrib import admin

from .models import PublishedDocument, Report, ReportRun


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("flow_name", "report_type", "updated_at")
    list_filter = ("report_type",)
    search_fields = ("flow_name",)


@admin.register(ReportRun)
class ReportRunAdmin(admin.ModelAdmin):
    list_display = ("id", "report", "status", "created_at")
    list_filter = ("status", "report__report_type")
    search_fields = ("report__flow_name", "idempotency_key")
    readonly_fields = ("id", "created_at")


@admin.register(PublishedDocument)
class PublishedDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "generated_at", "updated_at", "size_bytes")
    list_filter = ("kind", "content_type")
    search_fields = ("title", "source_path", "slug")
    readonly_fields = ("published_at", "updated_at", "checksum", "size_bytes")
