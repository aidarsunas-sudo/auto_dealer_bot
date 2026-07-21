# -*- coding: utf-8 -*-
"""
color_match.py — маленький общий хелпер: сверяет цвет объявления (как он
пришёл с сайта — по-корейски или латиницей) со списком желаемых цветов
(английские названия, как их вводит пользователь).

Namespace узкий и неполный — пополняется по мере необходимости, как
brand_names_ko.py/engine_filter.py. Площадки нередко не заполняют цвет
вообще (пусто/плейсхолдер вида "информация отсутствует") — это НЕ считается
совпадением ни с одним цветом (см. matches_color: возвращает None, а не
False, чтобы вызывающий код мог честно отличить "точно не тот цвет" от
"неизвестно").
"""

from typing import Optional

COLOR_KEYWORDS = {
    "white": ("흰색", "화이트", "펄화이트", "백색", "진주색"),
    "black": ("검정색", "검은색", "블랙"),
    "blue": ("파란색", "블루", "남색"),
    "grey": ("회색", "쥐색", "그레이"),
    "gray": ("회색", "쥐색", "그레이"),
    "silver": ("은색", "실버"),
    "red": ("빨간색", "레드"),
    "green": ("초록색", "그린"),
    "yellow": ("노란색", "옐로우"),
    "brown": ("갈색", "브라운"),
}

# Значения, которые сайты показывают вместо реального цвета, когда продавец
# его не указал (плейсхолдеры/UI-артефакты, а не настоящие цвета — см.
# разведку на kbchachacha 2026-07-06: колонка "차량색상" отдавала текст
# соседнего UI-лейбла "인기색상", а не цвет, если поле не заполнено).
_UNKNOWN_VALUES = {"정보없음", "인기색상", ""}


def normalize_color(raw: Optional[str]) -> Optional[str]:
    """Возвращает очищенный текст цвета, или None, если это плейсхолдер/пусто."""
    if not raw:
        return None
    value = raw.strip()
    if value in _UNKNOWN_VALUES:
        return None
    return value


def matches_color(raw_color: Optional[str], wanted: list) -> Optional[bool]:
    """
    True — цвет объявления совпал с одним из wanted (англ. названия, напр.
    ["white", "black", "blue"]). False — цвет известен и точно не совпал.
    None — цвет неизвестен (сайт не отдал/плейсхолдер) — не то же самое,
    что False, вызывающий код должен решить сам, отбрасывать ли такие.
    """
    color = normalize_color(raw_color)
    if color is None:
        return None

    color_lower = color.lower()
    for name in wanted:
        keywords = COLOR_KEYWORDS.get(name.strip().lower(), ())
        if any(kw in color for kw in keywords) or name.strip().lower() in color_lower:
            return True
    return False
