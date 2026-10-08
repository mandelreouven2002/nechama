import csv
import io
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services, twilio_client
from .models import Game, Mentor, MentorStatus, Message, SiteConfig
from .phone import normalize_phone

log = logging.getLogger(__name__)
TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def nav_context(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"nav_needs_human": Mentor.objects.filter(needs_human=True).count()}


def health(request):
    return HttpResponse("ok", content_type="text/plain")


# --- Twilio webhooks -----------------------------------------------------------

def _twilio_request_ok(request):
    if not settings.TWILIO_VALIDATE_SIGNATURE:
        return True
    base = settings.PUBLIC_URL or request.build_absolute_uri("/").rstrip("/")
    url = base + request.get_full_path()
    params = {k: request.POST.get(k) for k in request.POST}
    return twilio_client.valid_signature(url, params, request.headers.get("X-Twilio-Signature"))


@csrf_exempt
@require_POST
def twilio_inbound(request):
    if not _twilio_request_ok(request):
        log.warning("Rejected inbound webhook with bad signature")
        return HttpResponseForbidden("bad signature")
    params = {k: request.POST.get(k) for k in request.POST}
    if params.get("MessageStatus") and not params.get("Body") and not params.get("NumMedia"):
        services.update_delivery_status(params)
    else:
        try:
            services.handle_inbound(params)
        except ValueError as exc:
            log.warning("Ignored inbound webhook: %s", exc)
    return HttpResponse(TWIML_EMPTY, content_type="text/xml")


@csrf_exempt
@require_POST
def twilio_status(request):
    if not _twilio_request_ok(request):
        return HttpResponseForbidden("bad signature")
    services.update_delivery_status({k: request.POST.get(k) for k in request.POST})
    return HttpResponse(status=204)


# --- Dashboard -----------------------------------------------------------------

@login_required
def dashboard(request):
    last_msg = Message.objects.filter(mentor=OuterRef("pk")).exclude(author="system").order_by(
        "-created_at", "-id"
    )
    mentors = Mentor.objects.annotate(
        last_body=Subquery(last_msg.values("body")[:1]),
        last_author=Subquery(last_msg.values("author")[:1]),
        last_at=Subquery(last_msg.values("created_at")[:1]),
    )
    status = request.GET.get("status", "")
    flag = request.GET.get("flag", "")
    q = request.GET.get("q", "").strip()
    if status:
        mentors = mentors.filter(status=status)
    if flag == "human":
        mentors = mentors.filter(needs_human=True)
    elif flag == "paused":
        mentors = mentors.filter(bot_paused=True)
    elif flag == "optout":
        mentors = mentors.filter(opted_out=True)
    if q:
        mentors = mentors.filter(Q(name__icontains=q) | Q(phone__icontains=q))
    mentors = mentors.order_by("-needs_human", "-last_activity_at", "name")

    counts = dict(Mentor.objects.values_list("status").annotate(n=Count("id")))
    status_tiles = [
        {"value": value, "label": label, "count": counts.get(value, 0)}
        for value, label in MentorStatus.choices
    ]
    config = SiteConfig.get()
    context = {
        "mentors": mentors,
        "status_tiles": status_tiles,
        "total": sum(counts.values()),
        "needs_human": Mentor.objects.filter(needs_human=True).count(),
        "status": status,
        "flag": flag,
        "q": q,
        "config": config,
        "twilio_ready": twilio_client.configured(),
        "ai_ready": bool(settings.OPENAI_API_KEY),
        "free_games": Game.objects.filter(status="free").count(),
    }
    return render(request, "recruit/dashboard.html", context)


