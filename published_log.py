# -*- coding: utf-8 -*-
"""
published_log.py — лёгкий журнал "видели/публиковали" для main_pipeline.py,
отдельный от data/cars.db (та база заточена под другую задачу — отслеживание
изменения цены для мультиагентного пайплайна orchestrator.py, а здесь нужна
простая, независимая логика: не публиковать одно и то же объявление дважды,
и не публиковать объявление, которое видим уже дольше N дней).

Ключ записи — url объявления (стабилен и уникален в рамках источника, есть
у всех 4 адаптеров target_collector.py).

Формат published_log.json:
    {
        "<url>": {"first_seen": "2026-07-05T12:00:00", "posted_at": "2026-07-05T18:00:00" | null}
    }
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "published_log.json"

logger = logging.getLogger("published_log")


def load_log() -> dict:
    if not LOG_PATH.exists():
        return {}
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        logger.error(f"Не удалось прочитать {LOG_PATH.name}: {error} — начинаю с пустого журнала")
        return {}


def save_log(log: dict) -> None:
    try:
        LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning(f"Не удалось сохранить {LOG_PATH.name}: {error}")


def select_candidates(cars: list, freshness_days: int, log: dict, max_mileage_km: int | None = None) -> list:
    """
    Регистрирует новые объявления в журнале (first_seen = сейчас) и
    возвращает те, что подходят для публикации: ещё не публиковались, не
    старше freshness_days (считая от нашего first_seen, а не от даты на
    сайте — не все источники её вообще отдают) и с пробегом не больше
    max_mileage_km (если задан) — применяется одинаково для targeted и
    broad режимов, поверх любых per-модельных mileage_max из
    target_models.json/broad_search_config.json.

    Машины с пробегом больше max_mileage_km не регистрируются в журнале
    вообще (не нужно засорять published_log.json объявлениями, которые мы
    заведомо никогда не опубликуем).

    Мутирует переданный log (добавляет новые url) — сохранить на диск
    (save_log) должен вызывающий код, после того как решит, что публиковать.

    Returns:
        Список машин-кандидатов на публикацию, отсортированный по first_seen
        по возрастанию (сначала самые "старые" непроверенные объявления —
        чтобы не копился бесконечно растущий необработанный хвост).
    """
    now = datetime.now()
    candidates = []
    seen_urls = set()

    for car in cars:
        url = car.get("url")
        if not url:
            continue
        if url in seen_urls:
            continue  # тот же url уже встретился в ЭТОМ вызове — источники
                       # иногда отдают один и тот же физический автомобиль
                       # дважды (например, под двумя пересекающимися
                       # generation-фильтрами) — не показываем его дважды
        seen_urls.add(url)

        mileage_km = car.get("mileage_km")
        if max_mileage_km is not None and mileage_km is not None and mileage_km > max_mileage_km:
            continue  # пробег больше допустимого — вообще не рассматриваем

        entry = log.get(url)
        if entry is None:
            entry = {"first_seen": now.isoformat(timespec="seconds"), "posted_at": None}
            log[url] = entry

        if entry["posted_at"] is not None:
            continue  # уже публиковали это объявление — не дублируем

        first_seen = datetime.fromisoformat(entry["first_seen"])
        if now - first_seen > timedelta(days=freshness_days):
            continue  # видим уже слишком давно — считаем неактуальным

        candidates.append((first_seen, car))

    candidates.sort(key=lambda pair: pair[0])
    return [car for _, car in candidates]


def mark_posted(log: dict, url: str, brand: str | None = None, model: str | None = None) -> None:
    """
    brand/model сохраняются в ту же запись, чтобы recently_posted_models()
    могла позже определить, какая модель стоит за этим url (сам url этого
    не говорит). Записи, помеченные посте до этой правки, brand/model не
    имеют — recently_posted_models() их просто не учитывает.
    """
    entry = log.get(url)
    if entry is not None:
        entry["posted_at"] = datetime.now().isoformat(timespec="seconds")
        if brand is not None:
            entry["brand"] = brand
        if model is not None:
            entry["model"] = model


def recently_posted_models(log: dict, days: int) -> set:
    """
    Возвращает set из (brand, model), опубликованных за последние `days`
    дней — по данным, записанным mark_posted(). Записи без brand/model
    (опубликованные до этой правки) пропускаются — учесть их невозможно,
    не выдумывая данные.
    """
    now = datetime.now()
    recent = set()

    for entry in log.values():
        posted_at = entry.get("posted_at")
        if not posted_at:
            continue
        brand = entry.get("brand")
        model = entry.get("model")
        if brand is None or model is None:
            continue

        posted_dt = datetime.fromisoformat(posted_at)
        if now - posted_dt <= timedelta(days=days):
            recent.add((brand, model))

    return recent
