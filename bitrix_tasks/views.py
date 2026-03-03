from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import ConnectionForm, RegisterForm, TaskForm
from .models import (
    Bitrix24Task,
    BitrixConnection,
    Contact,
    ContactSegmentation,
    Run,
    _get_user_limit,
    get_recordings_remaining,
    get_recordings_used,
)


def home(request):
    """Главная: для гостей — приглашение войти; для пользователей — запуски и контакты с тегами."""
    if not request.user.is_authenticated:
        return render(request, "bitrix_tasks/home.html", {"runs": [], "contacts_with_tags": [], "recordings_remaining": None})
    runs_with_result = (
        Run.objects.filter(user=request.user, segmentation_result__isnull=False)
        .select_related("segmentation_result")
        .annotate(trans_count=Count("transcriptions"))
        .order_by("-created_at")[:20]
    )
    contacts_with_tags = (
        ContactSegmentation.objects.filter(user=request.user)
        .select_related("contact")
        .order_by("-updated_at")[:50]
    )
    return render(
        request,
        "bitrix_tasks/home.html",
        {
            "runs": runs_with_result,
            "contacts_with_tags": contacts_with_tags,
            "recordings_remaining": get_recordings_remaining(request.user),
            "max_recordings_allowed": _get_user_limit(request.user),
            "recordings_used": get_recordings_used(request.user),
        },
    )


class RegisterView(FormView):
    form_class = RegisterForm
    template_name = "bitrix_tasks/register.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        user = form.save()
        from .models import UserProfile
        UserProfile.objects.get_or_create(user=user, defaults={"max_recordings_allowed": None})
        login(self.request, user)
        return super().form_valid(form)


@login_required
def my_tasks(request):
    """Список заданий пользователя, форма создания задания, запуск."""
    tasks = Bitrix24Task.objects.filter(user=request.user).select_related("config").order_by("-updated_at")
    limit = _get_user_limit(request.user)
    recordings_used = get_recordings_used(request.user)
    recordings_remaining = get_recordings_remaining(request.user)
    connection = BitrixConnection.objects.filter(user=request.user).first()

    if request.method == "POST" and "create_task" in request.POST:
        form = TaskForm(request.POST)
        if form.is_valid():
            if not connection:
                messages.error(request, "Сначала добавьте вебхук Битрикс24 в разделе «Мой вебхук».")
            else:
                num = form.cleaned_data["num_recordings"]
                remaining = get_recordings_remaining(request.user)
                if remaining is not None and num > remaining:
                    messages.error(request, f"Недостаточно лимита: осталось записей {remaining}.")
                else:
                    task = form.save(commit=False)
                    task.user = request.user
                    task.num_clients = 1
                    task.config = None
                    task.is_active = True
                    task.save()
                    messages.success(request, f"Задание «{task.name}» создано.")
                    return redirect("my_tasks")
        else:
            messages.error(request, "Исправьте ошибки в форме.")
    else:
        form = TaskForm()

    return render(
        request,
        "bitrix_tasks/my_tasks.html",
        {
            "tasks": tasks,
            "max_recordings_allowed": limit,
            "recordings_used": recordings_used,
            "recordings_remaining": recordings_remaining,
            "form": form,
            "connection": connection,
        },
    )


@login_required
def run_task(request, task_id):
    """Запустить задание (POST). Проверка лимита записей."""
    if request.method != "POST":
        return redirect("my_tasks")
    try:
        task = Bitrix24Task.objects.get(pk=task_id, user=request.user)
    except Bitrix24Task.DoesNotExist:
        return redirect("my_tasks")
    remaining = get_recordings_remaining(request.user)
    if remaining is not None and task.num_recordings > remaining:
        messages.error(
            request,
            f"Недостаточно лимита: осталось записей {remaining}. Обратитесь к администратору.",
        )
        return redirect("my_tasks")
    from .tasks import run_download_transcribe_segment
    run_download_transcribe_segment.delay(
        request.user.id,
        task.num_recordings,
        task.num_clients,
        task.config_id,
        task.skip_existing,
    )
    messages.success(request, f"Задание «{task.name}» поставлено в очередь.")
    return redirect("my_tasks")


@login_required
def run_progress(request):
    """Прогресс последнего запуска пользователя (для простого прогресс-бара)."""
    run = (
        Run.objects.filter(user=request.user)
        .order_by("-created_at")
        .first()
    )
    if not run:
        return JsonResponse({"status": "none"})
    total = run.progress_total or run.num_recordings or 0
    current = run.progress_current or 0
    data = {
        "status": run.status,
        "current": current,
        "total": total,
        "run_id": run.id,
        "created_at": run.created_at.isoformat(),
    }
    return JsonResponse(data)


@login_required
def connection_edit(request):
    """Один вебхук Битрикс24 на пользователя: создание или редактирование."""
    connection = BitrixConnection.objects.filter(user=request.user).first()
    if request.method == "POST":
        form = ConnectionForm(request.POST, instance=connection)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.is_active = True
            obj.save()
            messages.success(request, "Вебхук сохранён.")
            return redirect("my_tasks")
    else:
        form = ConnectionForm(instance=connection)
    return render(
        request,
        "bitrix_tasks/connection_edit.html",
        {"form": form, "connection": connection},
    )


@login_required
def contact_list(request):
    """Список контактов пользователя с тегами (только свои)."""
    contacts = Contact.objects.filter(user=request.user).order_by("-updated_at")
    segs = ContactSegmentation.objects.filter(contact__in=contacts).select_related("contact")
    seg_by_cid = {s.contact_id: s for s in segs}
    # Берём по одному последнему brief по контакту (если есть)
    from .models import Transcription

    briefs_qs = (
        Transcription.objects.filter(contact__in=contacts)
        .exclude(brief="")
        .order_by("contact_id", "-updated_at")
        .values("contact_id", "brief")
    )
    brief_by_cid = {}
    for row in briefs_qs:
        cid = row["contact_id"]
        if cid not in brief_by_cid:
            brief_by_cid[cid] = row["brief"]

    contact_list_data = [(c, seg_by_cid.get(c.id), brief_by_cid.get(c.id)) for c in contacts]
    return render(request, "bitrix_tasks/contact_list.html", {"contact_list_data": contact_list_data})


@login_required
def contact_detail(request, contact_id):
    """Контакт: теги и список транскрипций записей."""
    contact = get_object_or_404(Contact, id=contact_id, user=request.user)
    transcriptions = contact.transcriptions.all().order_by("-updated_at")
    try:
        segmentation = contact.segmentation
    except ContactSegmentation.DoesNotExist:
        segmentation = None
    return render(
        request,
        "bitrix_tasks/contact_detail.html",
        {"contact": contact, "transcriptions": transcriptions, "segmentation": segmentation},
    )
