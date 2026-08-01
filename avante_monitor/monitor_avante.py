# -*- coding: utf-8 -*-
"""
monitor_avante.py — ОДИН прогон мониторинга HeyDealer по Hyundai Avante (CN7).

Запускается по расписанию (каждые 6 часов, см. .github/workflows/avante_monitor.yml)
либо вручную:  python avante_monitor/monitor_avante.py [--dry-run]

Что делает за прогон:
  1) парсит аукционный зал HeyDealer и детальные карточки всех Avante;
  2) фильтрует по ЖЁСТКИМ критериям (см. ниже);
  3) считает цену «под ключ» там, где цена указана;
  4) сравнивает с состоянием seen_lots.json — НОВЫЕ = те, которых там ещё нет;
  5) собирает report.html (все подходящие, новые с бейджем NEW) + report.xlsx,
     и, если есть новые, alert_<timestamp>.html ТОЛЬКО с новыми лотами;
  6) при наличии новых — шлёт alert-файл документом в Telegram + сводку
     со ссылками; если новых нет — молчит (только лог).

Ничего не переписывает — переиспользует готовое:
  · heydealer_scraper       — доступ к API дилерской сессии;
  · model_specs / PDF-справочник — фактическая мощность (Avante 1.6 G = 123 л.с.);
  · color_match             — сопоставление корейских названий цвета;
  · post_builder.calculate_price_details + rates — цена «под ключ» (тот же
    модуль, что у лидогена);
  · car_report_photo_embed  — встроенные фото (data URI) и лайтбокс;
  · telegram_publisher      — отправка документа/сообщения;
  · notifier.notify_error   — сообщение об ошибке прогона в служебный чат.

Критерии «нужного лота» (жёсткие):
  Avante CN7 РЕСТАЙЛИНГ (더 뉴; дореформенные 올 뉴 и старые AD/MD/HD — мимо),
  модельный год 2024 + год выпуска 2023, 1.6 бензин ≤ 160 л.с. (N/N-Line
  отсекаются), пробег ≤ 50 000 км, цвет белый/серый/чёрный, НИ ОДНОЙ страховой
  выплаты, без ДТП/тотала/потопа, без ремонта силовых элементов,
  косметический окрас ≤ 1 элемента. Бюджет НЕ фильтруется: лот без цены —
  тоже подходящий, с пометкой «цена не указана».
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from car_report_photo_embed import LIGHTBOX_HTML, build_embedded_gallery
from color_match import matches_color
from model_specs import find_spec
from post_builder import calculate_price_details
from rates import fetch_cbr_rates

HERE = ROOT / "avante_monitor"
HERE.mkdir(exist_ok=True)
PHOTOS = HERE / "photos"
STATE_PATH = HERE / "seen_lots.json"
LOG_PATH = HERE / "monitor.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("avante_monitor")

CARD_API = "https://api.heydealer.com/v2/dealers/web/cars/{h}/"
MAX_PAGES = 80
MAX_PHOTOS = 6          # на лот; alert-файл должен оставаться лёгким (лимит TG 50 МБ)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ── критерии ──
MODEL_YEAR = 2024
PROD_YEAR = 2023
MILEAGE_MAX = 50000
MAX_HP = 160            # PDF «Список машин до 160 л.с.»: Avante 1.6 (G) = 123 л.с.
COLORS = ["white", "grey", "black"]
PAINT_MAX = 1
N_MARKS = ["n라인", "n 라인", "n-라인", "n line", "n-line", "nline", " n ", "2.0 n", "1.6 t"]
STRUCTURAL = ["frame", "side_member", "sidemember", "wheel_house", "wheelhouse", "floor",
              "pillar", "dash_panel", "dashpanel", "rear_panel", "package_tray",
              "trunk_floor", "cross_member", "crossmember", "inside_panel", "insidepanel"]

CRIT_LABELS = {
    "cn7": "Avante CN7", "facelift": "рестайлинг 더 뉴", "not_n": "не N / N-Line",
    "model_year": f"модельный год {MODEL_YEAR}", "prod_year": f"год выпуска {PROD_YEAR}",
    "fuel": "бензин", "engine_16": "объём 1.6", "hp": f"мощность ≤ {MAX_HP} л.с.",
    "mileage": f"пробег ≤ {MILEAGE_MAX:,} км".replace(",", " "),
    "color": "цвет белый/серый/чёрный", "no_insurance": "без страховых выплат",
    "no_total_flood": "без тотала/потопа", "no_structural": "без ремонта силовых",
    "paint_ok": f"окрас ≤ {PAINT_MAX} элемента",
}


def fmt(n) -> str:
    try:
        return f"{int(round(n)):,}".replace(",", " ")
    except Exception:
        return "—"


# ═══════════════════ состояние (дедупликация) ═══════════════════
def load_state() -> dict:
    """seen_lots.json: {hash: {first_seen, notified_at, car_number, url}}.
    Ключ дедупа — hash лота HeyDealer (устойчив, в отличие от цены/статуса)."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"seen_lots.json повреждён ({e}) — начинаю с пустого состояния")
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════ сбор ═══════════════════
def collect_cards() -> list:
    from heydealer_login import ensure_session
    from heydealer_scraper import CARS_API_URL, _build_requests_session
    # Сессия HeyDealer живёт часы, а в Actions на чистой VM её вовсе нет —
    # поэтому логинимся сами, иначе прогон по расписанию всегда падал бы.
    if not ensure_session(log):
        raise RuntimeError("heydealer: не удалось получить сессию (логин не прошёл)")
    sess = _build_requests_session()

    hashes, total = [], 0
    for page in range(1, MAX_PAGES + 1):
        params = {"page": page, "type": "auction", "is_subscribed": "false",
                  "is_retried": "false", "is_previously_bid": "false", "order": "default"}
        r = sess.get(CARS_API_URL, params=params, timeout=30)
        if r.status_code != 200:
            log.warning(f"зал: стр.{page} -> HTTP {r.status_code}")
            break
        data = r.json()
        if not data:
            break
        total += len(data)
        for it in data:
            d = it.get("detail") or {}
            blob = f"{d.get('brand_name','')} {d.get('full_name','')} {d.get('model_part_name','')}"
            if "아반떼" in blob or "avante" in blob.lower() or "elantra" in blob.lower():
                hashes.append(it.get("hash_id"))
        time.sleep(0.3)
    log.info(f"зал: просмотрено {total} лотов, Avante найдено {len(hashes)}")

    cards = []
    for h in hashes:
        for attempt in range(3):
            try:
                r = sess.get(CARD_API.format(h=h), timeout=30)
                if r.status_code == 200:
                    cards.append(r.json())
                    break
            except Exception as e:
                log.warning(f"карточка {h}, попытка {attempt+1}: {e}")
            time.sleep(1.5 * (attempt + 1))
        time.sleep(0.35)
    log.info(f"карточек получено: {len(cards)} из {len(hashes)}")
    return cards


