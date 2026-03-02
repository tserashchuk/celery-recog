# Default Vosk model path: project root / vosk-model-ru-0.42

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bitrix_tasks", "0005_merge_0002_taskprofile_0004_remove_taskprofile"),
    ]

    operations = [
        migrations.AlterField(
            model_name="taskconfig",
            name="vosk_model_path",
            field=models.CharField(
                default="",  # в коде подставляется BASE_DIR / "vosk-model-ru-0.42"
                help_text="По умолчанию: папка vosk-model-ru-0.42 в корне проекта.",
                max_length=500,
                verbose_name="Путь к модели Vosk",
            ),
        ),
    ]
