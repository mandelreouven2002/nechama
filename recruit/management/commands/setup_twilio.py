"""Point Twilio at this deployment and submit templates for WhatsApp approval.

Safe to run on every deploy: it only changes what differs, and never fails the deploy.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from recruit import twilio_client
from recruit.models import SiteConfig


class Command(BaseCommand):
    help = "Configure Twilio webhooks and WhatsApp template approvals"

    def add_arguments(self, parser):
        parser.add_argument("--no-approvals", action="store_true")

    def log(self, text):
        self.stdout.write(f"[setup_twilio] {text}")

    def handle(self, *args, **options):
        if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
            self.log("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing; skipping")
            return
        inbound, status = twilio_client.inbound_url(), twilio_client.status_callback_url()
        if not inbound:
            self.log("PUBLIC_URL unknown; skipping webhook setup")
        else:
            self._sender_webhook(inbound, status)
            if settings.TWILIO_MESSAGING_SERVICE_SID:
                self._service_webhook(inbound)
        if not options["no_approvals"]:
            self._approvals()

    def _sender_webhook(self, inbound, status):
        target = settings.TWILIO_WHATSAPP_FROM
        if not target:
            self.log("TWILIO_WHATSAPP_FROM missing; cannot locate sender")
            return
        try:
            senders = twilio_client.list_whatsapp_senders()
        except twilio_client.TwilioError as exc:
            self.log(f"could not list senders: {exc}")
            return
        self.log("senders: " + ", ".join(f"{s.get('sender_id')}={s.get('status')}" for s in senders))
        sender = next((s for s in senders if s.get("sender_id") == f"whatsapp:{target}"), None)
        if not sender:
            self.log(f"sender whatsapp:{target} not found")
            return
        props = sender.get("properties") or {}
        self.log(
            f"sender detail: status={sender.get('status')} "
            f"offline_reasons={sender.get('offline_reasons')} "
            f"quality={props.get('quality_rating')} limit={props.get('messaging_limit')}"
        )
        current = sender.get("webhook") or {}
        if current.get("callback_url") == inbound and current.get("status_callback_url") == status:
            self.log("sender webhook already set")
            return
        try:
            twilio_client.update_sender_webhook(sender["sid"], inbound, status)
            self.log(f"sender webhook set to {inbound}")
        except twilio_client.TwilioError as exc:
            self.log(f"could not update sender webhook: {exc}")

    def _service_webhook(self, inbound):
        try:
            twilio_client.update_service_inbound(settings.TWILIO_MESSAGING_SERVICE_SID, inbound)
            self.log("messaging service inbound URL updated")
        except twilio_client.TwilioError as exc:
            self.log(f"could not update messaging service: {exc}")

    def _approvals(self):
        config = SiteConfig.get()
        for sid in filter(None, [config.first_template_sid, config.reminder_template_sid]):
            try:
                content = twilio_client.get_content(sid)
                approval = twilio_client.get_approval(sid)
                wa = approval.get("whatsapp") or {}
                state = wa.get("status") or "unsubmitted"
                if state in ("unsubmitted", "") :
                    name = content.get("friendly_name") or sid.lower()
                    twilio_client.submit_whatsapp_approval(sid, name)
                    self.log(f"template {name}: submitted for WhatsApp approval")
                else:
                    reason = wa.get("rejection_reason") or ""
                    self.log(f"template {content.get('friendly_name')}: {state} {reason}".strip())
            except twilio_client.TwilioError as exc:
                self.log(f"template {sid}: {exc}")