# ═══════════════════ фильтрация ═══════════════════
def classify_repairs(repairs: list) -> dict:
    paint, exchange, structural = [], [], []
    for r in repairs or []:
        part = str(r.get("part") or "").lower()
        rep = str(r.get("repair") or "").lower()
        item = f"{part}·{rep}"
        is_struct = any(s in part for s in STRUCTURAL)
        if "paint" in rep or "도색" in rep:
            (structural if is_struct else paint).append(item)
        else:
            (structural if is_struct else exchange).append(item)
    return {"paint": paint, "exchange": exchange, "structural": structural}


def analyse(card: dict, rates: dict) -> dict:
    d = card.get("detail") or {}
    au = card.get("auction") or {}
    ch = d.get("carhistory") or {}

    grade = d.get("grade_part_name") or ""
    blob = f"{d.get('full_name','')} {grade} {d.get('model_part_name','')}".lower()

    reg = (d.get("initial_registration_date") or "")[:10]
    reg_year = int(reg[:4]) if reg[:4].isdigit() else None
    reg_month = int(reg[5:7]) if reg[5:7].isdigit() else None

    spec = find_spec(f"Hyundai Avante {grade}", d.get("year")) or find_spec("Hyundai Avante 1.6 gasoline", 2023)
    hp = spec.hp if spec else None
    cc = ch.get("displacement") or (spec.cc if spec else None)

    desired = au.get("desired_price")
    price_krw = int(desired) * 10000 if desired else None
    reps = classify_repairs(d.get("accident_repairs"))

    my_cnt = ch.get("my_car_accident_count") or 0
    ot_cnt = ch.get("other_car_accident_count") or 0
    my_cost = ch.get("my_car_accident_cost") or 0
    ot_cost = ch.get("other_car_accident_cost") or 0
    total_loss = ch.get("total_loss_count") or 0
    flooded = (ch.get("flooded_total_loss_count") or 0) + (ch.get("flooded_part_loss_count") or 0)

    a = {
        "hash": card.get("hash_id"),
        "url": f"https://dealer.heydealer.com/cars/{card.get('hash_id')}/",
        "car_number": d.get("car_number"), "full_name": d.get("full_name") or "",
        "grade": grade, "model_year": d.get("year"), "reg_date": reg, "reg_year": reg_year,
        "mileage": d.get("mileage"), "fuel": d.get("fuel"), "color": d.get("color"),
        "cc": cc, "hp": hp, "price_krw": price_krw, "desired_manwon": desired,
        "status": card.get("status_display"), "location": d.get("short_location"),
        "photos": d.get("image_urls") or [],
        "accident_summary": d.get("accident_repairs_summary_display"),
        "my_acc": my_cnt, "other_acc": ot_cnt, "my_cost": my_cost, "other_cost": ot_cost,
        "total_loss": total_loss, "flooded": flooded,
        "owner_changed": ch.get("owner_changed_count"),
        "rent_use": ch.get("rent_use_record_count") or 0,
        "repairs": reps,
    }

    checks = {
        "cn7": "cn7" in blob,                              # строго CN7: "더 뉴 아반떼 1.6 GDi" — это старый AD
        "facelift": ("더 뉴" in blob) and ("올 뉴" not in blob),
        "not_n": not any(m in blob for m in N_MARKS),
        "model_year": d.get("year") == MODEL_YEAR,
        "prod_year": reg_year == PROD_YEAR,
        "fuel": d.get("fuel") == "gasoline",
        "engine_16": cc is not None and 1500 <= cc <= 1700,
        "hp": hp is not None and hp <= MAX_HP,
        "mileage": a["mileage"] is not None and a["mileage"] <= MILEAGE_MAX,
        "color": matches_color(a["color"], COLORS) is True,
        "no_insurance": my_cnt == 0 and ot_cnt == 0 and my_cost == 0 and ot_cost == 0,
        "no_total_flood": total_loss == 0 and flooded == 0,
        "no_structural": len(reps["structural"]) == 0 and len(reps["exchange"]) == 0,
        "paint_ok": len(reps["paint"]) <= PAINT_MAX,
    }
    a["checks"] = checks
    a["passed"] = all(checks.values())
    a["fail_reasons"] = [k for k, v in checks.items() if not v]

    if price_krw and cc and hp:
        det = calculate_price_details(
            {"price": price_krw, "cc": cc, "hp": hp, "fuel_code": "petrol",
             "year": reg_year or d.get("year"), "month": reg_month}, rates)
        a["total_rub"] = det["total_rub"] if det else None
    else:
        a["total_rub"] = None
    return a


