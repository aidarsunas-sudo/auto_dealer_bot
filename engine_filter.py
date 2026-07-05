# -*- coding: utf-8 -*-
"""
engine_filter.py — крошечный общий хелпер: по свободному текстовому полю
"engine" из target_models.json (например "2.0 TDI") определяет, какое
топливо ожидается, чтобы адаптеры источников могли отфильтровать явно
несовпадающие по мотору объявления (например, бензиновый Tiguan Allspace,
когда просили именно дизельный 2.0 TDI).

Namespace специально узкий — если строка engine не содержит ничего
узнаваемого, возвращает None ("нет явного предпочтения"), и адаптер должен
пропускать фильтрацию по топливу вместо того, чтобы гадать.
"""

from typing import Optional

_DIESEL_HINTS = ("tdi", "diesel", "디젤", "crdi", "dci", "cdti")
_PETROL_HINTS = ("tsi", "tfsi", "gdi", "gasoline", "petrol", "가솔린")


def engine_fuel_hint(engine: Optional[str]) -> Optional[str]:
    """Возвращает 'diesel' | 'petrol' | None (нет явного предпочтения)."""
    if not engine:
        return None
    e = engine.lower()
    if any(k in e for k in _DIESEL_HINTS):
        return "diesel"
    if any(k in e for k in _PETROL_HINTS):
        return "petrol"
    return None


# Соответствие "подсказки по топливу" -> как это топливо называется на
# конкретном сайте (корейский текст, как его отдают источники в разведке).
FUEL_NAME_KO = {
    "diesel": "디젤",
    "petrol": "가솔린",
}
