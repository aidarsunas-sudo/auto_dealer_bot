# -*- coding: utf-8 -*-
"""
used_car_scraper.py — парсер объявлений автомобилей С ПРОБЕГОМ с сайта
kbchachacha.com (в отличие от scraper.py, который смотрит каталог НОВЫХ
машин у дилеров).

Ключевое отличие в подходе:
- Страница поиска kbchachacha (то, что видно в адресной строке) сама по
  себе пустая — список машин подгружается отдельным запросом на
  /public/search/list.empty с теми же параметрами поиска.
- Объём двигателя и мощность на этой странице НЕ показаны (только марка,
  модель, год, пробег, регион, цена) — поэтому для расчёта таможни нужен
  отдельный справочник моделей (см. model_specs.py), который сопоставляет
  название модели с её реальными характеристиками.
"""

import re
import logging

import requests
from bs4 import BeautifulSoup

import config
from model_specs import find_spec

logger = logging.getLogger("used_car_scraper")

BASE_URL = "https://www.kbchachacha.com"
LIST_ENDPOINT = f"{BASE_URL}/public/search/list.empty"

_session = requests.Session()
_session.headers.update({
    **config.REQUEST_HEADERS,
    "X-Requested-With": "XMLHttpRequest",
})


def _build_referer(search_params: dict) -> str:
    """Собирает Referer-ссылку на страницу поиска — некоторые сайты
    проверяют этот заголовок и не отвечают без него."""
    query = "&".join(f"{k}={v}" for k, v in search_params.items())
    return f"{BASE_URL}/public/search/main.kbc#!?{query}"