# ═══════════════════ фото / HTML ═══════════════════
def download_photos(a: dict) -> list:
    PHOTOS.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, url in enumerate((a.get("photos") or [])[:MAX_PHOTOS]):
        fn = PHOTOS / f"{a['hash']}_{i}.jpg"
        if fn.exists() and fn.stat().st_size > 8000:
            saved.append(fn)
            continue
        try:
            r = requests.get(url, headers={"User-Agent": UA,
                             "Referer": "https://dealer.heydealer.com/"}, timeout=20)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") \
               and len(r.content) > 8000:
                fn.write_bytes(r.content)
                saved.append(fn)
        except Exception as e:
            log.warning(f"фото {a['hash']}#{i}: {e}")
        time.sleep(0.1)
    return saved


def card_html(a: dict, is_new: bool) -> str:
    gallery, _ = build_embedded_gallery(download_photos(a), thumb_class="thumb")
    reps = a.get("repairs") or {}
    price_line = (f"{fmt(a['total_rub'])} ₽ <span class=pk>под ключ</span>" if a.get("total_rub")
                  else "<span class=noprice>цена не указана</span> <span class=pk>(слепой аукцион)</span>")
    price_raw = (f"{fmt(a['price_krw'])} ₩ ({a['desired_manwon']}만원)" if a.get("price_krw")
                 else "продавец не указал цену; ставки скрыты")
    badge = "<span class=new>NEW</span> " if is_new else ""
    paint_txt = ", ".join(reps.get("paint") or []) or "нет"
    return f"""<div class="card{' isnew' if is_new else ''}">
  <div class=imgs>{gallery}</div>
  <div class=meta>
    <div class=price>{badge}{price_line}</div>
    <div class=title><a href="{escape(a['url'])}" target="_blank" rel="noopener">{escape(a['full_name'])} ↗</a></div>
    <div class=sub2>гос.номер <b>{escape(str(a['car_number']))}</b> · лот <code>{escape(str(a['hash']))}</code>
        · {escape(str(a.get('location') or '—'))} · {escape(str(a.get('status') or ''))}</div>
    <table class=spec>
      <tr><td>Модельный год / выпуск</td><td>{a['model_year']} / первая рег. {escape(str(a['reg_date']))}</td></tr>
      <tr><td>Пробег</td><td><b>{fmt(a['mileage'])} км</b></td></tr>
      <tr><td>Двигатель</td><td>{a['cc']} см³ бензин · <b>{a['hp']} л.с.</b> (атмосферный)</td></tr>
      <tr><td>Цвет</td><td>{escape(str(a['color']))}</td></tr>
      <tr><td>Цена площадки</td><td>{price_raw}</td></tr>
      <tr><td>Страховая история</td><td class=ok-txt><b>{escape(str(a['accident_summary']))}</b> —
          ДТП: свои {a['my_acc']} / чужие {a['other_acc']} · выплаты {fmt(a['my_cost'])} ₩ / {fmt(a['other_cost'])} ₩</td></tr>
      <tr><td>Ремонты</td><td>силовые {len(reps.get('structural') or [])} · замены {len(reps.get('exchange') or [])}
          · косметический окрас: {escape(paint_txt)}</td></tr>
      <tr><td>Владельцев / прокат</td><td>{a.get('owner_changed')} · прокат {a.get('rent_use')}</td></tr>
    </table>
    <a class=link href="{escape(a['url'])}" target="_blank" rel="noopener">открыть объявление на HeyDealer ↗</a>
  </div></div>"""


