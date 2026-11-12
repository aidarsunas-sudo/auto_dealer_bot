# -*- coding: utf-8 -*-
"""
render_bmw_x6_report.py — рендер report.html + data.json + data.csv из
bmw_x6_report/collected.json (см. build_bmw_x6_report.py).

ФОРМАТ ПО ТЗ:
- HTML офлайн, фото ЛОКАЛЬНЫЕ из ./photos (скачиваются при рендере,
  относительные пути), клик по фото — лайтбокс-галерея;
- сводная таблица сравнения в начале, сортировка по пробегу;
- блоки: основной (2026), отдельный блок 2025, «требует проверки»,
  «исключённые» с причинами;
- дата формирования и курсы валют в шапке/подвале;
- сырые данные: data.json (как collected.json) + data.csv (плоская таблица).

Запуск:
    python render_bmw_x6_report.py
"""

import argparse
import base64
import csv
import io
import json
import logging
from datetime import datetime
from html import escape
from pathlib import Path

import requests

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("render_bmw_x6_report")

OUT_DIR = Path(__file__).resolve().parent / "bmw_x6_report"
PHOTOS_DIR = OUT_DIR / "photos"
DATA_PATH = OUT_DIR / "collected.json"
HTML_PATH = OUT_DIR / "report.html"
JSON_PATH = OUT_DIR / "data.json"
CSV_PATH = OUT_DIR / "data.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
MAX_PHOTOS_PER_CAR = 5

SOURCE_LABELS = {
    "encar": ("Encar", "src-encar"),
    "heydealer": ("HeyDealer", "src-heydealer"),
    "kcar": ("KCar", "src-kcar"),
    "kbchachacha": ("KB ChaChaCha", "src-kb"),
}

