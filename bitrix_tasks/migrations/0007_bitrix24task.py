# Задание Битрикс24 — настройка запусков Celery Beat

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bitrix_tasks", "0006_taskconfig_vosk_model_path_default"),
    ]

    operations = [
        migrations.CreateModel(
            name="Bitrix24Task",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, verbose_name="Название")),
                ("num_recordings", models.PositiveIntegerField(default=5, verbose_name="Количество записей")),
                ("num_clients", models.PositiveIntegerField(default=1, verbose_name="Количество клиентов")),
                ("interval_minutes", models.PositiveIntegerField(default=60, verbose_name="Интервал (минут)")),
                ("is_active", models.BooleanField(default=True, verbose_name="Включено")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("periodic_task_id", models.PositiveIntegerField(blank=True, editable=False, null=True)),
                ("config", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bitrix24_tasks", to="bitrix_tasks.taskconfig", verbose_name="Настройка задачи")),
            ],
            options={
                "verbose_name": "Задание Битрикс24",
                "verbose_name_plural": "Задания Битрикс24",
            },
        ),
    ]
