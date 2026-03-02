# New flow: Run, Transcription, SegmentationResult, TaskConfig

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bitrix_tasks", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="По умолчанию", max_length=200, verbose_name="Название")),
                ("storage_name", models.CharField(default="Общий диск", max_length=200, verbose_name="Имя хранилища")),
                ("folder_path", models.CharField(blank=True, max_length=500, verbose_name="Путь к папке (названия через /)")),
                ("days_lookback", models.PositiveIntegerField(default=7, verbose_name="За сколько дней брать файлы")),
                ("vosk_model_path", models.CharField(max_length=500, verbose_name="Путь к модели Vosk")),
                ("language_code", models.CharField(default="ru", max_length=20, verbose_name="Код языка")),
                ("deepseek_api_key", models.CharField(blank=True, max_length=200, verbose_name="DeepSeek API Key")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активно")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Настройка задачи",
                "verbose_name_plural": "Настройки задачи",
            },
        ),
        migrations.CreateModel(
            name="Run",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("num_recordings", models.PositiveIntegerField(verbose_name="Запрошено записей")),
                ("num_clients", models.PositiveIntegerField(verbose_name="Запрошено клиентов")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("config", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="runs", to="bitrix_tasks.taskconfig", verbose_name="Настройка")),
            ],
            options={
                "verbose_name": "Запуск",
                "verbose_name_plural": "Запуски",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="Transcription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file_name", models.CharField(max_length=255, verbose_name="Имя файла")),
                ("text", models.TextField(verbose_name="Текст")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("connection", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="transcriptions", to="bitrix_tasks.bitrixconnection", verbose_name="Клиент")),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transcriptions", to="bitrix_tasks.run", verbose_name="Запуск")),
            ],
            options={
                "verbose_name": "Транскрипция",
                "verbose_name_plural": "Транскрипции",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SegmentationResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.TextField(verbose_name="Текст результата")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("run", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="segmentation_result", to="bitrix_tasks.run", verbose_name="Запуск")),
            ],
            options={
                "verbose_name": "Результат сегментации",
                "verbose_name_plural": "Результаты сегментации",
            },
        ),
    ]
