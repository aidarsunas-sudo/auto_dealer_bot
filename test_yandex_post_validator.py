# -*- coding: utf-8 -*-
"""
test_yandex_post_validator.py — самотест yandex_post_validator.py (в стиле
test_post_validator.py/test_post_format_schema.py — python
test_yandex_post_validator.py, без pytest).
"""

import re
import sys

from customs_calculator import DEFAULT_RATES
from yandex_post_builder import build_yandex_post
from yandex_post_validator import validate_yandex_post

STUB_CAR = {
    "brand": "Kia",
    "model": "K5",
    "trim": "1.6T",
    "external_id": "test-validator-stub",
    "year": 2021,
    "mileage_km": 45000,
    "cc": 1600,
    "hp": 150,  # <= 160 л.с. — реальный K5 1.6T, льготный утильсбор (см. CLAUDE.md)
    "fuel_code": "petrol",
    "price": 28000000,
    "specs": "климат-контроль, камера заднего вида, подогрев сидений",
    "photos": ["https://example.com/test-0.jpg"],
    "source": "test",
}

RATES = dict(DEFAULT_RATES)


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _check(label: str, result, expect_ok: bool) -> bool:
    ok = bool(result) == expect_ok
    _safe_print(f"[{'OK' if ok else 'FAIL'}] {label}: ok={bool(result)} "
                f"(ожидалось {expect_ok})")
    if not ok or (result and not result.ok):
        for error in result.errors:
            _safe_print(f"         - {error}")
    return ok


def run() -> None:
    results = []

    # 1. Оба шаблона в исходном виде (как их строит build_yandex_post) должны
    # проходить валидацию без единой ошибки — это и есть регресс-проверка на
    # то, что builder и validator не разъехались друг с другом.
    under_order_post, reason = build_yandex_post(STUB_CAR, RATES, stock_status="under_order")
    results.append(_check("under_order пост (как есть из builder'а)",
                           validate_yandex_post(under_order_post) if under_order_post else None,
                           True))

    in_stock_post, reason = build_yandex_post(STUB_CAR, RATES, stock_status="in_stock")
    results.append(_check("in_stock пост (как есть из builder'а)",
                           validate_yandex_post(in_stock_post) if in_stock_post else None,
                           True))

    # 2. Год отсутствует в заголовке (пробег на месте) -> блок.
    broken = dict(under_order_post, title="Kia K5 1.6T, 45 000 км, под заказ")
    results.append(_check("год отсутствует в заголовке", validate_yandex_post(broken), False))

    # 2b. Пробег отсутствует в заголовке (год на месте) -> блок.
    broken = dict(under_order_post, title="Kia K5 1.6T 2021, под заказ")
    results.append(_check("пробег отсутствует в заголовке", validate_yandex_post(broken), False))

    # 3. Цена диапазоном -> блок. Реальное значение цены зависит от курса
    # (customs_calculator) — подменяем по регэкспу, не хардкодим цифру.
    price_as_range_text = re.sub(r"\d[\d\s]{3,}\s*₽", "от 2 000 000 до 2 500 000 ₽",
                                  under_order_post["text"], count=1)
    broken = dict(under_order_post, text=price_as_range_text)
    results.append(_check("цена диапазоном", validate_yandex_post(broken), False))

    # 4. under_order с "просмотр" вместо "консультация" -> блок (и блок-слово,
    # и несовпадение CTA сработают одновременно).
    broken = dict(under_order_post, text=under_order_post["text"].replace(
        "Записаться на консультацию", "Записаться на просмотр"))
    results.append(_check("under_order с 'просмотр' вместо 'консультация'",
                           validate_yandex_post(broken), False))

    # 5. under_order с "растаможена" (блок-слово + рассогласование рода) -> блок.
    broken = dict(under_order_post, text=under_order_post["text"] + " Машина уже растаможена.")
    results.append(_check("under_order с 'растаможена'", validate_yandex_post(broken), False))

    # 6. Хэштег в Яндекс-тексте -> блок.
    broken = dict(under_order_post, text=under_order_post["text"] + " #автоизкореи")
    results.append(_check("хэштег в Яндекс-тексте", validate_yandex_post(broken), False))

    # 7. Дубль ключевой фразы -> блок.
    broken = dict(under_order_post, text=under_order_post["text"] + " Оформление под ключ в Казани ещё раз.")
    results.append(_check("дубль ключевой фразы 'под ключ в казани'", validate_yandex_post(broken), False))

    # 8. "ПТС" без "Э" -> блок.
    broken = dict(under_order_post, text=under_order_post["text"].replace("ЭПТС", "ПТС"))
    results.append(_check("'ПТС' вместо 'ЭПТС'", validate_yandex_post(broken), False))

    # 9. hp выше льготного порога у модели, для которой известна версия
    # В ПРЕДЕЛАХ льготы (Kia K5 — есть 2.0 MPi/160 л.с. в model_specs.py) -> блок.
    broken = dict(under_order_post, hp=200)
    results.append(_check("Kia K5 hp=200 (у модели есть версия <=160 л.с. в SPECS)",
                           validate_yandex_post(broken), False))

    # 10. hp выше порога, но модель НЕ отслеживается в model_specs.py вообще
    # (нет данных о версии в пределах льготы) -> НЕ блокируем, не гадаем.
    broken = dict(under_order_post, brand="НеизвестныйБренд", model="НеизвестнаяМодель", hp=200)
    results.append(_check("неизвестная модель, hp=200 (нет данных о версии <=160 л.с.)",
                           validate_yandex_post(broken), True))

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
