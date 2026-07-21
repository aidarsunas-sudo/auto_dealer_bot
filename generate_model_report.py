# -*- coding: utf-8 -*-
"""
generate_model_report.py — собирает HTML-отчёт из JSON-файлов, которые
сохраняет model_search.py (model_search_results/<slug>/*.json).

В отличие от generate_report.py (который писался под СЫРЫЕ форматы старых
тестовых скриптов test_k5_sonata_scraper.py/encar_test_scraper.py — разные
поля на каждый сайт), здесь схема уже единая для всех источников — её
приводят к общему виду адаптеры *_scraper.py (kbchachacha_target_adapter.py,
encar_scraper.py, charancha_scraper.py, onbid_scraper.py) плюс шаг
обогащения в model_search.py. Поэтому нормализатор один, не по-сайтовый.

Ожидаемые поля записи: brand, model, trim, year, mileage_km, price, url,
source, photos (list[str]), accident_free (True/False/None — None значит
"не удалось проверить"), damage_notes (str, опционально).

Запуск отдельно от model_search.py (например, чтобы перегенерировать отчёт
без повторного скрейпинга):
    python generate_model_report.py model_search_results/audi_a4_diesel_2022-2023_60000_accidentfree
"""

import json
import sys
from html import escape
from pathlib import Path


def load_records(input_dir: Path) -> list:
    records = []
    for path in sorted(input_dir.glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return records


def format_price(price_krw) -> str:
    if not price_krw:
        return "цена не указана"
    return f"{int(price_krw):,} ₩".replace(",", " ")


def format_mileage(mileage_km) -> str:
    if mileage_km is None:
        return "—"
    return f"{int(mileage_km):,} км".replace(",", " ")


def format_damage(rec: dict) -> tuple:
    """Возвращает (текст, css-класс) для бейджа ДТП/повреждений."""
    accident_free = rec.get("accident_free")
    notes = rec.get("damage_notes") or ""

    if accident_free is True:
        text = "без ДТП"
        css_class = "damage-ok"
    elif accident_free is False:
        text = "было ДТП"
        css_class = "damage-bad"
    else:
        text = "не проверено"
        css_class = "damage-unknown"

    if notes:
        text = f"{text} ({notes})"
    return text, css_class


CARD_TEMPLATE = """
<div class="card">
  <div class="photos">
    {photos_html}
  </div>
  <div class="info">
    <div class="badge {source_class}">{source}</div>
    <h3>{title}</h3>
    <div class="row">📅 {year} &nbsp;|&nbsp; 🛣 {mileage}</div>
    <div class="row price">💰 {price}</div>
    <div class="row damage {damage_class}">🔧 {damage}</div>
    <div class="row"><a href="{url}" target="_blank" rel="noopener">Открыть объявление →</a></div>
  </div>
</div>
"""

SOURCE_COLORS = {
    "kbchachacha": "src-kb",
    "encar": "src-encar",
    "charancha": "src-charancha",
    "onbid": "src-onbid",
}


def render_card(rec: dict) -> str:
    photos = (rec.get("photos") or [])[:5]
    if photos:
        photos_html = "".join(f'<img src="{escape(p)}" loading="lazy" alt="фото">' for p in photos)
    else:
        photos_html = '<div class="no-photo">нет фото</div>'

    damage_text, damage_class = format_damage(rec)
    title = " ".join(str(x) for x in (rec.get("brand"), rec.get("model"), rec.get("trim")) if x)
    source = rec.get("source", "?")

    return CARD_TEMPLATE.format(
        photos_html=photos_html,
        source_class=SOURCE_COLORS.get(source, "src-other"),
        source=escape(source),
        title=escape(title) or "—",
        year=escape(str(rec.get("year") or "—")),
        mileage=escape(format_mileage(rec.get("mileage_km"))),
        price=escape(format_price(rec.get("price"))),
        damage_class=damage_class,
        damage=escape(damage_text),
        url=escape(rec.get("url", "")),
    )


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{page_title}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #f4f5f7; margin: 0; padding: 24px; }}
  h1 {{ margin-bottom: 4px; }}
  .stats {{ color: #555; margin-bottom: 24px; }}
  .group-title {{ font-size: 20px; font-weight: 600; margin: 32px 0 12px; }}
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
  .src-other {{ background: #828282; }}
  .row {{ font-size: 13px; color: #333; margin: 4px 0; }}
  .price {{ font-weight: 600; }}
  .damage-ok {{ color: #1a7f37; }}
  .damage-bad {{ color: #cf222e; font-weight: 600; }}
  .damage-unknown {{ color: #999; }}
  a {{ color: #2f80ed; text-decoration: none; font-size: 13px; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>{page_title}</h1>
<div class="stats">{stats}</div>
{groups_html}
</body>
</html>
"""


def render_report(records: list) -> str:
    groups = {}
    for rec in records:
        key = rec.get("source", "?")
        groups.setdefault(key, []).append(rec)

    if records:
        brand = records[0].get("brand", "")
        model = records[0].get("model", "")
        page_title = f"{brand} {model}".strip() or "Подборка объявлений"
    else:
        page_title = "Подборка объявлений"

    stats_lines = [f"{source}: {len(items)}" for source, items in sorted(groups.items())]
    stats_html = " &nbsp;•&nbsp; ".join(stats_lines) + f" &nbsp;•&nbsp; Всего: {len(records)}"

    groups_html_parts = []
    for source, items in sorted(groups.items()):
        cards_html = "".join(render_card(rec) for rec in items)
        groups_html_parts.append(
            f'<div class="group-title">{escape(source)} ({len(items)})</div>'
            f'<div class="grid">{cards_html}</div>'
        )

    return PAGE_TEMPLATE.format(
        page_title=escape(page_title),
        stats=stats_html,
        groups_html="".join(groups_html_parts),
    )


def run(input_dir: Path) -> Path:
    records = load_records(input_dir)
    html = render_report(records)
    report_path = input_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python generate_model_report.py <папка с JSON>")
        sys.exit(1)
    out_path = run(Path(sys.argv[1]))
    print(f"Готово: {out_path}")