_COLOR_DISPLAY_RU = {
    "흰색": "белый", "화이트": "белый", "펄화이트": "жемчужно-белый", "알파인 화이트": "альпийский белый",
    "검정색": "чёрный", "검은색": "чёрный", "블랙": "чёрный", "블랙 사파이어": "чёрный сапфир",
    "파란색": "синий", "블루": "синий", "남색": "тёмно-синий",
    "회색": "серый", "쥐색": "серый", "그레이": "серый", "은회색": "серебристо-серый",
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


def download_photos_local(car: dict) -> list:
    """Скачивает до MAX_PHOTOS_PER_CAR фото в photos/{площадка}_{id}/NN.jpg
    и возвращает ОТНОСИТЕЛЬНЫЕ пути (отчёт офлайн, папка рядом с html)."""
    photos = (car.get("photos") or [])[:MAX_PHOTOS_PER_CAR]
    if not photos:
        return []
    subdir = f"{car.get('source')}_{slugify(car.get('external_id'))}"
    dest_dir = PHOTOS_DIR / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    local_paths = []
    for i, url in enumerate(photos, start=1):
        fname = f"{i:02d}.jpg"
        dest = dest_dir / fname
        if not dest.exists():
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                dest.write_bytes(r.content)
            except Exception as error:
                logger.warning(f"Не удалось скачать фото {url}: {error}")
                continue
        local_paths.append(f"photos/{subdir}/{fname}")
    return local_paths


def _resize_jpeg(content: bytes, max_width: int = 1200, quality: int = 82) -> bytes:
    """Ужимает фото для embed-режима (иначе файл распухает сверх разумного)."""
    if not HAS_PIL:
        return content
    try:
        img = Image.open(io.BytesIO(content)).convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception as error:
        logger.warning(f"Не удалось ужать фото: {error}")
        return content


def photos_to_embed(local_paths: list) -> list:
    """Локальные jpg -> data:-URI (для самодостаточного report_embed.html,
    который открывается на телефоне без папки photos рядом)."""
    data_uris = []
    for rel in local_paths:
        f = OUT_DIR / rel.replace("/", "\\")
        try:
            content = _resize_jpeg(f.read_bytes())
            data_uris.append("data:image/jpeg;base64," + base64.b64encode(content).decode("ascii"))
        except Exception as error:
            logger.warning(f"Не удалось встроить фото {f}: {error}")
    return data_uris


def format_money(value) -> str:
    if value is None:
        return "—"
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
    <div class="row price">💰 Цена на площадке: {price_krw}</div>
    <div class="row total">💵 В рублях по курсу ЦБ: {price_rub}</div>
    <details class="breakdown">
      <summary>🏁 «Под ключ» в Казани: <b>{total_with_margin}</b> ₽ (раскладка по статьям)</summary>
      <table class="breakdown-table">
        <tr><td>Цена авто в Корее (инвойс)</td><td>{price_krw_num} ₩ → {car_price_rub} ₽</td></tr>
        <tr><td>Услуги в Корее (агент, инспекция, документы) — в составе логистики</td><td>вкл. ниже</td></tr>
        <tr><td>Доставка + страховка (порт → Владивосток → Казань)</td><td>{logistics_rub} ₽</td></tr>
        <tr><td>Таможенный сбор (за оформление)</td><td>{fee_rub} ₽</td></tr>
        <tr><td>Пошлина — ЕТС, до 3 лет = 48% от стоимости</td><td>{ets_rub} ₽</td></tr>
        <tr><td>Утильсбор</td><td>{util_rub} ₽</td></tr>
        <tr><td>Брокер, СБКТС/ЭПТС, таможенное оформление</td><td>{broker_rub} ₽</td></tr>
        <tr><td>Банковская комиссия (перевод)</td><td>{bank_commission_rub} ₽</td></tr>
        <tr class="total-row"><td><b>Итого себестоимость</b></td><td><b>{total} ₽</b></td></tr>
        <tr><td>Комиссия Pick+ Auto</td><td>{margin_rub} ₽</td></tr>
        <tr class="total-row"><td><b>ИТОГО ПОД КЛЮЧ</b></td><td><b>{total_with_margin} ₽</b></td></tr>
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
            f'class="lightbox-trigger" loading="lazy" alt="фото {i+1}" title="Клик — увеличить">'
            for i, p in enumerate(local_photos)
        )
    else:
        photos_html = '<div class="no-photo">нет фото</div>'

    source = car.get("source", "?")
    source_label, source_class = SOURCE_LABELS.get(source, (source, "src-other"))
    title = f"BMW X6 — {car.get('trim')}" if car.get("trim") else "BMW X6"

    status = car.get("_condition_status", "unknown")
    condition_class = {"clean": "cond-clean", "minor_ok": "cond-minor"}.get(status, "cond-unknown")
    condition_icon = {"clean": "✅", "minor_ok": "⚠️"}.get(status, "❔")

    details = car.get("_price_details") or {}
    price_rub = car.get("_price_rub")
    price_rub_text = f"<b>{format_money(price_rub)} ₽</b>" if price_rub is not None else "цена не указана площадкой"
    price_krw_text = (format_money(car.get("price")) + " ₩") if car.get("price") else "не указана (торги)"
    pod_klyuch = details.get("total_with_margin_rub") or details.get("total_rub")
    pod_klyuch_text = format_money(pod_klyuch) if pod_klyuch else "не рассчитана"
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
        price_krw=price_krw_text,
        price_rub=price_rub_text,
        price_krw_num=format_money(car.get("price")) if car.get("price") else "—",
        total=format_money(details.get("total_rub")),
        total_with_margin=pod_klyuch_text,
        car_price_rub=format_money(details.get("car_price_rub")),
        logistics_rub=format_money(details.get("logistics_rub")),
        fee_rub=format_money(details.get("fee_rub")),
        ets_rub=format_money(details.get("ets_rub")),
        util_rub=format_money(details.get("util_rub")),
        broker_rub=format_money(details.get("broker_rub")),
        bank_commission_rub=format_money(details.get("bank_commission_rub")),
        margin_rub=format_money(details.get("margin_rub")),
    )


