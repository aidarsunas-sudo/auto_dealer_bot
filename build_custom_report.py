# -*- coding: utf-8 -*-
"""
build_custom_report.py — разовый ручной отчёт (НЕ публикация, только личный
просмотр) по нескольким моделям сразу. Использует model_search.py (уже
покрывает kbchachacha/Encar/Charancha/OnBid без логина + AJ с логином) для
поиска+обогащения+фильтрации по каждой модели, затем считает цену "под ключ
Казань" через post_builder.calculate_price_details() (та же функция, что и
боевые посты — цифры не расходятся) и собирает всё в один HTML с разделами
по моделям, отсортированными по итоговой цене (дешевле -> дороже).

Ссылка на исходное объявление показывается открыто (в отличие от публичных
постов — см. lead_post_builder.py, где площадка-источник намеренно скрыта) —
это личный отчёт для просмотра, а не материал для клиента.

Правь SECTIONS ниже под конкретный запрос — это одноразовый скрипт для
конкретной задачи, не универсальный CLI (для универсального поиска по одной
модели используйте model_search.py напрямую).

Запуск:
    python build_custom_report.py
"""

import json
import logging
from html import escape
from pathlib import Path

import model_search
from post_builder import calculate_price_details
from rates import fetch_cbr_rates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_custom_report")

REPORT_PATH = Path(__file__).resolve().parent / "model_search_results" / "custom_report.html"

ALL_SOURCES = ["kbchachacha", "encar", "charancha", "onbid", "aj"]

SECTIONS = [
    {
        "title": "Peugeot 2008 (2022-2024, ≤60 000 км, белый/чёрный/синий, без ДТП)",
        "requests": [
            {"brand": "Peugeot", "model": "2008", "year_from": 2022, "year_to": 2024,
             "mileage_max": 60000, "colors": ["white", "black", "blue"],
             "accident_free": True, "sources": ALL_SOURCES},
        ],
    },
    {
        "title": "Audi RS6 / RS7 (2023-2024, ≤10 000 км, без ДТП/ремонтов/страховых выплат)",
        "requests": [
            {"brand": "Audi", "model": "RS6", "year_from": 2023, "year_to": 2024,
             "mileage_max": 10000,
             "accident_free": True, "sources": ALL_SOURCES},
            {"brand": "Audi", "model": "RS7", "year_from": 2023, "year_to": 2024,
             "mileage_max": 10000,
             "accident_free": True, "sources": ALL_SOURCES},
        ],
    },
]


def _full_request(partial: dict) -> dict:
    """Достраивает словарь до полной схемы model_search.run() (см.
    model_search.build_request) — здесь запросы заданы напрямую в SECTIONS,
    без CLI/JSON-файла, поэтому недостающие поля со значениями по умолчанию
    проставляем сами."""
    return {
        "brand": partial["brand"],
        "model": partial["model"],
        "engine": partial.get("engine"),
        "year_from": partial.get("year_from"),
        "year_to": partial.get("year_to"),
        "mileage_max": partial.get("mileage_max"),
        "accident_free": partial.get("accident_free", False),
        "min_photos": partial.get("min_photos", 0),
        "colors": partial.get("colors") or [],
        "sources": partial.get("sources") or model_search.NOLOGIN_SOURCES,
        "confirm_login": set(partial.get("confirm_login") or []),
    }


