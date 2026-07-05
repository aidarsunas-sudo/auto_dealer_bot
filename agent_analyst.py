# -*- coding: utf-8 -*-
"""
agent_analyst.py — "агент-аналитик" в мультиагентном пайплайне (orchestrator.py).

Решает, какой тип поста стоит использовать для конкретной машины:
    - "обзор"               — нейтральный, спокойный (дефолт, если сигналов нет)
    - "акция"                — цена заметно снизилась / машина заметно дешевле обычного
    - "горячее предложение"  — сильное падение цены, самый эмоциональный тип поста

Это ЧИСТАЯ АРИФМЕТИКА без вызова Claude — простые пороги, а не ML, потому
что это небольшой сайд-проект, а не data science пайплайн. Пороги вынесены
константами вверху файла, чтобы их было легко подправить, не трогая логику.

Если что-то пошло не так (нет данных, ошибка БД и т.п.) — всегда возвращаем
безопасный дефолт "обзор", а не поднимаем исключение: одна ошибка анализа
не должна ронять публикацию поста целиком.
"""

import logging
import sqlite3
from typing import Optional

from database import get_average_price, was_post_type_used_today

logger = logging.getLogger("agent_analyst")

# Пороги для принятия решения — все в виде "доли от старой/средней цены".
HOT_DEAL_DROP_RATIO = 0.10   # цена упала на 10%+ с прошлого раза -> "горячее предложение"
PROMO_DROP_RATIO = 0.03      # цена упала на 3%+ с прошлого раза -> "акция"
CHEAP_VS_AVG_RATIO = 0.15    # цена на 15%+ ниже среднего по модели -> апгрейд на тир выше

POST_TYPE_OBZOR = "обзор"
POST_TYPE_AKCIYA = "акция"
POST_TYPE_HOT_DEAL = "горячее предложение"

# Порядок "тиров" — нужен, чтобы "апгрейдить" рекомендацию на одну ступень,
# если машина заметно дешевле среднего по модели.
_TIER_ORDER = [POST_TYPE_OBZOR, POST_TYPE_AKCIYA, POST_TYPE_HOT_DEAL]


def _upgrade_tier(post_type: str) -> str:
    """Поднимает тип поста на одну ступень вверх (обзор -> акция -> горячее предложение)."""
    idx = _TIER_ORDER.index(post_type)
    return _TIER_ORDER[min(idx + 1, len(_TIER_ORDER) - 1)]


def analyze_car(
    conn: sqlite3.Connection,
    car: dict,
    status: str,
    old_price: Optional[int],
    car_id: Optional[int],
    log: logging.Logger,
) -> str:
    """
    Возвращает рекомендованный тип поста: "обзор" | "акция" | "горячее предложение".

    Args:
        conn: открытое соединение с базой.
        car: словарь машины (нужны хотя бы brand, model, price).
        status: "new" или "updated" — результат save_car() для этой машины.
        old_price: цена ДО текущего сохранения (нужно достать через
                   get_last_price() ДО вызова save_car(), т.к. save_car()
                   перезаписывает цену на месте).
        car_id: id строки в cars (для логов/будущих расширений).
        log: логгер вызывающего кода.

    При любой внутренней ошибке возвращает "обзор" — безопасный дефолт.
    """
    try:
        candidate = POST_TYPE_OBZOR
        new_price = car.get("price")

        if status == "updated" and old_price and new_price:
            drop_ratio = (old_price - new_price) / old_price
            if drop_ratio >= HOT_DEAL_DROP_RATIO:
                candidate = POST_TYPE_HOT_DEAL
            elif drop_ratio >= PROMO_DROP_RATIO:
                candidate = POST_TYPE_AKCIYA

        # Сравнение "дешевле среднего по модели" имеет смысл только для б/у
        # объявлений: у них car["year"] — реальный год выпуска конкретного
        # экземпляра, и в базе для одной модели лежат разные ОБЪЯВЛЕНИЯ (то
        # есть сопоставимые предложения). У новых авто из каталога дилера
        # car["year"] всегда None, а в базе для одной модели лежат РАЗНЫЕ
        # КОМПЛЕКТАЦИИ (например, GLB 200d и GLB 35 AMG) — их цены сравнивать
        # бессмысленно: младшая комплектация просто дешевле старшей, это не
        # "выгодная сделка", а ложный сигнал.
        if new_price and car.get("year"):
            external_id = car.get("external_id") or car.get("trim")
            avg_price = get_average_price(
                conn, car.get("brand", ""), car.get("model", ""), exclude_external_id=external_id
            )
            if avg_price:
                vs_avg_ratio = (avg_price - new_price) / avg_price
                if vs_avg_ratio >= CHEAP_VS_AVG_RATIO:
                    candidate = _upgrade_tier(candidate)

        if candidate == POST_TYPE_HOT_DEAL and was_post_type_used_today(conn, POST_TYPE_HOT_DEAL):
            log.info("agent_analyst: 'горячее предложение' уже было сегодня — понижаю до 'акция'")
            candidate = POST_TYPE_AKCIYA

        return candidate

    except Exception as error:
        log.warning(f"agent_analyst: ошибка анализа, использую '{POST_TYPE_OBZOR}' по умолчанию ({error})")
        return POST_TYPE_OBZOR
