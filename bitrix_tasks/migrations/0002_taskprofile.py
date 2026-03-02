# Create TaskProfile if it was missing (e.g. DB migrated with old 0001)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bitrix_tasks", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, verbose_name="Название задачи")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активно")),
                ("storage_name", models.CharField(default="Общий диск", max_length=200, verbose_name="Имя хранилища")),
                ("folder_path", models.CharField(blank=True, max_length=500, verbose_name="Путь к папке (названия через /)")),
                ("file_mask", models.CharField(default="*", max_length=100, verbose_name="Маска файлов")),
                ("days_lookback", models.PositiveIntegerField(default=7, verbose_name="За сколько дней брать файлы")),
                ("max_files_per_run", models.PositiveIntegerField(default=10, verbose_name="Макс. файлов за один запуск")),
                ("vosk_model_path", models.CharField(max_length=500, verbose_name="Путь к модели Vosk")),
                ("language_code", models.CharField(default="ru", max_length=20, verbose_name="Код языка")),
                ("enable_punctuation", models.BooleanField(default=False, verbose_name="Авто-пунктуация (постобработка)")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="task_profiles", to="bitrix_tasks.bitrixconnection", verbose_name="Подключение Битрикс24")),
            ],
            options={
                "verbose_name": "Постановка задачи",
                "verbose_name_plural": "Постановки задач",
            },
        ),
    ]
