from django.contrib import admin

from .models import Game, Mentor, Message, SiteConfig


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("created_at", "author", "body", "delivery_status", "error")
    readonly_fields = fields
    can_delete = False
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Mentor)
class MentorAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "status", "needs_human", "bot_paused", "opted_out",
                    "outreach_count", "last_activity_at")
    list_filter = ("status", "needs_human", "bot_paused", "opted_out", "source")
    list_editable = ("status",)
    search_fields = ("name", "phone")
    readonly_fields = ("last_outreach_at", "last_inbound_at", "last_activity_at", "created_at", "updated_at")
    inlines = [MessageInline]


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "system", "genre", "audience", "status", "reserved_by", "reserved_at")
    list_filter = ("status", "experience", "genre")
    list_editable = ("status",)
    search_fields = ("code", "name", "system", "genre", "description")
    autocomplete_fields = ("reserved_by",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "mentor", "author", "short_body", "delivery_status")
    list_filter = ("author", "direction", "delivery_status")
    search_fields = ("body", "mentor__name", "mentor__phone")
    readonly_fields = [f.name for f in Message._meta.fields]

    @admin.display(description="הודעה")
    def short_body(self, obj):
        return obj.body[:80]

    def has_add_permission(self, request):
        return False


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        ("מתגים ראשיים", {"fields": ("bot_enabled", "outreach_enabled")}),
        ("תוכן", {"fields": ("registration_link", "context_doc_url", "extra_instructions")}),
        ("פניות ותזכורות", {"fields": ("reminder_days", "max_outreach", "outreach_batch",
                                         "send_hour_start", "send_hour_end", "reservation_days")}),
        ("תבניות וואטסאפ", {"fields": ("first_template_sid", "first_template_text",
                                         "reminder_template_sid", "reminder_template_text")}),
        ("גיבוי", {"classes": ("collapse",), "fields": ("context_fallback",)}),
    )

    def has_add_permission(self, request):
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