CSS = """
body{font-family:system-ui,Segoe UI,Arial;margin:24px;color:#111;max-width:1150px}
h1{margin:0 0 4px;font-size:22px} .sub{color:#666;font-size:14px;margin:2px 0}
h2{margin-top:24px} a{color:#1560d0}
.card{display:flex;gap:14px;border:1px solid #e2e2e2;border-radius:10px;padding:12px;margin:14px 0}
.card.isnew{border-color:#0a7d33;box-shadow:0 0 0 2px #e8f6ec}
.imgs{display:flex;flex-wrap:wrap;gap:5px;max-width:420px;align-content:flex-start}
.imgs img{height:90px;border-radius:5px;border:1px solid #e2e2e2}
.imgs img.thumb{cursor:pointer} .noimg{color:#999;padding:20px;background:#f5f5f5;border-radius:6px}
.meta{flex:1} .price{font-size:20px;font-weight:700} .pk{font-size:12px;color:#888;font-weight:400}
.noprice{color:#a06a00} .title{font-weight:600;margin:4px 0;font-size:15px}
.sub2{color:#888;font-size:13px;margin-bottom:6px}
.new{background:#0a7d33;color:#fff;font-size:12px;padding:2px 8px;border-radius:10px;vertical-align:middle}
table.spec{border-collapse:collapse;margin:6px 0;width:100%}
table.spec td{border:1px solid #eee;padding:4px 8px;font-size:13px;vertical-align:top}
table.spec td:first-child{color:#666;width:170px}
.ok-txt{color:#0a7d33} .link{font-size:13.5px}
.crit{background:#f7f9fc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 16px;margin:12px 0;font-size:13px}
.note{color:#555;font-size:13px;background:#fff7e6;border:1px solid #ffe0a3;border-radius:8px;padding:9px 13px;margin:10px 0}
.funnel{border-collapse:collapse;margin:8px 0} .funnel td{border:1px solid #dde3ea;padding:5px 10px;font-size:13px}
"""

CRIT_BLOCK = f"""<div class=crit><b>Критерии мониторинга (жёсткие):</b>
Avante <b>CN7 рестайлинг «더 뉴»</b> (дореформенные «올 뉴» и старые AD/MD/HD отсекаются; N / N-Line — тоже) ·
модельный год <b>{MODEL_YEAR}</b>, год выпуска <b>{PROD_YEAR}</b> ·
<b>1.6 бензин ≤ {MAX_HP} л.с.</b> (по справочнику Avante 1.6 (G) = 123 л.с.) ·
пробег <b>≤ {MILEAGE_MAX:,}</b> км ·
цвет <b>белый / серый / чёрный</b> ·
<b>без единой страховой выплаты</b>, без ДТП/тотала/потопа, без ремонта силовых, косметический окрас ≤ {PAINT_MAX} ·
бюджет не фильтруется (лот без цены — тоже подходящий).</div>""".replace(",", " ")