LIGHTBOX_CSS = """
  .photo-strip { display: flex; gap: 2px; overflow-x: auto; background: #ddd; }
  .photo-strip img { width: 90px; height: 90px; object-fit: cover; flex: 0 0 auto; cursor: zoom-in; display: block; }
  .no-photo { padding: 30px; text-align: center; color: #999; width: 100%; background: #eee; }
  #lightbox { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.9); z-index: 1000;
              align-items: center; justify-content: center; flex-direction: column; }
  #lightbox.open { display: flex; }
  #lightbox img { max-width: 90vw; max-height: 78vh; object-fit: contain; }
  #lightbox .lb-counter { color: #fff; margin-top: 10px; font-size: 14px; }
  #lightbox .lb-arrow { position: absolute; top: 50%; transform: translateY(-50%); font-size: 40px;
                         color: #fff; cursor: pointer; user-select: none; padding: 20px; background: rgba(0,0,0,0.3); }
  #lightbox .lb-prev { left: 10px; }
  #lightbox .lb-next { right: 10px; }
  #lightbox .lb-close { position: absolute; top: 20px; right: 20px; font-size: 32px; color: #fff; cursor: pointer; }
  #lightbox .lb-open { position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%);
                        color: #9ecbff; font-size: 13px; background: rgba(0,0,0,0.5); padding: 7px 12px;
                        border-radius: 8px; text-decoration: none; }
  #lightbox .lb-open:hover { color: #fff; text-decoration: underline; }
"""

LIGHTBOX_JS = """
(function() {
  var lb = document.getElementById('lightbox');
  var lbImg = document.getElementById('lb-img');
  var lbCounter = document.getElementById('lb-counter');
  var lbOpen = document.getElementById('lb-open');
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
    if (lbOpen) lbOpen.href = currentGroup[currentIdx];
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
<title>BMW X6 (G06) — 2026 MY, Корея, без ДТП/страховых выплат/окрасов</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #f4f5f7; margin: 0; padding: 24px; color: #1a1a1a; }}
  h1 {{ margin-bottom: 4px; font-size: 24px; }}
  h2 {{ font-size: 20px; margin: 30px 0 10px; border-bottom: 2px solid #ddd; padding-bottom: 6px; }}
  .generated {{ color: #888; font-size: 12px; margin-bottom: 8px; }}
  .stats {{ color: #444; font-size: 14px; margin-bottom: 16px; background: #fff; border-radius: 8px; padding: 12px 16px; }}
  .stats table {{ border-collapse: collapse; }}
  .stats td {{ padding: 2px 10px 2px 0; }}
  .summary {{ background: #fff; border-radius: 10px; padding: 14px 16px; margin-bottom: 24px; overflow-x: auto; }}
  .summary table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  .summary th, .summary td {{ border: 1px solid #e0e0e0; padding: 5px 8px; text-align: left; white-space: nowrap; }}
  .summary th {{ background: #f0f0f0; }}
  .summary tr.me-row td {{ background: #eafbea; }}
  .notes {{ background: #fff8e1; border: 2px solid #f39c12; border-radius: 10px; padding: 14px 18px; font-size: 13px; color: #7d5a00; margin-bottom: 24px; }}
  .notes ul {{ margin: 6px 0 0 18px; padding: 0; }}
  .notes li {{ margin-bottom: 4px; }}
  .empty {{ background: #fff0f0; border: 1px solid #ffd7d7; border-radius: 8px; padding: 12px 16px; color: #cf222e; margin-bottom: 16px; }}
  .warn {{ background: #fff8e1; border: 1px solid #f39c12; border-radius: 8px; padding: 12px 16px; color: #7d5a00; margin-bottom: 16px; font-size: 13px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }}
  .card {{ background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.12); }}
  {lightbox_css}
  .info {{ padding: 12px 14px; }}
  .info h3 {{ margin: 6px 0; font-size: 15px; }}
  .info h3 a {{ color: #1a1a1a; text-decoration: none; }}
  .info h3 a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; color: #fff; }}
  .src-encar {{ background: #eb5757; }}
  .src-heydealer {{ background: #f2994a; }}
  .src-kcar {{ background: #2c3e50; }}
  .src-kb {{ background: #2f80ed; }}
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
<h1>BMW X6 (G06) — 2026 MY, Корея, пробег ≤ 10 000 км, без ДТП/страховых выплат/окрасов</h1>
<div class="generated">Личный отчёт — никуда не публикуется. Сформировано: {generated_at}. Отсортировано по возрастанию пробега. Клик по фото — галерея (стрелки/Esc). Источники: Encar, HeyDealer, K Car, KB ChaChaCha.</div>
<div class="stats">{stats_html}</div>
<h2 style="border:none;margin-top:0">Сводная таблица</h2>
<div class="summary">{summary_html}</div>
<div class="notes"><b>Примечания:</b><ul>{notes_html}</ul></div>
<h2>Основной блок — 2026 модельный год ({main_count} шт.)</h2>
{main_html}
<h2>2025 модельный год — отдельный блок, год НЕ подменялся ({alt_count} шт.)</h2>
{alt_html}
<h2>Требует проверки — статус не подтверждён данными площадок ({check_count} шт.)</h2>
<div class="warn">В основной список не включены (по ТЗ). Перед решением проверить вручную: 성능점검표 (окрасы/ДТП), страховую историю (보험처리 이력), юридическую чистоту.</div>
{check_html}
<h2>Исключённые — ДТП/страховые выплаты/окрасы ({excl_count} шт.)</h2>
{excl_html}
<div class="rates">Курсы ЦБ РФ на момент расчёта ({rates_date}): {rates_text} · Сырые данные: data.json + data.csv рядом с отчётом</div>

<div id="lightbox">
  <span class="lb-close">×</span>
  <span class="lb-arrow lb-prev">‹</span>
  <img id="lb-img" src="" alt="">
  <span class="lb-arrow lb-next">›</span>
  <div class="lb-counter" id="lb-counter"></div>
  <a id="lb-open" class="lb-open" href="#" target="_blank" rel="noopener">Открыть в полном размере ↗</a>
</div>
<script>
{lightbox_js}
</script>
</body>
</html>
"""


