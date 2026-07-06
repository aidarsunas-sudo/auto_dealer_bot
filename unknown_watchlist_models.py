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
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "unknown_watchlist_models.json"

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