def build_full_report(passed: list, new_hashes: set, funnel: dict, n_total: int,
                      rates: dict, ts: str) -> str:
    cards = "\n".join(card_html(a, a["hash"] in new_hashes) for a in passed)
    frows = "".join(f"<tr><td>{escape(CRIT_LABELS.get(k, k))}</td>"
                    f"<td style='text-align:right'><b>{v}</b></td></tr>" for k, v in funnel.items())
    return f"""<!doctype html><meta charset=utf-8>
<title>Мониторинг Avante CN7 — HeyDealer</title><style>{CSS}</style>
<h1>Мониторинг Hyundai Avante (CN7) · HeyDealer</h1>
<div class=sub>Прогон: <b>{ts}</b> · курс 1000 ₩ = {rates['krw_rub']*1000:.2f} ₽ · новых лотов: <b>{len(new_hashes)}</b></div>
{CRIT_BLOCK}
<h2>Воронка отбора (проверено карточек: {n_total})</h2>
<table class=funnel>{frows}</table>
<h2>Подходящие лоты: {len(passed)}</h2>
{cards or '<p><i>Сейчас подходящих лотов нет.</i></p>'}
<div class=sub style="margin-top:18px">Фото встроены в файл (data URI) — отчёт открывается автономно, превью кликабельны.
Данные — из API дилерской сессии HeyDealer, ничего не выдумано.</div>
{LIGHTBOX_HTML}"""


def build_alert_report(new_lots: list, rates: dict, ts: str) -> str:
    cards = "\n".join(card_html(a, True) for a in new_lots)
    return f"""<!doctype html><meta charset=utf-8>
<title>НОВЫЕ лоты Avante CN7 — {ts}</title><style>{CSS}</style>
<h1>🔔 Новые подходящие лоты: {len(new_lots)}</h1>
<div class=sub>Прогон: <b>{ts}</b> · курс 1000 ₩ = {rates['krw_rub']*1000:.2f} ₽</div>
{CRIT_BLOCK}
{cards}
<div class=sub style="margin-top:18px">Только новые лоты этого прогона. Фото встроены (data URI), ссылки и превью кликабельны.</div>
{LIGHTBOX_HTML}"""


def build_xlsx(path: Path, passed: list, new_hashes: set) -> None:
    wb = Workbook(); ws = wb.active; ws.title = "avante"
    headers = ["NEW", "Лот", "Гос.номер", "Название", "Мод.год", "Год_выпуска", "Пробег_км",
               "Мощность_лс", "Объём_cc", "Цвет", "Страховая", "Цена_KRW",
               "Цена_под_ключ_RUB", "Локация", "Ссылка"]
    ws.append(headers)
    for i in range(1, len(headers) + 1):
        ws.cell(row=1, column=i).font = Font(bold=True)
        ws.cell(row=1, column=i).fill = PatternFill("solid", fgColor="F0F3F7")
    for a in passed:
        ws.append(["NEW" if a["hash"] in new_hashes else "", a["hash"], a["car_number"],
                   a["full_name"], a["model_year"], a["reg_year"], a["mileage"], a["hp"], a["cc"],
                   a["color"], f"{a['accident_summary']} (0 выплат)",
                   a.get("price_krw") or "не указана", a.get("total_rub") or "—",
                   a.get("location"), a["url"]])
        c = ws.cell(row=ws.max_row, column=len(headers))
        c.hyperlink = a["url"]; c.value = "открыть ↗"
        c.font = Font(color="1560D0", underline="single")
        if a["hash"] in new_hashes:
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color="0A7D33")
    for i, w in enumerate([6, 11, 12, 36, 9, 12, 10, 11, 9, 16, 26, 14, 17, 15, 12], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(ws.max_row, 2)}"
    for r in ws.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(vertical="top")
    wb.save(path)


# ═══════════════════ оповещение ═══════════════════
def build_summary(new_lots: list, ts: str) -> str:
    lines = [f"🔔 <b>Новые лоты Avante CN7</b> — {len(new_lots)} шт. ({escape(ts)})", ""]
    for a in new_lots:
        price = f"{fmt(a['total_rub'])} ₽ под ключ" if a.get("total_rub") else "цена не указана"
        lines += [
            f"🚗 <a href=\"{escape(a['url'])}\"><b>{escape(a['full_name'])}</b></a>",
            f"   {a['model_year']} мод. / выпуск {escape(str(a['reg_date']))} · "
            f"{fmt(a['mileage'])} км · {a['hp']} л.с. · {escape(str(a['color']))}",
            f"   💰 {escape(price)}",
            f"   ✅ {escape(str(a['accident_summary']))} — 0 выплат, без ДТП",
            "",
        ]
    return "\n".join(lines)


