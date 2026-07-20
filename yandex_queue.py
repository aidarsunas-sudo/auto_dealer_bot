# -*- coding: utf-8 -*-
"""
yandex_queue.py — очередь лотов на Яндекс-черновик и журнал уже отправленных
(см. main_pipeline.py): лидоген публикует лоты в TG/VK чаще, чем нужно слать
Яндекс-черновики, поэтому опубликованные лоты копятся в очереди, а забирает
из неё максимум ОДИН лот отдельный шаг в конце прогона — не чаще раза в
config.YANDEX_MIN_INTERVAL_DAYS дней.

Файлы (при корне репозитория, как published_log.json — коммитятся обратно в
git после каждого прогона GitHub Actions, см. .github/workflows/main_pipeline.yml,
иначе состояние не переживёт между прогонами на чистой VM):
    yandex_queue.json — [{"car": {...}, "added_at": iso}, ...]
    yandex_published.json — {"last_sent_at": iso|None, "history": [{"brand","model","sent_at"}]}
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import config

logger = logging.getLogger("yandex_queue")

QUEUE_PATH = Path(__file__).resolve().parent / "yandex_queue.json"
PUBLISHED_PATH = Path(__file__).resolve().parent / "yandex_published.json"


def _load_json(path: Path, default):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        logger.error(f"{path.name} повреждён ({error}) — использую значение по умолчанию")
        return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_queue() -> list:
    return _load_json(QUEUE_PATH, [])


def _load_published() -> dict:
    return _load_json(PUBLISHED_PATH, {"last_sent_at": None, "history": []})


def enqueue_lot(car: dict) -> None:
    """
    Добавляет лот в очередь на Яндекс-черновик — вызывать ПОСЛЕ успешной
    (не dry-run) публикации в TG/VK, не отправляет ничего сама. Дедуп по
    external_id/url — один и тот же лот дважды в очередь не попадёт.
    """
    identifier = car.get("external_id") or car.get("url")
    if not identifier:
        logger.warning("enqueue_lot: у лота нет external_id/url — пропускаю (не с чем дедупить)")
        return

    queue = _load_queue()
    if any((e.get("car", {}).get("external_id") or e.get("car", {}).get("url")) == identifier
           for e in queue):
        return

    queue.append({"car": car, "added_at": datetime.now().isoformat()})
    _save_json(QUEUE_PATH, queue)


def due_for_send() -> bool:
    """True, если с последней отправки прошло >= config.YANDEX_MIN_INTERVAL_DAYS,
    или отправок ещё не было вовсе."""
    last_sent_at = _load_published().get("last_sent_at")
    if not last_sent_at:
        return True
    return datetime.now() - datetime.fromisoformat(last_sent_at) >= timedelta(days=config.YANDEX_MIN_INTERVAL_DAYS)


def _is_stale(entry: dict) -> bool:
    added_at = entry.get("added_at")
    if not added_at:
        return True
    return datetime.now() - datetime.fromisoformat(added_at) > timedelta(days=config.YANDEX_QUEUE_MAX_AGE_DAYS)


def _recent_models(published: dict) -> set:
    now = datetime.now()
    recent = set()
    for entry in published.get("history", []):
        sent_at = entry.get("sent_at")
        brand, model = entry.get("brand"), entry.get("model")
        if not sent_at or brand is None or model is None:
            continue
        if now - datetime.fromisoformat(sent_at) <= timedelta(days=config.YANDEX_RECENT_MODEL_DAYS):
            recent.add((brand, model))
    return recent


def pick_best_lot() -> tuple[dict | None, int]:
    """
    Забирает из очереди ОДИН лот (удаляет его и все протухшие записи из
    yandex_queue.json — остальные свежие остаются). Предпочтение — модели,
    не публиковавшейся в Яндекс за последние config.YANDEX_RECENT_MODEL_DAYS
    дней; если ВСЕ свежие лоты в очереди — недавно публиковавшиеся модели
    (альтернатив нет), берём всё равно самый старый в очереди, а не
    пропускаем отправку совсем (тот же fallback, что у
    candidate_diversity.filter_recent_models() для лидогена).

    Returns:
        (car, queue_left) — car=None, если очередь пуста после чистки
        протухших записей (queue_left тогда 0). queue_left — сколько лотов
        останется в очереди ПОСЛЕ изъятия этого (для строки "в очереди ещё N").
    """
    queue = _load_queue()
    fresh = [e for e in queue if not _is_stale(e)]
    dropped = len(queue) - len(fresh)
    if dropped:
        logger.info(f"yandex_queue: выкинуто {dropped} протухших лотов "
                    f"(старше {config.YANDEX_QUEUE_MAX_AGE_DAYS} дн.)")

    if not fresh:
        _save_json(QUEUE_PATH, fresh)
        return None, 0

    recent_models = _recent_models(_load_published())
    preferred = [e for e in fresh if (e["car"].get("brand"), e["car"].get("model")) not in recent_models]
    pool = preferred if preferred else fresh

    chosen = pool[0]
    remaining = [e for e in fresh if e is not chosen]
    _save_json(QUEUE_PATH, remaining)

    return chosen["car"], len(remaining)


def mark_sent(car: dict) -> None:
    """Вызывать ПОСЛЕ успешной (не dry-run) отправки черновика — обновляет
    last_sent_at (для due_for_send()) и историю моделей (для pick_best_lot())."""
    published = _load_published()
    now_iso = datetime.now().isoformat()
    published["last_sent_at"] = now_iso
    published.setdefault("history", []).append({
        "brand": car.get("brand"),
        "model": car.get("model"),
        "sent_at": now_iso,
    })
    _save_json(PUBLISHED_PATH, published)