def fetch_used_car_list(search_params: dict, page: int = 1, max_retries: int = 3) -> str:
    """
    Скачивает HTML-фрагмент со списком объявлений для одной страницы поиска.

    Args:
        search_params: параметры поиска kbchachacha (makerCode, classCode,
                        carCode, modelCode, regiDay, countryOrder — те же,
                        что видны в адресной строке на сайте).
        page: номер страницы поиска.
        max_retries: сколько раз пробовать при сетевых сбоях.

    Returns:
        HTML-фрагмент со списком карточек объявлений.

    Raises:
        requests.RequestException: если все попытки провалились.
    """
    import time

    params = dict(search_params)
    params["page"] = page

    headers = {"Referer": _build_referer(params)}
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Загружаю список б/у авто, страница {page} (попытка {attempt}/{max_retries})")
            response = _session.get(LIST_ENDPOINT, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            return response.text
        except requests.RequestException as error:
            last_error = error
            logger.warning(f"Попытка {attempt} не удалась: {error}")
            if attempt < max_retries:
                time.sleep(2 * attempt)

    raise last_error


def parse_used_car_list(html: str) -> list[dict]:
    """
    Разбирает HTML-фрагмент списка объявлений и достаёт данные по каждой
    машине, приводя их к ТОЙ ЖЕ схеме полей, что использует scraper.py
    для новых авто (чтобы database.py и post_builder.py работали одинаково
    что с новыми, что с б/у машинами).

    Args:
        html: HTML-фрагмент от fetch_used_car_list().

    Returns:
        Список словарей: brand, model (человекочитаемое, см. ниже), trim
        (пусто), year (число или None), mileage_km, cc, hp, fuel_code,
        price, specs, url, external_id, raw_title (сырой заголовок с
        сайта — для вызывающего кода, которому нужна детализация, не для
        показа в посте как есть).
    """
    soup = BeautifulSoup(html, "html.parser")
    cars = []

    for area in soup.select("div.area[data-car-seq]"):
        car_seq = area.get("data-car-seq")
        if not car_seq:
            continue

        title_tag = area.select_one("strong.tit")
        title = title_tag.get_text(strip=True) if title_tag else ""

        data_line = area.select_one("div.data-line")
        year_month_raw, mileage_raw, region = None, None, None
        if data_line:
            spans = [s.get_text(strip=True) for s in data_line.select("span")]
            if len(spans) >= 3:
                year_month_raw, mileage_raw, region = spans[0], spans[1], spans[2]

        price_tag = area.select_one("span.price")
        price_raw = price_tag.get_text(strip=True) if price_tag else None

        # Год и месяц выпуска: "22/11식(22년형)" -> 2022, 11 (месяц нужен
        # для точного расчёта таможенного возраста — см. age_calculator.py:
        # наивное "текущий год минус год выпуска" может ошибиться на
        # границе 3/5/7 лет, если не знать месяц).
        manufacture_year = None
        manufacture_month = None
        if year_month_raw:
            m = re.match(r"(\d{2})/(\d{2})", year_month_raw)
            if m:
                manufacture_year = 2000 + int(m.group(1))
                manufacture_month = int(m.group(2))

        # Пробег: "34 234km" -> 34234 (просто оставляем только цифры)
        mileage_km = None
        if mileage_raw:
            digits = re.sub(r"[^\d]", "", mileage_raw)
            if digits:
                mileage_km = int(digits)

        # Цена: "4 050만원" -> 4050 -> 40 500 000 вон
        # (만원 значит "десять тысяч вон", поэтому умножаем на 10000)
        price_krw = None
        if price_raw:
            digits = re.sub(r"[^\d]", "", price_raw)
            if digits:
                price_krw = int(digits) * 10000

        # Объём/мощность/топливо на этой странице не показаны — ищем их
        # в справочнике по названию модели (см. model_specs.py). Если
        # модели там нет — оставляем None (post_builder.py сам разберётся,
        # что делать при нехватке данных).
        spec = find_spec(title, manufacture_year)

        cars.append({
            "brand": "",  # для б/у объявлений бренд обычно уже в model
            # ВАЖНО (инцидент 2026-07, см. FINAL_STATE.md): model — ВСЕГДА
            # человекочитаемое имя (spec.label), если find_spec() распознал
            # модель, а не сырой корейский title. Раньше model=title
            # (сырой текст) отдавался как есть, и только ОДИН из трёх
            # вызывающих модулей (kbchachacha_target_adapter.browse_broad)
            # исправлял это ПОСЛЕ вызова parse_used_car_list() — main.py,
            # вызывающий parse_all_pages() напрямую, эту правку не получал
            # никогда. Правильно чинить у источника, один раз, а не в
            # каждом вызывающем коде по отдельности.
            "model": spec.label if spec else title,
            # trim — раньше здесь был car_seq (ЧИСЛОВОЙ ID объявления) —
            # он попадал в заголовок поста как есть (см. lead_post_builder.
            # _build_title): "Mercedes GLB(X247) 200 d 28528478", где
            # "28528478" — это ID, а не часть названия. car_seq и так
            # доступен отдельно в external_id — здесь пусто, как и в
            # остальных источниках без отдельного описания комплектации.
            "trim": "",
            "external_id": car_seq,
            "year": manufacture_year,
            "month": manufacture_month,
            "mileage_km": mileage_km,
            "cc": spec.cc if spec else None,
            "hp": spec.hp if spec else None,
            "fuel_code": spec.fuel if spec else None,
            "price": price_krw,
            "specs": f"{spec.cc} см³, {spec.hp} л.с." if spec else "",
            "url": f"{BASE_URL}/public/car/detail.kbc?carSeq={car_seq}",
            # Сырой заголовок с сайта (марка+модель+комплектация целиком,
            # обычно по-корейски) — НЕ для показа в посте, только для
            # вызывающего кода, которому нужна детализация комплектации
            # (см. kbchachacha_target_adapter.search_target_model(): там
            # canonical brand/model берётся из target_models.json, а сюда
            # кладётся то, что раньше было в model ДО этого фикса).
            "raw_title": title,
        })

    return cars


def parse_all_pages(search_params: dict, max_pages: int = 10, delay: float = 1.0) -> list[dict]:
    """
    Проходит по нескольким страницам поиска, пока не кончатся объявления.

    Args:
        search_params: параметры поиска (см. fetch_used_car_list()).
        max_pages: защитный потолок числа страниц.
        delay: пауза между запросами страниц, секунды.

    Returns:
        Список всех найденных уникальных объявлений (по carSeq).
    """
    import time

    all_cars = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        try:
            html = fetch_used_car_list(search_params, page)
        except requests.RequestException as error:
            logger.error(f"Страница {page}: не удалось загрузить ({error})")
            break

        cars = parse_used_car_list(html)
        if not cars:
            logger.info(f"Страница {page}: пусто, останавливаюсь")
            break

        new_cars = [c for c in cars if c["external_id"] not in seen_ids]
        if not new_cars:
            logger.info(f"Страница {page}: только дубликаты, останавливаюсь")
            break

        for c in new_cars:
            seen_ids.add(c["external_id"])
        all_cars.extend(new_cars)

        logger.info(f"Страница {page}: найдено {len(cars)} объявлений (новых {len(new_cars)})")
        time.sleep(delay)

    return all_cars
