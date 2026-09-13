import uuid

from django.db import models


class Report(models.Model):
    flow_name = models.CharField(max_length=255)
    report_type = models.CharField(max_length=80, default="_default")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("flow_name", "report_type"), name="unique_report_flow_type"
            )
        ]
        ordering = ("flow_name", "report_type")
        permissions = [("view_all_reports", "Can view all reports")]

    def __str__(self):
        return f"{self.flow_name} ({self.report_type})"


class ReportRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="runs")
    idempotency_key = models.CharField(max_length=255, unique=True)
    attributes = models.JSONField(default=dict)
    payload = models.JSONField(null=True, blank=True)
    raw_payload = models.TextField(blank=True)
    status = models.CharField(max_length=40, default="completed")
    source_timestamp = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.report.flow_name} / {self.created_at:%Y-%m-%d %H:%M:%S}"

    @property
    def framework(self):
        return self.attributes.get("sim.framework") or self.attributes.get("framework") or "—"

    @property
    def backend(self):
        return (
            self.attributes.get("hardware.backend")
            or self.attributes.get("sim.backend")
            or self.attributes.get("backend")
            or "—"
        )


class PublishedDocument(models.Model):
    class Kind(models.TextChoices):
        GUIDE = "guide", "Guide"
        REPORT = "report", "Report"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    slug = models.SlugField(max_length=255)
    title = models.CharField(max_length=255)
    source_path = models.CharField(max_length=500)
    content_type = models.CharField(max_length=100)
    content = models.TextField()
    checksum = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    triggered_at = models.DateTimeField(null=True, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("kind", "slug"), name="unique_document_kind_slug")
        ]
        ordering = ("-generated_at", "-updated_at", "title")

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"
