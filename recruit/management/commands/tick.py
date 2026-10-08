"""Periodic job (Railway cron): outreach, reservation expiry, leftover replies."""
import logging

from django.core.management.base import BaseCommand

from recruit import services

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run outreach, expire stale reservations and answer leftover messages"

    def add_arguments(self, parser):
        parser.add_argument("--force-window", action="store_true", help="ignore sending hours")

    def handle(self, *args, **options):
        swept = services.sweep_pending()
        expired = services.expire_reservations()
        outreach = services.run_outreach(force_window=options["force_window"])
        self.stdout.write(f"[tick] swept={swept} expired_reservations={expired} outreach={outreach}")
