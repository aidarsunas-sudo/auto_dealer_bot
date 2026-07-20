# -*- coding: utf-8 -*-
"""
kbchachacha_target_adapter.py — тонкая обёртка над уже существующим
used_car_scraper.py для поиска по target_models.json на kbchachacha.

Используется в orchestrator.py (автоматика по расписанию) — сайт публичный,
логин не нужен вообще.

Марка/модель на kbchachacha — числовые коды (makerCode/classCode/carCode),
раскрывающиеся ТОЛЬКО через клики по каскадным фильтрам сайта (нет чистого
JSON-API для этого, в отличие от Charancha) — резолвинг требует Playwright.
Само получение списка объявлений после резолвинга — уже существующий,
чистый requests-based used_car_scraper.parse_all_pages(), Playwright для
этого не нужен.

Резолвинг кэшируется через target_model_resolver — Playwright запускается
только для НОВОЙ пары бренд+модель, не при каждом прогоне.

ВАЖНО (найдено вживую): параметр countryOrder в запросе списка ЛОМАЕТ
результат (возвращает 0 объявлений, хотя коды верны) — используем только
makerCode/classCode/carCode/regiDay, без countryOrder.
"""

import logging
import re
import time

import requests
from playwright.sync_api import sync_playwright

from brand_names_ko import to_korean_candidates, model_to_korean
from engine_filter import engine_fuel_hint
from target_model_resolver import resolve
from used_car_scraper import parse_all_pages

logger = logging.getLogger("kbchachacha_target_adapter")

SEARCH_PAGE_URL = "https://www.kbchachacha.com/public/search/main.kbc"
MAKER_LIST_URL = "https://www.kbchachacha.com/public/search/carMaker.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _lookup_maker_code(brand_candidates: list) -> int | None:
    """makerCode — через простой JSON-эндпоинт (без Playwright), см. разведку
    прошлой сессии: /public/search/carMaker.json отдаёт марки списками
    '국산' (домашние) и '수입' (импорт), каждая — {makerName, makerCode, ...}.

    brand_candidates — СПИСОК вариантов корейского названия (см.
    brand_names_ko.to_korean_candidates()), пробуются по очереди: сайт
    называет некоторые бренды иначе, чем encar (найдено 2026-07-20 —
    "르노코리아" вместо "르노코리아(삼성)", "한국GM" вместо "쉐보레(GM대우)")."""
    headers = {
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": SEARCH_PAGE_URL,
    }
    resp = requests.get(MAKER_LIST_URL, params={"page": 1}, headers=headers, timeout=20)
    resp.raise_for_status()
    result = resp.json().get("result", {})

    for group_key in ("국산", "수입"):
        for maker in result.get(group_key, []):
            if maker.get("makerName") in brand_candidates:
                return int(maker["makerCode"])
    return None


def _parse_generation_years(label: str) -> tuple:
    """
    Парсит диапазон лет поколения из текста вида "더 뉴 K3(BD) (21년~현재)"
    или "올 뉴 K3 (18년~21년)" -> (2021, 9999) или (2018, 2021).
    Возвращает (None, None), если не удалось распознать.
    """
    m = re.search(r"\((\d{2})년\s*~\s*(현재|\d{2})년?\)", label)
    if not m:
        return None, None
    start = 2000 + int(m.group(1))
    end = 9999 if m.group(2) == "현재" else 2000 + int(m.group(2))
    return start, end


def _lookup_codes(brand: str, model: str) -> dict | None:
    """
    Резолвит makerCode/classCode/список подходящих carCode через Playwright.
    Сайт нередко "спотыкается" на первом клике (перекрывающий баннер/оверлей
    ещё не исчез) — отрицательный результат в этом случае кэшируется на 7
    дней (see target_model_resolver), поэтому даём себе пару повторных
    попыток на чисто технические сбои, прежде чем сдаться.
    """
    for attempt in range(1, 3):
        result = _lookup_codes_once(brand, model)
        if result is not None:
            return result
        logger.warning(f"kbchachacha resolver: попытка {attempt}/2 не удалась для {brand} {model}")
    return None


