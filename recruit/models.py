from datetime import timedelta

from django.db import models
from django.utils import timezone


class MentorStatus(models.TextChoices):
    NEW = "new", "לא נפנה"
    CONTACTED = "contacted", "נשלחה פנייה"
    REPLIED = "replied", "ענה"
    INTERESTED = "interested", "מתעניין"
    REPORTED = "reported", "דיווח שהגיש"
    VERIFIED = "verified", "הגשה מאומתת"
    DECLINED = "declined", "לא מעוניין"


# Statuses Nechama (the model) may set. "verified" is set only by staff.
AI_STATUSES = [
    MentorStatus.REPLIED,
    MentorStatus.INTERESTED,
    MentorStatus.REPORTED,
    MentorStatus.DECLINED,
]


class Mentor(models.Model):
    name = models.CharField("שם", max_length=120)
    phone = models.CharField("טלפון", max_length=20, unique=True, help_text="בפורמט ‎+9725…")
    status = models.CharField(
        "סטטוס", max_length=20, choices=MentorStatus.choices, default=MentorStatus.NEW
    )
    opted_out = models.BooleanField("ביקש הסרה", default=False)
    needs_human = models.BooleanField("צריך מענה אנושי", default=False)
    bot_paused = models.BooleanField(
        "נחמה מושתקת", default=False, help_text="נחמה לא עונה למנחה הזה. צוות מטפל ידנית."
    )
    outreach_count = models.PositiveSmallIntegerField("מונה פניות", default=0)
    last_outreach_at = models.DateTimeField("פנייה אחרונה", null=True, blank=True)
    last_inbound_at = models.DateTimeField("הודעה אחרונה מהמנחה", null=True, blank=True)
    last_activity_at = models.DateTimeField("פעילות אחרונה", null=True, blank=True, db_index=True)
    preferences = models.TextField("העדפות (נחמה ממלאת)", blank=True)
    summary = models.TextField("סיכום שיחה (נחמה ממלאת)", blank=True)
    staff_notes = models.TextField("הערות צוות", blank=True)
    source = models.CharField(
        "מקור",
        max_length=20,
        choices=[("list", "רשימה"), ("inbound", "פנה בעצמו")],
        default="list",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "מנחה"
        verbose_name_plural = "מנחים"
        ordering = ["-last_activity_at", "name"]

    def __str__(self):
        return f"{self.name} ({self.phone})"

    @property
    def first_name(self):
        return (self.name or "").split()[0] if self.name else ""

    @property
    def in_session_window(self):
        """WhatsApp allows free-form messages only within 24h of the mentor's last message."""
        return bool(
            self.last_inbound_at and timezone.now() - self.last_inbound_at < timedelta(hours=24)
        )

    def touch(self, when=None):
        self.last_activity_at = when or timezone.now()


class Message(models.Model):
    DIRECTION_CHOICES = [("in", "נכנסת"), ("out", "יוצאת")]
    AUTHOR_CHOICES = [
        ("mentor", "מנחה"),
        ("nechama", "נחמה"),
        ("staff", "צוות"),
        ("template", "תבנית פנייה"),
        ("system", "מערכת"),
    ]

    mentor = models.ForeignKey(Mentor, on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES)
    author = models.CharField(max_length=10, choices=AUTHOR_CHOICES)
    body = models.TextField(blank=True)
    twilio_sid = models.CharField(max_length=64, unique=True, null=True, blank=True)
    delivery_status = models.CharField(max_length=20, blank=True)
    error = models.CharField(max_length=255, blank=True)
    processed = models.BooleanField(default=True)
    status_after = models.CharField(max_length=20, blank=True)
    staff_user = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "הודעה"
        verbose_name_plural = "הודעות"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["mentor", "created_at"])]

    def __str__(self):
        return f"{self.get_author_display()}: {self.body[:40]}"

    DELIVERY_LABELS = {
        "queued": "בתור",
        "accepted": "בתור",
        "sending": "נשלח",
        "sent": "נשלח",
        "delivered": "נמסר",
        "read": "נקרא",
        "failed": "נכשל",
        "undelivered": "לא נמסר",
    }

    @property
    def delivery_label(self):
        return self.DELIVERY_LABELS.get(self.delivery_status, self.delivery_status)


class GameStatus(models.TextChoices):
    FREE = "free", "פנוי"
    RESERVED = "reserved", "שוריין"
    ASSIGNED = "assigned", "שובץ"


