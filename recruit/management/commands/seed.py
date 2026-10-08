"""Create the settings row with sensible defaults (idempotent: never overwrites edits)."""
from django.core.management.base import BaseCommand

from recruit.models import SiteConfig

DEFAULTS = {
    "context_doc_url": "https://docs.google.com/document/d/1v-NX-6_WnJBGCe8-zOLaw0zDHJ4sQsLq0GqyRPlLBLU/edit",
    "first_template_sid": "HXc5de1870bf91096fffbc15c780391635",
    "reminder_template_sid": "HX849c6d9f98b5e6c8e6624fd23ce087fb",
    "first_template_text": (
        "היי [שם]! כאן נחמה מצוות משחקי התפקידים של אייקון 🎲 אנחנו מתחילים לגייס מנחים "
        "לאייקון הבא, ורציתי לשאול אם בא לך להנחות. צריך קישור, רעיון למשחק או שיש שאלה? "
        "אפשר פשוט לענות לי כאן. (לא רלוונטי? אפשר להשיב \"הסר\")"
    ),
    "reminder_template_text": (
        "היי [שם], נחמה מאייקון שוב 🙂 רק מקפיצה: בא לך להנחות באייקון הקרוב? "
        "אם צריך קישור או רעיון למשחק, אני פה. (להסרה אפשר להשיב \"הסר\")"
    ),
}


class Command(BaseCommand):
    help = "Create default settings if missing"

    def handle(self, *args, **options):
        config, created = SiteConfig.objects.get_or_create(pk=1, defaults=DEFAULTS)
        if not created:
            changed = [k for k, v in DEFAULTS.items() if not getattr(config, k)]
            for key in changed:
                setattr(config, key, DEFAULTS[key])
            if changed:
                config.save()
        self.stdout.write(f"settings ready (created={created})")
