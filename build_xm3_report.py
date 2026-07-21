# -*- coding: utf-8 -*-
"""
build_xm3_report.py — разовый ручной отчёт (не публикация) по Renault Korea
(Samsung) XM3 1.6 GTe, 2022-2024, пробег до 30 000 км, собранный по итогам
model_search.py (kbchachacha/encar/charancha/onbid).

Источник данных: model_search_results/renault_xm3_petrol_2022_2024_30000/
(уже собрано отдельными прогонами model_search.py — этот скрипт только
считает цену "под ключ" через post_builder.calculate_price_details() и
рендерит HTML, ничего не скрейпит сам).

Известные ограничения этого прогона (см. заметки в отчёте):
- kbchachacha: 32 объявления в категории XM3 отдали ОДИНАКОВЫЙ склеенный
  текст трима ("...E-TECH 하이브리드 1.6 GTe INSPIRE...") на каждой карточке
  списка — судя по всему, сайт в списке объявлений отдаёт не тримы
  конкретных объявлений, а общий список вариантов комплектации всего
  класса. Отличить 1.6 GTe от E-TECH Hybrid по этим данным нельзя без
  захода на каждую карточку — источник исключён из расчёта цены.
- onbid: 0 лотов XM3 в текущих активных аукционах.
"""

import json
import logging
from html import escape
from pathlib import Path

from post_builder import calculate_price_details
from rates import fetch_cbr_rates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_xm3_report")

RESULTS_DIR = Path(__file__).resolve().parent / "model_search_results" / "renault_xm3_petrol_2022_2024_30000_accidentfree"
REPORT_PATH = Path(__file__).resolve().parent / "model_search_results" / "xm3_1.6_gte_report.html"

TITLE = "Renault Korea (Samsung) XM3 1.6 GTe — 2022–2024, пробег до 30 000 км, без ДТП"

SOURCE_LABELS = {
    "encar": ("Encar", "src-encar"),
    "charancha": ("Charancha", "src-charancha"),
    "kbchachacha": ("KB Chachacha", "src-kb"),
    "onbid": ("OnBid", "src-onbid"),
}


