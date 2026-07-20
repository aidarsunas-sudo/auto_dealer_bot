# -*- coding: utf-8 -*-
"""
yandex_post_validator.py — жёсткий код-гейт для Яндекс/Дзен-витрины (см.
yandex_post_builder.build_yandex_post()) — ПЕРЕД отправкой черновика в
служебный чат (см. yandex_publisher.send_yandex_draft()). Тот же принцип,
что и у post_validator.py/post_format_schema.py: детерминированная функция,
не промпт-инструкция — код срабатывает одинаково каждый раз.

Полный чек-лист (нарушено хотя бы одно условие — REJECT):
    1. Год выпуска — обязателен и в заголовке, и в первой строке текста
       (см. _YEAR_RE) — как в утверждённых шаблонах ("Kia K5 2021").
    2. Пробег — точным числом в заголовке, без диапазона/"до".
    3. Цена — точным числом в тексте, без диапазона/"от"-"до"
       (ИСКЛЮЧЕНИЕ: срок поставки {delivery_days} — диапазон разрешён,
       это единственное поле, где он ожидаем и не блокируется).
    4. CTA обязателен и должен соответствовать статусу: under_order ->
       "Записаться на консультацию", in_stock -> "Записаться на просмотр".
       "просмотр" в under_order-тексте запрещён отдельно (машина ещё в
       Корее, смотреть физически нечего).
    5. under_order: слова "в наличии"/"привезён"/"привезена"/"растаможен"/
       "растаможена" запрещены (совершённый вид — авто ещё не доставлено).
    6. Согласование рода "автомобиль" -> привезён/растаможен: "привезена"/
       "растаможена" запрещены ВСЕГДА, независимо от статуса.
    7. "ПТС" (не "ЭПТС") запрещено — see yandex_post_builder._PTS_RE, здесь
       независимая повторная проверка перед отправкой.
    8. Минимум 2 из 3 ключевых фраз (KEY_PHRASES) должны присутствовать.
    9. Ни одна ключевая фраза не должна повторяться (дубли между абзацами —
       см. инцидент при ручной проверке: "под ключ" дважды в одном посте).
    10. Символ "#" запрещён (хэштеги — только для TG/VK, не для Яндекса).
    11. hp лота > MAX_HP_FOR_PHYS_LGOTA (160) — блок ТОЛЬКО если для этой же
        модели (brand+model) в model_specs.py есть известная версия В
        ПРЕДЕЛАХ льготы (см. model_specs.model_has_privileged_hp_variant()).
        Если у модели вообще нет варианта в пределах льготы (или модель не
        отслеживается) — не блокируем, не гадаем. Пример: у Kia K5 в SPECS
        есть 2.0 MPi/160 л.с., но реальная комплектация лота может оказаться
        1.6T GT-line мощнее — тогда пост скорее всего собран по неверным
        данным о моторе, стоит сверить у брокера, прежде чем публиковать
        по коммерческому утильсбору вместо льготного.
"""

import re
from dataclasses import dataclass, field

from customs_calculator import MAX_HP_FOR_PHYS_LGOTA
from model_specs import model_has_privileged_hp_variant

KEY_PHRASES = ("авто из кореи под заказ", "под ключ в казани", "с корейского аукциона")
MIN_KEY_PHRASES = 2

# Год выпуска — 4 цифры, 19xx/20xx (тот же диапазон правдоподобия, что и в
# lead-gen ниже по смыслу, но здесь без привязки к config.MIN_YEAR/MAX_YEAR:
# у собственного стока дилера год лота может быть любым, не только 2022-2023
# из target_models.json — эта проверка про ПРИСУТСТВИЕ года, не про диапазон).
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

BLOCKED_WORDS_UNDER_ORDER = ("в наличии", "привезён", "привезена", "растаможен", "растаможена", "просмотр")

# Запрещено всегда, вне зависимости от статуса — "автомобиль" мужского рода.
FEMININE_FORMS = ("привезена", "растаможена")

CTA_BY_STATUS = {
    "under_order": "Записаться на консультацию — по телефону или сообщением в этом профиле.",
    "in_stock": "Записаться на просмотр — по телефону или сообщением в этом профиле.",
}

_MILEAGE_IN_TITLE_RE = re.compile(r"\d[\d\s]*\d\s*км")
_MILEAGE_RANGE_RE = re.compile(r"\bдо\s+[\d\s]+\s*км", re.IGNORECASE)
_PRICE_RE = re.compile(r"\d[\d\s]{3,}\s*₽")
_PRICE_RANGE_RE = re.compile(r"\b(?:от|до)\s+[\d\s]+\s*₽", re.IGNORECASE)
_PTS_RE = re.compile(r"(?<![А-Яа-яЁё])ПТС(?![А-Яа-яЁё])")


