# -*- coding: utf-8 -*-
"""
post_validator.py — жёсткий код-гейт ПЕРЕД публикацией (см. agent_publisher.publish_post()).

Намеренно НЕ промпт-инструкция агенту, а обычная детерминированная функция:
инструкции в промптах агенты периодически игнорируют (уже несколько раз
случалось на практике — см. FINAL_STATE.md), а код исполняется одинаково
каждый раз, без исключений. Три жёстких условия — нарушено хотя бы одно —
REJECT, публикации не будет (ни в Telegram, ни в VK):

    1. Фото: >= config.MIN_PHOTOS_PER_POST штук (не None, не пустой список).
    2. Название модели (brand+model+trim, как оно уходит в пост — та же
       сборка, что и в самом посте, см. lead_post_builder._build_title):
       только латиница/кириллица/цифры/пробелы/дефисы/точки, никаких
       символов корейского алфавита (хангыль).
    3. Год выпуска: обязателен (не None) и внутри [config.MIN_YEAR,
       config.MAX_YEAR]. Если год не распознан — REJECT, а не публикация
       "на всякий случай" с дефолтным годом.

filter_year_range() — тот же диапазон года, но на этапе ПАРСИНГА (сразу
после сбора объявлений с источника, до дедупа/расчёта цены/генерации поста)
— см. вызовы в target_collector.py/broad_collector.py. validate_post_before_publish()
проверяет год ПОВТОРНО, прямо перед публикацией, как финальный чек — это
не дублирование логики, а два независимых рубежа защиты (сравни с тем, как
уже было решено для фото: блокировать публикацию, а не полагаться на то,
что более ранний шаг пайплайна "не пропустит брак").

Все отклонения дописываются в rejected_posts.log (причина, ID/URL
объявления, время) — чтобы можно было проверить, что фильтр реально
работает, а не просто "тихо ничего не публикует".
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import config
from lead_post_builder import _build_title

logger = logging.getLogger("post_validator")

REJECTED_LOG_PATH = Path(__file__).resolve().parent / "rejected_posts.log"


class PublishBlockedError(Exception):
    """
    Поднимается ИЗ agent_publisher.publish_post() — единственной функции,
    через которую проходят все пути публикации (main.py, main_pipeline.py,
    publish_now.py). Именно raise, а не warning/return False: инцидент
    2026-07-07 (пост Mercedes GLB X247 без фото) случился не из-за дыры в
    логике проверки, а из-за того, что сама проверка стояла на уровень выше
    (в вызывающих скриптах) и физически не была задеплоена — про raise
    прямо в точке отправки забыть/обойти нельзя, в отличие от отдельного
    вызова validate_post_before_publish() где-то выше по стеку.
    """

# Хангыль (корейский алфавит): слоговые блоки 가-힣 + отдельные
# буквы чамо (изначальный блок Hangul Jamo) ᄀ-ᇿ.
HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ]")

# Разрешено в названии: латиница, кириллица, цифры, пробелы, дефисы, точки.
ALLOWED_TITLE_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 \-.]+$")


def _log_rejection(car: dict, reason: str) -> None:
    identifier = car.get("url") or car.get("external_id") or "неизвестно"
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"{timestamp}\tREJECT\t{identifier}\t{reason}\n"
    try:
        with open(REJECTED_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as error:
        logger.error(f"Не удалось записать в {REJECTED_LOG_PATH.name}: {error}")
    logger.error(f"Пост отклонён ({identifier}): {reason}")


def year_in_range(year) -> bool:
    return year is not None and config.MIN_YEAR <= year <= config.MAX_YEAR


def filter_year_range(cars: list, log: logging.Logger, context: str = "") -> list:
    """
    Отбрасывает объявления вне [config.MIN_YEAR, config.MAX_YEAR] сразу на
    этапе сбора — до дедупа/расчёта цены/генерации поста. Год отсутствует
    (None, не распарсился) — тоже отбрасываем, без подстановки дефолта.
    """
    kept = []
    dropped = 0
    for car in cars:
        if year_in_range(car.get("year")):
            kept.append(car)
        else:
            dropped += 1
    if dropped:
        label = f" ({context})" if context else ""
        log.info(f"filter_year_range{label}: отброшено {dropped} объявлений вне диапазона "
                 f"года {config.MIN_YEAR}-{config.MAX_YEAR} (или без года)")
    return kept


def validate_post_before_publish(car: dict) -> tuple[bool, str]:
    """
    Финальная проверка ПЕРЕД вызовом publish_post() — см. докстринг файла.

    Args:
        car: словарь объявления (та же схема, что уходит в build_lead_post/
             build_price_alert_post) — должен содержать 'photos', 'brand'/
             'model'/'trim', 'year'.

    Returns:
        (True, "") — можно публиковать.
        (False, reason) — нельзя; REJECT уже записан в rejected_posts.log,
        вызывающий код должен пропустить эту машину и НЕ звать publish_post().
    """
    photos = car.get("photos") or []
    if len(photos) < config.MIN_PHOTOS_PER_POST:
        reason = f"фото {len(photos)} < минимума {config.MIN_PHOTOS_PER_POST}"
        _log_rejection(car, reason)
        return False, reason

    title = _build_title(car)
    if not title:
        reason = "пустое название модели"
        _log_rejection(car, reason)
        return False, reason
    if HANGUL_RE.search(title):
        reason = f"корейские символы в названии: {title!r}"
        _log_rejection(car, reason)
        return False, reason
    if not ALLOWED_TITLE_RE.match(title):
        reason = f"недопустимые символы в названии: {title!r}"
        _log_rejection(car, reason)
        return False, reason

    year = car.get("year")
    if year is None:
        reason = "год выпуска не определён"
        _log_rejection(car, reason)
        return False, reason
    if not year_in_range(year):
        reason = f"год {year} вне диапазона {config.MIN_YEAR}-{config.MAX_YEAR}"
        _log_rejection(car, reason)
        return False, reason

    return True, ""