def _cards(cars: list, car_idx: int, embed: bool = False) -> tuple:
    parts = []
    for c in cars:
        car_idx += 1
        local = download_photos_local(c)
        photos = photos_to_embed(local) if embed else local
        parts.append(render_card(c, car_idx, photos))
    return "".join(parts), car_idx


def _excluded_table(items: list) -> str:
    if not items:
        return '<div class="empty">Исключённых нет.</div>'
    rows = []
    for it in items:
        label = SOURCE_LABELS.get(it.get("source"), (it.get("source"), ""))[0]
        rows.append(
            f"<tr><td>{escape(label)}</td><td>{escape(str(it.get('year') or '—'))}</td>"
            f"<td>{escape(str(it.get('mileage_km') or '—'))}</td>"
            f"<td>{escape(str(it.get('trim') or '—'))}</td>"
            f"<td>{escape(it.get('reasons') or '')}</td>"
            f"<td><a href='{escape(it.get('url') or '')}' target='_blank' rel='noopener'>лот →</a></td></tr>"
        )
    return ('<table class="rejected-table"><tr><th>Площадка</th><th>Год</th><th>Пробег</th>'
            '<th>Комплектация</th><th>Причина исключения</th><th>Ссылка</th></tr>'
            + "".join(rows) + "</table>")


