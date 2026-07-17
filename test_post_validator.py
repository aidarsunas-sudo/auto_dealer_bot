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
import encar_scraper
from post_validator import (
    validate_post_before_publish, filter_year_range, PublishBlockedError,
    check_price_sanity, ValidatedCar,
)
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


def _check(label: str, car: dict, expect_ok: bool, fields: dict | None = None) -> bool:
    validated, reason = validate_post_before_publish(car, fields)
    ok = validated is not None
    passed = ok == expect_ok
    type_ok = ok == isinstance(validated, ValidatedCar)  # None при reject, ValidatedCar при успехе — не bool
    status = "OK" if (passed and type_ok) else "FAIL"
    _safe_print(f"[{status}] {label}: ok={ok} reason={reason!r} (ожидалось ok={expect_ok})")
    return passed and type_ok


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


def _check_generate_videos_rejects_raw_dict() -> bool:
    """
    Регрессия инцидента 2026-07-17 (см. FINAL_STATE.md): тестовый скрипт
    вызвал video_generator.generate_videos() напрямую на сыром dict, в обход
    гейта — в ролик с озвучкой попала машина с пробегом больше
    MAX_MILEAGE_KM. Проверяет, что теперь это физически невозможно —
    generate_videos()/ValidatedCar() поднимают TypeError на голом dict, до
    любой сетевой активности (isinstance-проверка — самая первая строка
    generate_videos()).
    """
    from post_validator import ValidatedCar as VC, _VALIDATION_TOKEN

    ok_all = True

    try:
        import video_generator
    except ImportError:
        # video_generator.py — отдельная фича (видео/озвучка), коммитится
        # своим коммитом (см. FINAL_STATE.md) — если её ещё нет в этой
        # копии репозитория, часть теста про generate_videos() пропускаем,
        # не проваливаем: ValidatedCar() без токена ниже всё равно
        # проверяется, а это ядро того же самого физического ограничения.
        _safe_print("[SKIP] video_generator.py не найден в этой копии репозитория — "
                     "пропускаю проверку generate_videos(), она из отдельной фичи")
        video_generator = None

    if video_generator is not None:
        try:
            video_generator.generate_videos({"photos": ["x"]}, {})
            _safe_print("[FAIL] generate_videos() НЕ поднял TypeError на голом dict")
            ok_all = False
        except TypeError as error:
            _safe_print(f"[OK] generate_videos() отклонил голый dict: {error}")
        except Exception as error:
            _safe_print(f"[FAIL] generate_videos() поднял не TypeError, а {type(error).__name__}: {error}")
            ok_all = False

    try:
        VC({"x": 1})  # без токена — должно упасть
        _safe_print("[FAIL] ValidatedCar() создался БЕЗ токена — дыра в защите")
        ok_all = False
    except TypeError:
        _safe_print("[OK] ValidatedCar() без токена поднимает TypeError")

    validated = VC({"x": 1}, _VALIDATION_TOKEN)
    passed = isinstance(validated, dict) and validated["x"] == 1
    _safe_print(f"[{'OK' if passed else 'FAIL'}] ValidatedCar(..., правильный токен) создаётся и ведёт себя как dict")
    ok_all &= passed

    return ok_all