def notify(new_lots: list, alert_path: Path, ts: str, dry_run: bool) -> None:
    import telegram_publisher as tg
    chat = config.ADMIN_CHAT_ID or config.TELEGRAM_CHAT_ID
    summary = build_summary(new_lots, ts)

    size_mb = alert_path.stat().st_size / 1024 / 1024
    log.info(f"alert-файл: {alert_path.name} ({size_mb:.2f} МБ)")
    if size_mb > 45:   # запас до лимита Telegram в 50 МБ
        log.warning("alert-файл близок к лимиту Telegram — отправляю только сводку")
        tg.send_to_telegram(summary, dry_run=dry_run, chat_id=chat, parse_mode="HTML")
        return

    ok_doc = tg.send_document_to_telegram(str(alert_path), caption=summary, dry_run=dry_run,
                                          chat_id=chat, parse_mode="HTML")
    if not ok_doc:
        # подпись могла не пройти по лимиту 1024 символов — шлём сводку отдельно
        log.warning("sendDocument не прошёл — отправляю сводку отдельным сообщением")
        tg.send_to_telegram(summary, dry_run=dry_run, chat_id=chat, parse_mode="HTML")


# ═══════════════════ прогон ═══════════════════
def run_once(dry_run: bool = False) -> dict:
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M")
    log.info(f"=== прогон {ts} (dry_run={dry_run}) ===")

    cards = collect_cards()
    rates = fetch_cbr_rates()
    analysed = [analyse(c, rates) for c in cards]
    passed = [a for a in analysed if a["passed"]]
    passed.sort(key=lambda a: (a["total_rub"] is None, a["total_rub"] or 0))

    keys = ["cn7", "facelift", "not_n", "model_year", "prod_year", "fuel", "engine_16",
            "hp", "mileage", "color", "no_insurance", "no_total_flood", "no_structural", "paint_ok"]
    funnel, pool = {}, analysed
    for k in keys:
        pool = [a for a in pool if a["checks"][k]]
        funnel[k] = len(pool)

    state = load_state()
    new_lots = [a for a in passed if a["hash"] not in state]
    new_hashes = {a["hash"] for a in new_lots}
    log.info(f"подходящих: {len(passed)} | новых: {len(new_lots)}")

    (HERE / "report.html").write_text(
        build_full_report(passed, new_hashes, funnel, len(analysed), rates, ts), encoding="utf-8")
    build_xlsx(HERE / "report.xlsx", passed, new_hashes)

    alert_path = None
    if new_lots:
        alert_path = HERE / f"alert_{stamp}.html"
        alert_path.write_text(build_alert_report(new_lots, rates, ts), encoding="utf-8")
        notify(new_lots, alert_path, ts, dry_run)
        now_iso = datetime.now(timezone.utc).isoformat()
        for a in new_lots:
            state[a["hash"]] = {"first_seen": now_iso, "notified_at": now_iso,
                                "car_number": a["car_number"], "url": a["url"]}
        if not dry_run:
            save_state(state)
        log.info(f"оповещение отправлено, состояние дополнено ({len(new_lots)} шт.)")
    else:
        log.info("новых лотов нет — не оповещаю")

    return {"checked": len(analysed), "passed": len(passed), "new": len(new_lots),
            "alert": str(alert_path) if alert_path else None, "funnel": funnel}


def main() -> int:
    ap = argparse.ArgumentParser(description="Мониторинг Avante CN7 на HeyDealer (один прогон)")
    ap.add_argument("--dry-run", action="store_true", help="не отправлять в Telegram и не писать состояние")
    args = ap.parse_args()
    try:
        res = run_once(dry_run=args.dry_run)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    except Exception as e:
        # Один сбой не должен ронять расписание: логируем, сообщаем в служебный
        # чат и выходим с 0 — следующий прогон по cron состоится как обычно.
        log.error(f"прогон упал: {e}\n{traceback.format_exc()}")
        try:
            from notifier import notify_error
            notify_error(f"мониторинг Avante: прогон не выполнен — {e}")
        except Exception as inner:
            log.error(f"не удалось отправить уведомление об ошибке: {inner}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
