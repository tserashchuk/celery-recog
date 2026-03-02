import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-change-me-in-production")
DEBUG = os.environ.get("DEBUG", "True").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = [
    x.strip()
    for x in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").replace(" ", ",").split(",")
    if x.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "django_celery_results",
    "bitrix_tasks",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Auth
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Bitrix / Vosk / DeepSeek
# Путь к модели Vosk (по умолчанию: папка vosk-model-ru-0.42 в корне проекта)
VOSK_MODEL_PATH = os.environ.get(
    "VOSK_MODEL_PATH",
    str(BASE_DIR / "vosk-model-small-ru-0.22"),
)
# Глобальный DeepSeek API Key (можно задать здесь или через переменную окружения)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-7f570d1b87dc401c87a80638509eb4a5")
# Для варианта с диском Битрикс24: имя хранилища и путь к папке
BITRIX_STORAGE_NAME = os.environ.get("BITRIX_STORAGE_NAME", "Общий диск")
BITRIX_FOLDER_PATH = os.environ.get("BITRIX_FOLDER_PATH", "").strip()

# Celery
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND", "django_celery_results.backends.DatabaseBackend"
)
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers.DatabaseScheduler"
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
# Без Redis: задачи выполняются сразу в процессе (для теста без worker). Задайте CELERY_EAGER=1
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_EAGER", "").strip() in ("1", "true", "yes")
