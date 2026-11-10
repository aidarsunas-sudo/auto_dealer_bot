# -*- coding: utf-8 -*-
"""
render_bmw_x4_report.py — рендер report.html + report.xlsx из
bmw_x4_report/collected.json (см. build_bmw_x4_report.py).

Интерактив: лайтбокс галереи (клик по фото, стрелки/клавиатура, счётчик) —
как в render_compact_suv_2022_report.py. Фото скачиваются в ./photos/ И
встраиваются в HTML как base64 (самодостаточный файл, открывается где угодно).

Запуск:
    python render_bmw_x4_report.py
"""

import base64
import json
import logging
from datetime import datetime
from html import escape
from pathlib import Path

import requests
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("render_bmw_x4_report")

OUT_DIR = Path(__file__).resolve().parent / "bmw_x4_report"
PHOTOS_DIR = OUT_DIR / "photos"
DATA_PATH = OUT_DIR / "collected.json"
HTML_PATH = OUT_DIR / "report.html"
XLSX_PATH = OUT_DIR / "report.xlsx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
MAX_PHOTOS_PER_CAR = 5

SOURCE_LABELS = {
    "encar": ("Encar", "src-encar"),
    "charancha": ("Charancha", "src-charancha"),
    "heydealer": ("HeyDealer", "src-heydealer"),
    "kcar": ("KCar", "src-kcar"),
    "aj": ("AJ", "src-aj"),
}

_COLOR_DISPLAY_RU = {
    "흰색": "белый", "화이트": "белый", "펄화이트": "жемчужно-белый",
    "검정색": "чёрный", "검은색": "чёрный", "블랙": "чёрный",
    "파란색": "синий", "블루": "синий", "남색": "тёмно-синий",
    "회색": "серый", "쥐색": "серый", "그레이": "серый",
    "은색": "серебристый", "실버": "серебристый",
    "빨간색": "красный", "초록색": "зелёный", "갈색": "коричневый",
}


def display_color(raw: str | None) -> str:
    if not raw:
        return "не указан"
    return _COLOR_DISPLAY_RU.get(raw, raw)


def slugify(text) -> str:
    import re
    return re.sub(r"[^\w]+", "_", str(text or "").strip().lower()).strip("_") or "car"


def download_photos(car: dict) -> list:
    """Скачивает фото в ./photos/ (резервная копия на диске) И возвращает
    data:-URI (base64) для встраивания в HTML — самодостаточный файл."""
    photos = (car.get("photos") or [])[:MAX_PHOTOS_PER_CAR]
    data_uris = []
    slug = slugify(car.get("model"))
    for i, url in enumerate(photos, start=1):
        fname = f"{car.get('source')}_{slug}_{car.get('external_id')}_{i}.jpg"
        dest = PHOTOS_DIR / fname
        if not dest.exists():
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                dest.write_bytes(r.content)
            except Exception as error:
                logger.warning(f"Не удалось скачать фото {url}: {error}")
                data_uris.append(url)  # fallback — внешняя ссылка
                continue
        try:
            content = dest.read_bytes()
            b64 = base64.b64encode(content).decode("ascii")
            data_uris.append(f"data:image/jpeg;base64,{b64}")
        except Exception as error:
            logger.warning(f"Не удалось встроить фото {dest}: {error}")
            data_uris.append(url)
    return data_uris


def format_money(value) -> str:
    if value is None:
        return "не рассчитана"
    return f"{int(value):,}".replace(",", " ")