def collect_section_records(section: dict) -> list:
    records = []
    for partial in section["requests"]:
        request = _full_request(partial)
        slug = model_search.build_slug(request)
        out_dir = model_search.OUTPUT_ROOT / slug

        # Идемпотентность: если поиск по этим же критериям уже прогнан (папка
        # с результатами существует — даже с 0 найденных, там будет хотя бы
        # report.html от model_search.py) — не повторяем дорогой прогон
        # (Playwright по kbchachacha, полный обход каталога AJ) заново.
        already_ran = (out_dir / "report.html").exists()
        if already_ran:
            existing = list(out_dir.glob("*.json"))
            logger.info(f"=== {request['brand']} {request['model']}: уже прогнано ранее "
                        f"({len(existing)} результатов) в {out_dir}, повторно не ищу ===")
        else:
            logger.info(f"=== {request['brand']} {request['model']} ===")
            out_dir = model_search.run(request)

        for path in sorted(out_dir.glob("*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
    return records


def price_details_for(car: dict, rates: dict) -> dict | None:
    return calculate_price_details(car, rates)


_COLOR_DISPLAY_RU = {
    "흰색": "белый", "화이트": "белый", "펄화이트": "жемчужно-белый", "백색": "белый", "진주색": "перламутровый",
    "검정색": "чёрный", "검은색": "чёрный", "블랙": "чёрный",
    "파란색": "синий", "블루": "синий", "남색": "тёмно-синий",
    "회색": "серый", "쥐색": "серый", "그레이": "серый",
    "은색": "серебристый", "실버": "серебристый",
}


def display_color(raw: str | None) -> str:
    if not raw:
        return "не указан"
    return _COLOR_DISPLAY_RU.get(raw, raw)


CARD_TEMPLATE = """
<div class="card">
  <div class="photos">
    {photos_html}
  </div>
  <div class="info">
    <div class="badge {source_class}">{source}</div>
    <h3>{title}</h3>
    <div class="row">📅 {year} &nbsp;|&nbsp; 🛣 {mileage} &nbsp;|&nbsp; 🎨 {color}</div>
    <div class="row price">💰 {price_label}: {price}</div>
    <div class="row damage damage-ok">✅ {damage}</div>
    {breakdown_html}
    <div class="row"><a href="{url}" target="_blank" rel="noopener">Открыть объявление →</a></div>
  </div>
</div>
"""

SOURCE_LABELS = {
    "kbchachacha": ("KB Chachacha", "src-kb"),
    "encar": ("Encar", "src-encar"),
    "charancha": ("Charancha", "src-charancha"),
    "onbid": ("OnBid", "src-onbid"),
    "aj": ("AJ Auction", "src-aj"),
}


def format_money(value) -> str:
    if value is None:
        return "уточняется"
    return f"{int(value):,}".replace(",", " ")


def render_card(car: dict, rates: dict) -> str:
    photos = (car.get("photos") or [])[:5]
    if photos:
        photos_html = "".join(f'<img src="{escape(p)}" loading="lazy" alt="фото">' for p in photos)
    else:
        photos_html = '<div class="no-photo">нет фото</div>'

    source = car.get("source", "?")
    source_label, source_class = SOURCE_LABELS.get(source, (source, "src-other"))
    title = " ".join(str(x) for x in (car.get("brand"), car.get("model"), car.get("trim")) if x)

    details = price_details_for(car, rates)
    if details is None:
        price_label = "Цена под ключ в Казани"
        price_text = "не рассчитана (не хватает данных по объёму/мощности двигателя)"
        breakdown_html = ""
        total_rub = None
    else:
        price_label = "Стартовая цена аукциона под ключ (итоговая может быть выше)" if source == "onbid" \
            else "Стоимость под ключ в Казани"
        price_text = f"{format_money(details['total_rub'])} ₽"
        total_rub = details["total_rub"]
        breakdown_html = (
            f'<div class="row breakdown">'
            f"машина: {format_money(details['car_price_rub'])} ₽ · "
            f"пошлина/ЕТС/утильсбор: {format_money(details['customs_rub'])} ₽ · "
            f"банк+логистика+брокер: {format_money(details['bank_commission_rub'] + details['extra_costs_rub'])} ₽"
            f"</div>"
        )

    damage_bits = []
    if car.get("accident_free") is True:
        damage_bits.append("без ДТП")
    if car.get("damage_notes"):
        damage_bits.append(car["damage_notes"])
    damage = ", ".join(damage_bits) if damage_bits else "без ДТП"

    html = CARD_TEMPLATE.format(
        photos_html=photos_html,
        source_class=source_class,
        source=escape(source_label),
        title=escape(title) or "—",
        year=escape(str(car.get("year") or "—")),
        mileage=f"{car['mileage_km']:,} км".replace(",", " ") if car.get("mileage_km") is not None else "—",
        color=escape(display_color(car.get("color"))),
        price_label=price_label,
        price=price_text,
        damage=escape(damage),
        breakdown_html=breakdown_html,
        url=escape(car.get("url", "")),
    )
    return html, total_rub


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Персональный отчёт по подбору</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #f4f5f7; margin: 0; padding: 24px; }}
  h1 {{ margin-bottom: 4px; }}
  .generated {{ color: #888; font-size: 12px; margin-bottom: 24px; }}
  .section-title {{ font-size: 22px; font-weight: 700; margin: 36px 0 6px; }}
  .section-empty {{ color: #cf222e; background: #fff0f0; border: 1px solid #ffd7d7; border-radius: 8px;
                     padding: 14px 18px; margin-bottom: 16px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
  .card {{ background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }}
  .photos {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 2px; background: #ddd; }}
  .photos img {{ width: 100%; height: 90px; object-fit: cover; display: block; }}
  .no-photo {{ padding: 30px; text-align: center; color: #999; grid-column: 1 / -1; }}
  .info {{ padding: 12px 14px; }}
  .info h3 {{ margin: 6px 0; font-size: 16px; }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; color: #fff; }}
  .src-kb {{ background: #2f80ed; }}
  .src-encar {{ background: #eb5757; }}
  .src-charancha {{ background: #9b51e0; }}
  .src-onbid {{ background: #219653; }}
  .src-aj {{ background: #f2994a; }}
  .src-other {{ background: #828282; }}
  .row {{ font-size: 13px; color: #333; margin: 4px 0; }}
  .price {{ font-weight: 600; }}
  .breakdown {{ color: #777; font-size: 11px; }}
  .damage-ok {{ color: #1a7f37; }}
</style>
</head>
<body>
<h1>Персональный отчёт по подбору</h1>
<div class="generated">Только для просмотра, никуда не публикуется. Прямые ссылки на площадки скрыты.</div>
{sections_html}
</body>
</html>
"""


def render_section(section: dict, rates: dict) -> str:
    records = collect_section_records(section)

    if not records:
        return (
            f'<div class="section-title">{escape(section["title"])}</div>'
            f'<div class="section-empty">По заданным критериям на подключённых источниках '
            f'({", ".join(SOURCE_LABELS.get(s, (s, ""))[0] for s in ALL_SOURCES)}) ничего не найдено.</div>'
        )

    cards_with_price = [render_card(car, rates) for car in records]
    cards_with_price.sort(key=lambda pair: (pair[1] is None, pair[1] if pair[1] is not None else 0))
    cards_html = "".join(html for html, _ in cards_with_price)

    return (
        f'<div class="section-title">{escape(section["title"])} — найдено: {len(records)}</div>'
        f'<div class="grid">{cards_html}</div>'
    )


def run() -> Path:
    rates = fetch_cbr_rates()
    sections_html = "".join(render_section(section, rates) for section in SECTIONS)
    html = PAGE_TEMPLATE.format(sections_html=sections_html)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8")
    logger.info(f"=== Готово: {REPORT_PATH} ===")
    return REPORT_PATH


if __name__ == "__main__":
    run()
