from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import BitrixConnection, Bitrix24Task


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=False, label="Email")

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")


class ConnectionForm(forms.ModelForm):
    """Один вебхук Битрикс24 на пользователя."""

    class Meta:
        model = BitrixConnection
        fields = ("name", "webhook_url")
        labels = {"name": "Название", "webhook_url": "URL вебхука"}
        help_texts = {
            "webhook_url": "Ссылка вида https://ваш-портал.bitrix24.ru/rest/1/xxx/",
        }


class TaskForm(forms.ModelForm):
    """Создание задания из личного кабинета. Запуск только вручную."""

    class Meta:
        model = Bitrix24Task
        fields = ("name", "num_recordings", "skip_existing")
        labels = {
            "name": "Название задания",
            "num_recordings": "Количество записей за запуск",
            "skip_existing": "Пропускать уже транскрибированные записи",
        }
