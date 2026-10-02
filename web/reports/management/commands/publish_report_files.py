import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from web.reports.models import PublishedDocument


class Command(BaseCommand):
    help = "Publish locally generated HTML reports into the Quanifi web application"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reports-dir",
            default=str(settings.BASE_DIR / "reports"),
            help="Directory containing HTML report files (defaults to repo reports/ directory)",
        )

    def handle(self, *args, **options):
        reports_dir = Path(options["reports_dir"]).resolve()
        if not reports_dir.exists():
            self.stdout.write(
                self.style.WARNING(f"Reports directory does not exist: {reports_dir}")
            )
            return

        html_files = sorted(reports_dir.rglob("*.html"))
        if not html_files:
            self.stdout.write(
                self.style.NOTICE(f"No HTML files found in {reports_dir}")
            )
            return

        published_count = 0
        updated_count = 0

        for file_path in html_files:
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception as exc:
                self.stderr.write(f"Failed to read {file_path}: {exc}")
                continue

            rel_path = file_path.relative_to(reports_dir)
            stem_parts = list(rel_path.parent.parts) + [file_path.stem]
            slug = slugify("-".join(stem_parts)) or slugify(file_path.stem) or "report"

            title_match = re.search(
                r"<title>(.*?)</title>", content, re.IGNORECASE | re.DOTALL
            )
            if title_match:
                title = (
                    title_match.group(1)
                    .replace("&mdash;", "—")
                    .replace("&middot;", "·")
                    .strip()
                )
                if "—" in title:
                    title = title.split("—", 1)[-1].strip()
            else:
                title = file_path.stem.replace("-", " ").replace("_", " ").title()

            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
            size_bytes = len(content.encode("utf-8"))
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)

            doc, created = PublishedDocument.objects.update_or_create(
                kind=PublishedDocument.Kind.REPORT,
                slug=slug,
                defaults={
                    "title": title,
                    "source_path": str(
                        file_path.relative_to(settings.BASE_DIR)
                        if file_path.is_relative_to(settings.BASE_DIR)
                        else file_path
                    ),
                    "content_type": "text/html",
                    "content": content,
                    "checksum": checksum,
                    "size_bytes": size_bytes,
                    "metadata": {
                        "filename": file_path.name,
                        "relative_path": str(rel_path),
                    },
                    "generated_at": mtime,
                },
            )

            if created:
                published_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Published report: {title} ({slug})")
                )
            else:
                updated_count += 1
                self.stdout.write(f"Updated report: {title} ({slug})")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Published {published_count} new report(s), updated {updated_count} report(s)."
            )
        )
