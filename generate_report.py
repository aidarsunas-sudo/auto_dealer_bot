# -*- coding: utf-8 -*-
"""
generate_report.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает все JSON-файлы
из test_k5_sonata/ (результаты test_k5_sonata_scraper.py и encar_test_scraper.py)
в одну наглядную HTML-страницу test_k5_sonata/report.html — фото, марка/модель/
год/пробег/цена и статус повреждений по каждой машине, без просмотра сырого JSON.

kbchachacha и Encar отдают РАЗНЫЕ по форме JSON (разные названия полей для
цены/пробега/истории повреждений) — этот скрипт не трогает сами файлы, а
только ЧИТАЕТ их и приводит к единому виду карточки для отображения.

Запуск (после того, как test_k5_sonata_scraper.py и/или encar_test_scraper.py
уже сохранили результаты в test_k5_sonata/):
    python generate_report.py
"""

import json
from html import escape
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "test_k5_sonata"
REPORT_PATH = OUTPUT_DIR / "report.html"

KRW_PER_MANWON = 10000  # у Encar Price указан в "만원" (десятках тысяч вон)


def normalize_kbchachacha(data: dict) -> dict:
    photos = data.get("photos") or []

    history_bits = []
    status = data.get("accident_status", "")
    if "있음" in status and "없음" not in status:
        history_bits.append("ДТП было")
    elif "없음" in status:
        history_bits.append("без ДТП")
    if data.get("total_loss_history") == "있음":
        history_bits.append("тотальное повреждение")
    if data.get("flood_damage_history") == "있음":
        history_bits.append("было затопление")
    damage_summary = ", ".join(history_bits) if history_bits else "нет данных"

    price_krw = data.get("price_krw") or data.get("price")

    return {
        "source": "kbchachacha.com",
        "model_label": data.get("model_label", ""),
        "title": data.get("car_name") or data.get("model", ""),
        "year": data.get("year", ""),
        "mileage_km": data.get("mileage_km"),
        "price_krw": price_krw,
        "photos": photos,
        "damage_summary": damage_summary,
        "url": data.get("url", ""),
    }


def normalize_encar(data: dict) -> dict:
    photos = data.get("photos") or []

    master = ((data.get("inspection") or {}).get("master")) or {}
    outers = ((data.get("inspection") or {}).get("outers")) or []

    history_bits = []
    if master.get("accdient") is False:
        history_bits.append("без ДТП")
    elif master.get("accdient") is True:
        history_bits.append("ДТП было")
    if master.get("waterlog"):
        history_bits.append("было затопление")
    if outers:
        panel_names = [o.get("type", {}).get("title", "?") for o in outers]
        history_bits.append(f"повреждены панели: {', '.join(panel_names)}")
    damage_summary = ", ".join(history_bits) if history_bits else "нет данных"

    price_krw = data.get("Price")
    if price_krw is not None:
        price_krw = int(price_krw * KRW_PER_MANWON)

    title = f"{data.get('Manufacturer', '')} {data.get('Model', '')} {data.get('Badge', '')}".strip()

    return {
        "source": "encar.com",
        "model_label": data.get("model_label", ""),
        "title": title,
        "year": data.get("FormYear", ""),
        "mileage_km": int(data["Mileage"]) if data.get("Mileage") is not None else None,
        "price_krw": price_krw,
        "photos": photos,
        "damage_summary": damage_summary,
        "url": f"https://fem.encar.com/cars/detail/{data.get('Id', '')}",
    }


def load_all_records() -> list:
    records = []
    for path in sorted(OUTPUT_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if data.get("source") == "encar.com":
            records.append(normalize_encar(data))
        else:
            records.append(normalize_kbchachacha(data))

    return records


def format_price(price_krw) -> str:
    if not price_krw:
        return "цена не указана"
    return f"{price_krw:,} ₩".replace(",", " ")


def format_mileage(mileage_km) -> str:
    if mileage_km is None:
        return "—"
    return f"{mileage_km:,} км".replace(",", " ")


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


def render_card(rec: dict) -> str:
    photos = rec["photos"][:5]
    if photos:
        photos_html = "".join(f'<img src="{escape(p)}" loading="lazy" alt="фото">' for p in photos)
    else:
        photos_html = '<div class="no-photo">нет фото</div>'

    damage_class = "damage-bad" if any(
        w in rec["damage_summary"] for w in ("ДТП было", "затопление", "повреждены")
    ) else "damage-ok"

    source_class = "src-kb" if rec["source"] == "kbchachacha.com" else "src-encar"

    return CARD_TEMPLATE.format(
        photos_html=photos_html,
        source_class=source_class,
        source=escape(rec["source"]),
        title=escape(rec["title"]) or "—",
        year=escape(str(rec["year"] or "—")),
        mileage=escape(format_mileage(rec["mileage_km"])),
        price=escape(format_price(rec["price_krw"])),
        damage_class=damage_class,
        damage=escape(rec["damage_summary"]),
        url=escape(rec["url"]),
    )


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>K5 / Sonata DN8 — сравнение источников</title>
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
  .row {{ font-size: 13px; color: #333; margin: 4px 0; }}
  .price {{ font-weight: 600; }}
  .damage-ok {{ color: #1a7f37; }}
  .damage-bad {{ color: #cf222e; font-weight: 600; }}
  a {{ color: #2f80ed; text-decoration: none; font-size: 13px; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>Kia K5 / Hyundai Sonata DN8 — kbchachacha vs Encar</h1>
<div class="stats">{stats}</div>
{groups_html}
</body>
</html>
"""


def run() -> None:
    records = load_all_records()

    groups = {}
    for rec in records:
        key = (rec["model_label"], rec["source"])
        groups.setdefault(key, []).append(rec)

    stats_lines = []
    for (model_label, source), items in sorted(groups.items()):
        stats_lines.append(f"{model_label} / {source}: {len(items)}")
    stats_html = " &nbsp;•&nbsp; ".join(stats_lines) + f" &nbsp;•&nbsp; Всего: {len(records)}"

    groups_html_parts = []
    for (model_label, source), items in sorted(groups.items()):
        cards_html = "".join(render_card(rec) for rec in items)
        groups_html_parts.append(
            f'<div class="group-title">{escape(model_label)} — {escape(source)} ({len(items)})</div>'
            f'<div class="grid">{cards_html}</div>'
        )

    html = PAGE_TEMPLATE.format(stats=stats_html, groups_html="".join(groups_html_parts))
    REPORT_PATH.write_text(html, encoding="utf-8")
    print(f"Готово: {REPORT_PATH} ({len(records)} машин)")


if __name__ == "__main__":
    run()
