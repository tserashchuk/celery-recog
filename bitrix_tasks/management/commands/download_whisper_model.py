"""
Скачать модель faster-whisper в каталог из WHISPER_DOWNLOAD_ROOT один раз.
После этого задачи транскрипции будут брать модель оттуда без повторной загрузки.

Запуск:
    python manage.py download_whisper_model
"""
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Скачать модель faster-whisper в каталог проекта (один раз)."

    def handle(self, *args, **options):
        download_root = getattr(settings, "WHISPER_DOWNLOAD_ROOT", None)
        model_size = getattr(settings, "WHISPER_MODEL_SIZE", "base")
        device = getattr(settings, "WHISPER_DEVICE", "cpu")
        compute_type = getattr(settings, "WHISPER_COMPUTE_TYPE", "int8")

        self.stdout.write(
            f"Загрузка модели '{model_size}' в {download_root or 'кэш по умолчанию'} ..."
        )
        try:
            from bitrix_tasks.tasks import _load_whisper_model

            _load_whisper_model()
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Ошибка: {e}"))
            raise
        self.stdout.write(self.style.SUCCESS("Модель загружена. Дальнейшие запуски будут использовать локальный кэш."))