def build_html(data: dict, embed: bool = False) -> Path:
    main = data.get("main_2026", {})
    alt = data.get("alt_2025", {})
    main_kept = main.get("kept", [])
    alt_kept = alt.get("kept", [])
    check = main.get("needs_check", []) + alt.get("needs_check", [])
    excl = main.get("excluded", []) + alt.get("excluded", [])

    # ── сводка по площадкам ──
    stats_rows = []
    for block_label, block in (("2026", main), ("2025", alt)):
        src = data.get("sources", {}).get("main_2026" if block_label == "2026" else "alt_2025", {})
        for skey, label in (("encar", "Encar"), ("heydealer", "HeyDealer"), ("kcar", "KCar"), ("kbchachacha", "KB ChaChaCha")):
            info = src.get(skey, {})
            if info.get("error"):
                stats_rows.append((f"{label} ({block_label})", f"недоступна: {info['error']}"))
            elif skey == "kbchachacha":
                stats_rows.append((f"{label} ({block_label})", f"воронка: {info.get('raw_matches', 0)} (цвет/ДТП не проверялись)"))
            else:
                stats_rows.append((f"{label} ({block_label})", f"в основном списке: {sum(1 for c in block.get('kept', []) if c.get('source') == skey)}"))
    stats_html = "<table>" + "".join(f"<tr><td><b>{escape(k)}</b></td><td>{escape(v)}</td></tr>" for k, v in stats_rows) + "</table>"

    # ── сводная таблица сравнения ──
    all_kept = [("2026", c) for c in main_kept] + [("2025", c) for c in alt_kept]
    all_kept.sort(key=lambda p: (p[1].get("mileage_km") is None, p[1].get("mileage_km") or 0))
    srows = []
    for year_label, c in all_kept:
        d = c.get("_price_details") or {}
        label = SOURCE_LABELS.get(c.get("source"), (c.get("source"), ""))[0]
        srows.append(
            f"<tr class='{'' if year_label == '2026' else 'me-row'}'>"
            f"<td>{year_label}</td><td>{escape(label)}</td>"
            f"<td>{escape(str(c.get('mileage_km') or '—'))}</td>"
            f"<td>{escape(str(c.get('trim') or '—'))}</td>"
            f"<td>{escape(display_color(c.get('color')))}</td>"
            f"<td>{format_money(c.get('price')) if c.get('price') else '—'}</td>"
            f"<td>{format_money(c.get('_price_rub')) if c.get('_price_rub') else '—'}</td>"
            f"<td><b>{format_money(d.get('total_with_margin_rub')) if d else '—'}</b></td>"
            f"<td><a href='{escape(c.get('url', ''))}' target='_blank' rel='noopener'>лот →</a></td></tr>"
        )
    summary_html = (
        "<table><tr><th>Год MY</th><th>Площадка</th><th>Пробег, км</th><th>Комплектация</th><th>Цвет</th>"
        "<th>Цена, ₩</th><th>Цена, ₽ (ЦБ)</th><th>Под ключ, ₽</th><th>Ссылка</th></tr>"
        + ("".join(srows) if srows else "<tr><td colspan='9'>В основные списки не попало ни одной машины — см. блоки ниже.</td></tr>")
        + "</table>"
        + ("<div style='font-size:11px;color:#777;margin-top:6px'>Зелёная строка — 2025 MY (отдельный блок, не подменяет 2026).</div>" if alt_kept else "")
    )

    notes = [
        "<b>Отбор (по ТЗ)</b>: 2026 MY — основной блок; 2025 MY — отдельный блок без подмены года. "
        "Пробег ≤ 10 000 км, сортировка по возрастанию.",
        "<b>Без ДТП</b> (무사고 по 성능점검표) + <b>без страховых выплат</b> (보험처리 이력 = 0) + "
        "<b>без окрасов</b> (도색 이력 없음). Любая страховая выплата или окрас → «исключённые». "
        "Непроверяемый статус → «требует проверки».",
        "<b>Цены</b>: вон + ₽ по курсу ЦБ; «под ключ» — раскладка по статьям той же логикой, что боевые "
        "посты компании (customs_calculator.py + config.py: логистика/страховка 320 000 ₽, брокер+СБКТС/ЭПТС "
        "100 000 ₽, комиссия Pick+ 150 000 ₽, банк 0.5%). Пошлина для авто до 3 лет — ЕТС, 48% от стоимости.",
        "<b>Полнота разная по площадкам</b>: Encar — 성능점검표 (окрасы/ДТП) + страховая история; "
        "HeyDealer — carhistory (0 страховых случаев) + осмотр; K Car — флаг ДТП. KB ChaChaCha из этой "
        "сети отдаёт антибот-заглушку — только воронка, в отбор не входит.",
    ]
    notes_html = "".join(f"<li>{n}</li>" for n in notes)

    car_idx = 0
    main_html = '<div class="grid">'
    if main_kept:
        cards, car_idx = _cards(main_kept, car_idx, embed)
        main_html += cards + '</div>'
    else:
        main_html = ('<div class="empty">Предложений BMW X6 2026 MY по критериям нет. '
                     'X6 (G06) выпускается и 2026 модельного года (в Корее продаётся), но лотов с пробегом '
                     '≤10 000 км и чистой историей на момент сбора не нашлось — см. блок 2025.</div>')

    if alt_kept:
        cards, car_idx = _cards(alt_kept, car_idx, embed)
        alt_html = f'<div class="grid">{cards}</div>'
    else:
        alt_html = '<div class="empty">По 2025 MY тоже ничего не найдено.</div>'

    if check:
        cards, car_idx = _cards(check, car_idx, embed)
        check_html = f'<div class="grid">{cards}</div>'
    else:
        check_html = '<div class="empty">Не проверенных карточек нет.</div>'

    excl_html = _excluded_table(excl)

    rates_text = (f"₩→₽ {data['rates']['krw_rub']:.6f} · $→₽ {data['rates']['usd_rub']:.2f} · "
                  f"€→₽ {data['rates']['eur_rub']:.2f}"
                  + (" (живой курс ЦБ)" if data.get("rates_source") == "cbr" else " (ЗАПАСНЫЕ — ЦБ недоступен!)"))

    html = PAGE_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        rates_date=datetime.now().strftime("%d.%m.%Y"),
        stats_html=stats_html,
        summary_html=summary_html,
        notes_html=notes_html,
        main_count=len(main_kept),
        main_html=main_html,
        alt_count=len(alt_kept),
        alt_html=alt_html,
        check_count=len(check),
        check_html=check_html,
        excl_count=len(excl),
        excl_html=excl_html,
        rates_text=escape(rates_text),
        lightbox_css=LIGHTBOX_CSS,
        lightbox_js=LIGHTBOX_JS,
    )
    out_path = OUT_DIR / "report_embed.html" if embed else HTML_PATH
    out_path.write_text(html, encoding="utf-8")
    logger.info(f"HTML готов: {out_path}")
    return out_path