class Game(models.Model):
    code = models.CharField("מזהה", max_length=20, unique=True, help_text="קצר וקבוע, למשל G01")
    name = models.CharField("שם", max_length=150)
    system = models.CharField("מערכת", max_length=100, blank=True)
    genre = models.CharField("ז'אנר", max_length=100, blank=True)
    audience = models.CharField("קהל יעד", max_length=100, blank=True)
    ages = models.CharField("גילאים", max_length=30, blank=True)
    players_min = models.PositiveSmallIntegerField("שחקנים מינימום", null=True, blank=True)
    players_max = models.PositiveSmallIntegerField("שחקנים מקסימום", null=True, blank=True)
    duration = models.CharField("משך", max_length=30, blank=True)
    experience = models.CharField(
        "ניסיון נדרש מהמנחה",
        max_length=20,
        blank=True,
        choices=[("beginner", "מתחיל"), ("intermediate", "בינוני"), ("experienced", "מנוסה")],
    )
    description = models.TextField("תיאור קצר", blank=True, help_text="זה מה שנחמה מציגה למנחה")
    materials_url = models.URLField("קישור לחומרים", blank=True)
    status = models.CharField(
        "סטטוס שיבוץ", max_length=20, choices=GameStatus.choices, default=GameStatus.FREE
    )
    reserved_by = models.ForeignKey(
        Mentor,
        verbose_name="משוריין ל",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="games",
    )
    reserved_at = models.DateTimeField("תאריך שריון", null=True, blank=True)

    class Meta:
        verbose_name = "משחק"
        verbose_name_plural = "משחקים"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} – {self.name}"

    def as_prompt_line(self):
        players = ""
        if self.players_min or self.players_max:
            players = f"{self.players_min or '?'}–{self.players_max or '?'} שחקנים"
        parts = [
            self.code,
            self.name,
            self.system,
            self.genre,
            self.audience,
            self.ages,
            players,
            self.duration,
            self.get_experience_display() if self.experience else "",
            self.description,
        ]
        return " | ".join(p for p in parts if p)


class SiteConfig(models.Model):
    """Single row of settings the team edits in the admin."""

    registration_link = models.URLField("קישור הרשמה להנחייה", blank=True)
    context_doc_url = models.URLField(
        "קישור למסמך ההקשר (Google Docs)",
        blank=True,
        help_text="המסמך צריך להיות משותף כ'כל מי שיש לו את הקישור יכול לצפות'.",
    )
    context_fallback = models.TextField(
        "עותק גיבוי של מסמך ההקשר",
        blank=True,
        help_text="מתעדכן אוטומטית מהמסמך. משמש אם Google לא זמין.",
    )
    extra_instructions = models.TextField(
        "הנחיות נוספות לנחמה", blank=True, help_text="מתווסף להנחיות הקבועות. לא חובה."
    )
    bot_enabled = models.BooleanField("נחמה עונה להודעות", default=True)
    outreach_enabled = models.BooleanField(
        "פנייה יזומה פעילה",
        default=False,
        help_text="כבוי כברירת מחדל. כשדולק, נחמה שולחת פניות ותזכורות למנחים ברשימה.",
    )
    reminder_days = models.PositiveSmallIntegerField("ימים בין תזכורות", default=3)
    max_outreach = models.PositiveSmallIntegerField("מקסימום פניות למנחה", default=3)
    reservation_days = models.PositiveSmallIntegerField("ימי תפוגת שריון", default=7)
    outreach_batch = models.PositiveSmallIntegerField("מקסימום פניות בכל ריצה", default=25)
    send_hour_start = models.PositiveSmallIntegerField("שעת התחלה לפניות", default=10)
    send_hour_end = models.PositiveSmallIntegerField("שעת סיום לפניות", default=20)
    first_template_sid = models.CharField("Content SID – פנייה ראשונה", max_length=64, blank=True)
    reminder_template_sid = models.CharField("Content SID – תזכורת", max_length=64, blank=True)
    first_template_text = models.TextField(
        "טקסט הפנייה הראשונה", blank=True, help_text="לתיעוד בשיחה. [שם] יוחלף בשם המנחה."
    )
    reminder_template_text = models.TextField("טקסט התזכורת", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "הגדרות"
        verbose_name_plural = "הגדרות"

    def __str__(self):
        return "הגדרות נחמה"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
