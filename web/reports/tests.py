import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import PublishedDocument, Report, ReportRun


@override_settings(REPORT_INGESTION_TOKEN="test-ingestion-token")
class IngestionTests(TestCase):
    endpoint = "/api/v1/report-runs/"

    def test_token_is_required(self):
        response = self.client.post(
            self.endpoint, data=json.dumps({"flow_name": "grover"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 401)

    def test_ingests_structured_report(self):
        response = self.client.post(
            self.endpoint,
            data=json.dumps(
                {
                    "flow_name": "grover",
                    "report_type": "simulation",
                    "attributes": {"sim.framework": "qiskit"},
                    "payload": {"11": 256},
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-ingestion-token",
            HTTP_IDEMPOTENCY_KEY="run-1",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(ReportRun.objects.get().payload, {"11": 256})

    def test_idempotency_key_prevents_duplicate_runs(self):
        kwargs = {
            "data": json.dumps({"flow_name": "grover", "report_type": "simulation"}),
            "content_type": "application/json",
            "HTTP_AUTHORIZATION": "Bearer test-ingestion-token",
            "HTTP_IDEMPOTENCY_KEY": "same-run",
        }
        self.assertEqual(self.client.post(self.endpoint, **kwargs).status_code, 201)
        self.assertEqual(self.client.post(self.endpoint, **kwargs).status_code, 200)
        self.assertEqual(ReportRun.objects.count(), 1)

    def test_publishes_and_updates_document(self):
        endpoint = "/api/v1/documents/"
        document = {
            "kind": "guide",
            "slug": "getting-started",
            "title": "Getting started",
            "source_path": "docs/guides/getting-started.html",
            "content_type": "text/html",
            "content": "<h1>First version</h1>",
            "checksum": "abc",
            "metadata": {"owner": "Quanifi"},
            "generated_at": "2026-08-27T10:00:00Z",
        }
        kwargs = {
            "content_type": "application/json",
            "HTTP_AUTHORIZATION": "Bearer test-ingestion-token",
        }
        self.assertEqual(self.client.post(endpoint, data=json.dumps(document), **kwargs).status_code, 201)
        document["content"] = "<h1>Second version</h1>"
        self.assertEqual(self.client.post(endpoint, data=json.dumps(document), **kwargs).status_code, 200)
        self.assertEqual(PublishedDocument.objects.count(), 1)
        self.assertIn("Second", PublishedDocument.objects.get().content)


class AuthenticationTests(TestCase):
    def test_health_endpoint_is_public(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_report_list_requires_login(self):
        response = self.client.get(reverse("reports:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_authenticated_user_can_view_reports(self):
        user = get_user_model().objects.create_user("viewer@example.com", password="safe-test-pass")
        self.client.force_login(user)
        response = self.client.get(reverse("reports:list"))
        self.assertEqual(response.status_code, 200)

    def test_report_table_supports_search_and_pagination(self):
        PublishedDocument.objects.bulk_create(
            [
                PublishedDocument(
                    kind="report",
                    slug=f"report-{number}",
                    title=f"Report {number:02d}",
                    source_path=f"reports/report-{number}.html",
                    content_type="text/html",
                    content="<p>report</p>",
                    checksum=str(number),
                )
                for number in range(21)
            ]
        )
        user = get_user_model().objects.create_user("table@example.com", password="safe-test-pass")
        self.client.force_login(user)
        first_page = self.client.get(reverse("reports:list"))
        self.assertContains(first_page, "Page 1 of 2")
        self.assertEqual(len(first_page.context["published_reports"]), 20)
        second_page = self.client.get(reverse("reports:list"), {"page": 2})
        self.assertEqual(len(second_page.context["published_reports"]), 1)
        search = self.client.get(reverse("reports:list"), {"q": "Report 20"})
        self.assertContains(search, "Report 20")
        self.assertNotContains(search, "Report 19")

    def test_document_content_requires_login_and_is_sandboxed(self):
        document = PublishedDocument.objects.create(
            kind="guide",
            slug="safe-guide",
            title="Safe guide",
            source_path="docs/guides/safe.html",
            content_type="text/html",
            content="<h1>Guide</h1><script>alert(1)</script>",
            checksum="abc",
        )
        url = reverse("reports:document_content", args=(document.kind, document.slug))
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user("viewer@example.com", password="safe-test-pass")
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("sandbox", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")


@override_settings(ADMIN_EMAIL="admin@example.com")
class BootstrapAdminTests(TestCase):
    @override_settings(ADMIN_EMAIL="")
    @patch.dict("os.environ", {"QUANIFI_ADMIN_PASSWORD": "safe-bootstrap-pass"})
    def test_bootstrap_requires_a_configured_admin_email(self):
        from django.core.management import call_command

        with self.assertRaises(CommandError):
            call_command("bootstrap_admin")
        self.assertFalse(get_user_model().objects.exists())

    @patch.dict("os.environ", {"QUANIFI_ADMIN_PASSWORD": "safe-bootstrap-pass"})
    def test_bootstrap_creates_configured_superuser(self):
        from django.core.management import call_command

        call_command("bootstrap_admin")
        user = get_user_model().objects.get(username="admin@example.com")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("safe-bootstrap-pass"))

    @patch.dict("os.environ", {"QUANIFI_ADMIN_PASSWORD": "replacement-pass"})
    def test_bootstrap_does_not_reset_existing_admin_password(self):
        from django.core.management import call_command

        user = get_user_model().objects.create_user(
            "admin@example.com", password="owner-chosen-pass"
        )
        call_command("bootstrap_admin")
        user.refresh_from_db()
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("owner-chosen-pass"))

    @patch.dict("os.environ", {"QUANIFI_ADMIN_PASSWORD": "replacement-pass"})
    def test_bootstrap_can_explicitly_reset_existing_admin_password(self):
        from django.core.management import call_command

        user = get_user_model().objects.create_user(
            "admin@example.com", password="owner-chosen-pass"
        )
        call_command("bootstrap_admin", reset_password=True)
        user.refresh_from_db()
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("replacement-pass"))