CSV_COLUMNS = ["year_block", "source", "external_id", "year", "month", "mileage_km", "trim",
               "cc", "hp", "fuel_code", "color", "price_krw", "price_rub_cbr",
               "pod_klyuch_sebestoimost_rub", "pod_klyuch_s_komissiey_rub",
               "condition_status", "condition_note", "url"]


def export_json_csv(data: dict) -> None:
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for block_key, block_label in (("main_2026", "2026"), ("alt_2025", "2025")):
            block = data.get(block_key, {})
            for section in ("kept", "needs_check"):
                for c in block.get(section, []):
                    d = c.get("_price_details") or {}
                    writer.writerow([
                        block_label, c.get("source"), c.get("external_id"), c.get("year"), c.get("month"),
                        c.get("mileage_km"), c.get("trim"), c.get("cc"), c.get("hp"), c.get("fuel_code"),
                        display_color(c.get("color")), c.get("price"), c.get("_price_rub"),
                        d.get("total_rub"), d.get("total_with_margin_rub"),
                        {"clean": "ok", "unknown": "ТРЕБУЕТ ПРОВЕРКИ"}.get(c.get("_condition_status"), c.get("_condition_status")),
                        c.get("_condition_note"), c.get("url"),
                    ])
            for e in block.get("excluded", []):
                writer.writerow([
                    block_label, e.get("source"), "", e.get("year"), "", e.get("mileage_km"),
                    e.get("trim"), "", "", "", display_color(e.get("color")), "", "", "", "",
                    "ИСКЛЮЧЁН", e.get("reasons"), e.get("url"),
                ])
    logger.info(f"JSON/CSV готовы: {JSON_PATH}, {CSV_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embed-photos", action="store_true",
                        help="самодостаточный report_embed.html — фото внутри файла "
                             "(base64), открывается на телефоне без папки photos рядом")
    args = parser.parse_args()
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = build_html(data, embed=args.embed_photos)
    export_json_csv(data)
    print(f"Готово: {out_path}")