def _lookup_codes_once(brand: str, model: str) -> dict | None:
    """
    Резолвит makerCode/classCode/список подходящих carCode через Playwright
    (см. докстринг файла — вызывается только при промахе кэша).

    Возвращает {"makerCode": int, "classCode": int, "car_codes": [{"code": int,
    "year_from": int, "year_to": int}, ...]} — по одному элементу на каждое
    найденное поколение модели (может быть несколько, если название модели
    неоднозначно matches несколько generations).
    """
    brand_candidates = to_korean_candidates(brand)
    model_candidates = [model.strip().lower(), model_to_korean(model).lower()]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        try:
            page.goto(SEARCH_PAGE_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as error:
            logger.warning(f"kbchachacha resolver: goto warning: {error}")
        time.sleep(2)

        try:
            page.click("text=차종", timeout=8000)
            time.sleep(1.5)
        except Exception as error:
            logger.warning(f"kbchachacha resolver: не удалось открыть меню 차종: {error}")
            browser.close()
            return None

        # Пробуем варианты названия бренда по очереди (не одну "правильную"
        # строку) — сайты называют некоторые бренды по-разному между собой
        # (найдено 2026-07-20: kbchachacha "르노코리아"/"한국GM" вместо
        # encar-формы "르노코리아(삼성)"/"쉐보레(GM대우)" — см.
        # brand_names_ko.BRAND_KO_ALT). Раньше здесь была ОДНА строка —
        # промах по ней МОЛЧА проваливал резолв для всей модели.
        brand_clicked = None
        last_error = None
        for brand_ko in brand_candidates:
            try:
                page.click(f"text={brand_ko}", timeout=8000)
                brand_clicked = brand_ko
                break
            except Exception as error:
                last_error = error
        if brand_clicked is None:
            logger.warning(f"kbchachacha resolver: не удалось открыть марку ни по одному из "
                            f"вариантов {brand_candidates}: {last_error}")
            browser.close()
            return None
        time.sleep(1.5)

        # Ищем ссылку на classCode, чей видимый текст совпадает с моделью
        # (латиницей или корейской транслитерацией).
        class_links = page.eval_on_selector_all(
            "a[href*='setClassCode']",
            "els => els.map(e => ({text: e.innerText.trim(), href: e.getAttribute('href')}))",
        )
        class_match = next(
            (c for c in class_links if any(cand in c["text"].lower() for cand in model_candidates)),
            None,
        )
        if class_match is None:
            browser.close()
            return None

        class_code_m = re.search(r"setClassCode\('(\d+)'\)", class_match["href"])
        if not class_code_m:
            browser.close()
            return None
        class_code = int(class_code_m.group(1))

        try:
            page.click(f"a[href*=\"setClassCode('{class_code}')\"]", timeout=8000)
            time.sleep(2)
        except Exception as error:
            logger.warning(f"kbchachacha resolver: не удалось выбрать классCode {class_code}: {error}")
            browser.close()
            return None

        car_items = page.eval_on_selector_all(
            "input[id^='carCode-']",
            """els => els.map(e => {
                const label = document.querySelector(`label[for="${e.id}"]`);
                return {value: e.value, text: label ? label.innerText.trim() : ''};
            })""",
        )
        browser.close()

    maker_code = _lookup_maker_code(brand_candidates)
    if maker_code is None:
        logger.warning(f"kbchachacha resolver: не удалось определить makerCode ни по одному "
                        f"из вариантов {brand_candidates}")
        return None

    car_codes = []
    for item in car_items:
        year_from, year_to = _parse_generation_years(item["text"])
        car_codes.append({
            "code": int(item["value"]),
            "text": item["text"],
            "year_from": year_from,
            "year_to": year_to,
        })

    if not car_codes:
        return None

    return {"makerCode": maker_code, "classCode": class_code, "car_codes": car_codes}


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на kbchachacha по целевой модели. Возвращает список,
    совместимый с database.save_car (+ доп. поля для отчётов).
    """
    codes = resolve("kbchachacha", target["brand"], target["model"], _lookup_codes, log)
    if codes is None:
        # РАНЬШЕ здесь не было ни строки в лог — резолвер мог проваливаться
        # для ВСЕХ моделей подряд (см. 2026-07-17: 17 из 26 записей кэша
        # ушли в отрицательный результат разом из-за устаревшего названия
        # бренда в BRAND_KO), а source_collector.collect_target_model_cars()
        # просто получал [] и молча шёл дальше — источник выглядел
        # "работающим", но за прогон не находил ничего ни по одной модели.
        # Явный "найдено 0" makes это видимым в логе так же, как обычный
        # нулевой результат — не гадаем, была ли это реальная пустая выдача
        # или сбой резолвинга (для этого разбора см. rate-changelog/аудит).
        log.info(f"kbchachacha: {target['brand']} {target['model']} -> найдено 0 "
                 f"(резолвинг марки/модели не удался, см. WARNING выше)")
        return []

    year_from = target.get("year_from", 0)
    year_to = target.get("year_to", 9999)

    # Берём только те поколения (carCode), чей диапазон лет пересекается с
    # запрошенным — модель может быть неоднозначна (несколько generations).
    matching_car_codes = [
        c["code"] for c in codes["car_codes"]
        if c["year_from"] is None or (c["year_from"] <= year_to and c["year_to"] >= year_from)
    ]
    if not matching_car_codes:
        matching_car_codes = [c["code"] for c in codes["car_codes"]]  # честно берём все, если не распознали года

    fuel_hint = engine_fuel_hint(target.get("engine"))

    all_cars = []
    for car_code in matching_car_codes:
        search_params = {
            "makerCode": codes["makerCode"],
            "classCode": codes["classCode"],
            "carCode": car_code,
        }
        try:
            cars = parse_all_pages(search_params, max_pages=15, delay=0.5)
        except Exception as error:
            log.error(f"kbchachacha: ошибка поиска для {target['brand']} {target['model']} "
                      f"(carCode={car_code}): {error}")
            continue

        for car in cars:
            if car.get("year") is not None and not (year_from <= car["year"] <= year_to):
                continue
            if target.get("mileage_max") is not None and car.get("mileage_km") is not None \
                    and car["mileage_km"] > target["mileage_max"]:
                continue
            if fuel_hint is not None and car.get("fuel_code") != fuel_hint:
                # На kbchachacha fuel_code берётся ТОЛЬКО из model_specs.py
                # (сырого поля топлива в списке объявлений нет) — если
                # запрошен конкретный мотор (engine в target_models.json), а
                # спека не нашлась (fuel_code is None, напр. для смежного
                # варианта кузова вроде Tiguan Allspace на TSI под тем же
                # classCode), это тоже "не совпадает", а не "неизвестно,
                # пропускаем" — иначе в отчёт/базу попадают чужие моторы.
                continue

            # used_car_scraper.parse_used_car_list() кладёт человекочитаемое
            # имя в car["model"] и сырой заголовок с сайта (марка+модель+
            # комплектация целиком, ПО-КОРЕЙСКИ) в car["raw_title"].
            # РАНЬШЕ raw_title шёл в trim как есть — тот же класс бага, что
            # был найден и исправлен у charancha_scraper.py 2026-07-20:
            # raw_title почти всегда содержит корейский текст, что валит
            # validate_post_before_publish()/prepare_post_fields() на
            # проверке "корейские символы в названии" — 100% отказ для
            # ЛЮБОЙ модели, найденной этим путём (найдено вживую через
            # plan_next_posts.py на Chevrolet Trax: "한국GM 트랙스
            # 크로스오버..."). brand/model уже даны каноническим текстом из
            # target_models.json — trim без чистого английского бэджа
            # оставляем пустым, не корейским.
            car["trim"] = ""
            car["brand"] = target["brand"]
            car["model"] = target["model"]
            car["source"] = "kbchachacha"
            all_cars.append(car)

    # Один и тот же физический автомобиль может попасться дважды, если два
    # соседних поколения (carCode) пересекаются по годам и сайт по ошибке
    # относит объявление к обоим — дедуп по external_id (car_seq) на выходе.
    seen_ids = set()
    deduped = []
    for car in all_cars:
        external_id = car.get("external_id")
        if external_id in seen_ids:
            continue
        seen_ids.add(external_id)
        deduped.append(car)

    log.info(f"kbchachacha: {target['brand']} {target['model']} -> найдено {len(deduped)} (после фильтров)")
    return deduped


def browse_broad(config: dict, log: logging.Logger) -> list:
    """
    Широкий поиск (Режим 2) — без makerCode/classCode/carCode вообще
    (подтверждено вживую: работает, отдаёт объявления вперемешку по всем
    маркам). Резолвер/кэш здесь не нужны — марка не фиксирована.

    used_car_scraper.parse_used_car_list() уже кладёт человекочитаемое имя
    в car["model"] (spec.label, если модель распознана — см. его докстринг)
    — brand оставляем пустым (как и для остальных источников без
    структурированного brand/model в свободном тексте).

    Фильтр по max_hp/min_hp — см. docstring charancha_scraper.browse_broad
    (машины с неопределённым hp отбрасываются, а не пропускаются).
    """
    max_hp = config.get("max_hp")
    min_hp = config.get("min_hp")
    max_pages = config.get("max_pages_per_source", 5)

    try:
        cars = parse_all_pages({}, max_pages=max_pages, delay=0.5)
    except Exception as error:
        log.error(f"kbchachacha (broad): ошибка поиска: {error}")
        return []

    all_cars = []
    for car in cars:
        if car.get("hp") is None:
            continue
        if max_hp is not None and car["hp"] > max_hp:
            continue
        if min_hp is not None and car["hp"] < min_hp:
            continue

        year = car.get("year")
        if config.get("year_from") is not None and year is not None and year < config["year_from"]:
            continue
        mileage_km = car.get("mileage_km")
        if config.get("mileage_max") is not None and mileage_km is not None and mileage_km > config["mileage_max"]:
            continue
        fuel_types = config.get("fuel_types")
        if fuel_types is not None and car.get("fuel_code") not in fuel_types:
            continue

        # car["model"]/car["trim"] уже в нужном виде — used_car_scraper.
        # parse_used_car_list() отдаёт человекочитаемое имя в model и пустой
        # trim напрямую у источника (см. его докстринг) — здесь больше
        # ничего переписывать не нужно (раньше здесь был override,
        # дублирующий эту же логику — убран, чтобы не было двух мест,
        # которые могут разойтись, см. FINAL_STATE.md).
        car["source"] = "kbchachacha"
        all_cars.append(car)

    log.info(f"kbchachacha (broad): найдено {len(all_cars)} машин с hp в [{min_hp or 0}, {max_hp}]")
    return all_cars
