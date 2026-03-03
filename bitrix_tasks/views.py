from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import ConnectionForm, DeepSeekQueryForm, RegisterForm, TaskForm
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
    return render(
        request,
        "bitrix_tasks/contact_list.html",
        {
            "contact_list_data": contact_list_data,
        },
    )


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


@login_required
def enrich_contacts(request):
    """Запустить фоновое обогащение контактов CRM-данными для текущего пользователя."""
    from .tasks import enrich_contacts_from_crm

    enrich_contacts_from_crm.delay(request.user.id)
    messages.success(
        request,
        "Обогащение клиентов по CRM запущено. Через некоторое время обновите список клиентов.",
    )
    return redirect("contact_list")


@login_required
def deepseek_query(request):
    """
    Страница, где пользователь может задать произвольный вопрос,
    а DeepSeek ответит на основе его звонков.
    """
    from .models import Transcription
    from .tasks import _deepseek_client

    result = None

    if request.method == "POST":
        form = DeepSeekQueryForm(request.POST)
        if form.is_valid():
            question = form.cleaned_data["question"]

            texts_qs = (
                Transcription.objects.filter(contact__user=request.user)
                .order_by("-updated_at")
                .values_list("text", flat=True)[:100]
            )
            texts = [t or "" for t in texts_qs]
            if not texts:
                result = "Пока нет транскрипций звонков для анализа."
            else:
                client = _deepseek_client()
                joined = "\n\n---\n\n".join(texts)
                max_chars = 20000
                joined = joined[:max_chars]

                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Ты аналитик телефонных звонков и продуктовый консультант. "
                                "Отвечай по-русски, давай структурированные, практические рекомендации."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Вот транскрипции моих звонков с клиентами (обрезаны по длине если нужно):\n\n"
                                f"{joined}\n\n"
                                f"Мой вопрос: {question}"
                            ),
                        },
                    ],
                )
                result = (response.choices[0].message.content or "").strip()
    else:
        form = DeepSeekQueryForm()

    return render(
        request,
        "bitrix_tasks/deepseek_query.html",
        {"form": form, "result": result},
    )


@login_required
def plan_with_ai(request):
    """
    Страница «Составить план работ с ИИ».

    DeepSeek получает срез по контактам (теги + CRM-сnapshot по сделкам/лидам)
    и возвращает краткий приоритизированный план действий по клиентам.
    """
    from .models import ContactSegmentation
    from .tasks import _deepseek_client

    plan_text = None

    if request.method == "POST":
        # Берём до 100 последних сегментаций по контактам пользователя
        segs = (
            ContactSegmentation.objects.filter(user=request.user)
            .select_related("contact")
            .order_by("-updated_at")[:100]
        )
        if not segs:
            plan_text = "Пока нет сегментаций по клиентам. Сначала запустите обработку звонков и сегментацию."
        else:
            lines = []
            for seg in segs:
                c = seg.contact
                snap = seg.crm_snapshot or {}
                deals = snap.get("deals") or {}
                leads = snap.get("leads") or {}
                tags = seg.tags or []

                line_parts = [
                    f"Клиент: {str(c)} (entity_type={c.entity_type}, entity_id={c.entity_id})",
                ]
                if tags:
                    line_parts.append("теги: " + ", ".join(tags))
                if deals:
                    line_parts.append(
                        f"сделки: count={deals.get('count', 0)}, "
                        f"total_amount={deals.get('total_amount', 0)}, "
                        f"won_amount={deals.get('won_amount', 0)}, "
                        f"last_stage_id={deals.get('last_stage_id')}, "
                        f"last_close_date={deals.get('last_close_date')}"
                    )
                if leads:
                    line_parts.append(
                        f"лиды: count={leads.get('count', 0)}, "
                        f"last_status_id={leads.get('last_status_id')}, "
                        f"last_source_id={leads.get('last_source_id')}, "
                        f"last_date_create={leads.get('last_date_create')}"
                    )
                lines.append(" | ".join(line_parts))

            context_block = "\n".join(lines)
            # Ограничиваем длину, чтобы не переполнить контекст
            max_chars = 20000
            context_block = context_block[:max_chars]

            client = _deepseek_client()
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты руководитель отдела продаж и продуктовый аналитик. "
                            "У тебя есть список клиентов с тегами сегментации и агрегатами по сделкам/лидам. "
                            "Нужно составить краткий приоритизированный план работы с каждым клиентом. "
                            "Формат ответа: строго валидный JSON без пояснений и markdown. "
                            "Структура: {\"clients\": [{\"client_label\": str, \"priority\": int, \"segment\": str, \"reason\": str, \"suggested_actions\": [str, ...]}]}. "
                            "client_label — краткий идентификатор клиента (можно брать из строки \"Клиент: ...\"), "
                            "priority — целое число (1 — самый высокий приоритет, далее 2, 3, ...), "
                            "segment — краткое описание сегмента клиента, "
                            "reason — 1–2 фразы, почему именно этот клиент/сегмент в таком приоритете, "
                            "suggested_actions — список конкретных шагов для менеджеров по этому клиенту."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Вот данные по клиентам (одна строка на клиента, содержит тип сущности, ID, теги, агрегаты по сделкам и лидам):\n\n"
                            f"{context_block}\n\n"
                            "На основе этих данных сформируй JSON с ключом \"clients\", "
                            "где элементы отсортированы по возрастанию priority (1, 2, 3, ...)."
                        ),
                    },
                ],
            )
            raw = (response.choices[0].message.content or "").strip()
            try:
                import json as _json

                # Если модель вернула JSON внутри ``` или ```json, убираем обёртку
                if raw.startswith("```"):
                    # отрезаем первую строку с ``` или ```json
                    parts = raw.split("\n")
                    parts = parts[1:]
                    # если в конце есть закрывающие ```, убираем их
                    if parts and parts[-1].strip().startswith("```"):
                        parts = parts[:-1]
                    raw = "\n".join(parts).strip()

                parsed = _json.loads(raw)
                # Поддерживаем оба варианта ключа на всякий случай
                steps = parsed.get("clients") or parsed.get("steps") or []

                # Аккуратно нормализуем переносы строк и лишние пробелы
                def _norm(s: str) -> str:
                    return " ".join((s or "").split())

                normalized_steps = []
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    step = dict(step)
                    for key in ("client_label", "segment", "reason"):
                        if isinstance(step.get(key), str):
                            step[key] = _norm(step[key])
                    actions = step.get("suggested_actions")
                    if isinstance(actions, list):
                        step["suggested_actions"] = [
                            _norm(a) for a in actions if isinstance(a, str)
                        ]
                    normalized_steps.append(step)
                steps = normalized_steps
            except Exception:
                # Если не удалось распарсить JSON — показываем сырой ответ
                plan_text = raw
                steps = []
            else:
                plan_text = None
    else:
        steps = []

    return render(
        request,
        "bitrix_tasks/plan_with_ai.html",
        {"plan": plan_text, "steps": steps},
    )
