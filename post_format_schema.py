# -*- coding: utf-8 -*-
"""
post_format_schema.py — эталонная схема формата поста для канала
@auto_generals, зафиксированная на основе текущих шаблонов
(lead_post_template.txt — main_pipeline.py, price_alert_template.txt —
main.py). Оба сейчас работают на детерминированных шаблонах, БЕЗ Claude API
и без фолбэка — это независимый, готовый к использованию модуль, задел на
будущее, а не подключённый прямо сейчас валидатор для рабочего пути.

Зачем это нужно:
Если в будущем Claude-генерация вернётся (в main_pipeline.py или в другой
модуль) — validate_post_format() можно подключить к ЛЮБОМУ источнику текста
поста (шаблон или ответ модели), чтобы гарантировать: пост никогда не
откатится незаметно к уже исправленному старому формату (видна ссылка на
источник, построчная разбивка расходов, нет фото).

Как подключить в будущем (пример):
    from post_format_schema import validate_post_format

    post_text = call_claude_api(...)  # гипотетический будущий источник
    result = validate_post_format(post_text)
    if not result:
        logger.error(f"Сгенерированный пост не прошёл валидацию формата: {result.errors}")
        post_text = template_fallback(...)  # откат на детерминированный шаблон

Эталонная структура поста (см. lead_post_template.txt/price_alert_template.txt):
    {emoji хука} {заголовок} — {фраза}!
    (пустая строка)
    📋 Характеристики:
    📅 Год: {год}
    🔧 Двигатель: {объём, мощность}
    ⚙️ КПП: {значение или "не указано"}
    🛣 Пробег: {значение}
    (пустая строка)
    💰 Стоимость под ключ в Казани: {ОДНА итоговая цифра} ₽
    (пустая строка)
    👉 {текст} {@бот}
"""

import re
from dataclasses import dataclass, field
from typing import Optional

# Первая строка поста (хук) должна начинаться с одного из этих эмодзи —
# 🔥 у lead_post_template.txt (лидогенерация), 📊 у price_alert_template.txt
# (уведомление об изменении цены/новом поступлении).
HOOK_EMOJIS = ("🔥", "📊")

CHARACTERISTICS_HEADER = "📋 Характеристики:"
YEAR_PREFIX = "📅 Год:"
ENGINE_PREFIX = "🔧 Двигатель:"
TRANSMISSION_PREFIX = "⚙️ КПП:"
MILEAGE_PREFIX = "🛣 Пробег:"
PRICE_PREFIX = "💰 Стоимость под ключ в Казани:"
CTA_MARKER = "👉"

# Обязательные блоки, в этом порядке (см. YEAR_PREFIX..PRICE_PREFIX выше) —
# CHARACTERISTICS_HEADER идёт первым, дальше — по возрастанию позиции в тексте.
REQUIRED_BLOCKS_IN_ORDER = (
    CHARACTERISTICS_HEADER, YEAR_PREFIX, ENGINE_PREFIX,
    TRANSMISSION_PREFIX, MILEAGE_PREFIX, PRICE_PREFIX,
)

# Признаки СТАРОГО, уже исправленного формата (post_builder.build_telegram_post,
# ранняя версия lead_post_template.txt с построчной разбивкой) — если хоть
# один встретится в тексте, пост считается невалидным, независимо от
# остальных проверок. Это самое важное правило файла.
OLD_FORMAT_MARKERS = (
    r"https?://",                       # видимая ссылка на источник в тексте поста
    r"Включает пошлину",                 # post_builder.build_telegram_post()
    r"ЕТС",
    r"утильсбор",
    r"Банк\.?\s*комиссия",               # ранняя построчная разбивка lead_post_template.txt
    r"Таможня\s*\+\s*утильсбор",
    r"Доставка\s*\+\s*брокер",
    r"Цена в Корее",                     # текст-фолбэк из post_builder.py при нехватке данных
    r"Цена под ключ Казань",             # старая формулировка (не "Стоимость под ключ в Казани")
    r"🔗",                                # эмодзи-маркер ссылки в старом формате
)

MAX_PHOTOS_PER_ALBUM = 10  # ограничение Telegram Bot API для sendMediaGroup


@dataclass
class ValidationResult:
    ok: bool
    errors: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def validate_post_format(post_text: str) -> ValidationResult:
    """
    Проверяет текст поста на соответствие эталонной схеме. Не зависит от
    того, как получен текст — детерминированным шаблоном или (в будущем)
    моделью — оба должны проходить одну и ту же проверку.
    """
    errors = []

    if not post_text or not post_text.strip():
        return ValidationResult(False, ["Пустой текст поста"])

    lines = post_text.strip().split("\n")

    # 1. Признаки старого формата — самая важная проверка, идёт первой.
    for pattern in OLD_FORMAT_MARKERS:
        if re.search(pattern, post_text, re.IGNORECASE):
            errors.append(f"Найден признак СТАРОГО формата: паттерн {pattern!r}")

    # 2. Хук — первая строка должна начинаться с одного из HOOK_EMOJIS.
    if not lines[0].startswith(HOOK_EMOJIS):
        errors.append(f"Первая строка должна начинаться с {HOOK_EMOJIS}, "
                       f"получено: {lines[0]!r}")

    # 3. Обязательные блоки должны присутствовать и идти по порядку.
    last_index = -1
    for block in REQUIRED_BLOCKS_IN_ORDER:
        idx = post_text.find(block)
        if idx == -1:
            errors.append(f"Не найден обязательный блок: {block!r}")
            continue
        if idx < last_index:
            errors.append(f"Блок {block!r} расположен не по порядку")
        last_index = idx

    # 4. CTA — строка с 👉 и упоминанием бота (@имя).
    if not re.search(rf"{CTA_MARKER}.*@\w+", post_text):
        errors.append(f"Не найдена строка CTA с {CTA_MARKER!r} и упоминанием бота (@...)")

    # 5. Цена — ОДНА итоговая цифра после PRICE_PREFIX, не построчная
    # разбивка (построчная разбивка дополнительно ловится через
    # OLD_FORMAT_MARKERS выше — здесь проверяем именно формат самой цифры).
    if not re.search(rf"{re.escape(PRICE_PREFIX)}\s*[\d\s]+\s*₽", post_text):
        errors.append(f"Не найдена корректно оформленная итоговая цена после {PRICE_PREFIX!r}")

    return ValidationResult(ok=not errors, errors=errors)


def validate_media_group(photos: list, has_caption_on_first: bool = True) -> ValidationResult:
    """
    Проверяет формальную структуру фотоальбома (см.
    telegram_publisher.send_media_group_to_telegram): не больше
    MAX_PHOTOS_PER_ALBUM фото; если фото больше одного — подпись должна
    быть на первом элементе (у остальных её быть не должно — это уже
    проверяется на уровне вызова send_media_group_to_telegram, здесь —
    только количественные ограничения).
    """
    errors = []
    if len(photos) > MAX_PHOTOS_PER_ALBUM:
        errors.append(f"Слишком много фото в альбоме: {len(photos)} "
                       f"(максимум {MAX_PHOTOS_PER_ALBUM})")
    if len(photos) > 1 and not has_caption_on_first:
        errors.append("У альбома из нескольких фото должна быть подпись на первом элементе")
    return ValidationResult(ok=not errors, errors=errors)
