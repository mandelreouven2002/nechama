"""Core flows: inbound messages, Nechama's replies, outreach, reservations."""
import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone

from . import agent, twilio_client
from .context import get_context_text
from .models import AI_STATUSES, Game, GameStatus, Mentor, MentorStatus, Message, SiteConfig
from .phone import normalize_phone
from .prompts import GAME_TAKEN_REPLY, OPT_OUT_KEYWORDS, OPT_OUT_REPLY

log = logging.getLogger(__name__)
HISTORY_LIMIT = 30
LOCKED_STATUSES = {MentorStatus.VERIFIED}


# --- inbound -----------------------------------------------------------------

def is_opt_out(text):
    return (text or "").strip().strip(".!").lower() in OPT_OUT_KEYWORDS


def record_inbound(params):
    """Store an inbound WhatsApp message from a Twilio webhook.

    Returns (mentor, message, created). created=False means a duplicate webhook.
    """
    phone = normalize_phone(params.get("From") or params.get("WaId"))
    if not phone:
        raise ValueError(f"Unrecognised sender: {params.get('From')!r}")
    body = (params.get("Body") or "").strip()
    num_media = int(params.get("NumMedia") or 0)
    if not body and num_media:
        body = "[נשלחה מדיה ללא טקסט]"
    now = timezone.now()
    mentor, _ = Mentor.objects.get_or_create(
        phone=phone,
        defaults={
            "name": (params.get("ProfileName") or phone).strip()[:120],
            "source": "inbound",
            "status": MentorStatus.REPLIED,
        },
    )
    try:
        with transaction.atomic():
            message = Message.objects.create(
                mentor=mentor,
                direction="in",
                author="mentor",
                body=body,
                twilio_sid=params.get("MessageSid") or None,
                processed=False,
                status_after=mentor.status,
                created_at=now,
            )
    except IntegrityError:
        return mentor, None, False
    Mentor.objects.filter(pk=mentor.pk).update(last_inbound_at=now, last_activity_at=now)
    mentor.refresh_from_db()
    return mentor, message, True


def handle_inbound(params):
    """Entry point for the webhook. Fast: stores the message and schedules the reply."""
    mentor, message, created = record_inbound(params)
    if not created:
        return
    if is_opt_out(message.body):
        handle_opt_out(mentor, message)
        return
    schedule_reply(mentor.pk)


def handle_opt_out(mentor, message):
    Mentor.objects.filter(pk=mentor.pk).update(opted_out=True)
    Message.objects.filter(pk=message.pk).update(processed=True)
    mentor.refresh_from_db()
    send_and_log(mentor, OPT_OUT_REPLY, author="nechama")


def schedule_reply(mentor_id):
    if settings.NECHAMA_PROCESS_INLINE:
        process_mentor(mentor_id)
        return
    thread = threading.Thread(target=_process_in_thread, args=(mentor_id,), daemon=True)
    thread.start()


def _process_in_thread(mentor_id):
    try:
        time.sleep(settings.NECHAMA_REPLY_DELAY)
        process_mentor(mentor_id)
    except Exception:  # noqa: BLE001 - never let a thread die silently
        log.exception("Failed processing mentor %s", mentor_id)
    finally:
        close_old_connections()


# --- reply generation --------------------------------------------------------

def _history_text(mentor):
    labels = {"mentor": "המנחה", "nechama": "נחמה", "template": "נחמה", "staff": "צוות (אדם)"}
    rows = list(
        mentor.messages.exclude(author="system").order_by("-created_at", "-id")[:HISTORY_LIMIT]
    )
    lines = []
    for m in reversed(rows):
        stamp = timezone.localtime(m.created_at).strftime("%d.%m %H:%M")
        lines.append(f"{labels.get(m.author, m.author)} ({stamp}): {m.body}")
    return "\n".join(lines) or "(אין הודעות קודמות)"


