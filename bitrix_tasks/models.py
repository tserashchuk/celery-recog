import json
from pathlib import Path

from django.conf import settings
from django.db import models
from django.contrib.auth.models import User


def _default_vosk_model_path():
    """Путь к модели Vosk в корне проекта (vosk-model-ru-0.42)."""
    return str(Path(settings.BASE_DIR) / "vosk-model-ru-0.42")


class UserProfile(models.Model):
    """Профиль пользователя: общий лимит записей (сквозной)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    max_recordings_allowed = models.PositiveIntegerField(
        "Всего разрешено записей",
        null=True,
        blank=True,
        help_text="Общий лимит обработанных записей для пользователя. Пусто — без ограничения.",
    )

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"

    def __str__(self):
        return str(self.user)


def _get_user_limit(user):
    """Вернуть общий лимит записей для пользователя или None (без лимита)."""
    if not user or not user.is_authenticated:
        return None
    try:
        return user.profile.max_recordings_allowed
    except UserProfile.DoesNotExist:
        return None


def get_recordings_used(user):
    """Сколько записей уже обработано (транскрипций по контактам пользователя)."""
    if not user or not user.is_authenticated:
        return 0
    return Transcription.objects.filter(contact__user=user).count()


def get_recordings_remaining(user):
    """Сколько записей осталось (None = без лимита)."""
    limit = _get_user_limit(user)
    if limit is None:
        return None
    return max(0, limit - get_recordings_used(user))


class BitrixConnection(models.Model):
    """Клиент: подключение к Битрикс24 (вебхук)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="bitrix_connections",
        verbose_name="Пользователь",
    )
    name = models.CharField("Название", max_length=200)
    webhook_url = models.URLField("URL вебхука", max_length=500)
    is_active = models.BooleanField("Активно", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Подключение Битрикс24"
        verbose_name_plural = "Подключения Битрикс24"

    def __str__(self):
        return self.name


class TaskConfig(models.Model):
    """
    Единственная настройка запуска: откуда брать записи и как расшифровывать.
    На вход задачи передаются: количество записей, количество клиентов.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="task_configs",
        verbose_name="Пользователь",
    )
    name = models.CharField("Название", max_length=200, default="По умолчанию")
    # Откуда качать (общий шаблон для всех клиентов)
    storage_name = models.CharField("Имя хранилища", max_length=200, default="Общий диск")
    folder_path = models.CharField(
        "Путь к папке (названия через /)",
        max_length=500,
        blank=True,
        help_text="Оставьте пустым — записи берутся через API телефонии (voximplant.statistic.get), как в sa. Либо укажите путь по диску: Телефония - записи звонков / 2025-03",
    )
    days_lookback = models.PositiveIntegerField("За сколько дней брать файлы", default=7)
    # Vosk (по умолчанию: vosk-model-ru-0.42 в корне проекта)
    vosk_model_path = models.CharField(
        "Путь к модели Vosk",
        max_length=500,
        default=_default_vosk_model_path,
        help_text="По умолчанию: папка vosk-model-ru-0.42 в корне проекта.",
    )
    language_code = models.CharField("Код языка", max_length=20, default="ru")
    # DeepSeek
    deepseek_api_key = models.CharField("DeepSeek API Key", max_length=200, blank=True)
    is_active = models.BooleanField("Активно", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройка задачи"
        verbose_name_plural = "Настройки задачи"

    def __str__(self):
        return self.name


def get_global_config():
    """Общая настройка с одним ключом DeepSeek для всех пользователей (user=None)."""
    return TaskConfig.objects.filter(user__isnull=True, is_active=True).first()


class Bitrix24Task(models.Model):
    """
    Задание Битрикс24: параметры запуска и расписание.
    При сохранении создаётся/обновляется периодическая задача Celery Beat.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="bitrix24_tasks",
        verbose_name="Пользователь",
    )
    name = models.CharField("Название", max_length=200)
    num_recordings = models.PositiveIntegerField("Количество записей", default=5)
    num_clients = models.PositiveIntegerField("Количество клиентов", default=1)
    config = models.ForeignKey(
        TaskConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bitrix24_tasks",
        verbose_name="Настройка задачи",
    )
    interval_minutes = models.PositiveIntegerField(
        "Интервал (минут)",
        default=0,
        help_text="Не используется: задания запускаются только вручную.",
    )
    is_active = models.BooleanField("Включено", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    periodic_task_id = models.PositiveIntegerField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Задание Битрикс24"
        verbose_name_plural = "Задания Битрикс24"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._remove_periodic_task()

    def delete(self, *args, **kwargs):
        self._remove_periodic_task()
        super().delete(*args, **kwargs)

    def _remove_periodic_task(self):
        """Задания запускаются только вручную; убираем запись из расписания Beat (если была)."""
        if self.periodic_task_id:
            try:
                from django_celery_beat.models import PeriodicTask
                PeriodicTask.objects.filter(pk=self.periodic_task_id).delete()
            except Exception:
                pass
            Bitrix24Task.objects.filter(pk=self.pk).update(periodic_task_id=None)


class Run(models.Model):
    """Один запуск задачи: N записей с M клиентов (с прогрессом)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="bitrix_runs",
        verbose_name="Пользователь",
    )
    num_recordings = models.PositiveIntegerField("Запрошено записей")
    num_clients = models.PositiveIntegerField("Запрошено клиентов")
    config = models.ForeignKey(
        TaskConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    status = models.CharField(
        "Статус",
        max_length=20,
        default="pending",
        help_text="pending / running / done / error",
    )
    progress_current = models.PositiveIntegerField(
        "Обработано записей", default=0
    )
    progress_total = models.PositiveIntegerField(
        "Всего записей в запуске", default=0
    )

    class Meta:
        verbose_name = "Запуск"
        verbose_name_plural = "Запуски"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Запуск {self.created_at:%Y-%m-%d %H:%M}"


class Contact(models.Model):
    """Сущность CRM (контакт/лид/сделка), к которой привязаны звонки и теги."""

    ENTITY_TYPES = (("LEAD", "Лид"), ("CONTACT", "Контакт"), ("DEAL", "Сделка"), ("CALL", "Звонок"))

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="bitrix_contacts",
        verbose_name="Пользователь",
    )
    connection = models.ForeignKey(
        BitrixConnection,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="contacts",
    )
    entity_type = models.CharField("Тип сущности", max_length=20, choices=ENTITY_TYPES, default="CALL")
    entity_id = models.CharField("ID в Битрикс", max_length=100, db_index=True)
    display_name = models.CharField("Название", max_length=255, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Контакт (сущность CRM)"
        verbose_name_plural = "Контакты (сущности CRM)"
        unique_together = [["user", "connection", "entity_type", "entity_id"]]
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.get_entity_type_display()} {self.entity_id}" + (f" ({self.display_name})" if self.display_name else "")


class Transcription(models.Model):
    """Одна расшифровка записи; при повторной обработке того же файла по контакту — обновляется."""

    run = models.ForeignKey(
        Run,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transcriptions",
    )
    connection = models.ForeignKey(
        BitrixConnection,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transcriptions",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transcriptions",
        verbose_name="Контакт",
    )
    file_name = models.CharField("Имя файла", max_length=255)
    text = models.TextField("Текст")
    brief = models.TextField(
        "Краткая характеристика звонка",
        blank=True,
        help_text="Короткое описание звонка от DeepSeek",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Транскрипция"
        verbose_name_plural = "Транскрипции"
        ordering = ["-updated_at"]
        unique_together = [["contact", "file_name"]]

    def __str__(self):
        return self.file_name


class SegmentationResult(models.Model):
    """Результат анализа по запуску (сырой JSON от DeepSeek), для истории."""

    run = models.OneToOneField(
        Run,
        on_delete=models.CASCADE,
        related_name="segmentation_result",
    )
    text = models.TextField("Текст результата (JSON с тегами)")
    summary = models.TextField(
        "Саммари по запуску",
        blank=True,
        help_text="Краткое текстовое саммари всех звонков запуска",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Результат сегментации (запуск)"
        verbose_name_plural = "Результаты сегментации (запуск)"

    def __str__(self):
        return f"Результат для запуска {self.run_id}"


class ContactSegmentation(models.Model):
    """Теги сегментации по контакту; при новом запуске — обновляются (перезапись)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="contact_segmentations",
        verbose_name="Пользователь",
    )
    contact = models.OneToOneField(
        Contact,
        on_delete=models.CASCADE,
        related_name="segmentation",
    )
    tags = models.JSONField("Теги", default=list, help_text="Список тегов из DeepSeek")
    raw_response = models.TextField("Ответ DeepSeek (JSON)", blank=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Сегментация контакта"
        verbose_name_plural = "Сегментации контактов"

    def __str__(self):
        return f"Теги для {self.contact}"
