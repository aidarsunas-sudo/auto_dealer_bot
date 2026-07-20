# -*- coding: utf-8 -*-
"""
unknown_watchlist_models.py — журнал машин из WATCHLIST/USED_CAR_SEARCHES,
для которых model_specs.find_spec() не смог определить hp (значит —
price_alert_builder.build_price_alert_post() НЕ публикует пост про них, а
просто пропускает, см. решение: "молчать, не откатываться на старый
шаблон" — иначе появилась бы непредсказуемая смесь старого/нового формата).

Ключ записи — сырой текст (raw title), чтобы повторные встречи одной и той
же нерезолвленной модели не плодили дубли, а увеличивали счётчик
"сколько раз уже встретилась" — так удобнее решить, какие модели
приоритетно добавить в model_specs.py через add_model.py.

Формат unknown_watchlist_models.json:
    {
        "<raw title>": {
            "brand": "...", "model": "...", "source": "...",
            "first_seen": "2026-07-06T12:00:00", "last_seen": "...", "times_seen": N
        }
    }
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "unknown_watchlist_models.json"
DIGEST_STATE_PATH = Path(__file__).resolve().parent / "unknown_watchlist_digest_state.json"
DIGEST_INTERVAL_HOURS = 24
DIGEST_TOP_N = 5

logger = logging.getLogger("unknown_watchlist_models")


def _load() -> dict:
    if not LOG_PATH.exists():
        return {}
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        logger.error(f"Не удалось прочитать {LOG_PATH.name}: {error} — начинаю с пустого журнала")
        return {}


def _save(data: dict) -> None:
    try:
        LOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning(f"Не удалось сохранить {LOG_PATH.name}: {error}")


def log_unknown_model(raw_title: str, brand: str, model: str, source: str) -> None:
    """Регистрирует (или обновляет счётчик) нерезолвленную модель. Тихо —
    это не ошибка выполнения, просто информация для последующего ручного
    добавления через add_model.py."""
    if not raw_title:
        return

    data = _load()
    now = datetime.now().isoformat(timespec="seconds")
    entry = data.get(raw_title)

    if entry is None:
        data[raw_title] = {
            "brand": brand, "model": model, "source": source,
            "first_seen": now, "last_seen": now, "times_seen": 1,
        }
    else:
        entry["last_seen"] = now
        entry["times_seen"] = entry.get("times_seen", 0) + 1

    _save(data)


def _load_digest_state() -> dict:
    if not DIGEST_STATE_PATH.exists():
        return {"last_sent_at": None}
    try:
        return json.loads(DIGEST_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_sent_at": None}


def _save_digest_state(state: dict) -> None:
    try:
        DIGEST_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning(f"Не удалось сохранить {DIGEST_STATE_PATH.name}: {error}")


def send_daily_digest(notify_fn=None) -> bool:
    """
    Раз в DIGEST_INTERVAL_HOURS часов (не на каждый прогон — main.py
    запускается несколько раз в сутки) шлёт в служебный чат топ-
    DIGEST_TOP_N нерезолвленных моделей по times_seen — ПО ТЗ 2026-07-20:
    "не ослаблять валидацию молча, вместо этого — ежедневная сводка", чтобы
    решение "что добавить в model_specs.py" принимал человек на основе
    реальной частоты встречаемости, а не гадание/автослабление гейта.

    Вызывать из main.py (единственный источник записей в этот момент —
    price_alert_builder.py при нерезолвленном hp, см. log_unknown_model()).

    Returns:
        True, если сводка была отправлена в этот вызов (для тестов/лога).
    """
    if notify_fn is None:
        from notifier import notify_error as notify_fn

    state = _load_digest_state()
    last_sent_at = state.get("last_sent_at")
    if last_sent_at:
        elapsed = datetime.now() - datetime.fromisoformat(last_sent_at)
        if elapsed < timedelta(hours=DIGEST_INTERVAL_HOURS):
            return False

    data = _load()
    if not data:
        return False

    top = sorted(data.items(), key=lambda kv: kv[1].get("times_seen", 0), reverse=True)[:DIGEST_TOP_N]

    lines = ["📋 Топ нерезолвленных моделей (не хватает hp/см³ в model_specs.py):"]
    for raw_title, entry in top:
        lines.append(
            f"  • {entry.get('brand', '')} {entry.get('model', '')} — "
            f"{entry.get('times_seen', 0)}× (источник: {entry.get('source', '?')}) "
            f"[{raw_title}]"
        )
    lines.append("\nДобавь нужные через add_model.py, если стоит того.")

    notify_fn("\n".join(lines))

    state["last_sent_at"] = datetime.now().isoformat(timespec="seconds")
    _save_digest_state(state)
    return True
