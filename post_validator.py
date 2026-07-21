# -*- coding: utf-8 -*-
"""
post_validator.py — жёсткий код-гейт ПЕРЕД публикацией (см. agent_publisher.publish_post()).

Намеренно НЕ промпт-инструкция агенту, а обычная детерминированная функция:
инструкции в промптах агенты периодически игнорируют (уже несколько раз
случалось на практике — см. FINAL_STATE.md), а код исполняется одинаково
каждый раз, без исключений. Нарушено хотя бы одно условие ниже — REJECT,
публикации не будет (ни в Telegram, ни в VK, ни в видео/озвучку — см.
ValidatedCar):

    1. Фото: >= config.MIN_PHOTOS_PER_POST штук (не None, не пустой список).
    2. Название модели (brand+model+trim, как оно уходит в пост — та же
       сборка, что и в самом посте, см. post_text_builder._build_title):
       только латиница/кириллица/цифры/пробелы/стандартная пунктуация
       (см. post_text_builder.ALLOWED_TITLE_RE), никаких символов
       корейского алфавита (хангыль).
    3. Год выпуска: обязателен (не None) и внутри [config.MIN_YEAR,
       config.MAX_YEAR]. Если год не распознан — REJECT, а не публикация
       "на всякий случай" с дефолтным годом.
    4. Пробег: не выше config.MAX_MILEAGE_KM (см. check_mileage()) — добавлено
       2026-07-17, единая точка вместо разрозненных фильтров (раньше был
       только внутри main_pipeline.select_candidates(), НЕ в main.py/
       test_post.py — см. FINAL_STATE.md/аудит).
    5. Цена объявления (для Encar): не выше config.MAX_MARKUP_OVER_ORIGIN от
       заводской цены этой комплектации (см. check_price_sanity()) —
       инцидент 2026-07: реальное объявление Trax стояло на сайте за +95%
       от заводской цены, расчёт таможни при этом был математически верным
       (проверено вручную), но сама исходная цена — нет. Для источников без
       доступной заводской цены (Charancha/kbchachacha/OnBid) проверка не
       блокирует — не гадаем.
    5b. Цена объявления (любой источник): не выше config.MAX_MARKUP_OVER_ORIGIN
       от справочной цены нового этой модели, model_specs.ModelSpec.
       new_price_max_krw (см. check_price_vs_new_krw()) — добавлено
       2026-07-21 после инцидента: Kia K5 (encar #42247766) ушёл в пост по
       37 900 000 ₩ при цене нового 27 630 000 ₩ (+37%) — check_price_sanity()
       эту наценку не поймал (originPrice для этой конкретной комплектации
       площадка не отдала), а price_range_rub/check_price_relative() сравнивает
       уже ИТОГОВУЮ рублёвую цену, не корейскую исходную. source-независимый
       рубеж поверх check_price_sanity(), не замена ему.
    6. Итоговая цена под ключ — абсолютный диапазон [config.PRICE_MIN_RUB,
       config.PRICE_MAX_RUB] (см. check_price_absolute()) И, если для модели
       есть данные, узкий модельный диапазон model_specs.ModelSpec.
       price_range_rub (см. check_price_relative()) — добавлено 2026-07-17
       после инцидента: пост Chevrolet Trax ~5 млн ₽ ушёл в канал — цена
       ОБЪЯВЛЕНИЯ не была завышена (проверка #5 не сработала бы), но
       ИТОГОВАЯ цена под ключ была абсурдной для этой модели. Требует fields
       (post_text_builder.prepare_post_fields()) — без него проверки цены
       #6 молча пропускаются, см. докстринг validate_post_before_publish().

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
from datetime import datetime, timezone
from pathlib import Path

import config
from post_text_builder import _build_title, HANGUL_RE, ALLOWED_TITLE_RE
from model_specs import find_spec

logger = logging.getLogger("post_validator")

REJECTED_LOG_PATH = Path(__file__).resolve().parent / "rejected_posts.log"

# Токен, который знает только этот модуль — ValidatedCar можно создать
# ТОЛЬКО передав его, то есть только изнутри validate_post_before_publish()
# после успешного прохождения ВСЕХ проверок. См. докстринг ValidatedCar.
_VALIDATION_TOKEN = object()


class ValidatedCar(dict):
    """
    Обёртка над car (ведёт себя как обычный dict — car["x"]/car.get("x")
    работают без изменений везде по проекту), которую физически нельзя
    создать напрямую — только вернуть её может validate_post_before_publish()
    при успехе. video_generator.generate_videos() требует именно этот тип
    (проверяет isinstance, см. его докстринг), а не голый dict — так
    генерацию видео/озвучки (tts_generator вызывается ИЗНУТРИ generate_videos)
    нельзя запустить в обход гейта, даже случайно.

    Инцидент 2026-07-17: тестовый скрипт вызвал video_generator.generate_videos()
    напрямую на результате поиска, минуя publish_post()/validate_post_before_publish()
    — в ролик с озвучкой попала машина с пробегом заметно больше
    config.MAX_MILEAGE_KM. До этой правки ничего не мешало вызвать
    generate_videos() на любом dict — теперь мешает: TypeError сразу же.
    """

    def __init__(self, car: dict, _token: object = None):
        if _token is not _VALIDATION_TOKEN:
            raise TypeError(
                "ValidatedCar нельзя создать напрямую — только через "
                "успешный validate_post_before_publish()"
            )
        super().__init__(car)


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


def check_price_sanity(car: dict) -> tuple[bool, str]:
    """
    Сверяет цену объявления с заводской ценой той же комплектации (если
    источник умеет её дать — сейчас только Encar, см.
    encar_scraper.fetch_origin_price_krw()). Наценка выше
    config.MAX_MARKUP_OVER_ORIGIN — REJECT: инцидент 2026-07 (Chevrolet
    Trax, +95% от заводской) показал, что расчёт таможни может быть
    математически безупречным, а исходная цена объявления — нет
    (дилерская наценка за срочную доступность/редкую комплектацию).

    Returns:
        (True, "") — цену проверить не удалось (источник не Encar, нет
            external_id, сеть недоступна) ИЛИ проверка пройдена — в обоих
            случаях НЕ блокируем: нет данных для сравнения — не гадаем,
            это не то же самое, что "подтверждённая проблема".
        (False, reason) — цена подтверждённо завышена относительно
            заводской.
    """
    if car.get("source") != "encar":
        return True, ""
    external_id = car.get("external_id")
    price = car.get("price")
    if not external_id or not price:
        return True, ""

    from encar_scraper import fetch_origin_price_krw  # лениво — не тянуть Playwright всем, кто импортирует этот модуль
    origin_price = fetch_origin_price_krw(external_id)
    if origin_price is None:
        return True, ""

    max_price = origin_price * config.MAX_MARKUP_OVER_ORIGIN
    if price > max_price:
        markup_pct = int((price / origin_price - 1) * 100)
        return False, (f"цена {price:,.0f} ₩ выше заводской {origin_price:,.0f} ₩ на {markup_pct}% "
                        f"(допустимо до {int((config.MAX_MARKUP_OVER_ORIGIN - 1) * 100)}%)")
    return True, ""


def check_price_vs_new_krw(car: dict) -> tuple[bool, str]:
    """
    Цена ОБЪЯВЛЕНИЯ (car["price"], в вонах, до перевода в рубли) выше цены
    нового автомобиля этой модели (model_specs.ModelSpec.new_price_max_krw)
    больше чем на config.MAX_MARKUP_OVER_ORIGIN — REJECT.

    В отличие от check_price_sanity() (живая заводская цена ИМЕННО этой
    комплектации через encar_scraper.fetch_origin_price_krw() — точнее, но
    доступна только для Encar и только когда дилер заполнил это поле у
    площадки) — эта проверка детерминированная и source-независимая (работает
    для kbchachacha/charancha/OnBid тоже, не зависит от сети, не может
    промолчать из-за сбоя стороннего API). Расплата за это — грубее: один
    match в model_specs.py может покрывать несколько тримов (Trendy/Prestige/
    Noblesse под одним мотором) — используем тот же допуск
    MAX_MARKUP_OVER_ORIGIN, что и check_price_sanity() (см. комментарий у
    ModelSpec.new_price_max_krw), чтобы не резать честные старшие тримы по
    цене базового.

    Найдено вживую 2026-07-20/21: Kia K5 (DL3) 2.0, encar #42247766 —
    цена объявления 37 900 000 ₩ (BadgeDetail "노블레스"/Noblesse) при
    справочной цене нового базового 27 630 000 ₩ — 37% наценка, выше
    допустимых 30% (MAX_MARKUP_OVER_ORIGIN=1.3) — REJECT. Раньше такие лоты
    проходили в пост: check_price_sanity() ловит только Encar-объявления, у
    которых площадка отдаёт originPrice именно для этой комплектации, а
    price_range_rub (check_price_relative()) сравнивает уже ИТОГОВУЮ цену под
    ключ, а не исходную корейскую — обе проверки могли молча пропустить этот
    лот, если для него не было данных. Регрессия — см.
    test_post_validator.py::_check_price_vs_new_krw().

    Returns:
        (True, "") — нет car["price"]/спеки в model_specs.py/у спеки не
            заполнен new_price_max_krw (не гадаем) ИЛИ проверка пройдена.
        (False, reason) — цена подтверждённо выше цены нового с учётом допуска.
    """
    price = car.get("price")
    if not price:
        return True, ""
    title = _build_title(car)
    spec = find_spec(title, car.get("year"))
    if spec is None or spec.new_price_max_krw is None:
        return True, ""

    max_price = spec.new_price_max_krw * config.MAX_MARKUP_OVER_ORIGIN
    if price > max_price:
        markup_pct = int((price / spec.new_price_max_krw - 1) * 100)
        return False, (f"цена {price:,.0f} ₩ выше цены нового ({spec.label}, "
                        f"{spec.new_price_max_krw:,.0f} ₩) на {markup_pct}% "
                        f"(допустимо до {int((config.MAX_MARKUP_OVER_ORIGIN - 1) * 100)}%)")
    return True, ""


def check_mileage(car: dict) -> tuple[bool, str]:
    """
    Пробег > config.MAX_MILEAGE_KM — REJECT. mileage_km отсутствует (None,
    источник его не отдаёт) — не блокируем, не гадаем (тот же принцип, что
    у check_price_sanity()). Раньше этот предел применялся только внутри
    main_pipeline.select_candidates() — то есть НЕ для потока б/у авто
    main.py (USED_CAR_SEARCHES) и НЕ для test_post.py; здесь — единая точка,
    работает для всех путей одинаково (см. аудит 2026-07-17).
    """
    mileage = car.get("mileage_km")
    if mileage is not None and mileage > config.MAX_MILEAGE_KM:
        reason = f"пробег {mileage:,} км выше допустимого {config.MAX_MILEAGE_KM:,} км".replace(",", " ")
        return False, reason
    return True, ""


def check_price_absolute(total_rub) -> tuple[bool, str]:
    """
    Итоговая цена под ключ вне [config.PRICE_MIN_RUB, config.PRICE_MAX_RUB] —
    REJECT. total_rub отсутствует (fields не передан вызывающим кодом) — не
    блокируем, см. докстринг validate_post_before_publish() про то, когда
    это происходит.
    """
    if total_rub is None:
        return True, ""
    if total_rub < config.PRICE_MIN_RUB or total_rub > config.PRICE_MAX_RUB:
        reason = (f"цена под ключ {total_rub:,} ₽ вне допустимого диапазона "
                   f"{config.PRICE_MIN_RUB:,}-{config.PRICE_MAX_RUB:,} ₽").replace(",", " ")
        return False, reason
    return True, ""


def check_price_relative(car: dict, total_rub) -> tuple[bool, str]:
    """
    Итоговая цена вне ModelSpec.price_range_rub ДЛЯ ЭТОЙ КОНКРЕТНОЙ модели —
    REJECT. Уже отклонённых по check_price_absolute() сюда не докатывается
    (см. порядок вызовов ниже), но диапазон здесь узкий и специфичный —
    ловит то, что широкий абсолютный диапазон пропустит (инцидент 2026-07-17:
    Chevrolet Trax ~5 млн ₽ был бы формально внутри 800 000-6 000 000, но
    абсурден именно для этой бюджетной модели).

    Нет total_rub, нет спеки в model_specs.py ИЛИ у найденной спеки не
    заполнен price_range_rub — не блокируем, не гадаем.
    """
    if total_rub is None:
        return True, ""
    title = _build_title(car)
    spec = find_spec(title, car.get("year"))
    if spec is None or spec.price_range_rub is None:
        return True, ""
    lo, hi = spec.price_range_rub
    if not (lo <= total_rub <= hi):
        reason = (f"цена под ключ {total_rub:,} ₽ вне ожидаемого диапазона для "
                   f"{spec.label}: {lo:,}-{hi:,} ₽").replace(",", " ")
        return False, reason
    return True, ""


def validate_post_before_publish(car: dict, fields: dict | None = None) -> tuple["ValidatedCar | None", str]:
    """
    Финальная проверка ПЕРЕД вызовом publish_post() — см. докстринг файла.

    Args:
        car: словарь объявления (та же схема, что уходит в build_lead_post/
             build_price_alert_post) — должен содержать 'photos', 'brand'/
             'model'/'trim', 'year'.
        fields: словарь из post_text_builder.prepare_post_fields() (нужен
             ключ "total_rub_raw" — целое число, см. его докстринг). Не
             передан (None) — проверки цены (check_price_absolute/
             check_price_relative) пропускаются молча, не гадаем на пустом
             месте; фото/название/год/пробег/наценка над заводской
             проверяются в любом случае — это единственные проверки,
             которым fields не нужен.

    Returns:
        (ValidatedCar, "") — можно публиковать. ValidatedCar — dict-обёртка
        над car (см. её докстринг) — video_generator.generate_videos()
        требует именно её, не голый car.
        (None, reason) — нельзя; REJECT уже записан в rejected_posts.log,
        вызывающий код должен пропустить эту машину и НЕ звать publish_post().
    """
    photos = car.get("photos") or []
    if len(photos) < config.MIN_PHOTOS_PER_POST:
        reason = f"фото {len(photos)} < минимума {config.MIN_PHOTOS_PER_POST}"
        _log_rejection(car, reason)
        return None, reason

    title = _build_title(car)
    if not title:
        reason = "пустое название модели"
        _log_rejection(car, reason)
        return None, reason
    if HANGUL_RE.search(title):
        reason = f"корейские символы в названии: {title!r}"
        _log_rejection(car, reason)
        return None, reason
    if not ALLOWED_TITLE_RE.match(title):
        reason = f"недопустимые символы в названии: {title!r}"
        _log_rejection(car, reason)
        return None, reason

    year = car.get("year")
    if year is None:
        reason = "год выпуска не определён"
        _log_rejection(car, reason)
        return None, reason
    if not year_in_range(year):
        reason = f"год {year} вне диапазона {config.MIN_YEAR}-{config.MAX_YEAR}"
        _log_rejection(car, reason)
        return None, reason

    valid_mileage, mileage_reason = check_mileage(car)
    if not valid_mileage:
        _log_rejection(car, mileage_reason)
        return None, mileage_reason

    valid_price, price_reason = check_price_sanity(car)
    if not valid_price:
        _log_rejection(car, price_reason)
        return None, price_reason

    valid_new, new_price_reason = check_price_vs_new_krw(car)
    if not valid_new:
        _log_rejection(car, new_price_reason)
        return None, new_price_reason

    total_rub_raw = fields.get("total_rub_raw") if fields else None

    valid_abs, abs_reason = check_price_absolute(total_rub_raw)
    if not valid_abs:
        _log_rejection(car, abs_reason)
        return None, abs_reason

    valid_rel, rel_reason = check_price_relative(car, total_rub_raw)
    if not valid_rel:
        _log_rejection(car, rel_reason)
        return None, rel_reason

    return ValidatedCar(car, _VALIDATION_TOKEN), ""
