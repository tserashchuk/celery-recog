"""
Задача по образцу sa: на вход — количество записей и количество клиентов.
Выкачивание → по одной транскрибация (faster-whisper) → сохранение в БД → отправка в DeepSeek → результат сегментации.
API Битрикс24 вызывается через requests (как в sa), без пакета bitrix24.
"""
import io
import json as json_lib
import logging
import os
import tempfile
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from celery import shared_task
from django.conf import settings

from bitrix_tasks.models import (
    BitrixConnection,
    Contact,
    ContactSegmentation,
    Run,
    SegmentationResult,
    Transcription,
    UserProfile,
    _get_user_limit,
    get_recordings_used,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="bitrix_tasks.run_download_transcribe_segment")
def run_download_transcribe_segment(
    self,
    user_id,
    num_recordings: int,
    num_clients: int,
    config_id=None,
    skip_existing: bool = True,
):
    """
    user_id — владелец (или None для «все подключения», совместимость с админкой).
    Для каждого подключения качаем записи, по одной расшифровываем Vosk, группируем по контакту (CRM),
    обновляем/создаём транскрипции и теги сегментации по контактам.
    """
    user = _get_user(user_id) if user_id else None
    limit = _get_user_limit(user) if user_id else None
    if user_id and limit is not None:
        used = get_recordings_used(user)
        if used + num_recordings > limit:
            return {"error": f"Превышен общий лимит записей: использовано {used}, осталось {limit - used}"}

    # Для пользователя всегда одно подключение (его вебхук)
    connections = list(
        _get_connections(user_id, 1 if user_id else num_clients)
    )
    if not connections:
        logger.warning("Нет активных подключений Битрикс24")
        return {"error": "Нет активных клиентов (подключений)"}

    print(f"[Bitratata] Запуск задачи: до {num_recordings} записей, {len(connections)} подключений")
    run = Run.objects.create(
        user_id=user_id,
        num_recordings=num_recordings,
        num_clients=num_clients,
        config=None,
        status="running",
        progress_total=num_recordings,
        progress_current=0,
    )

    # Модель faster-whisper загружаем один раз на весь запуск (не на каждый файл)
    whisper_model = _load_whisper_model()

    per_client = max(1, num_recordings // len(connections))
    total_processed = 0

    for conn in connections:
        if total_processed >= num_recordings:
            break
        want = min(per_client, num_recordings - total_processed)
        try:
            files = _fetch_files_from_bitrix(conn, limit=want)
        except Exception as e:
            logger.exception("Ошибка загрузки списка файлов для %s: %s", conn.name, e)
            continue
        print(f"[Bitratata] Подключение «{conn.name}»: получено {len(files)} файлов")
        for file_info in files:
            if total_processed >= num_recordings:
                break
            try:
                name = file_info.get("NAME", "?")
                # Вариант: пропускать уже существующие транскрипции (по настройке задания)
                if skip_existing:
                    if not user_id:
                        if Transcription.objects.filter(connection=conn, file_name=name).exists():
                            print(f"[Bitratata] Пропуск {name}: транскрипция уже есть в БД")
                            continue
                    else:
                        contact = _get_or_create_contact(
                            user_id=user_id,
                            connection=conn,
                            entity_type=file_info.get("ENTITY_TYPE") or "CALL",
                            entity_id=str(file_info.get("ENTITY_ID") or file_info.get("ID") or total_processed),
                        )
                        if Transcription.objects.filter(contact=contact, file_name=name).exists():
                            print(f"[Bitratata] Пропуск {name}: транскрипция уже есть для контакта")
                            continue
                else:
                    # Если не пропускаем существующие — всё равно создаём/обновляем contact,
                    # чтобы ниже update_or_create сработал корректно.
                    if user_id:
                        contact = _get_or_create_contact(
                            user_id=user_id,
                            connection=conn,
                            entity_type=file_info.get("ENTITY_TYPE") or "CALL",
                            entity_id=str(file_info.get("ENTITY_ID") or file_info.get("ID") or total_processed),
                        )

                print(f"[Bitratata] Обработка {total_processed + 1}/{num_recordings}: {name} ...")
                text = _download_and_transcribe(file_info, conn, whisper_model)
                if not text:
                    continue
                if not user_id:
                    Transcription.objects.create(
                        run=run,
                        connection=conn,
                        file_name=name,
                        text=text,
                    )
                    total_processed += 1
                    print(f"[Bitratata] Готово: {len(text.split())} слов")
                    if total_processed % 3 == 0 or total_processed == num_recordings:
                        Run.objects.filter(pk=run.id).update(progress_current=total_processed)
                    continue
                # user_id есть: contact уже создан выше
                display = _fetch_contact_display_name(
                    conn,
                    file_info.get("ENTITY_TYPE") or "CALL",
                    str(file_info.get("ENTITY_ID") or file_info.get("ID") or ""),
                )
                if display:
                    contact.display_name = display[:255]
                    contact.save(update_fields=["display_name", "updated_at"])
                trans, created = Transcription.objects.update_or_create(
                    contact=contact,
                    file_name=name,
                    defaults={
                        "run": run,
                        "connection": conn,
                        "text": text,
                    },
                )
                total_processed += 1
                print(
                    "[Bitratata] Готово: "
                    f"{len(text.split())} слов"
                    + (" (обновлено)" if not created else "")
                )
                if total_processed % 3 == 0 or total_processed == num_recordings:
                    Run.objects.filter(pk=run.id).update(progress_current=total_processed)
            except Exception as e:
                logger.exception("Ошибка транскрибации %s: %s", file_info.get("NAME"), e)
                print(f"[Bitratata] Ошибка: {file_info.get('NAME')} — {e}")

    if not user_id:
        texts = list(run.transcriptions.values_list("text", flat=True))
        if not texts:
            print("[Bitratata] Нет транскрипций для DeepSeek")
            Run.objects.filter(pk=run.id).update(status="done", progress_current=total_processed)
            return {"run_id": run.id, "transcriptions_count": 0}
        print(f"[Bitratata] Транскрипций: {len(texts)}. Отправка в DeepSeek ...")
        try:
            segment_text = _send_to_deepseek_flat(texts)
            seg = SegmentationResult.objects.create(run=run, text=segment_text)
            # Полное саммари по запуску (с разбиением на части при необходимости)
            try:
                summary = _send_run_summary(texts)
                seg.summary = summary
                seg.save(update_fields=["summary"])
            except Exception as e:
                logger.debug("Не удалось получить саммари запуска: %s", e)
            Run.objects.filter(pk=run.id).update(status="done", progress_current=total_processed)
            print("[Bitratata] DeepSeek готов. Запуск завершён.")
        except Exception as e:
            logger.exception("Ошибка DeepSeek: %s", e)
            print(f"[Bitratata] Ошибка DeepSeek: {e}")
            Run.objects.filter(pk=run.id).update(status="error")
            return {"run_id": run.id, "transcriptions_count": len(texts), "deepseek_error": str(e)}
        return {"run_id": run.id, "transcriptions_count": len(texts)}

    contact_ids = list(
        run.transcriptions.filter(contact__isnull=False)
        .values_list("contact_id", flat=True)
        .distinct()
    )
    if not contact_ids:
        print("[Bitratata] Нет контактов с транскрипциями для DeepSeek")
        Run.objects.filter(pk=run.id).update(status="done", progress_current=total_processed)
        return {"run_id": run.id, "transcriptions_count": 0}

    contacts = Contact.objects.filter(id__in=contact_ids).select_related("connection")
    payload = []
    for c in contacts:
        texts = list(c.transcriptions.order_by("updated_at").values_list("text", flat=True))
        if texts:
            payload.append((c.entity_type, c.entity_id, texts))

    if not payload:
        Run.objects.filter(pk=run.id).update(status="done", progress_current=total_processed)
        return {"run_id": run.id, "transcriptions_count": 0}

    print(f"[Bitratata] Контактов: {len(payload)}. Отправка в DeepSeek ...")
    try:
        raw_json = _send_to_deepseek_by_contacts(payload)
        seg = SegmentationResult.objects.create(run=run, text=raw_json)
        parsed = json_lib.loads(raw_json)
        tags_global = parsed.get("tags_global") or []
        for ent in parsed.get("entities") or []:
            eid = str(ent.get("entity_id", ""))
            etype = (ent.get("entity_type") or "CALL").upper()
            if etype not in ("LEAD", "CONTACT", "DEAL"):
                etype = "CALL"
            contact = Contact.objects.filter(
                user_id=user_id,
                entity_id=eid,
                entity_type=etype,
            ).first()
            if contact:
                tags = list(ent.get("tags") or [])
                ContactSegmentation.objects.update_or_create(
                    contact=contact,
                    defaults={"user_id": user_id, "tags": tags, "raw_response": raw_json},
                )
        print("[Bitratata] DeepSeek готов. Теги закреплены за контактами.")
        # Финальное саммари по запуску (может быть больше лимита контекста, поэтому с разбиением)
        try:
            all_texts = list(run.transcriptions.values_list("text", flat=True))
            if all_texts:
                summary = _send_run_summary(all_texts)
                seg.summary = summary
                seg.save(update_fields=["summary"])
        except Exception as e:
            logger.debug("Не удалось получить саммари запуска: %s", e)
        Run.objects.filter(pk=run.id).update(status="done", progress_current=total_processed)
    except Exception as e:
        logger.exception("Ошибка DeepSeek: %s", e)
        print(f"[Bitratata] Ошибка DeepSeek: {e}")
        Run.objects.filter(pk=run.id).update(status="error")
        return {"run_id": run.id, "deepseek_error": str(e)}

    return {"run_id": run.id, "transcriptions_count": total_processed, "contacts_count": len(contact_ids)}


def _get_user(user_id):
    if not user_id:
        return None
    try:
        from django.contrib.auth.models import User
        return User.objects.get(pk=user_id)
    except Exception:
        return None


def _get_connections(user_id, num_clients):
    qs = BitrixConnection.objects.filter(is_active=True).order_by("id")
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    return qs[:num_clients]


def _get_or_create_contact(user_id, connection, entity_type: str, entity_id: str):
    entity_type = (entity_type or "CALL").upper()
    if entity_type not in ("LEAD", "CONTACT", "DEAL", "CALL"):
        entity_type = "CALL"
    contact, _ = Contact.objects.get_or_create(
        user_id=user_id,
        connection=connection,
        entity_type=entity_type,
        entity_id=str(entity_id),
        defaults={"display_name": ""},
    )
    return contact


def _call_bitrix(webhook_url: str, method: str, params: dict = None) -> dict:
    """Вызов REST API Битрикс24 через requests (как в sa). Возвращает ответ data; при ошибке API — RuntimeError."""
    url = urljoin(webhook_url.rstrip("/") + "/", method)
    payload = params or {}
    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(
            data.get("error_description") or data.get("error", "Bitrix24 API error")
        )
    return data


def _fetch_contact_display_name(connection, entity_type: str, entity_id: str) -> str:
    """Получить из CRM отображаемое имя контакта/лида/сделки (имя, фамилия, компания)."""
    if not connection or not entity_id or entity_type == "CALL":
        return ""
    webhook = connection.webhook_url
    parts = []
    try:
        if entity_type == "CONTACT":
            data = _call_bitrix(webhook, "crm.contact.get", {"id": entity_id})
            r = data.get("result") or {}
            name = (r.get("NAME") or "").strip()
            last = (r.get("LAST_NAME") or "").strip()
            if name or last:
                parts.append(f"{name} {last}".strip())
            if r.get("COMPANY_TITLE"):
                parts.append(str(r.get("COMPANY_TITLE")).strip())
        elif entity_type == "LEAD":
            data = _call_bitrix(webhook, "crm.lead.get", {"id": entity_id})
            r = data.get("result") or {}
            name = (r.get("NAME") or "").strip()
            last = (r.get("LAST_NAME") or "").strip()
            if name or last:
                parts.append(f"{name} {last}".strip())
            if r.get("COMPANY_TITLE"):
                parts.append(str(r.get("COMPANY_TITLE")).strip())
        elif entity_type == "DEAL":
            data = _call_bitrix(webhook, "crm.deal.get", {"id": entity_id})
            r = data.get("result") or {}
            title = (r.get("TITLE") or "").strip()
            if title:
                parts.append(title)
    except Exception as e:
        logger.debug("Не удалось получить имя из CRM %s %s: %s", entity_type, entity_id, e)
        return ""
    return ", ".join(parts) if parts else ""


def _fetch_files_from_bitrix(connection, limit: int):
    """
    Список записей для скачивания. Если в settings.BITRIX_FOLDER_PATH пусто — ищем через API телефонии
    (voximplant.statistic.get + RECORD_FILE_ID), как в sa. Иначе — по папке на диске.
    """
    folder_path = getattr(settings, "BITRIX_FOLDER_PATH", "").strip()
    if not folder_path:
        return _fetch_files_via_telephony(connection, limit)
    return _fetch_files_via_disk_folder(connection, limit)


def _fetch_files_via_telephony(connection, limit: int):
    """
    Как в sa: простой HTTP POST к voximplant.statistic.get и disk.file.get (без пакета bitrix24).
    Один запрос за список звонков, пагинация как в sa.
    """
    webhook = connection.webhook_url

    # Один запрос: SORT, ORDER, start=0 (как в sa), без ограничения по дням
    try:
        data = _call_bitrix(
            webhook,
            "voximplant.statistic.get",
            {
                "SORT": "CALL_START_DATE",
                "ORDER": "DESC",
                "start": 0,
            },
        )
    except Exception as e:
        raise RuntimeError(
            f"Ошибка voximplant.statistic.get (нужны права Телефония): {e}"
        ) from e

    result = data.get("result")
    calls = result if isinstance(result, list) else [result] if result else []
    with_record = [c for c in calls if c.get("RECORD_FILE_ID")][:limit]
    if not with_record:
        return []

    files = []
    for call in with_record:
        record_file_id = call.get("RECORD_FILE_ID")
        try:
            file_data = _call_bitrix(webhook, "disk.file.get", {"id": record_file_id})
            res = file_data.get("result") or {}
            url = res.get("DOWNLOAD_URL") or res.get("DOWNLOAD_LINK")
            name = res.get("NAME") or _build_call_filename(call)
            if url:
                entity_type = (call.get("CRM_ENTITY_TYPE") or "CALL").strip().upper() or "CALL"
                if entity_type not in ("LEAD", "CONTACT", "DEAL"):
                    entity_type = "CALL"
                entity_id = call.get("CRM_ENTITY_ID") or record_file_id
                files.append({
                    "ID": record_file_id,
                    "NAME": name,
                    "DOWNLOAD_URL": url,
                    "ENTITY_TYPE": entity_type,
                    "ENTITY_ID": str(entity_id) if entity_id is not None else str(record_file_id),
                })
        except Exception as e:
            logger.warning("disk.file.get id=%s: %s", record_file_id, e)
    return files


def _build_call_filename(call: dict) -> str:
    """Имя файла по данным звонка (как в sa)."""
    import re
    date_str = ""
    if call.get("CALL_START_DATE"):
        try:
            dt = datetime.fromisoformat(
                str(call["CALL_START_DATE"]).replace("Z", "+00:00")
            )
            date_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
        except Exception:
            date_str = str(call.get("CALL_START_DATE", ""))[:19]
    if not date_str:
        date_str = "nodate"
    phone = (call.get("PHONE_NUMBER") or "nophone").replace("+", "").replace(" ", "")
    phone = re.sub(r"\D", "", phone) or "nophone"
    call_id = call.get("CALL_ID") or call.get("ID") or "0"
    return f"{date_str}_{phone}_call{call_id}.mp3"


def _fetch_files_via_disk_folder(connection, limit: int):
    """Записи по папке на диске (хранилище + путь из settings). Вызовы через _call_bitrix."""
    webhook = connection.webhook_url
    storage_name = getattr(settings, "BITRIX_STORAGE_NAME", "Общий диск")
    folder_path = getattr(settings, "BITRIX_FOLDER_PATH", "")

    data = _call_bitrix(webhook, "disk.storage.getlist", {})
    storages = data.get("result") or []
    if not isinstance(storages, list):
        storages = [storages]
    root_id = None
    for s in storages:
        if (s.get("NAME") or "").strip().lower() == (storage_name or "").strip().lower():
            root_id = s.get("ROOT_OBJECT_ID")
            break
    if not root_id:
        raise RuntimeError(f"Хранилище «{storage_name}» не найдено")

    folder_id = root_id
    for part in [p.strip() for p in (folder_path or "").split("/") if p.strip()]:
        data = _call_bitrix(webhook, "disk.folder.getchildren", {"id": folder_id})
        children = data.get("result") or []
        if not isinstance(children, list):
            children = [children]
        folder_id = None
        for c in children:
            if (c.get("NAME") or "").strip() == part:
                folder_id = c.get("ID")
                break
        if not folder_id:
            raise RuntimeError(f"Папка «{part}» не найдена в пути «{folder_path}»")

    data = _call_bitrix(webhook, "disk.folder.getchildren", {"id": folder_id})
    items = data.get("result") or []
    if not isinstance(items, list):
        items = [items]
    files = [i for i in items if i.get("TYPE") == "file"]
    out = []
    for f in files[:limit]:
        rec = dict(f)
        if "ENTITY_TYPE" not in rec:
            rec["ENTITY_TYPE"] = "CALL"
            rec["ENTITY_ID"] = str(f.get("ID") or "")
        out.append(rec)
    return out


def _load_whisper_model():
    """Загрузить модель faster-whisper один раз (настройки из settings)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        import sys
        raise RuntimeError(
            f"Модуль faster-whisper не установлен. Задача выполняется в Python: {sys.executable}. "
            f"Установите: {sys.executable} -m pip install faster-whisper"
        ) from None
    model_size = getattr(settings, "WHISPER_MODEL_SIZE", "base")
    device = getattr(settings, "WHISPER_DEVICE", "cpu")
    compute_type = getattr(settings, "WHISPER_COMPUTE_TYPE", "int8")
    download_root = getattr(settings, "WHISPER_DOWNLOAD_ROOT", None)
    kwargs = {"device": device, "compute_type": compute_type}
    if download_root:
        kwargs["download_root"] = str(download_root)
    return WhisperModel(model_size, **kwargs)


def _download_and_transcribe(file_info: dict, connection, whisper_model) -> str:
    import wave

    download_url = file_info.get("DOWNLOAD_URL")
    if not download_url:
        file_data = _call_bitrix(connection.webhook_url, "disk.file.get", {"id": file_info["ID"]})
        res = file_data.get("result") or {}
        download_url = res.get("DOWNLOAD_URL") or res.get("DOWNLOAD_LINK")
    if not download_url:
        raise RuntimeError("Нет ссылки на скачивание")

    resp = requests.get(download_url, timeout=60)
    resp.raise_for_status()
    raw = resp.content

    try:
        with wave.open(io.BytesIO(raw), "rb") as wf:
            pass
        wav_io = io.BytesIO(raw)
    except Exception:
        try:
            import sys
            if "audioop" not in sys.modules:
                try:
                    import audioop_lts
                    sys.modules["audioop"] = audioop_lts
                except ImportError:
                    pass
            from pydub import AudioSegment
            seg = AudioSegment.from_file(io.BytesIO(raw))
            wav_io = io.BytesIO()
            seg.export(wav_io, format="wav")
            wav_io.seek(0)
        except Exception as e:
            raise RuntimeError(f"Аудио не прочитано (wav/pydub): {e}")

    wav_io.seek(0)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_io.read())
        tmp_path = tmp.name
    try:
        segments, _ = whisper_model.transcribe(tmp_path, language="ru", 
        beam_size=1,
        best_of=1,
        temperature=0,
        condition_on_previous_text=False,
    )
        parts = [s.text for s in segments if s.text]
        return " ".join(parts).strip()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _deepseek_client():
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("Установите openai: pip install openai")
    api_key = getattr(settings, "DEEPSEEK_API_KEY", None) or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("Нет DeepSeek API Key (настройка задачи или DEEPSEEK_API_KEY)")
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def _segment_prompt_parts():
    return [
        "По расшифровкам телефонных звонков с клиентами нужно сделать сегментацию и выделить ",
        "Ниже для каждой сущности CRM (контакт/лид/сделка) приведены тексты звонков.",
        "",
        "Задачи:",
        "1) Предложи общий набор тегов для сегментации клиентов (по продукту, ценности, этапу продажи, теме и т.п.). Теги — короткие фразы на русском с названиями потенциальных продуктов и услуг, которые могут быть предложены клиенту.",
        "2) Для каждой сущности назначь только те теги из этого набора, которые ей подходят по содержанию звонков.",
        "",
        "Ответ строго в формате JSON без markdown и пояснений:",
        '{"tags_global": ["тег1", "тег2", ...], "entities": [{"entity_type": "LEAD", "entity_id": "123", "tags": ["тег1", "тег3"]}, ...]}',
        "",
        "Данные:",
    ]


def _send_to_deepseek_flat(texts: list) -> str:
    """Список текстов — один блок на звонок (entity_id = индекс). Для режима без пользователя."""
    client = _deepseek_client()
    prompt_parts = _segment_prompt_parts()
    data_lines = []
    for i, text in enumerate(texts[:50], start=1):
        data_lines.append(f"\n--- Звонок {i} (entity_id: \"{i}\") ---\n{text}")
    if len(texts) > 50:
        data_lines.append(f"\n... и ещё {len(texts) - 50} записей.")
    user_content = "\n".join(prompt_parts) + "\n" + "\n".join(data_lines)
    print(f"[Bitratata] DeepSeek: подготовлено {len(texts)} транскрипций (режим без пользователя)")
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "Ты выполняешь задачу сегментации по транскрипциям звонков. Отвечай только валидным JSON без markdown и комментариев."},
            {"role": "user", "content": user_content},
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    print(f"[Bitratata] DeepSeek: получен ответ (длина {len(content)} символов)")
    return content


def _send_to_deepseek_by_contacts(payload: list) -> str:
    """payload: список (entity_type, entity_id, list of texts). Один блок на контакт, теги закрепляются за entity_id.

    При большом количестве контактов вызываем DeepSeek чанками, затем объединяем ответы:
    tags_global мержим, entities конкатенируем.
    """
    client = _deepseek_client()
    prompt_parts = _segment_prompt_parts()

    def _one_batch(batch, batch_index: int, total_batches: int):
        data_lines = []
        for entity_type, entity_id, texts in batch:
            combined = "\n\n".join(texts)
            data_lines.append(f'\n--- Сущность {entity_type} (entity_id: "{entity_id}") ---\n{combined}')
        user_content = "\n".join(prompt_parts) + "\n" + "\n".join(data_lines)
        print(
            f"[Bitratata] DeepSeek: батч {batch_index}/{total_batches}, "
            f"сущностей в батче: {len(batch)}"
        )
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "Ты выполняешь задачу сегментации по транскрипциям звонков. Отвечай только валидным JSON без markdown и комментариев.",
                },
                {"role": "user", "content": user_content},
            ],
        )
        content = (response.choices[0].message.content or "").strip()
        print(
            "[Bitratata] DeepSeek: батч "
            f"{batch_index}/{total_batches} — ответ длиной {len(content)} символов"
        )
        return content

    # Разбиваем по количеству сущностей, чтобы не переполнить контекст
    batch_size = 15
    if len(payload) <= batch_size:
        return _one_batch(payload, 1, 1)

    all_entities = []
    all_tags = []
    total_batches = (len(payload) + batch_size - 1) // batch_size
    for i in range(0, len(payload), batch_size):
        batch = payload[i : i + batch_size]
        batch_index = i // batch_size + 1
        raw = _one_batch(batch, batch_index, total_batches)
        try:
            parsed = json_lib.loads(raw)
        except Exception:
            # Если пришёл не JSON, просто возвращаем сырой ответ
            return raw
        all_tags.extend(parsed.get("tags_global") or [])
        all_entities.extend(parsed.get("entities") or [])

    merged = {
        "tags_global": sorted(set(all_tags)),
        "entities": all_entities,
    }
    return json_lib.dumps(merged, ensure_ascii=False)


def _send_call_brief(text: str) -> str:
    """Краткая характеристика одного звонка (1–2 короткие фразы)."""
    client = _deepseek_client()
    # Ограничиваем размер текста, чтобы экономить токены
    max_chars = 4000
    short = (text or "")[:max_chars]
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты аналитик звонков. По транскрипции телефонного разговора сформулируй очень короткую "
                    "характеристику звонка (1–2 фразы): с кем говорили, по какому поводу, на какой стадии продаж, "
                    "какое основное настроение/результат. Не используй markdown."
                ),
            },
            {"role": "user", "content": short},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _send_run_summary(texts: list) -> str:
    """Краткое саммари по всему запуску, с разбиением на части при большом объёме."""
    if not texts:
        return ""
    client = _deepseek_client()
    full = "\n\n---\n\n".join(t or "" for t in texts)
    max_chunk = 12000  # по символам, грубая оценка лимита контекста

    def _summarize_piece(piece: str, idx: int, total: int) -> str:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты аналитик телефонных звонков. Составь краткое структурированное саммари по этим "
                        "транскрипциям: какие были типы клиентов, темы, этапы продаж, ключевые выводы и рекомендации. "
                        "Не используй markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Это часть {idx} из {total} транскрипций звонков.\n"
                        f"Сделай краткое саммари только по этой части:\n\n{piece}"
                    ),
                },
            ],
        )
        return (response.choices[0].message.content or "").strip()

    if len(full) <= max_chunk:
        return _summarize_piece(full, 1, 1)

    # Делим на куски по символам
    chunks = [full[i : i + max_chunk] for i in range(0, len(full), max_chunk)]
    partial_summaries = [
        _summarize_piece(ch, idx + 1, len(chunks)) for idx, ch in enumerate(chunks)
    ]

    if len(partial_summaries) == 1:
        return partial_summaries[0]

    # Финальное саммари по саммари
    combined = "\n\n---\n\n".join(partial_summaries)
    return _summarize_piece(combined, 1, 1)
