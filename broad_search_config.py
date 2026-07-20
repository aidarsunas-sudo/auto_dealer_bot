# -*- coding: utf-8 -*-
"""
broad_search_config.py — настройки широкого поиска (Режим 2, см.
broad_collector.py): "любые модели с hp<=max_hp", без привязки к конкретным
брендам из target_models.json. Пользователь редактирует
broad_search_config.json вручную — так же, как target_models.json.

Поля:
    max_hp:                верхний порог мощности (л.с.) — 160 = порог
                            льготного утильсбора для физлиц (см.
                            customs_calculator.util_calc). Реальная проверка
                            всё равно идёт по hp из model_specs.find_spec()
                            (ни один из 4 источников не отдаёт л.с. напрямую,
                            см. разбор в чате) — параметр здесь используется
                            как постфильтр ПОСЛЕ того, как hp определён.
    min_hp:                 нижний порог мощности (л.с.), или null — без
                            ограничения. Добавлено 2026-07-20 вместе с
                            broad_search_segments.json — позволяет честно
                            развести "слабее"/"мощнее" сегменты, а не только
                            один общий "hp<=160".
    max_pages_per_source:   сколько страниц (сортировка "сначала новые")
                            просматривать на каждом источнике за один
                            прогон — весь рынок целиком просматривать
                            нереально (десятки-сотни тысяч объявлений).
    fuel_types:             список кодов топлива ("diesel"/"petrol"/...)
                            для фильтрации, или null — без ограничения.
    year_from:               минимальный год выпуска, или null.
    mileage_max:             максимальный пробег (км), или null.

Вариация параметров между прогонами (broad_search_segments.json, см.
load_rotating_segment()) — ПО ТЗ 2026-07-20 ("вариация параметров запроса
между прогонами... чтобы вы сами могли рулить тем, что чаще видят
подписчики"): именованный пул сегментов (например "экономичные
малолитражки" hp<=130, "мощнее/кроссоверы" hp 131-160), main_pipeline.py
на каждый прогон берёт СЛЕДУЮЩИЙ по кругу — счётчик в broad_search_state.json
(тот же принцип ротации, что у video_state.py). Если файла сегментов нет —
используется единственный "сегмент по умолчанию" из broad_search_config.json
(текущее поведение, без вариации) — обратная совместимость, ничего не
ломается для тех, кто не завёл файл сегментов.
"""

import json
import logging
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "broad_search_config.json"
SEGMENTS_PATH = Path(__file__).resolve().parent / "broad_search_segments.json"
STATE_PATH = Path(__file__).resolve().parent / "broad_search_state.json"

logger = logging.getLogger("broad_search_config")

DEFAULTS = {
    "max_hp": 160,
    "min_hp": None,
    "max_pages_per_source": 5,
    "fuel_types": None,
    "year_from": None,
    "mileage_max": None,
}


def load_broad_search_config() -> dict:
    """
    Загружает broad_search_config.json. Отсутствие файла — не ошибка,
    возвращаются безопасные дефолты (см. DEFAULTS).
    """
    if not CONFIG_PATH.exists():
        logger.info(f"{CONFIG_PATH.name} не найден — использую дефолты: {DEFAULTS}")
        return dict(DEFAULTS)

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        logger.error(f"Не удалось разобрать {CONFIG_PATH.name}: {error} — использую дефолты")
        return dict(DEFAULTS)

    return {**DEFAULTS, **data}


def _load_segments() -> list:
    if not SEGMENTS_PATH.exists():
        return []
    try:
        data = json.loads(SEGMENTS_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        logger.error(f"Не удалось разобрать {SEGMENTS_PATH.name}: {error} — вариация сегментов отключена")
        return []
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        logger.warning(f"{SEGMENTS_PATH.name}: пустой или некорректный список segments — вариация отключена")
        return []
    return segments


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"run_count": 0}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        logger.warning(f"Не удалось сохранить {STATE_PATH.name}: {error}")


def load_rotating_segment(log: logging.Logger) -> dict:
    """
    Возвращает конфиг широкого поиска на ЭТОТ прогон — если задан
    broad_search_segments.json, каждый вызов забирает СЛЕДУЮЩИЙ сегмент по
    кругу (round-robin), иначе — просто load_broad_search_config() как
    раньше (обратная совместимость).

    Вызывать РОВНО один раз на прогон (см. broad_collector.collect_broad_cars()) —
    как и video_state.should_generate_video_for_this_post(), увеличивает
    счётчик при каждом вызове.
    """
    base = load_broad_search_config()
    segments = _load_segments()
    if not segments:
        return base

    state = _load_state()
    index = state.get("run_count", 0) % len(segments)
    state["run_count"] = state.get("run_count", 0) + 1
    _save_state(state)

    segment = segments[index]
    merged = {**base, **{k: v for k, v in segment.items() if k != "name"}}
    log.info(f"broad_search: сегмент этого прогона — {segment.get('name', f'#{index}')} "
             f"({index + 1}/{len(segments)})")
    return merged