@login_required
def mentor_detail(request, pk):
    mentor = get_object_or_404(Mentor, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reply":
            text = request.POST.get("text", "").strip()
            if not text:
                messages.error(request, "אין מה לשלוח.")
            elif not mentor.in_session_window:
                messages.error(
                    request,
                    "עברו יותר מ-24 שעות מההודעה האחרונה של המנחה, ווטסאפ לא מאפשר הודעה חופשית. "
                    "אפשר לשלוח רק תבנית מאושרת.",
                )
            else:
                msg = services.send_and_log(
                    mentor, text, author="staff", staff_user=request.user.get_username()
                )
                if not mentor.bot_paused:
                    Mentor.objects.filter(pk=mentor.pk).update(bot_paused=True)
                    messages.info(request, "נחמה הושתקה מול המנחה הזה כי ענית ידנית. אפשר להחזיר אותה בכפתור.")
                if msg.error:
                    messages.error(request, f"השליחה נכשלה: {msg.error}")
                else:
                    messages.success(request, "נשלח.")
        elif action == "toggle":
            field = request.POST.get("field")
            if field in {"needs_human", "bot_paused", "opted_out"}:
                setattr(mentor, field, not getattr(mentor, field))
                mentor.save(update_fields=[field, "updated_at"])
        elif action == "status":
            value = request.POST.get("status")
            if value in MentorStatus.values:
                mentor.status = value
                mentor.save(update_fields=["status", "updated_at"])
        elif action == "notes":
            mentor.staff_notes = request.POST.get("staff_notes", "")
            mentor.save(update_fields=["staff_notes", "updated_at"])
            messages.success(request, "ההערות נשמרו.")
        return redirect("mentor_detail", pk=mentor.pk)

    chat = mentor.messages.order_by("created_at", "id")
    context = {
        "mentor": mentor,
        "chat": chat,
        "statuses": MentorStatus.choices,
        "games": mentor.games.all(),
    }
    return render(request, "recruit/mentor.html", context)


@login_required
def import_mentors(request):
    if request.method == "POST":
        raw = request.POST.get("rows", "")
        upload = request.FILES.get("file")
        if upload:
            raw += "\n" + upload.read().decode("utf-8-sig", errors="replace")
        created = updated = 0
        bad = []
        for row in csv.reader(io.StringIO(raw.replace("\t", ","))):
            cells = [c.strip() for c in row if c and c.strip()]
            if not cells:
                continue
            phone_cell = next((c for c in cells if normalize_phone(c)), None)
            if not phone_cell:
                if cells[0] not in ("שם", "name", "Name"):
                    bad.append(", ".join(cells))
                continue
            phone = normalize_phone(phone_cell)
            name = next((c for c in cells if c != phone_cell), phone)
            obj, was_created = Mentor.objects.get_or_create(phone=phone, defaults={"name": name})
            if was_created:
                created += 1
            elif name and obj.name != name and obj.name == obj.phone:
                obj.name = name
                obj.save(update_fields=["name", "updated_at"])
                updated += 1
        messages.success(request, f"נוספו {created} מנחים, עודכנו {updated}.")
        if bad:
            messages.warning(request, "שורות שלא זוהה בהן טלפון: " + " | ".join(bad[:10]))
        return redirect("dashboard")
    return render(request, "recruit/import.html")


@login_required
def export_csv(request, kind):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("﻿")
    writer = csv.writer(response)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    if kind == "mentors":
        response["Content-Disposition"] = f'attachment; filename="mentors-{stamp}.csv"'
        writer.writerow(["שם", "טלפון", "סטטוס", "הוסר", "צריך מענה אנושי", "מושתקת", "פניות",
                         "פעילות אחרונה", "העדפות", "סיכום", "הערות צוות"])
        for m in Mentor.objects.order_by("name"):
            writer.writerow([m.name, m.phone, m.get_status_display(), m.opted_out, m.needs_human,
                             m.bot_paused, m.outreach_count,
                             timezone.localtime(m.last_activity_at).strftime("%Y-%m-%d %H:%M") if m.last_activity_at else "",
                             m.preferences, m.summary, m.staff_notes])
    elif kind == "messages":
        response["Content-Disposition"] = f'attachment; filename="messages-{stamp}.csv"'
        writer.writerow(["זמן", "שם", "טלפון", "כותב", "הודעה", "סטטוס מסירה", "שגיאה"])
        for msg in Message.objects.select_related("mentor").order_by("created_at"):
            writer.writerow([timezone.localtime(msg.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                             msg.mentor.name, msg.mentor.phone, msg.get_author_display(),
                             msg.body, msg.delivery_status, msg.error])
    else:
        return HttpResponse(status=404)
    return response
