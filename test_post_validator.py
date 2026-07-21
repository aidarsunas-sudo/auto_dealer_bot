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

import logging
import sys
import traceback

import config
import encar_scraper
from post_validator import (
    validate_post_before_publish, filter_year_range, PublishBlockedError,
    check_price_sanity, check_price_vs_new_krw, check_price_vs_batch_median,
    REJECTED_LOG_PATH, ValidatedCar,
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


class _CollectingLogHandler(logging.Handler):
    """Ловит записи лога post_validator, чтобы доказать, что отсутствие
    эталонной цены логируется ГРОМКО (WARNING), а не молчит — см. запрос
    2026-07-21 п.2 ("молчаливый пропуск = та же дыра, что была с
    валидаторами")."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records: list = []

    def emit(self, record):
        self.records.append(record)


def _check_price_vs_new_krw() -> bool:
    """
    Регрессия на реальный инцидент 2026-07-21: Kia K5 (DL3) 2.0, encar
    #42247766 — цена объявления 37 900 000 ₩ (BadgeDetail "노블레스"/Noblesse,
    Badge/trim "2.0" — латиница, не путать с корейским-текст-в-title багом,
    это отдельный класс проблемы) при справочной цене нового ТОП-трима
    (Signature) 34 690 000 ₩ (model_specs.py, ModelSpec.new_price_max_krw)
    — REJECT, БЕЗ допуска на наценку (потолок уже самый дорогой трим — см.
    докстринг check_price_vs_new_krw() про то, почему первая версия
    анкорилась на базовый трим и была исправлена). Никакого мока:
    find_spec()/model_specs.py — чистые локальные функции, сеть не нужна,
    в отличие от _check_price_sanity() (encar_scraper.fetch_origin_price_krw()
    — живой API).

    Плюс доказательство п.2 запроса 2026-07-21: справочник new_price_max_krw
    заполнен НЕ по всему каталогу model_specs.py (53 модели, из них с
    new_price_max_krw — см. print в конце run()) — для моделей без данных
    check_price_vs_new_krw() обязана писать WARNING в лог logger'а
    "post_validator" (не молчать), но не блокировать публикацию.
    """
    ok_all = True

    # Точь-в-точь как в инциденте (см. main_pipeline.log 2026-07-20 09:11:22,
    # test_k5_sonata/encar_42247766.json) — REJECT.
    car_overpriced = {"brand": "Kia", "model": "K5", "trim": "2.0", "year": 2022,
                       "price": 37_900_000}
    ok, reason = check_price_vs_new_krw(car_overpriced)
    passed = ok is False
    ok_all &= passed
    _safe_print(f"[{'OK' if passed else 'FAIL'}] Kia K5 encar #42247766 (37.9М ₩, "
                f"цена нового топ-трима 34.69М ₩): ok={ok} reason={reason!r}")

    # Ниже цены нового топ-трима (Signature) — честный близкий к топу лот
    # не должен резаться.
    car_reasonable = {"brand": "Kia", "model": "K5", "trim": "2.0", "year": 2022,
                       "price": 34_000_000}
    ok2, reason2 = check_price_vs_new_krw(car_reasonable)
    passed2 = ok2 is True
    ok_all &= passed2
    _safe_print(f"[{'OK' if passed2 else 'FAIL'}] Kia K5 34М ₩ (ниже цены нового "
                f"топ-трима): ok={ok2}")

    # Ровно на границе (= цене нового топ-трима) — НЕ должно резаться (строго
    # "выше", не "выше либо равно").
    car_boundary = {"brand": "Kia", "model": "K5", "trim": "2.0", "year": 2022,
                     "price": 34_690_000}
    ok2b, reason2b = check_price_vs_new_krw(car_boundary)
    passed2b = ok2b is True
    ok_all &= passed2b
    _safe_print(f"[{'OK' if passed2b else 'FAIL'}] Kia K5 ровно 34.69М ₩ "
                f"(= цене нового топ-трима, не выше): ok={ok2b}")

    handler = _CollectingLogHandler()
    pv_logger = logging.getLogger("post_validator")
    pv_logger.addHandler(handler)
    try:
        # Модель вообще не распознана в model_specs.py (заведомо не
        # существующая в SPECS — не Kia Ray: она там ЕСТЬ, просто без
        # new_price_max_krw, см. следующую проверку) — нет данных для
        # сравнения, не блокирует, НО логирует WARNING.
        car_no_spec = {"brand": "Zzz", "model": "Nonexistent9999", "trim": "",
                        "year": 2022, "price": 999_999_999}
        ok3, reason3 = check_price_vs_new_krw(car_no_spec)
        passed3 = ok3 is True and any(
            "не распознана" in r.getMessage() for r in handler.records)
        ok_all &= passed3
        _safe_print(f"[{'OK' if passed3 else 'FAIL'}] Модель не распознана в model_specs.py "
                    f"— не блокирует, но WARNING в лог: ok={ok3}, "
                    f"залогировано={[r.getMessage() for r in handler.records]}")

        # Модель распознана (Kia K3 — есть cc/hp/fuel в справочнике), но
        # new_price_max_krw для неё не заполнен — тоже не блокирует, но
        # ДОЛЖНО логировать WARNING (это и есть п.2 запроса: "поле пустое" —
        # не то же самое, что "модель не распознана", проверяем ОБЕ ветки).
        handler.records.clear()
        car_no_price_ref = {"brand": "Kia", "model": "K3", "trim": "1.6",
                             "year": 2022, "price": 999_999_999}
        ok4, reason4b = check_price_vs_new_krw(car_no_price_ref)
        passed4 = ok4 is True and any(
            "не заполнен" in r.getMessage() for r in handler.records)
        ok_all &= passed4
        _safe_print(f"[{'OK' if passed4 else 'FAIL'}] Модель распознана, но "
                    f"new_price_max_krw пуст (Kia K3) — не блокирует, но WARNING в лог: "
                    f"ok={ok4}, залогировано={[r.getMessage() for r in handler.records]}")
    finally:
        pv_logger.removeHandler(handler)

    # Сквозная проверка ЧЕРЕЗ validate_post_before_publish() — тот же путь,
    # что и настоящая публикация (main_pipeline.py -> agent_publisher.
    # publish_post() -> validate_post_before_publish()), не только голая
    # check_price_vs_new_krw(). check_price_sanity() (запускается ПЕРЕД
    # check_price_vs_new_krw() внутри validate_post_before_publish()) тоже
    # дёрнула бы Encar за живой originPrice — подменяем на None (площадка
    # не отдала originPrice именно для этой комплектации), ИМЕННО тот
    # сценарий, который и позволил инциденту случиться: проверка не
    # блокирует и ничего не решает — не гадаем — тест не должен зависеть
    # ни от сети, ни от того, жив ли ещё #42247766 на сайте.
    original_fetch = encar_scraper.fetch_origin_price_krw
    try:
        encar_scraper.fetch_origin_price_krw = lambda external_id: None

        car_k5_full = {
            "brand": "Kia", "model": "K5", "trim": "2.0",
            "year": 2022, "month": 5, "mileage_km": 7222,
            "price": 37_900_000, "source": "encar", "external_id": "42247766",
            "photos": list(ENOUGH_PHOTOS), "url": "https://fem.encar.com/cars/detail/42247766",
        }
        validated, reason4 = validate_post_before_publish(car_k5_full)
        passed4 = validated is None and "цены нового" in reason4
        ok_all &= passed4
        _safe_print(f"[{'OK' if passed4 else 'FAIL'}] validate_post_before_publish() на полном "
                    f"лоте #42247766: ok={validated is not None} reason={reason4!r}")
    finally:
        encar_scraper.fetch_origin_price_krw = original_fetch

    return ok_all


def _check_price_vs_batch_median() -> bool:
    """
    Регрессия — универсальный гейт (запрос 2026-07-21 п.2, продолжение):
    check_price_vs_new_krw() ловит перегрев цены только там, где кто-то
    вручную заполнил model_specs.ModelSpec.new_price_max_krw (1 из 99
    моделей). check_price_vs_batch_median() не требует справочника — эталон
    строится из медианы партии САМОЙ модели прямо в текущем прогоне.

    Партия: 5 "обычных" K5 (28.0-29.2М ₩) + оверпрайс #42247766 (37.9М ₩) =
    6 лотов. Медиана 6 значений = (28.6М+28.9М)/2 = 28.75М ₩,
    ×config.BATCH_MEDIAN_FACTOR(1.3) = 37.375М ₩ — 37.9М ₩ выше, REJECT.
    Плюс в той же партии — 2 лота Audi Q3 с ОГРОМНЫМ разбросом цены
    (20М/80М), но их всего 2 (< config.MIN_BATCH_N=5) — группе не доверяем,
    ни один не режется, ДОКАЗЫВАЕТ, что группировка по (brand, model)
    изолирована (Q3 не влияет на K5 и наоборот) и что маленькие группы
    пропускаются целиком, как и задумано.
    """
    normal_k5_prices = [28_000_000, 28_300_000, 28_600_000, 28_900_000, 29_200_000]
    overpriced_k5 = {"brand": "Kia", "model": "K5", "trim": "2.0", "year": 2022,
                      "price": 37_900_000, "source": "encar", "external_id": "42247766",
                      "url": "https://fem.encar.com/cars/detail/42247766"}
    normal_k5_cars = [
        {"brand": "Kia", "model": "K5", "trim": "2.0", "year": 2022, "price": p,
         "source": "encar", "external_id": f"k5-normal-{i}", "url": f"https://example.com/k5/{i}"}
        for i, p in enumerate(normal_k5_prices)
    ]
    small_q3_batch = [
        {"brand": "Audi", "model": "Q3", "trim": "35 TDI", "year": 2022, "price": 20_000_000,
         "source": "encar", "external_id": "q3-cheap", "url": "https://example.com/q3/1"},
        {"brand": "Audi", "model": "Q3", "trim": "35 TDI", "year": 2022, "price": 80_000_000,
         "source": "encar", "external_id": "q3-expensive", "url": "https://example.com/q3/2"},
    ]
    batch = normal_k5_cars + [overpriced_k5] + small_q3_batch

    log = logging.getLogger("test_post_validator")
    result = check_price_vs_batch_median(batch, log, "test")

    result_ids = {c["external_id"] for c in result}
    k5_overpriced_dropped = "42247766" not in result_ids
    k5_normal_kept = all(f"k5-normal-{i}" in result_ids for i in range(5))
    q3_both_kept = {"q3-cheap", "q3-expensive"} <= result_ids
    total_ok = len(result) == len(batch) - 1  # ровно 1 отклонённый — #42247766

    passed = k5_overpriced_dropped and k5_normal_kept and q3_both_kept and total_ok
    _safe_print(f"[{'OK' if passed else 'FAIL'}] check_price_vs_batch_median(): "
                f"K5 #42247766 (37.9М) отклонён={k5_overpriced_dropped}, "
                f"5 обычных K5 сохранены={k5_normal_kept}, "
                f"2 Q3 (группа < MIN_BATCH_N) сохранены оба={q3_both_kept}, "
                f"итого сохранено {len(result)}/{len(batch)}")

    # Причина реально дописана в rejected_posts.log (не только "выпал из
    # kept-списка в памяти") — см. запрос: "причина в rejected_posts.log".
    log_text = REJECTED_LOG_PATH.read_text(encoding="utf-8") if REJECTED_LOG_PATH.exists() else ""
    logged = "42247766" in log_text and "медианы партии" in log_text
    passed &= logged
    _safe_print(f"[{'OK' if logged else 'FAIL'}] Причина дописана в rejected_posts.log: {logged}")

    return passed


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

    # Sanity-check цены относительно цены НОВОГО (Kia K5 encar #42247766, 2026-07-21)
    results.append(_check_price_vs_new_krw())

    # Групповой (batch) ценовой гейт — не требует справочника (2026-07-21)
    results.append(_check_price_vs_batch_median())

    # ValidatedCar физически блокирует generate_videos() на невалидированных данных
    results.append(_check_generate_videos_rejects_raw_dict())

    # Покрытие справочника new_price_max_krw (запрос 2026-07-21, п.3) — не
    # тест на pass/fail, просто видимость масштаба дыры "нет данных = не
    # проверяем" (для остального каталога check_price_vs_new_krw() логирует
    # WARNING вместо тихого пропуска, см. _check_price_vs_new_krw() выше).
    import model_specs
    total_specs = len(model_specs.SPECS)
    filled_specs = [s for s in model_specs.SPECS if s.new_price_max_krw is not None]
    _safe_print(f"\nПокрытие model_specs.ModelSpec.new_price_max_krw: "
                f"{len(filled_specs)}/{total_specs} записей SPECS заполнено "
                f"({', '.join(s.label for s in filled_specs) or '—'})")

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
