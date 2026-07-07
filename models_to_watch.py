# -*- coding: utf-8 -*-
"""
models_to_watch.py — простой список моделей для поиска и публикации,
без JSON. Файл models_to_watch.txt редактируется руками: одна модель на
строку, просто текстом, например:

    Hyundai Sonata
    Kia K5

Пустые строки и строки, начинающиеся с "#", игнорируются (можно оставлять
заметки для себя). Первое слово в строке — марка, всё остальное — модель.

sync_to_target_models() вызывается автоматически в начале каждого запуска
main_pipeline.py (и локального, и по расписанию) — переносит из этого
файла в target_models.json любые модели, которых там ещё нет, с разумными
дефолтами (год выпуска — фиксированный диапазон 2022-2024, см.
add_model.YEAR_FROM_DEFAULT/YEAR_TO_DEFAULT, пробег — как у остальных
моделей). Существующие записи в target_models.json (например, с более
точными фильтрами по годам/пробегу, добавленные вручную) не трогает и не
удаляет — этот файл только ДОБАВЛЯЕТ новые модели, никогда не убирает.
Чтобы перестать искать модель — эту строку можно удалить из
models_to_watch.txt, но саму запись из target_models.json придётся убрать
отдельно (см. CONTROL.md).

Переиспользует add_model.append_to_target_models() — ту же функцию и те
же дефолты, что и ручной add_model.py, чтобы не было двух разных мест,
где решается "какие дефолты ставить новой модели".
"""

import logging
from pathlib import Path

import config
from add_model import append_to_target_models, YEAR_FROM_DEFAULT, YEAR_TO_DEFAULT

MODELS_TXT_PATH = Path(__file__).resolve().parent / "models_to_watch.txt"

logger = logging.getLogger("models_to_watch")


def parse_models_txt() -> list[tuple[str, str]]:
    if not MODELS_TXT_PATH.exists():
        return []

    result = []
    for raw_line in MODELS_TXT_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            logger.warning(f"models_to_watch.txt: непонятная строка (нужно 'Марка Модель') — пропускаю: {raw_line!r}")
            continue
        brand, model = parts
        result.append((brand.strip(), model.strip()))
    return result


def sync_to_target_models(log: logging.Logger | None = None) -> None:
    log = log or logger
    entries = parse_models_txt()
    if not entries:
        return

    for brand, model in entries:
        status = append_to_target_models(
            brand, model, engine="",
            year_from=YEAR_FROM_DEFAULT, year_to=YEAR_TO_DEFAULT,
            mileage_max=config.MAX_MILEAGE_KM,
        )
        if status == "added":
            log.info(f"models_to_watch.txt: добавлена новая модель в target_models.json — {brand} {model}")