def _check_price_sanity() -> bool:
    """
    Регрессия на реальный инцидент 2026-07 (Chevrolet Trax, +95% от
    заводской цены — расчёт таможни был математически верным, а исходная
    цена — нет, см. FINAL_STATE.md). encar_scraper.fetch_origin_price_krw()
    подменяется на фиксированное значение — тест не должен зависеть от
    того, живо ли ещё это конкретное объявление на сайте.
    """
    original_fetch = encar_scraper.fetch_origin_price_krw
    ok_all = True
    try:
        encar_scraper.fetch_origin_price_krw = lambda external_id: 27_600_000

        # +95% от заводской (точь-в-точь как в инциденте) — REJECT.
        car_overpriced = {"source": "encar", "external_id": "42113103", "price": 53_900_000}
        ok, reason = check_price_sanity(car_overpriced)
        passed = ok is False
        ok_all &= passed
        _safe_print(f"[{'OK' if passed else 'FAIL'}] Encar +95% от заводской: ok={ok} reason={reason!r}")

        # +15% (в пределах допустимой наценки) — проходит.
        car_reasonable = {"source": "encar", "external_id": "1", "price": 31_740_000}
        ok2, reason2 = check_price_sanity(car_reasonable)
        passed2 = ok2 is True
        ok_all &= passed2
        _safe_print(f"[{'OK' if passed2 else 'FAIL'}] Encar +15% от заводской (в пределах): ok={ok2}")

        # Источник не Encar — проверка не блокирует (нет данных для сравнения),
        # и НЕ должна даже пытаться дёрнуть сеть — если бы попыталась, здесь
        # был бы результат "35_000_000", а не пропуск проверки.
        calls_made = []
        encar_scraper.fetch_origin_price_krw = lambda external_id: calls_made.append(external_id) or 35_000_000
        car_other_source = {"source": "charancha", "external_id": "1", "price": 999_999_999}
        ok3, reason3 = check_price_sanity(car_other_source)
        passed3 = ok3 is True and not calls_made
        ok_all &= passed3
        _safe_print(f"[{'OK' if passed3 else 'FAIL'}] Источник charancha (нет заводской цены для сравнения): "
                    f"ok={ok3}, сеть дёрнута={bool(calls_made)}")
    finally:
        encar_scraper.fetch_origin_price_krw = original_fetch

    return ok_all


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

    # 5. Пробег выше допустимого (добавлено 2026-07-17, см. FINAL_STATE.md)
    car_high_mileage = _base_car()
    car_high_mileage["mileage_km"] = config.MAX_MILEAGE_KM + 1
    results.append(_check("Пробег выше MAX_MILEAGE_KM", car_high_mileage, expect_ok=False))

    car_ok_mileage = _base_car()
    car_ok_mileage["mileage_km"] = config.MAX_MILEAGE_KM
    results.append(_check("Пробег ровно на границе MAX_MILEAGE_KM", car_ok_mileage, expect_ok=True))

    car_unknown_mileage = _base_car()
    car_unknown_mileage["mileage_km"] = None
    results.append(_check("Пробег неизвестен (None) — не блокирует", car_unknown_mileage, expect_ok=True))

    # 6. Цена под ключ вне абсолютного диапазона (добавлено 2026-07-17,
    # регрессия инцидента Chevrolet Trax ~5 млн ₽ — см. FINAL_STATE.md)
    fields_too_expensive = {"total_rub_raw": config.PRICE_MAX_RUB + 1}
    results.append(_check("Цена выше PRICE_MAX_RUB", _base_car(), expect_ok=False,
                           fields=fields_too_expensive))

    fields_too_cheap = {"total_rub_raw": config.PRICE_MIN_RUB - 1}
    results.append(_check("Цена ниже PRICE_MIN_RUB", _base_car(), expect_ok=False,
                           fields=fields_too_cheap))

    fields_reasonable = {"total_rub_raw": (config.PRICE_MIN_RUB + config.PRICE_MAX_RUB) // 2}
    results.append(_check("Цена внутри абсолютного диапазона", _base_car(), expect_ok=True,
                           fields=fields_reasonable))

    results.append(_check("Без fields — проверки цены молча пропускаются",
                           _base_car(), expect_ok=True, fields=None))

    # 7. Цена вне УЗКОГО модельного диапазона (Chevrolet Trax — см.
    # model_specs.py ModelSpec.price_range_rub, тот же инцидент)
    car_trax = _base_car()
    car_trax["brand"] = "Chevrolet"
    car_trax["model"] = "Trax"
    car_trax["trim"] = "1.2 ACTIV"
    fields_trax_absurd = {"total_rub_raw": 5_000_000}  # внутри абсолютного, но абсурдно для Trax
    results.append(_check("Chevrolet Trax ~5 млн ₽ (абсурдно для модели, но внутри "
                           "абсолютного диапазона)", car_trax, expect_ok=False,
                           fields=fields_trax_absurd))

    fields_trax_ok = {"total_rub_raw": 2_000_000}
    results.append(_check("Chevrolet Trax ~2 млн ₽ (внутри модельного диапазона)",
                           car_trax, expect_ok=True, fields=fields_trax_ok))

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

    # Sanity-check цены относительно заводской (Chevrolet Trax, 2026-07)
    results.append(_check_price_sanity())

    # ValidatedCar физически блокирует generate_videos() на невалидированных данных
    results.append(_check_generate_videos_rejects_raw_dict())

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