CARD_TEMPLATE = """
<div class="card">
  <div class="photo-strip" data-car="{car_idx}">
    {photos_html}
  </div>
  <div class="info">
    <div class="badge {source_class}">{source}</div>
    <h3><a href="{url}" target="_blank" rel="noopener">{title}</a></h3>
    <div class="row">📅 {year}{month} &nbsp;|&nbsp; 🛣 {mileage} &nbsp;|&nbsp; 🎨 {color} &nbsp;|&nbsp; ⚙ {transmission}</div>
    <div class="row">🔧 {cc_hp}</div>
    <div class="row condition {condition_class}">{condition_icon} {condition_note}</div>
    <div class="row price">💰 Цена на площадке: {price_krw} ₩</div>
    <div class="row total">💵 В рублях по курсу ЦБ: {price_rub}</div>
    <details class="breakdown">
      <summary>🏁 «Под ключ» в Казани: <b>{total}</b> (разбивка)</summary>
      <table class="breakdown-table">
        <tr><td>Цена авто</td><td>{price_krw} ₩ → {car_price_rub} ₽</td></tr>
        <tr><td>Пошлина (сбор)</td><td>{fee_rub} ₽</td></tr>
        <tr><td>ЕТС</td><td>{ets_rub} ₽</td></tr>
        <tr><td>Утильсбор</td><td>{util_rub} ₽</td></tr>
        <tr><td>Банк.комиссия</td><td>{bank_commission_rub} ₽</td></tr>
        <tr><td>Логистика+брокер</td><td>{logistics_broker_rub} ₽</td></tr>
        <tr class="total-row"><td><b>Итого (себестоимость)</b></td><td><b>{total} ₽</b></td></tr>
        <tr><td>Итого с наценкой (справочно)</td><td>{total_with_margin} ₽</td></tr>
      </table>
    </details>
    <div class="row"><a class="open-btn" href="{url}" target="_blank" rel="noopener">Открыть на площадке ({source}) →</a></div>
  </div>
</div>
"""


def render_card(car: dict, car_idx: int, local_photos: list) -> str:
    if local_photos:
        photos_html = "".join(
            f'<img src="{escape(p)}" data-full="{escape(p)}" data-idx="{i}" '
            f'class="lightbox-trigger" loading="lazy" alt="фото {i+1}">'
            for i, p in enumerate(local_photos)
        )
    else:
        photos_html = '<div class="no-photo">нет фото</div>'

    source = car.get("source", "?")
    source_label, source_class = SOURCE_LABELS.get(source, (source, "src-other"))
    title = "BMW X4"
    if car.get("trim"):
        title += f" — {car['trim']}"

    status = car.get("_condition_status", "unknown")
    condition_class = {"clean": "cond-clean", "minor_ok": "cond-minor"}.get(status, "cond-unknown")
    condition_icon = {"clean": "✅", "minor_ok": "⚠️"}.get(status, "❔")

    details = car.get("_price_details") or {}
    price_rub = car.get("_price_rub")
    price_rub_text = f"<b>{format_money(price_rub)} ₽</b>" if price_rub is not None else "цена не указана площадкой"
    month = f".{car['month']:02d}" if car.get("month") else ""

    return CARD_TEMPLATE.format(
        car_idx=car_idx,
        photos_html=photos_html,
        source_class=source_class,
        source=escape(source_label),
        url=escape(car.get("url", "")),
        title=escape(title),
        year=escape(str(car.get("year") or "—")),
        month=month,
        mileage=f"{car['mileage_km']:,} км".replace(",", " ") if car.get("mileage_km") is not None else "—",
        color=escape(display_color(car.get("color"))),
        transmission=escape(car.get("transmission") or "не указано"),
        cc_hp=f"{car.get('cc') or '?'} см³ / {car.get('hp') or '?'} л.с. / {car.get('fuel_code') or '?'}",
        condition_class=condition_class,
        condition_icon=condition_icon,
        condition_note=escape(car.get("_condition_note", "")),
        price_krw=format_money(car.get("price")),
        price_rub=price_rub_text,
        total=format_money(details.get("total_rub")),
        car_price_rub=format_money(details.get("car_price_rub")),
        fee_rub=format_money(details.get("fee_rub")),
        ets_rub=format_money(details.get("ets_rub")),
        util_rub=format_money(details.get("util_rub")),
        bank_commission_rub=format_money(details.get("bank_commission_rub")),
        logistics_broker_rub=format_money(details.get("logistics_broker_rub")),
        total_with_margin=format_money(details.get("total_with_margin_rub")),
    )


LIGHTBOX_CSS = """
  .photo-strip { display: flex; gap: 2px; overflow-x: auto; background: #ddd; }
  .photo-strip img { width: 90px; height: 90px; object-fit: cover; flex: 0 0 auto; cursor: pointer; display: block; }
  .no-photo { padding: 30px; text-align: center; color: #999; width: 100%; background: #eee; }
  #lightbox { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.9); z-index: 1000;
              align-items: center; justify-content: center; flex-direction: column; }
  #lightbox.open { display: flex; }
  #lightbox img { max-width: 90vw; max-height: 80vh; object-fit: contain; }
  #lightbox .lb-counter { color: #fff; margin-top: 10px; font-size: 14px; }
  #lightbox .lb-arrow { position: absolute; top: 50%; transform: translateY(-50%); font-size: 40px;
                         color: #fff; cursor: pointer; user-select: none; padding: 20px; background: rgba(0,0,0,0.3); }
  #lightbox .lb-prev { left: 10px; }
  #lightbox .lb-next { right: 10px; }
  #lightbox .lb-close { position: absolute; top: 20px; right: 20px; font-size: 32px; color: #fff; cursor: pointer; }
"""

