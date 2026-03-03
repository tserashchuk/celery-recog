from django.contrib import admin, messages
from .models import (
    Bitrix24Task,
    BitrixConnection,
    Contact,
    ContactSegmentation,
    Run,
    SegmentationResult,
    Transcription,
    UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "max_recordings_allowed")
    search_fields = ("user__username",)


@admin.register(Bitrix24Task)
class Bitrix24TaskAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "num_recordings", "num_clients", "config", "interval_minutes", "is_active", "updated_at")
    list_filter = ("is_active", "config", "user")
    search_fields = ("name",)
    actions = ["run_now"]

    def run_now(self, request, queryset):
        from .tasks import run_download_transcribe_segment
        for task in queryset:
            run_download_transcribe_segment.delay(
                task.user_id,
                task.num_recordings,
                task.num_clients,
                task.config_id,
                task.skip_existing,
            )
            self.message_user(
                request,
                f"Запущено: «{task.name}» ({task.num_recordings} записей, {task.num_clients} клиент(ов)).",
                messages.SUCCESS,
            )
        if queryset.count() > 1:
            self.message_user(request, f"Поставлено в очередь: {queryset.count()} заданий.", messages.SUCCESS)

    run_now.short_description = "Запустить сейчас (тестовый запуск)"


@admin.register(BitrixConnection)
class BitrixConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "webhook_url_short", "is_active", "updated_at")
    list_filter = ("is_active", "user")
    search_fields = ("name", "webhook_url")

    def webhook_url_short(self, obj):
        url = obj.webhook_url
        return url[:50] + "…" if len(url) > 50 else url

    webhook_url_short.short_description = "Вебхук"


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "num_recordings", "num_clients", "status", "progress_current", "progress_total", "created_at", "transcriptions_count")
    list_filter = ("status", "user")
    date_hierarchy = "created_at"

    def transcriptions_count(self, obj):
        return obj.transcriptions.count()

    transcriptions_count.short_description = "Транскрипций"


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("entity_type", "entity_id", "user", "connection", "display_name", "updated_at")
    list_filter = ("entity_type", "user")


@admin.register(ContactSegmentation)
class ContactSegmentationAdmin(admin.ModelAdmin):
    list_display = ("contact", "user", "updated_at")
    list_filter = ("user",)


@admin.register(Transcription)
class TranscriptionAdmin(admin.ModelAdmin):
    list_display = ("file_name", "contact", "run", "connection", "updated_at")
    list_filter = ("run", "connection", "contact")
    search_fields = ("file_name", "text")


@admin.register(SegmentationResult)
class SegmentationResultAdmin(admin.ModelAdmin):
    list_display = ("run", "created_at")
    search_fields = ("text",)
