"""Create the first staff user from DJANGO_ADMIN_USERNAME / DJANGO_ADMIN_PASSWORD (idempotent)."""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create an admin user from environment variables if it does not exist"

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_ADMIN_USERNAME")
        password = os.environ.get("DJANGO_ADMIN_PASSWORD")
        email = os.environ.get("DJANGO_ADMIN_EMAIL", "")
        if not username or not password:
            self.stdout.write("DJANGO_ADMIN_USERNAME/PASSWORD not set; skipping")
            return
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"admin user '{username}' already exists")
            return
        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(f"created admin user '{username}'")