LIGHTBOX_JS = """
(function() {
  var lb = document.getElementById('lightbox');
  var lbImg = document.getElementById('lb-img');
  var lbCounter = document.getElementById('lb-counter');
  var currentGroup = [];
  var currentIdx = 0;

  function openLightbox(stripEl, idx) {
    var imgs = Array.prototype.slice.call(stripEl.querySelectorAll('img.lightbox-trigger'));
    currentGroup = imgs.map(function(im) { return im.getAttribute('data-full'); });
    currentIdx = idx;
    show();
    lb.classList.add('open');
  }
  function show() {
    lbImg.src = currentGroup[currentIdx];
    lbCounter.textContent = (currentIdx + 1) + ' / ' + currentGroup.length;
  }
  function next() { currentIdx = (currentIdx + 1) % currentGroup.length; show(); }
  function prev() { currentIdx = (currentIdx - 1 + currentGroup.length) % currentGroup.length; show(); }
  function close() { lb.classList.remove('open'); }

  document.querySelectorAll('.photo-strip').forEach(function(strip) {
    strip.querySelectorAll('img.lightbox-trigger').forEach(function(img, idx) {
      img.addEventListener('click', function() {
        openLightbox(strip, parseInt(img.getAttribute('data-idx'), 10));
      });
    });
  });
  document.querySelector('.lb-next').addEventListener('click', next);
  document.querySelector('.lb-prev').addEventListener('click', prev);
  document.querySelector('.lb-close').addEventListener('click', close);
  lb.addEventListener('click', function(e) { if (e.target === lb) close(); });
  document.addEventListener('keydown', function(e) {
    if (!lb.classList.contains('open')) return;
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowRight') next();
    if (e.key === 'ArrowLeft') prev();
  });
})();
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>BMW X4 (G02) — 2025–2026, пробег ≤ 5 000 км, без окрасов/ДТП</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #f4f5f7; margin: 0; padding: 24px; color: #1a1a1a; }}
  h1 {{ margin-bottom: 4px; font-size: 24px; }}
  h2 {{ font-size: 20px; margin: 30px 0 10px; border-bottom: 2px solid #ddd; padding-bottom: 6px; }}
  .generated {{ color: #888; font-size: 12px; margin-bottom: 8px; }}
  .stats {{ color: #444; font-size: 14px; margin-bottom: 16px; background: #fff; border-radius: 8px; padding: 12px 16px; }}
  .stats table {{ border-collapse: collapse; }}
  .stats td {{ padding: 2px 10px 2px 0; }}
  .notes {{ background: #fff8e1; border: 2px solid #f39c12; border-radius: 10px; padding: 14px 18px; font-size: 13px; color: #7d5a00; margin-bottom: 24px; }}
  .notes ul {{ margin: 6px 0 0 18px; padding: 0; }}
  .notes li {{ margin-bottom: 4px; }}
  .empty {{ background: #fff0f0; border: 1px solid #ffd7d7; border-radius: 8px; padding: 12px 16px; color: #cf222e; margin-bottom: 16px; }}
  .warn {{ background: #fff8e1; border: 1px solid #f39c12; border-radius: 8px; padding: 12px 16px; color: #7d5a00; margin-bottom: 16px; font-size: 13px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
  .card {{ background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.12); }}
  {lightbox_css}
  .info {{ padding: 12px 14px; }}
  .info h3 {{ margin: 6px 0; font-size: 15px; }}
  .info h3 a {{ color: #1a1a1a; text-decoration: none; }}
  .info h3 a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; color: #fff; }}
  .src-encar {{ background: #eb5757; }}
  .src-charancha {{ background: #9b51e0; }}
  .src-heydealer {{ background: #f2994a; }}
  .src-kcar {{ background: #2c3e50; }}
  .src-aj {{ background: #e67e22; }}
  .src-other {{ background: #828282; }}
  .row {{ font-size: 13px; color: #333; margin: 4px 0; }}
  .price {{ color: #666; }}
  .total {{ font-weight: 700; font-size: 14px; color: #1a5fb4; }}
  .cond-clean {{ color: #1a7f37; }}
  .cond-minor {{ color: #b26a00; }}
  .cond-unknown {{ color: #999; }}
  .breakdown {{ margin: 6px 0; font-size: 12px; }}
  .breakdown summary {{ cursor: pointer; color: #1a5fb4; font-size: 13px; }}
  .breakdown-table {{ width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 11px; color: #555; }}
  .breakdown-table td {{ padding: 2px 4px; border-bottom: 1px solid #f0f0f0; }}
  .breakdown-table .total-row td {{ border-top: 2px solid #ccc; color: #1a1a1a; }}
  a {{ color: #2f80ed; text-decoration: none; font-size: 13px; }}
  a:hover {{ text-decoration: underline; }}
  .open-btn {{ display: inline-block; margin-top: 6px; padding: 6px 10px; background: #2f80ed; color: #fff !important;
               border-radius: 6px; text-decoration: none; font-size: 12px; }}
  .open-btn:hover {{ background: #1a5fb4; }}
  .rejected-table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: 12px; }}
  .rejected-table th, .rejected-table td {{ border: 1px solid #e0e0e0; padding: 6px 8px; text-align: left; }}
  .rejected-table th {{ background: #f0f0f0; }}
  .rates {{ font-size: 12px; color: #999; margin-top: 28px; }}
</style>
</head>
<body>
<h1>BMW X4 (G02) — 2025–2026 г.в., пробег ≤ 5 000 км, без окрасов/ДТП</h1>
<div class="generated">Личный отчёт — никуда не публикуется. Собрано: {generated_at}. Отсортировано по возрастанию пробега. Клик по фото — галерея (стрелки/Esc).</div>
<div class="stats">{stats_html}</div>
<div class="notes"><b>Примечания:</b><ul>{notes_html}</ul></div>
<h2>Основной список — «без окрасов/ДТП» подтверждено ({primary_count} шт.)</h2>
{primary_html}
<h2>Требует проверки — статус ДТП/окрасов НЕ подтверждён данными площадки ({needs_check_count} шт.)</h2>
<div class="warn">В основной список не включены (по вашему правилу). Перед решением проверить вручную: техпаспорт/страховую историю, окрасы, юридическую чистоту (залог/ограничения).</div>
{needs_check_html}
{rejected_html}
<div class="rates">Курсы ЦБ РФ на момент расчёта: {rates_text}</div>

<div id="lightbox">
  <span class="lb-close">×</span>
  <span class="lb-arrow lb-prev">‹</span>
  <img id="lb-img" src="" alt="">
  <span class="lb-arrow lb-next">›</span>
  <div class="lb-counter" id="lb-counter"></div>
</div>
<script>
{lightbox_js}
</script>
</body>
</html>
"""

