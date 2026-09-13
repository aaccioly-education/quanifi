import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Report",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("flow_name", models.CharField(max_length=255)),
                ("report_type", models.CharField(default="_default", max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ("flow_name", "report_type"),
                "permissions": [("view_all_reports", "Can view all reports")],
            },
        ),
        migrations.CreateModel(
            name="ReportRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("idempotency_key", models.CharField(max_length=255, unique=True)),
                ("attributes", models.JSONField(default=dict)),
                ("payload", models.JSONField(blank=True, null=True)),
                ("raw_payload", models.TextField(blank=True)),
                ("status", models.CharField(default="completed", max_length=40)),
                ("source_timestamp", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("report", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="runs", to="reports.report")),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddConstraint(
            model_name="report",
            constraint=models.UniqueConstraint(fields=("flow_name", "report_type"), name="unique_report_flow_type"),
        ),
    ]
