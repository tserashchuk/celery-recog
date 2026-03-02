# Удаление TaskProfile (старый артефакт, логика переехала на TaskConfig + Run)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("bitrix_tasks", "0002_taskprofile"),
        ("bitrix_tasks", "0003_taskconfig_run_transcription_segmentationresult"),
    ]

    operations = [
        migrations.DeleteModel(name="TaskProfile"),
    ]