def build_input(mentor, pending, config):
    games = Game.objects.filter(status=GameStatus.FREE)
    games_text = "\n".join(g.as_prompt_line() for g in games) or "(אין כרגע משחקים פנויים ברשימה)"
    mine = Game.objects.filter(reserved_by=mentor).exclude(status=GameStatus.FREE)
    mine_text = ", ".join(f"{g.code} {g.name} ({g.get_status_display()})" for g in mine) or "אין"
    now = timezone.localtime().strftime("%A %d.%m.%Y %H:%M")
    new_text = "\n".join(m.body for m in pending)
    return (
        f"## עכשיו\n{now} (שעון ישראל)\n\n"
        f"## פרטי המנחה\nשם: {mentor.name}\n"
        f"סטטוס נוכחי: {mentor.status} ({mentor.get_status_display()})\n"
        f"העדפות שנשמרו: {mentor.preferences or '(אין עדיין)'}\n"
        f"סיכום השיחה עד עכשיו: {mentor.summary or '(אין עדיין)'}\n"
        f"משחקים שכבר משוריינים לו/ה: {mine_text}\n\n"
        f"## קישור הרשמה להנחייה\n{config.registration_link or '⟦למילוי: קישור הרשמה⟧'}\n\n"
        f"## מסמך ההקשר על אייקון\n{get_context_text(config) or '(המסמך ריק)'}\n\n"
        f"## משחקים פנויים לשידוך (מזהה | שם | מערכת | ז'אנר | קהל | גילאים | שחקנים | משך | ניסיון | תיאור)\n{games_text}\n\n"
        f"## השיחה עד עכשיו (מהישנה לחדשה)\n{_history_text(mentor)}\n\n"
        f"## ההודעות החדשות של המנחה, שעלייך לענות עליהן\n{new_text}\n\n"
        "החזירי JSON לפי הסכמה."
    )


def try_reserve(mentor, code):
    code = (code or "").strip()
    if not code:
        return None
    updated = Game.objects.filter(code__iexact=code, status=GameStatus.FREE).update(
        status=GameStatus.RESERVED, reserved_by=mentor, reserved_at=timezone.now()
    )
    if updated:
        return True
    already_mine = Game.objects.filter(code__iexact=code, reserved_by=mentor).exists()
    return True if already_mine else False


def process_mentor(mentor_id):
    """Answer all unprocessed inbound messages of one mentor with a single reply."""
    config = SiteConfig.get()
    with transaction.atomic():
        mentor = Mentor.objects.select_for_update().get(pk=mentor_id)
        pending = list(
            mentor.messages.filter(direction="in", processed=False).order_by("created_at", "id")
        )
        if not pending:
            return None
        ids = [m.pk for m in pending]
        if not config.bot_enabled or mentor.bot_paused:
            Message.objects.filter(pk__in=ids).update(processed=True)
            if not mentor.bot_paused:
                Mentor.objects.filter(pk=mentor.pk).update(needs_human=True)
            return None

        try:
            result = agent.ask_nechama(build_input(mentor, pending, config), config.extra_instructions)
        except agent.AgentError as exc:
            log.error("Agent error for mentor %s: %s", mentor.pk, exc)
            Message.objects.filter(pk__in=ids).update(processed=True)
            Mentor.objects.filter(pk=mentor.pk).update(needs_human=True)
            Message.objects.create(
                mentor=mentor, direction="out", author="system",
                body=f"נחמה לא הצליחה לענות (שגיאת AI). ההודעה סומנה למענה אנושי. {exc}"[:2000],
            )
            return None

        reply = result["reply"].strip()
        reserved = try_reserve(mentor, result.get("reserve_game_id"))
        if reserved is False:
            reply = GAME_TAKEN_REPLY

        new_status = result.get("status")
        if mentor.status in LOCKED_STATUSES or new_status not in AI_STATUSES:
            new_status = mentor.status
        mentor.status = new_status
        mentor.preferences = result.get("preferences", mentor.preferences)
        mentor.summary = result.get("summary", mentor.summary)
        mentor.needs_human = mentor.needs_human or bool(result.get("needs_human"))
        if result.get("opt_out"):
            mentor.opted_out = True
        mentor.save(
            update_fields=["status", "preferences", "summary", "needs_human", "opted_out", "updated_at"]
        )
        Message.objects.filter(pk__in=ids).update(processed=True)
        return send_and_log(mentor, reply, author="nechama")


# --- sending -----------------------------------------------------------------