REJECTED_SECTION_TEMPLATE = """
<h2>Отсеяно ({count} шт.) — подтверждённые ДТП/окрасы/арест</h2>
<table class="rejected-table">
<tr><th>Площадка</th><th>Комплектация</th><th>Год</th><th>Пробег</th><th>Причина</th><th>Ссылка</th></tr>
{rows}
</table>
"""


def build_html(data: dict) -> None:
    primary = data["primary"]
    needs_check = data["needs_check"]
    funnel = data["funnel_only"]
    login_info = data.get("login_info", {})
    counts = data["counts"]

    stats_rows = [
        ("Encar", f"{counts['encar_ok']} прошли «без окрасов» (по техпаспорту)"),
        ("Charancha", f"{counts['charancha_ok']} прошли «без ДТП» (по данным площадки)"),
        ("HeyDealer", f"{counts['heydealer_ok']} прошли (0 страховых случаев)"
         if not login_info.get("heydealer", {}).get("error")
         else f"НЕДОСТУПНА: {login_info['heydealer']['error']}"),
        ("KCar", f"{counts['kcar_ok']} прошли"
         if not login_info.get("kcar", {}).get("error")
         else f"НЕДОСТУПНА: {login_info['kcar']['error']}"),
        ("AJ / SellCarAuction", f"{counts['aj_ok']} прошли"
         if not login_info.get("aj", {}).get("error")
         else f"НЕДОСТУПНА: {login_info['aj']['error']}"),
        ("SK Car Rental", "парсер не реализован (лоты за авторизацией) — отдельная задача"),
    ]
    for name, info in funnel.items():
        label = {"kbchachacha": "KB Chachacha", "onbid": "OnBid",
                 "glovis": "Glovis", "lotte": "Lotte"}.get(name, name)
        if info.get("error"):
            stats_rows.append((label, f"НЕДОСТУПНА: {info['error']}"))
        else:
            stats_rows.append((label, f"{info['raw_matches']} по фильтрам года/пробега (цвет/ДТП не проверялись)"))
    stats_html = "<table>" + "".join(f"<tr><td><b>{escape(k)}</b></td><td>{escape(v)}</td></tr>" for k, v in stats_rows) + "</table>"

    notes = [
        "<b>Год 2026</b>: BMW X4 (G02) снят с производства — конвейер завершён в 2025 году, "
        "нового поколения с ДВС не выпускается (модель в каталоге drom на 2026 год — японская "
        "остаточная витрина). Поэтому предложений «X4 2026» на площадках нет — отчёт построен "
        "на ближайшем доступном: 2025 г.в. с минимальным пробегом (≤ 5 000 км).",
        "<b>«Без окрасов»</b> — строгий допуск: 0 отмеченных панелей в техпаспорте Encar / "
        "чистый флаг ДТП на Charancha / 0 страховых случаев на HeyDealer. Ни одного исключения.",
        "<b>«Требует проверки»</b> — объявления, где площадка не смогла подтвердить статус "
        "(детальная диагностика Encar недоступна для этих карточек). По вашему правилу они НЕ "
        "вошли в основной список.",
        "<b>Цены</b>: «В рублях» — перевод цены площадки по живому курсу ЦБ; «Под ключ в Казани» — "
        "полная разбивка (пошлина+ЕТС+утильсбор+банк+логистика+брокер) той же логикой, что боевые "
        "посты (customs_calculator.py), наценка — отдельной справочной строкой.",
        "<b>Двигатель</b>: все найденные машины — xDrive20i 2.0 бензин (1998 см³, 184 л.с.), "
        "спека добавлена в model_specs.py со ссылкой на источник.",
        "<b>KB Chachacha</b> — из этой сети отдаёт антибот-заглушку вместо страницы фильтров "
        "(проверено дважды), <b>OnBid</b> — таймаут соединения, <b>Lotte</b> — недоступна из "
        "этой сети. Эти площадки в выборку не вошли.",
        "<b>AJ / SellCarAuction (sellcarauction.co.kr)</b> — площадка переехала со старого "
        "домена autohubauction.co.kr; логин в aj_scraper.py починен под новый домен "
        "(проверка по токену вместо текста «로그아ут»). Каталог ~2 260 машин — BMW X4 в "
        "диапазоне года/пробега не найдено.",
        "<b>KCar</b> (www.kcar.com/bc/search) — сессия дилерская, просмотрен список: X4 нет.",
        "<b>SK Car Rental (auction.skcarrental.com)</b> — страница открывается, но список "
        "лотов отдаётся только после авторизации, готового парсера под SK в проекте нет — "
        "это отдельная задача разработки, в этот отчёт не входит.",
    ]
    notes_html = "".join(f"<li>{n}</li>" for n in notes)

    car_idx = 0
    if primary:
        cards = []
        for c in primary:
            car_idx += 1
            cards.append(render_card(c, car_idx, download_photos(c)))
        primary_html = f'<div class="grid">{"".join(cards)}</div>'
    else:
        primary_html = ('<div class="empty">По заданным критериям (2024-2026, ≤5000 км, без окрасов/ДТП) '
                        'на доступных площадках ничего не найдено — см. сводку выше.</div>')

    if needs_check:
        cards = []
        for c in needs_check:
            car_idx += 1
            cards.append(render_card(c, car_idx, download_photos(c)))
        needs_check_html = f'<div class="grid">{"".join(cards)}</div>'
    else:
        needs_check_html = '<div class="empty">Объявлений с неподтверждённым статусом нет.</div>'

    rejected = data.get("rejected") or []
    rejected_rows = []
    for item in rejected:
        mileage_km = item.get("mileage_km")
        mileage_text = f"{mileage_km:,} км".replace(",", " ") if mileage_km is not None else "—"
        url = item.get("url") or ""
        rejected_rows.append(
            f"<tr><td>{escape(str(item.get('source') or '—'))}</td><td>{escape(str(item.get('trim') or '—'))}</td>"
            f"<td>{escape(str(item.get('year') or '—'))}</td>"
            f"<td>{escape(mileage_text)}</td>"
            f"<td>{escape(item.get('reasons') or '')}</td>"
            f"<td><a href='{escape(url)}' target='_blank' rel='noopener'>объявление →</a></td></tr>"
        )
    rejected_html = (REJECTED_SECTION_TEMPLATE.format(count=len(rejected_rows), rows="".join(rejected_rows))
                     if rejected_rows else "")

    krw_rub = data.get("rub_per_krw") or 0
    rates_text = (f"₩→₽ {krw_rub:.6f} (1000 ₩ = {krw_rub*1000:.2f} ₽)"
                  + (" — живой курс ЦБ" if data.get("rates_source") == "cbr" else " — ЗАПАСНЫЕ значения (ЦБ недоступен!)"))

    html = PAGE_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        stats_html=stats_html,
        notes_html=notes_html,
        primary_count=len(primary),
        primary_html=primary_html,
        needs_check_count=len(needs_check),
        needs_check_html=needs_check_html,
        rejected_html=rejected_html,
        rates_text=escape(rates_text),
        lightbox_css=LIGHTBOX_CSS,
        lightbox_js=LIGHTBOX_JS,
    )
    HTML_PATH.write_text(html, encoding="utf-8")
    logger.info(f"HTML готов: {HTML_PATH}")


