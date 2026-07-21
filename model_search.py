# -*- coding: utf-8 -*-
"""
model_search.py — универсальный ручной поиск по ЛЮБОЙ модели через CLI-флаги
или car_search_request.json, без правки кода под конкретную модель.

Фаза 1 (реализовано): 4 источника без логина — kbchachacha, Encar, Charancha,
OnBid. Переиспользует уже существующие адаптеры (kbchachacha_target_adapter.py,
encar_scraper.py, charancha_scraper.py, onbid_scraper.py), которые
target_collector.py использует для target_models.json — те же
search_target_model(target, log), тот же кэш резолвинга в
target_models_cache.json. Этот скрипт НЕ пишет/не читает target_models.json —
запрос одноразовый, а не постоянный watchlist для orchestrator.py.

Фаза 2 (в процессе): 5 логин-площадок (AJ/Glovis/HeyDealer/KCar/Lotte) — общий
интерфейс search_target_model() добавляется по одной; готово: AJ (aj_scraper.py).
Остальные пока только как черновые *_test_scraper.py. См. план в
C:\\Users\\Xiaomi\\.claude\\plans\\stateless-skipping-stream.md.

Логин-площадки НИКОГДА не логинятся сами по себе молча (см. login_gate.py) —
по умолчанию --sources их не включает; чтобы использовать площадку с
истёкшей/отсутствующей сессией, нужно явно подтвердить именно этот запуск:
    python model_search.py --brand Kia --model K5 --sources aj \\
        --confirm-login aj

Особенность фильтра по топливу (--fuel): Encar и Charancha фильтруют по
своему надёжному сайтовому полю (FuelType/fuelName) — их встроенный фильтр
используется как есть. kbchachacha и OnBid топливо в списке не показывают —
их fuel_code в адаптерах берётся из ОЧЕНЬ узкого model_specs.py и для новых
моделей часто пуст, поэтому для этих двух источников engine НЕ передаётся
адаптеру, а фильтрация делается здесь же по тексту комплектации
(engine_fuel_hint против car["trim"]) — тот же приём, которым для Audi A4
вручную разделили "35 TDI" (2.0) и "40 TDI Quattro" (объём не проверен).
Такие совпадения помечены в JSON как "fuel_match_is_heuristic": true.

Запуск:
    python model_search.py --brand Audi --model A4 --fuel diesel \\
        --year-from 2022 --year-to 2023 --mileage-max 60000 --accident-free \\
        --min-photos 5

    python model_search.py --config car_search_request.json
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path

import requests

import aj_scraper
import charancha_scraper
import encar_scraper
import glovis_scraper
import heydealer_scraper
import kbchachacha_target_adapter
import kcar_scraper
import lotte_scraper
import onbid_scraper
from color_match import matches_color
from encar_test_scraper import DETAIL_URL as ENCAR_DETAIL_URL
from encar_test_scraper import HEADERS as ENCAR_HEADERS
from encar_test_scraper import fetch_diagnosis_and_inspection
from engine_filter import engine_fuel_hint
from generate_model_report import run as generate_report
from kb_detail_scraper import fetch_car_detail
from login_gate import LoginRequiredError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("model_search")

OUTPUT_ROOT = Path(__file__).resolve().parent / "model_search_results"

NOLOGIN_SEARCH_FN = {
    "kbchachacha": kbchachacha_target_adapter.search_target_model,
    "encar": encar_scraper.search_target_model,
    "charancha": charancha_scraper.search_target_model,
    "onbid": onbid_scraper.search_target_model,
}
NOLOGIN_SOURCES = list(NOLOGIN_SEARCH_FN.keys())

# Логин-площадки — НЕ входят в --sources по умолчанию (см. докстринг файла).
LOGIN_SEARCH_FN = {
    "aj": aj_scraper.search_target_model,
    "glovis": glovis_scraper.search_target_model,
    "heydealer": heydealer_scraper.search_target_model,
    "kcar": kcar_scraper.search_target_model,
    "lotte": lotte_scraper.search_target_model,
}

SOURCE_SEARCH_FN = {**NOLOGIN_SEARCH_FN, **LOGIN_SEARCH_FN}

# Источники, чей встроенный фильтр по "engine" опирается на надёжное
# сайтовое поле (не на узкий model_specs.py) — им можно смело передавать
# engine как есть.
RELIABLE_ENGINE_FILTER_SOURCES = {"encar", "charancha", "aj", "glovis", "kcar"}


def slugify(text) -> str:
    return re.sub(r"[^\w]+", "_", str(text).strip().lower()).strip("_")


def build_slug(request: dict) -> str:
    parts = [request["brand"], request["model"]]
    if request.get("engine"):
        parts.append(request["engine"])
    year_from, year_to = request.get("year_from"), request.get("year_to")
    if year_from or year_to:
        parts.append(f"{year_from or ''}-{year_to or ''}")
    if request.get("mileage_max"):
        parts.append(str(request["mileage_max"]))
    if request.get("accident_free"):
        parts.append("accidentfree")
    if request.get("min_photos"):
        parts.append(f"minphotos{request['min_photos']}")
    if request.get("colors"):
        parts.append("_".join(request["colors"]))
    return "_".join(slugify(p) for p in parts if p)


def build_request(args: argparse.Namespace) -> dict:
    config = {}
    if args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))

    request = {
        "brand": args.brand or config.get("brand"),
        "model": args.model or config.get("model"),
        "engine": args.fuel or config.get("engine") or config.get("fuel"),
        "year_from": args.year_from if args.year_from is not None else config.get("year_from"),
        "year_to": args.year_to if args.year_to is not None else config.get("year_to"),
        "mileage_max": args.mileage_max if args.mileage_max is not None else config.get("mileage_max"),
        "accident_free": args.accident_free or bool(config.get("accident_free")),
        "min_photos": args.min_photos if args.min_photos is not None else config.get("min_photos", 0),
        "colors": [c.strip().lower() for c in (args.colors.split(",") if args.colors else config.get("colors") or [])],
        "sources": (args.sources.split(",") if args.sources else config.get("sources")) or NOLOGIN_SOURCES,
        "confirm_login": set((args.confirm_login.split(",") if args.confirm_login else config.get("confirm_login")) or []),
    }

    if not request["brand"] or not request["model"]:
        raise SystemExit("Нужно указать --brand и --model (или задать их в --config)")

    return request


def collect_for_source(source_name: str, request: dict, log: logging.Logger) -> list:
    search_fn = SOURCE_SEARCH_FN[source_name]

    target = {"brand": request["brand"], "model": request["model"]}
    if request.get("year_from") is not None:
        target["year_from"] = request["year_from"]
    if request.get("year_to") is not None:
        target["year_to"] = request["year_to"]
    if request.get("mileage_max") is not None:
        target["mileage_max"] = request["mileage_max"]
    if source_name in RELIABLE_ENGINE_FILTER_SOURCES and request.get("engine"):
        target["engine"] = request["engine"]
    if source_name in LOGIN_SEARCH_FN:
        target["allow_fresh_login"] = source_name in request.get("confirm_login", set())

    try:
        cars = search_fn(target, log)
    except LoginRequiredError as error:
        log.error(f"{error} (источник '{source_name}' пропущен на этот запуск)")
        return []
    except Exception as error:
        log.error(f"{source_name}: ошибка поиска: {error}")
        return []

    if source_name not in RELIABLE_ENGINE_FILTER_SOURCES and request.get("engine"):
        wanted = engine_fuel_hint(request["engine"])
        if wanted is not None:
            before = len(cars)
            cars = [c for c in cars if engine_fuel_hint(c.get("trim", "")) == wanted]
            log.info(f"{source_name}: текстовый фильтр топлива по комплектации — "
                     f"{before} -> {len(cars)} (эвристика, не проверенная спецификация)")
            for car in cars:
                car["fuel_match_is_heuristic"] = True

    for car in cars:
        car["source"] = source_name
    return cars


def enrich_kbchachacha(car: dict, log: logging.Logger) -> dict:
    try:
        detail = fetch_car_detail(car["url"])
    except Exception as error:
        log.error(f"kbchachacha: не удалось получить детали {car['url']}: {error}")
        detail = {}

    # Страница детально отдаёт accident_status не всегда с первого раза
    # (нестабильная загрузка, см. kb_detail_scraper.py) — если статус не
    # пришёл, пробуем ещё раз перед тем, как молча оставить его "неизвестным".
    if "accident_status" not in detail:
        log.warning(f"accident_status не пришёл с первого раза, повтор: {car['url']}")
        try:
            detail = fetch_car_detail(car["url"])
        except Exception as error:
            log.error(f"Повтор не удался {car['url']}: {error}")

    car["photos"] = detail.get("photos", [])
    car["color"] = detail.get("color")
    status = detail.get("accident_status")
    if status is not None:
        car["accident_free"] = "없음" in status

    notes = []
    if detail.get("total_loss_history") == "있음":
        notes.append("тотальное повреждение")
    if detail.get("flood_damage_history") == "있음":
        notes.append("потоп")
    if notes:
        car["damage_notes"] = ", ".join(notes)

    time.sleep(2)
    return car


def enrich_encar(car: dict, log: logging.Logger, need_accident: bool, need_color: bool) -> dict:
    # ВАЖНО (найдено вживую 2026-07-07): item["Photos"] из СПИСКА поиска
    # (encar_scraper.py) всегда обрезан до 4 фото, несмотря на комментарий в
    # том файле, что это "уже полная галерея" — на самом деле полную галерею
    # (20-35+ фото) отдаёт только детальная карточка
    # (/v1/readside/vehicle/{id}, поле "photos"/"path") — тот же запрос, что
    # ниже даёт цвет, поэтому просто всегда его выполняем и берём фото оттуда.
    try:
        resp = requests.get(ENCAR_DETAIL_URL.format(id=car["external_id"]), headers=ENCAR_HEADERS, timeout=20)
        resp.raise_for_status()
        detail = resp.json()
        spec = detail.get("spec") or {}
        if need_color:
            car["color"] = spec.get("colorName")
        full_photos = [f"https://ci.encar.com{p['path']}" for p in (detail.get("photos") or []) if p.get("path")]
        if full_photos:
            car["photos"] = full_photos
    except Exception as error:
        log.error(f"encar: не удалось получить detail (фото/цвет) {car['external_id']}: {error}")
    time.sleep(0.3)

    if not need_accident:
        return car

    try:
        extra = fetch_diagnosis_and_inspection(car["external_id"])
    except Exception as error:
        log.error(f"encar: не удалось получить diagnosis/inspection {car['external_id']}: {error}")
        extra = {}

    inspection = extra.get("inspection") or {}
    master = inspection.get("master") or {}

    # ВАЖНО (найдено вживую 2026-07-07, на реальном примере — Peugeot 2008,
    # carId 42059650): master["accdient"] — это упрощённый самоотчётный флаг
    # осмотрщика, он может быть False ДАЖЕ ПРИ замене капота/крыла/двери и
    # рихтовке другой двери — это видно только в inspection["outers"]
    # (карта состояния кузова по панелям; там перечисляются ТОЛЬКО панели с
    # отклонением от нормы — пустой список = отклонений нет). Доверять
    # одному accdient недостаточно — проверяем оба источника, outers в
    # приоритете (он детальнее и это и есть основание для accdient).
    outers = inspection.get("outers") or []
    if outers:
        car["accident_free"] = False
        panel_names = [o.get("type", {}).get("title", "?") for o in outers]
        car["damage_notes"] = f"повреждены/заменены панели: {', '.join(panel_names)}"
    elif "accdient" in master:
        car["accident_free"] = master["accdient"] is False

    if master.get("waterlog"):
        car["damage_notes"] = "потоп" + (f", {car['damage_notes']}" if car.get("damage_notes") else "")

    time.sleep(0.5)
    return car


def enrich_charancha(car: dict, log: logging.Logger) -> dict:
    is_accident = car.get("is_accident")
    if is_accident is not None:
        car["accident_free"] = is_accident is False
    if car.get("is_seizure"):
        car["damage_notes"] = "арест/залог"
    return car


def enrich_and_filter(cars: list, request: dict, log: logging.Logger) -> list:
    accident_free_required = bool(request.get("accident_free"))
    min_photos = request.get("min_photos") or 0
    colors_wanted = request.get("colors") or []

    result = []
    for car in cars:
        source = car["source"]
        if source == "kbchachacha":
            car = enrich_kbchachacha(car, log)
        elif source == "encar":
            car = enrich_encar(car, log, need_accident=accident_free_required, need_color=bool(colors_wanted))
        elif source == "charancha":
            car = enrich_charancha(car, log)
        # onbid/aj: обогатить цветом нечем — ни один не отдаёт его надёжно.

        if accident_free_required:
            accident_free = car.get("accident_free")
            if accident_free is None:
                log.info(f"Пропуск ({source}, статус ДТП не проверить): {car.get('url')}")
                continue
            if accident_free is False:
                log.info(f"Пропуск ({source}, найдено ДТП): {car.get('url')}")
                continue

        if colors_wanted:
            color_ok = matches_color(car.get("color"), colors_wanted)
            if color_ok is not True:
                reason = "цвет не подошёл" if color_ok is False else "цвет неизвестен"
                log.info(f"Пропуск ({source}, {reason}): {car.get('url')}")
                continue

        photo_count = len(car.get("photos") or [])
        if min_photos > 0 and photo_count < min_photos:
            log.info(f"Пропуск ({source}, фото {photo_count} < {min_photos}): {car.get('url')}")
            continue

        result.append(car)

    return result


def run(request: dict) -> Path:
    slug = build_slug(request)
    out_dir = OUTPUT_ROOT / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    all_cars = []
    for source_name in request["sources"]:
        if source_name not in SOURCE_SEARCH_FN:
            logger.error(f"Источник '{source_name}' пока не поддерживается "
                         f"(без логина: {', '.join(NOLOGIN_SOURCES)}; "
                         f"логин-площадки: {', '.join(LOGIN_SEARCH_FN)})")
            continue
        logger.info(f"=== Источник: {source_name} ===")
        cars = collect_for_source(source_name, request, logger)
        logger.info(f"{source_name}: {len(cars)} кандидатов после базовых фильтров")
        all_cars.extend(cars)

    enriched = enrich_and_filter(all_cars, request, logger)

    for car in enriched:
        filename = f"{car['source']}_{car.get('external_id')}.json"
        (out_dir / filename).write_text(json.dumps(car, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"=== Готово: сохранено {len(enriched)} объявлений в {out_dir} ===")

    report_path = generate_report(out_dir)
    logger.info(f"Отчёт: {report_path}")
    return out_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--brand")
    parser.add_argument("--model")
    parser.add_argument("--fuel", choices=["diesel", "petrol", "hybrid", "electric"])
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument("--mileage-max", type=int)
    parser.add_argument("--accident-free", action="store_true")
    parser.add_argument("--min-photos", type=int)
    parser.add_argument("--colors", help="csv английских названий цветов (white,black,blue,grey,silver,...) — "
                                          "см. color_match.COLOR_KEYWORDS")
    parser.add_argument("--sources",help=f"csv, по умолчанию все 4 без логина: {','.join(NOLOGIN_SOURCES)}. "
                                           f"Логин-площадки: {','.join(LOGIN_SEARCH_FN)}")
    parser.add_argument("--confirm-login", help="csv логин-площадок, для которых можно выполнить живой логин "
                                                  "при истёкшей/отсутствующей сессии (см. докстринг файла)")
    parser.add_argument("--config", help="путь к car_search_request.json (CLI-флаги переопределяют его поля)")
    return parser.parse_args()


if __name__ == "__main__":
    run(build_request(parse_args()))