def send_and_log(mentor, body, author, staff_user="", content_sid=None, variables=None):
    """Send a WhatsApp message and store it. On failure, store it with the error."""
    sid, error = None, ""
    try:
        sid = twilio_client.send_whatsapp(
            mentor.phone, body=body, content_sid=content_sid, variables=variables
        )
    except twilio_client.TwilioError as exc:
        error = str(exc)[:255]
        log.error("Send to %s failed: %s", mentor.phone, exc)
    now = timezone.now()
    msg = Message.objects.create(
        mentor=mentor,
        direction="out",
        author=author,
        body=body,
        twilio_sid=sid,
        delivery_status="queued" if sid else "failed",
        error=error,
        status_after=mentor.status,
        staff_user=staff_user,
        created_at=now,
    )
    Mentor.objects.filter(pk=mentor.pk).update(last_activity_at=now)
    return msg


def update_delivery_status(params):
    sid = params.get("MessageSid") or params.get("SmsSid")
    status = params.get("MessageStatus") or params.get("SmsStatus") or ""
    if not sid:
        return 0
    fields = {"delivery_status": status[:20]}
    if params.get("ErrorCode"):
        fields["error"] = f"{params.get('ErrorCode')} {params.get('ErrorMessage', '')}"[:255]
    return Message.objects.filter(twilio_sid=sid).update(**fields)


# --- outreach & housekeeping ---------------------------------------------------

def in_send_window(config, now=None):
    local = timezone.localtime(now or timezone.now())
    # Sunday–Thursday (Python: Mon=0 … Sun=6)
    if local.weekday() not in (6, 0, 1, 2, 3):
        return False
    return config.send_hour_start <= local.hour < config.send_hour_end


def outreach_candidates(config, now=None):
    now = now or timezone.now()
    due_before = now - timedelta(days=config.reminder_days)
    base = Mentor.objects.filter(
        opted_out=False,
        bot_paused=False,
        status__in=[MentorStatus.NEW, MentorStatus.CONTACTED],
        outreach_count__lt=config.max_outreach,
    )
    first = base.filter(outreach_count=0)
    reminders = base.filter(outreach_count__gt=0, last_outreach_at__lte=due_before)
    return list(first.order_by("created_at")) + list(reminders.order_by("last_outreach_at"))


def run_outreach(now=None, force_window=False):
    config = SiteConfig.get()
    if not config.outreach_enabled:
        return {"skipped": "outreach disabled"}
    if not force_window and not in_send_window(config, now):
        return {"skipped": "outside sending window"}
    sent = failed = 0
    for mentor in outreach_candidates(config, now)[: config.outreach_batch]:
        first = mentor.outreach_count == 0
        sid = config.first_template_sid if first else config.reminder_template_sid
        text = config.first_template_text if first else config.reminder_template_text
        if not sid:
            continue
        body = (text or "[תבנית פנייה]").replace("[שם]", mentor.first_name)
        msg = send_and_log(
            mentor, body, author="template", content_sid=sid, variables={"1": mentor.first_name}
        )
        if msg.twilio_sid:
            sent += 1
            Mentor.objects.filter(pk=mentor.pk).update(
                status=MentorStatus.CONTACTED,
                outreach_count=mentor.outreach_count + 1,
                last_outreach_at=timezone.now(),
            )
        else:
            failed += 1
    return {"sent": sent, "failed": failed}


def expire_reservations(now=None):
    config = SiteConfig.get()
    cutoff = (now or timezone.now()) - timedelta(days=config.reservation_days)
    stale = Game.objects.filter(status=GameStatus.RESERVED, reserved_at__lt=cutoff).exclude(
        reserved_by__status__in=[MentorStatus.REPORTED, MentorStatus.VERIFIED]
    )
    return stale.update(status=GameStatus.FREE, reserved_by=None, reserved_at=None)


def sweep_pending(older_than_seconds=120):
    """Answer inbound messages a crashed/restarted thread left behind."""
    cutoff = timezone.now() - timedelta(seconds=older_than_seconds)
    ids = (
        Message.objects.filter(direction="in", processed=False, created_at__lt=cutoff)
        .values_list("mentor_id", flat=True)
        .distinct()
    )
    count = 0
    for mentor_id in ids:
        try:
            process_mentor(mentor_id)
            count += 1
        except Exception:  # noqa: BLE001
            log.exception("Sweep failed for mentor %s", mentor_id)
    return count
