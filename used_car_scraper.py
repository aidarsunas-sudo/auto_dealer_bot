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
- Фото — два источника:
  1) Страница СПИСКА (карусель превью, 1-4 штуки, ?width=720) — основной
     источник, добавлен 2026-07-20.
  2) Detail-страница (car/detail.kbc) — fallback через enrich_photos_from_detail(),
     добавлен 2026-09-21. На GHA IP список отдаёт только 1 фото (анти-бот
     деградация по IP дата-центра), а detail-страница работает через plain
     requests (Session + прогрев куки + паузы) и содержит 15-20 фото в JSON-LD
     (schema.org/Product). Обогащение вызывается из main_pipeline.py ДО
     пре-фильтра MIN_PHOTOS_PER_POST.
"""

import json
import re
import logging

import requests
from bs4 import BeautifulSoup

import config
from model_specs import find_spec

logger = logging.getLogger("used_car_scraper")

BASE_URL = "https://www.kbchachacha.com"
LIST_ENDPOINT = f"{BASE_URL}/public/search/list.empty"
DETAIL_URL_TEMPLATE = f"{BASE_URL}/public/car/detail.kbc?carSeq={{seq}}"

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
        price, specs, photos (1-4 URL превью со страницы списка — см.
        ниже), url, external_id, raw_title (сырой заголовок с сайта — для
        вызывающего кода, которому нужна детализация, не для показа в
        посте как есть).
    """
    soup = BeautifulSoup(html, "html.parser")
    cars = []

    for area in soup.select("div.area[data-car-seq]"):
        car_seq = area.get("data-car-seq")
        if not car_seq:
            continue

        title_tag = area.select_one("strong.tit")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Фото — ПРЯМО на странице списка (карусель превью, div.thumnail —
        # опечатка в самом сайте, не наша), 1-4 полноразмерных фото на
        # объявление (?width=720), никакого дополнительного запроса не
        # нужно. НЕ пытаться доставать больше/оригинальные фото с
        # детальной страницы (car/detail.kbc) — та защищена reCAPTCHA
        # ("로봇여부 확인"), даже через Playwright не проходит (см.
        # kb_detail_scraper.py, проверено вживую 2026-07-20) — обходить
        # антибот-защиту специально не пытаемся, этого и не требуется:
        # список даёт достаточно фото сам по себе. Дедуп на всякий случай
        # (реальных дублей на сайте не встречалось, но не полагаемся).
        #
        # 2026-09-15: на GitHub Actions IP массово приходит только 1 фото
        # вместо 4 (локально — стабильно 4/4), хотя фикс уже в main —
        # похоже на анти-бот деградацию контента по IP (тот же класс, что
        # уже был у encar). Добавлены: (1) фолбэк на lazy-load атрибуты
        # (data-src и т.п.) — вдруг сайт на некоторых IP отдаёт превью
        # именно так, а не в src; (2) диагностика в лог при недоборе —
        # именно то, чего не хватало, чтобы понять, ЧТО приходит вместо
        # фото 2..N на GHA, не гадая локально (см. FINAL_STATE.md).
        thumnail_div = area.select_one("div.thumnail")
        img_tags = thumnail_div.select("img") if thumnail_div else []

        photo_urls = []
        for img in img_tags:
            src = (img.get("src") or img.get("data-src") or img.get("data-original")
                   or img.get("data-lazy-src") or img.get("data-lazy"))
            if src and src.startswith("http") and src not in photo_urls:
                photo_urls.append(src)

        if len(photo_urls) < config.MIN_PHOTOS_PER_POST:
            attrs_dump = [
                {k: v for k, v in img.attrs.items()
                 if k in ("src", "data-src", "data-original", "data-lazy-src", "data-lazy", "class")}
                for img in img_tags
            ]
            logger.warning(
                f"used_car_scraper: carSeq={car_seq} — только {len(photo_urls)} фото "
                f"(<img> тегов найдено: {len(img_tags)}, div.thumnail найден: {thumnail_div is not None}), "
                f"атрибуты картинок: {attrs_dump}"
            )

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
            "photos": photo_urls,
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


# ═══════════════════════════════════════════════════════════════════
# Обогащение фото с detail-страницы kbchachacha
# ═══════════════════════════════════════════════════════════════════
# На GHA IP kbchachacha отдаёт только 1 фото в карусели списка вместо 4
# (анти-бот деградация контента по IP дата-центра). Detail-страница при
# этом отдаётся нормально через plain requests (без Playwright/reCAPTCHA),
# если прогреть Session GET-запросом на главную и выдерживать паузы
# (проверено в enrich_kb.py 2026-09). JSON-LD (schema.org/Product)
# содержит 15-20 фото — берём оттуда.

_detail_session: requests.Session | None = None


def _get_detail_session() -> requests.Session:
    global _detail_session
    if _detail_session is not None:
        return _detail_session
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": f"{BASE_URL}/public/search/main.kbc",
        "Connection": "keep-alive",
    })
    try:
        s.get(f"{BASE_URL}/public/search/main.kbc", timeout=20)
    except Exception:
        pass
    _detail_session = s
    return s


def _fetch_detail_photos(car_seq: str, session: requests.Session) -> list[str] | None:
    """Достаёт список фото из JSON-LD на detail-странице kbchachacha."""
    import time
    url = DETAIL_URL_TEMPLATE.format(seq=car_seq)
    for attempt in range(3):
        try:
            r = session.get(url, timeout=25)
            if r.status_code != 200 or len(r.text) < 50000:
                time.sleep(2.0 * (attempt + 1))
                continue
            m = re.search(
                r'<script\s+type="application/ld\+json">(.*?)</script>',
                r.text, re.S,
            )
            if not m:
                return None
            ld = json.loads(m.group(1))
            images = [u for u in (ld.get("image") or [])
                      if isinstance(u, str) and u.startswith("http")]
            return images if images else None
        except Exception as e:
            logger.debug(f"detail fetch {car_seq} attempt {attempt+1}: {e}")
            time.sleep(2.0 * (attempt + 1))
    return None


def enrich_photos_from_detail(
    cars: list[dict],
    min_photos: int | None = None,
    delay: float = 1.5,
) -> int:
    """Дозагружает фото с detail-страницы для лотов с недостатком фото.

    Мутирует car["photos"] in-place. Возвращает число обогащённых лотов.
    """
    import time
    if min_photos is None:
        min_photos = config.MIN_PHOTOS_PER_POST
    need_enrich = [c for c in cars if len(c.get("photos") or []) < min_photos]
    if not need_enrich:
        return 0

    logger.info(
        f"enrich_photos: {len(need_enrich)} лотов с <{min_photos} фото, "
        f"дозагружаю с detail-страниц kbchachacha"
    )
    session = _get_detail_session()
    enriched = 0
    for i, car in enumerate(need_enrich):
        seq = car.get("external_id")
        if not seq:
            continue
        photos = _fetch_detail_photos(str(seq), session)
        if photos and len(photos) >= min_photos:
            car["photos"] = photos
            enriched += 1
            logger.info(f"enrich_photos [{i+1}/{len(need_enrich)}]: "
                        f"carSeq={seq} — {len(photos)} фото из detail-страницы")
        else:
            logger.warning(f"enrich_photos [{i+1}/{len(need_enrich)}]: "
                           f"carSeq={seq} — detail не помогла "
                           f"({len(photos) if photos else 0} фото)")
        if i < len(need_enrich) - 1:
            time.sleep(delay)

    logger.info(f"enrich_photos: обогащено {enriched}/{len(need_enrich)} лотов")
    return enriched