@dataclass
class ValidationResult:
    ok: bool
    errors: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def validate_yandex_post(post: dict) -> ValidationResult:
    """
    Args:
        post: {"title": str, "text": str, "stock_status": "in_stock"|"under_order"}
              — см. yandex_post_builder.build_yandex_post().
    """
    errors = []

    title = post.get("title", "")
    text = post.get("text", "")
    status = post.get("stock_status")
    full = f"{title}\n{text}"
    full_lower = full.lower()

    if status not in CTA_BY_STATUS:
        return ValidationResult(False, [f"неизвестный/отсутствующий stock_status: {status!r}"])

    # 1. Год выпуска — обязателен в заголовке и в первой строке текста.
    first_line = text.split("\n", 1)[0]
    if not _YEAR_RE.search(title):
        errors.append(f"год выпуска отсутствует в заголовке: {title!r}")
    if not _YEAR_RE.search(first_line):
        errors.append(f"год выпуска отсутствует в первой строке текста: {first_line!r}")

    # 2. Пробег — точное число в заголовке, не диапазон.
    if not _MILEAGE_IN_TITLE_RE.search(title):
        errors.append(f"пробег отсутствует в заголовке (или не оформлен числом): {title!r}")
    if _MILEAGE_RANGE_RE.search(full):
        errors.append("пробег указан как диапазон/оценка (\"до ... км\") — нужно точное число")

    # 3. Цена — точное число, не диапазон (срок поставки — исключение, его
    # не проверяем на диапазон отдельно, т.к. паттерн ищет только "₽").
    if not _PRICE_RE.search(text):
        errors.append("цена отсутствует или не оформлена как число с ₽")
    if _PRICE_RANGE_RE.search(text):
        errors.append("цена указана как диапазон (\"от ... ₽\"/\"до ... ₽\") — нужно точное число")

    # 4-5. CTA по статусу + блок-слово "просмотр" для under_order.
    expected_cta = CTA_BY_STATUS[status]
    if expected_cta not in text:
        errors.append(f"не найден обязательный CTA для статуса {status!r}: {expected_cta!r}")

    if status == "under_order":
        for word in BLOCKED_WORDS_UNDER_ORDER:
            if word in full_lower:
                errors.append(f"under_order: запрещённое слово/фраза {word!r} найдено в тексте")

    # 6. Согласование рода — запрещено всегда.
    for word in FEMININE_FORMS:
        if word in full_lower:
            errors.append(f"родовое рассогласование: {word!r} — 'автомобиль' мужского рода "
                          f"(привезён/растаможен)")

    # 7. "ПТС" без "Э" запрещено.
    if _PTS_RE.search(full):
        errors.append("встречается 'ПТС' вместо 'ЭПТС'")

    # 8. Минимум 2 из 3 ключевых фраз.
    present = [phrase for phrase in KEY_PHRASES if phrase in full_lower]
    if len(present) < MIN_KEY_PHRASES:
        errors.append(f"недостаточно ключевых фраз: {len(present)} из {len(KEY_PHRASES)} "
                      f"(минимум {MIN_KEY_PHRASES}), найдены: {present}")

    # 9. Ни одна ключевая фраза не повторяется (дубль между абзацами).
    for phrase in KEY_PHRASES:
        count = full_lower.count(phrase)
        if count > 1:
            errors.append(f"ключевая фраза {phrase!r} повторяется {count} раз — "
                          f"не дублировать между абзацами")

    # 10. Хэштеги — только TG/VK, не Яндекс.
    if "#" in full:
        errors.append("символ '#' запрещён в Яндекс-версии (хэштеги — только для TG/VK)")

    # 11. hp выше льготного порога — блок ТОЛЬКО если для этой модели
    # известна версия в пределах льготы (см. докстринг файла и
    # model_specs.model_has_privileged_hp_variant()).
    hp = post.get("hp")
    if hp is not None and hp > MAX_HP_FOR_PHYS_LGOTA:
        brand = post.get("brand", "")
        model = post.get("model", "")
        if model_has_privileged_hp_variant(brand, model, MAX_HP_FOR_PHYS_LGOTA):
            errors.append(
                f"hp={hp} выше льготного порога {MAX_HP_FOR_PHYS_LGOTA} л.с., а для "
                f"{brand} {model} есть известная версия в пределах льготы — сверьте "
                f"реальную комплектацию мотора у брокера перед публикацией"
            )

    return ValidationResult(ok=not errors, errors=errors)