XLSX_COLUMNS = [
    "Площадка", "Комплектация", "Год", "Месяц", "Пробег (км)", "Мощность (л.с.)",
    "Объём (см3)", "Топливо", "КПП", "Цвет",
    "Цена на площадке (KRW)", "Цена в рублях (курс ЦБ)",
    "Под ключ, себестоимость (RUB)", "Под ключ с наценкой (RUB)",
    "Статус ДТП/окраса", "Примечание", "Ссылка",
]


def build_xlsx(data: dict) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "BMW X4"

    for col, header in enumerate(XLSX_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"

    all_rows = data["primary"] + data["needs_check"]

    for r, car in enumerate(all_rows, start=2):
        details = car.get("_price_details") or {}
        source_label = SOURCE_LABELS.get(car.get("source"), (car.get("source"), ""))[0]
        ws.cell(row=r, column=1, value=source_label)
        ws.cell(row=r, column=2, value=car.get("trim") or "")
        ws.cell(row=r, column=3, value=car.get("year"))
        ws.cell(row=r, column=4, value=car.get("month"))
        ws.cell(row=r, column=5, value=car.get("mileage_km"))
        ws.cell(row=r, column=6, value=car.get("hp"))
        ws.cell(row=r, column=7, value=car.get("cc"))
        ws.cell(row=r, column=8, value=car.get("fuel_code"))
        ws.cell(row=r, column=9, value=car.get("transmission") or "не указано")
        ws.cell(row=r, column=10, value=display_color(car.get("color")))
        ws.cell(row=r, column=11, value=car.get("price") or None)
        ws.cell(row=r, column=12, value=car.get("_price_rub"))
        ws.cell(row=r, column=13, value=details.get("total_rub"))
        ws.cell(row=r, column=14, value=details.get("total_with_margin_rub"))
        ws.cell(row=r, column=15, value={"clean": "без окрасов/ДТП", "minor_ok": "незначительно",
                                         "unknown": "ТРЕБУЕТ ПРОВЕРКИ"}.get(car.get("_condition_status"), "?"))
        ws.cell(row=r, column=16, value=car.get("_condition_note") or "")
        link_cell = ws.cell(row=r, column=17, value=car.get("url") or "")
        if car.get("url"):
            link_cell.hyperlink = car["url"]
            link_cell.font = Font(color="0563C1", underline="single")

    last_row = len(all_rows) + 1
    last_col_letter = get_column_letter(len(XLSX_COLUMNS))
    ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"

    widths = [12, 30, 8, 8, 12, 10, 10, 10, 12, 14, 16, 16, 18, 18, 18, 45, 45]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    wb.save(XLSX_PATH)
    logger.info(f"XLSX готов: {XLSX_PATH} ({len(all_rows)} строк)")


if __name__ == "__main__":
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    build_html(data)
    build_xlsx(data)
    print(f"Готово: {HTML_PATH}, {XLSX_PATH}")
