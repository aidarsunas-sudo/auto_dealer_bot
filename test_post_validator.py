# -*- coding: utf-8 -*-
"""
test_post_validator.py — самотест post_validator.py (в стиле
test_post_format_schema.py — python test_post_validator.py, без pytest,
без новых зависимостей).

Кейсы регрессии (все четыре ДОЛЖНЫ давать REJECT):
    1. Пост без фото (пустой список).
    2. Пост с корейским названием (регрессия конкретного прод-бага — см.
       FINAL_STATE.md, "기아 더 뉴 기아 레이" вместо "Kia Ray").
    3. Пост с годом вне диапазона [config.MIN_YEAR, config.MAX_YEAR].
    4. Пост без года (None) — не должен проходить "на всякий случай".

Плюс один позитивный контроль (валидная машина проходит) — иначе тест
доказывал бы только "всё падает", а не "падает ИМЕННО то, что должно".

Плюс _check_publish_post_raises_from_send_point() — отдельное доказательство
для инцидента 2026-07-07 (пост Mercedes GLB X247 без фото ушёл в канал,
хотя validate_post_before_publish() существовала): проверяет не саму
validate_post_before_publish() (это уже покрыто выше), а то, что
PublishBlockedError реально поднимается ИЗ agent_publisher.publish_post()
— единственной функции, которая по-настоящему вызывает send_to_telegram/
send_to_vk — а не из какого-то промежуточного слоя, который можно забыть
подключить или случайно обойти (именно так и случился инцидент).
"""

import sys
import traceback

import config
from post_validator import validate_post_before_publish, filter_year_range, PublishBlockedError
from agent_publisher import publish_post


def _safe_print(text: str) -> None:
    """Windows-консоль с не-UTF8 кодировкой (cp1251) падает на корейских
    символах в reason — то же решение, что и в vk_publisher._safe_print."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))

VALID_YEAR = (config.MIN_YEAR + config.MAX_YEAR) // 2
ENOUGH_PHOTOS = [f"https://example.com/{i}.jpg" for i in range(config.MIN_PHOTOS_PER_POST)]


def _base_car() -> dict:
    return {
        "brand": "Kia",
        "model": "Ray",
        "trim": "1.0 Gasoline",
        "year": VALID_YEAR,
        "photos": list(ENOUGH_PHOTOS),
        "url": "https://example.com/car/1",
    }


def _check(label: str, car: dict, expect_ok: bool) -> bool:
    ok, reason = validate_post_before_publish(car)
    passed = ok == expect_ok
    status = "OK" if passed else "FAIL"
    _safe_print(f"[{status}] {label}: ok={ok} reason={reason!r} (ожидалось ok={expect_ok})")
    return passed


def _check_publish_post_raises_from_send_point() -> bool:
    """
    Доказательство ШАГ 4 (см. докстринг файла): вызывает РЕАЛЬНУЮ
    agent_publisher.publish_post() (dry_run=True — сеть не трогаем, но вся
    остальная логика функции исполняется как в проде) на посте без фото и
    убеждается, что PublishBlockedError поднимается именно из
    agent_publisher.py, а не откуда-то ещё.
    """
    car_no_photos = _base_car()
    car_no_photos["photos"] = []

    try:
        publish_post(car_no_photos, "тестовый текст поста", dry_run=True)
        _safe_print("[FAIL] publish_post() НЕ поднял PublishBlockedError на посте без фото")
        return False
    except PublishBlockedError as error:
        tb = traceback.extract_tb(error.__traceback__)
        raise_frame = tb[-1]  # последний кадр стека — где именно сработал raise
        raised_in_agent_publisher = raise_frame.filename.endswith("agent_publisher.py")
        status = "OK" if raised_in_agent_publisher else "FAIL"
        _safe_print(f"[{status}] PublishBlockedError поднят из "
                    f"{raise_frame.filename}:{raise_frame.lineno} ({raise_frame.name}) — {error}")
        return raised_in_agent_publisher


def run() -> None:
    results = []

    # 0. Позитивный контроль — валидная машина должна пройти.
    results.append(_check("Валидная машина (контроль)", _base_car(), expect_ok=True))

    # 1. Без фото
    car_no_photos = _base_car()
    car_no_photos["photos"] = []
    results.append(_check("Без фото", car_no_photos, expect_ok=False))

    car_none_photos = _base_car()
    car_none_photos["photos"] = None
    results.append(_check("photos=None", car_none_photos, expect_ok=False))

    car_few_photos = _base_car()
    car_few_photos["photos"] = ENOUGH_PHOTOS[:1]  # меньше минимума (если минимум > 1)
    if config.MIN_PHOTOS_PER_POST > 1:
        results.append(_check("Фото меньше минимума", car_few_photos, expect_ok=False))

    # 2. Корейское название (регрессия реального бага, см. FINAL_STATE.md)
    car_korean = _base_car()
    car_korean["brand"] = ""
    car_korean["model"] = "기아 더 뉴 기아 레이 1.0 가솔린 시그니처"
    car_korean["trim"] = ""
    results.append(_check("Корейское название модели", car_korean, expect_ok=False))

    # 3. Год вне диапазона
    car_old_year = _base_car()
    car_old_year["year"] = config.MIN_YEAR - 5
    results.append(_check(f"Год вне диапазона ({car_old_year['year']})", car_old_year, expect_ok=False))

    car_future_year = _base_car()
    car_future_year["year"] = config.MAX_YEAR + 5
    results.append(_check(f"Год вне диапазона ({car_future_year['year']})", car_future_year, expect_ok=False))

    # 4. Год отсутствует
    car_no_year = _base_car()
    car_no_year["year"] = None
    results.append(_check("Год отсутствует (None)", car_no_year, expect_ok=False))

    # Доп.: filter_year_range() — парсинг-этапный фильтр (тот же диапазон)
    import logging
    log = logging.getLogger("test_post_validator")
    cars = [_base_car(), car_old_year, car_no_year, car_future_year]
    filtered = filter_year_range(cars, log, "test")
    filter_ok = filtered == [cars[0]]
    results.append(filter_ok)
    print(f"[{'OK' if filter_ok else 'FAIL'}] filter_year_range: оставил {len(filtered)}/4 "
          f"(ожидалось 1 — только валидная машина)")

    # ШАГ 4: PublishBlockedError реально летит из точки отправки (agent_publisher.py)
    results.append(_check_publish_post_raises_from_send_point())

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