def load_records() -> tuple:
    """Возвращает (records, excluded_other_engine) — вторые это найденные, но
    отфильтрованные как ДРУГОЙ мотор (1.3 TCe, не 1.6 GTe — оба "petrol",
    поэтому фильтр --fuel petrol их не разделяет, разделяем здесь по cc/hp)."""
    records = []
    excluded_other_engine = 0
    for path in sorted(RESULTS_DIR.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            logger.error(f"Не удалось прочитать {path}: {error}")
            continue
        if rec.get("cc") == 1332:  # 1.3 TCe Turbo — другой мотор, не 1.6 GTe
            excluded_other_engine += 1
            continue
        records.append(rec)
    return records, excluded_other_engine


def format_money(value) -> str:
    if value is None:
        return "уточняется"
    return f"{int(value):,}".replace(",", " ")


CARD_TEMPLATE = """
<div class="card">
  <div class="photos">
    {photos_html}
  </div>
  <div class="info">
    <div class="badge {source_class}">{source}</div>
    <h3>{title}</h3>
    <div class="row">📅 {year}{month} &nbsp;|&nbsp; 🛣 {mileage} &nbsp;|&nbsp; 🎨 {color} &nbsp;|&nbsp; ⚙ {transmission}</div>
    <div class="row price">💰 Цена в Корее: {price_krw} ₩</div>
    <div class="row total">🏁 Под ключ в Казани: {total}</div>
    <div class="row breakdown">{breakdown}</div>
    <div class="row damage">{damage}</div>
    <div class="row"><a href="{url}" target="_blank" rel="noopener">Открыть объявление →</a></div>
  </div>
</div>
"""


def render_card(car: dict, rates: dict) -> tuple:
    photos = (car.get("photos") or [])[:5]
    if photos:
        photos_html = "".join(f'<img src="{escape(p)}" loading="lazy" alt="фото">' for p in photos)
    else:
        photos_html = '<div class="no-photo">нет фото</div>'

    source = car.get("source", "?")
    source_label, source_class = SOURCE_LABELS.get(source, (source, "src-other"))
    title = " ".join(str(x) for x in (car.get("brand") or "Renault", car.get("model") or "XM3", "1.6 GTe") if x)

    details = calculate_price_details(car, rates)
    if details is None:
        total_text = "не рассчитана (не хватает данных)"
        breakdown = ""
        total_rub = None
    else:
        total_rub = details["total_rub"]
        total_text = f"<b>{format_money(total_rub)} ₽</b>"
        breakdown = (
            f"машина: {format_money(details['car_price_rub'])} ₽ · "
            f"пошлина+ЕТС+утильсбор: {format_money(details['customs_rub'])} ₽ · "
            f"банк+логистика+брокер+наша наценка: "
            f"{format_money(details['bank_commission_rub'] + details['extra_costs_rub'])} ₽ "
            f"(возраст для расчёта: {details['age_bucket']})"
        )

    accident_free = car.get("accident_free")
    if accident_free is True:
        damage = "✅ без ДТП (по данным площадки)"
    elif accident_free is False:
        damage = "⚠️ была авария/повреждение"
    else:
        damage = "❔ статус ДТП не проверялся этой площадкой"
    if car.get("damage_notes"):
        damage += f" — {car['damage_notes']}"
    if car.get("_hp_fuel_manual_note"):
        damage += f'<br><span class="manual-note">⚠ {car["_hp_fuel_manual_note"]}</span>'

    month = f".{car['month']:02d}" if car.get("month") else ""

    html = CARD_TEMPLATE.format(
        photos_html=photos_html,
        source_class=source_class,
        source=escape(source_label),
        title=escape(title),
        year=escape(str(car.get("year") or "—")),
        month=month,
        mileage=f"{car['mileage_km']:,} км".replace(",", " ") if car.get("mileage_km") is not None else "—",
        color=escape(car.get("color") or "не указан"),
        transmission=escape(car.get("transmission") or "—"),
        price_krw=format_money(car.get("price")),
        total=total_text,
        breakdown=breakdown,
        damage=damage,
        url=escape(car.get("url", "")),
    )
    return html, total_rub


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #f4f5f7; margin: 0; padding: 24px; color: #1a1a1a; }}
  h1 {{ margin-bottom: 4px; font-size: 24px; }}
  .generated {{ color: #888; font-size: 12px; margin-bottom: 8px; }}
  .stats {{ color: #444; font-size: 14px; margin-bottom: 16px; }}
  .notes {{ background: #fff8e1; border: 2px solid #f39c12; border-radius: 10px; padding: 14px 18px; font-size: 13px; color: #7d5a00; margin-bottom: 24px; }}
  .notes ul {{ margin: 6px 0 0 18px; padding: 0; }}
  .notes li {{ margin-bottom: 4px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
  .card {{ background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.12); }}
  .photos {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 2px; background: #ddd; }}
  .photos img {{ width: 100%; height: 90px; object-fit: cover; display: block; }}
  .no-photo {{ padding: 30px; text-align: center; color: #999; grid-column: 1 / -1; }}
  .info {{ padding: 12px 14px; }}
  .info h3 {{ margin: 6px 0; font-size: 16px; }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; color: #fff; }}
  .src-kb {{ background: #2f80ed; }}
  .src-encar {{ background: #eb5757; }}
  .src-charancha {{ background: #9b51e0; }}
  .src-onbid {{ background: #219653; }}
  .src-other {{ background: #828282; }}
  .row {{ font-size: 13px; color: #333; margin: 4px 0; }}
  .price {{ color: #666; }}
  .total {{ font-weight: 700; font-size: 15px; color: #c0392b; }}
  .breakdown {{ color: #777; font-size: 11px; }}
  .damage {{ color: #1a7f37; }}
  .manual-note {{ color: #b26a00; }}
  a {{ color: #2f80ed; text-decoration: none; font-size: 13px; }}
  a:hover {{ text-decoration: underline; }}
  .rates {{ font-size: 12px; color: #999; margin-top: 28px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="generated">Личный отчёт для просмотра — никуда не публикуется. Сформировано автоматически из model_search.py + customs_calculator.py (те же формулы, что и в боевых постах). Отсортировано по цене "под ключ Казань" — от дешевле к дороже.</div>
<div class="stats">{stats}</div>
<div class="notes">
  <b>Примечания по источникам:</b>
  <ul>{notes_html}</ul>
</div>
{cards_html}
<div class="rates">Курсы ЦБ РФ на момент расчёта: {rates_text}</div>
</body>
</html>
"""


def run() -> Path:
    records, excluded_other_engine = load_records()
    rates = fetch_cbr_rates()

    cards_with_price = [render_card(car, rates) for car in records]
    cards_with_price.sort(key=lambda pair: (pair[1] is None, pair[1] if pair[1] is not None else 0))
    cards_html = f'<div class="grid">{"".join(html for html, _ in cards_with_price)}</div>'

    counts = {}
    for car in records:
        counts[car.get("source", "?")] = counts.get(car.get("source", "?"), 0) + 1
    stats_parts = [f"{SOURCE_LABELS.get(s, (s, ''))[0]}: {n}" for s, n in sorted(counts.items())]
    stats = " &nbsp;•&nbsp; ".join(stats_parts) + f" &nbsp;•&nbsp; Всего в отчёте: {len(records)}"

    notes = [
        "<b>Фильтр «без ДТП»</b> — из 100 найденных объявлений (Encar + Charancha) отобраны только "
        "подтверждённо без ДТП: 11 объявлений исключены, т.к. Encar по официальной диагностике/техпаспорту "
        "нашёл повреждение (замена/ремонт кузовной панели или флаг «сведения об аварии»); ещё 40 объявлений "
        "исключены, т.к. Encar не смог однозначно подтвердить статус (детальные данные диагностики по "
        "конкретному объявлению недоступны/неполные) — по вашему требованию «только без ДТП» неизвестный "
        "статус тоже трактуется как исключение, а не как «предположительно ок».",
        "<b>KB Chachacha</b> — на площадке найдено 32 объявления XM3 в заданных годе/пробеге, "
        "но список отдаёт для каждой карточки один и тот же склеенный текст комплектации "
        "(одновременно упоминает и «E-TECH 하이브리드», и «1.6 GTe») — похоже на ошибку "
        "на стороне сайта (общий список вариантов класса вместо трима конкретного объявления). "
        "Достоверно отличить 1.6 GTe от гибрида по этим данным нельзя, поэтому источник "
        "<b>исключён</b> из расчёта, чтобы не показать неверную цену.",
        "<b>OnBid</b> (гос. аукционы) — активных лотов Renault XM3 на момент запроса нет (0).",
        "Цена «под ключ Казань» включает: пошлина+ЕТС+утильсбор (физлицо, легковой бензин ≤160 л.с. — "
        "льготная ставка), банковскую комиссию 2.5% от стоимости авто, логистику, услуги брокера и "
        "фиксированную наценку — те же значения и формулы, что в основном боевом посте (config.py / "
        "customs_calculator.py), поэтому цифры не расходятся с реальными постами компании.",
        "Charancha (2 объявления) не публикует явное обозначение мотора в названии — мощность/топливо "
        "определены вручную по объёму двигателя (1598 см³, из поля сайта) и цене/АКПП, см. пометку "
        "на карточке.",
    ]
    if excluded_other_engine:
        notes.append(
            f"Encar также нашёл {excluded_other_engine} объявлений XM3 <b>1.3 TCe Turbo</b> "
            f"(152 л.с., другой мотор, не GTe) в тех же году/пробеге — они <b>не относятся к "
            f"запрошенной комплектации 1.6 GTe</b> и в отчёт не включены."
        )
    notes_html = "".join(f"<li>{n}</li>" for n in notes)

    rates_text = (
        f"₩→₽ {rates['krw_rub']:.6f} · €→₽ {rates['eur_rub']:.2f} · $→₽ {rates['usd_rub']:.2f}"
    )

    html = PAGE_TEMPLATE.format(
        title=escape(TITLE),
        stats=stats,
        notes_html=notes_html,
        cards_html=cards_html,
        rates_text=escape(rates_text),
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8")
    logger.info(f"=== Готово: {REPORT_PATH} ({len(records)} объявлений) ===")
    return REPORT_PATH


if __name__ == "__main__":
    run()
