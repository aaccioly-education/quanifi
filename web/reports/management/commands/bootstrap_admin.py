import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the configured Quanifi administrator"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Reset an existing administrator's password from QUANIFI_ADMIN_PASSWORD",
        )

    def handle(self, *args, **options):
        email = settings.ADMIN_EMAIL
        password = os.getenv("QUANIFI_ADMIN_PASSWORD")
        if not password:
            raise CommandError("QUANIFI_ADMIN_PASSWORD is required")

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=email,
            defaults={"email": email},
        )
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        if created or options["reset_password"]:
            user.set_password(password)
        user.save()
        action = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"Administrator {email} {action}"))
