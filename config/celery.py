import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("bitratata")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.broker_connection_retry_on_startup = True
app.conf.broker_pool_limit = 20
app.conf.broker_heartbeat = None
app.conf.broker_connection_timeout = 30
app.autodiscover_tasks()
