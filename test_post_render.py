# -*- coding: utf-8 -*-
"""
test_post_render.py — самотест post_render.render_post() (python test_post_render.py,
без pytest и без сети). Курсы и параметры формулы цены фиксированы внутри теста.

Реальные данные:
  - lead: сохранённый лот Encar #42047647 (Renault XM3 1.6 GTe RE, 2022, 14 140 км) из
    model_search_results/ — если файла нет, проверка пропускается (SKIP);
  - auction: лот HeyDealer nEB6WaPQ (Kia Ray Signature, пост 5286) — поля как в lot_raw.json
    (manufactured_date 2022-12-09, mileage 20266, auction.end_at '2026-10-09 13:17:09')
    и offer_config (двигатель 998 см³ / 76 л.с., ставки 12 и 13 млн ₩).
"""

import json
import re
from pathlib import Path

import config
import post_render as pr
from lead_post_builder import build_lead_post
from post_format_schema import validate_post_format
from post_render import PostRenderError, render_post

RATES = {"krw_rub": 0.0625, "eur_rub": 95.33, "usd_rub": 85.0, "cny_rub": 12.0, "jpy_rub": 0.55, "_source": "cbr"}
PARAMS = {"KOREA_COSTS_PCT": 0.14, "BANK_COMMISSION_PCT": 0.005, "LOGISTICS_RUB": 320000.0,
          "BROKER_FEE_RUB": 100000.0, "MARGIN_RUB": 150000.0, "IMPORTER_TYPE": "phys"}
XM3_PATH = Path(__file__).resolve().parent / "model_search_results" / "renault_xm3_petrol_2022_2024_30000_accidentfree" / "encar_42047647.json"

# Прежний lead_post_template.txt (до переноса в post_render) — для проверки, что структура не изменилась.
OLD_LEAD_TEMPLATE = """🔥 {title} — уже можно забронировать!

📋 Характеристики:
📅 Год: {year}
🔧 Двигатель: {engine_display}
⚙️ КПП: {transmission}
🛣 Пробег: {mileage_km}

💰 {price_label}: {total_rub} ₽

👉 Пишите мне — посчитаю точный расчёт под ваш бюджет: {cta_bot}

{hashtags}
"""

KIA_RAY_HISTORY = ("страховых выплат по ДТП нет, замер толщины ЛКП в норме; 2 смены владельца (август и сентябрь 2025 г.). "
                   "По осмотру: 2 наружные панели с дефектами, царапина переднего бампера, царапины на 3 дисках.")


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def _raises(fn) -> str | None:
    try:
        fn()
    except PostRenderError as error:
        return str(error)
    return None


def _lead_lot(**kw) -> dict:
    lot = {"brand": "Kia", "model": "K3", "trim": "1.6", "year": 2023, "month": 3, "mileage_km": 14635, "cc": 1598, "hp": 123,
           "fuel_code": "petrol", "transmission": "자동", "price": 19_300_000, "source": "kbchachacha",
           "url": "https://www.kbchachacha.com/x", "photos": ["https://example.com/1.jpg"]}
    lot.update(kw)
    return lot


def _ray(**kw) -> dict:
    lot = {"title": "Kia Ray Signature (The New Ray)", "year": 2022, "month": 12, "year_note": " (модельный год 2023)",
           "mileage_km": 20266, "cc": 998, "hp": 76, "fuel_code": "petrol", "transmission": "автомат",
           "price": 12_000_000, "cap_price": 13_000_000, "history": KIA_RAY_HISTORY, "auction_end_at": "2026-10-09 13:17:09"}
    lot.update(kw)
    return lot


def run() -> None:
    results = []
    saved = {name: getattr(config, name) for name in PARAMS}
    for name, value in PARAMS.items():
        setattr(config, name, value)
    try:
        # ── lead на реальном лоте ────────────────────────────────────────
        if XM3_PATH.exists():
            car = json.loads(XM3_PATH.read_text(encoding="utf-8"))
            post = render_post(car, "lead", RATES)
            lines = post.text.splitlines()
            results.append(_check("lead (реальный лот XM3): заголовок «🔥 Renault XM3 1.6 GTe RE — уже можно забронировать!»",
                                  lines[0] == "🔥 Renault XM3 1.6 GTe RE — уже можно забронировать!"))
            results.append(_check("lead: двигатель с десятичной запятой «1,6 л (1598 см³), 123 л.с., бензин»",
                                  "🔧 Двигатель: 1,6 л (1598 см³), 123 л.с., бензин" in post.text))
            results.append(_check("lead: год 2022, пробег «14 140 км», КПП «не указано» (Encar не отдаёт)",
                                  "📅 Год: 2022" in post.text and "🛣 Пробег: 14 140 км" in post.text and "⚙️ КПП: не указано" in post.text))
            results.append(_check("lead: хэштеги — один набор #автоизкореи #подзаказ #казань #renaultxm3",
                                  lines[-1] == "#автоизкореи #подзаказ #казань #renaultxm3"))
            results.append(_check("lead: блоков «История» и «Торги завершаются» нет, слова «чистая» нет",
                                  "📄" not in post.text and "⏳" not in post.text and "чистая" not in post.text.lower()))
            results.append(_check("lead: проходит формат канала, цена одной цифрой", bool(validate_post_format(post.text))
                                  and re.search(r"💰 Стоимость под ключ в Казани: [\d ]+ ₽\n", post.text) is not None))
            # структура прежнего шаблона не изменилась (отличается только строка двигателя)
            old_text = OLD_LEAD_TEMPLATE.format(price_label=pr.PRICE_LABEL, **post.fields)
            new_swapped = post.text.replace(f"🔧 Двигатель: {post.fields['engine_line']}", f"🔧 Двигатель: {post.fields['engine_display']}")
            results.append(_check("lead: текст совпадает с прежним lead_post_template.txt, кроме строки двигателя", new_swapped == old_text))
            results.append(_check("lead_post_builder.build_lead_post() возвращает тот же текст",
                                  build_lead_post(car, RATES) == post.text))
        else:
            print("SKIP lead на реальном лоте XM3: файл", XM3_PATH.name, "не найден")

        # ── auction на реальных данных Kia Ray (5286) ───────────────────
        ray = render_post(_ray(), "auction", RATES)
        t = ray.text
        results.append(_check("auction: заголовок «🔥 … — торги идут, участвуем за вас!»",
                              t.startswith("🔥 Kia Ray Signature (The New Ray) — торги идут, участвуем за вас!\n")))
        results.append(_check("auction: «Год: 2022 (модельный год 2023)», двигатель «1,0 л (998 см³), 76 л.с., бензин», КПП автомат, 20 266 км",
                              "📅 Год: 2022 (модельный год 2023)" in t and "🔧 Двигатель: 1,0 л (998 см³), 76 л.с., бензин" in t
                              and "⚙️ КПП: автомат" in t and "🛣 Пробег: 20 266 км" in t))
        results.append(_check("auction: цена при ставке 12 млн ₩ = 1 583 959 ₽, до 13 млн ₩ — 1 655 565 ₽ (формула 14% + 0,5%)",
                              "💰 Стоимость под ключ в Казани: 1 583 959 ₽ при ставке 12 млн ₩ "
                              "(итог зависит от торгов; при ставке до 13 млн ₩ — до 1 655 565 ₽)" in t))
        results.append(_check("auction: дедлайн в двух поясах «9 октября, 13:17 по Сеулу (07:17 МСК)»",
                              "⏳ Торги завершаются 9 октября, 13:17 по Сеулу (07:17 МСК)." in t))
        results.append(_check("auction: история выводится как передана, блок идёт между ценой и дедлайном",
                              f"📄 История: {KIA_RAY_HISTORY}" in t and t.index("💰") < t.index("📄") < t.index("⏳") < t.index("👉")))
        results.append(_check("auction: тот же призыв и те же хэштеги, что у lead (#kiaray из названия)",
                              f"👉 {pr.CTA_TEXT}: {pr.CTA_BOT}" in t and t.strip().endswith("#автоизкореи #подзаказ #казань #kiaray")))
        results.append(_check("auction: проходит формат канала; в тексте нет слов «утильсбор»/«ЕТС» и ссылок",
                              bool(validate_post_format(t)) and not re.search(r"утильсбор|ЕТС|https?://", t, re.I)))
        results.append(_check("auction: фото не требуются (текст собирается без photos)", "photos" not in _ray()))

        # ── история ───────────────────────────────────────────────────────
        for value, label in ((None, "нет данных"), ("", "пустая строка"), ("   ", "пробелы")):
            text = render_post(_ray(history=value), "auction", RATES).text
            results.append(_check(f"история: {label} -> блока «История» нет и «чистая» не пишется", "📄" not in text and "чистая" not in text.lower()))

        # ── дедлайн: два пояса ────────────────────────────────────────────
        results.append(_check("дедлайн: смена даты между Сеулом и Москвой указывается (02:30 Сеул = 20:30 МСК накануне)",
                              "9 октября, 02:30 по Сеулу (8 октября, 20:30 МСК)" in render_post(_ray(auction_end_at="2026-10-09 02:30:00"), "auction", RATES).text))
        results.append(_check("дедлайн: время с поясом +03:00 пересчитывается (07:17 МСК -> 13:17 Сеул)",
                              "9 октября, 13:17 по Сеулу (07:17 МСК)" in render_post(_ray(auction_end_at="2026-10-09T07:17:00+03:00"), "auction", RATES).text))
        results.append(_check("дедлайн: формат OnBid YYYYMMDDHHMMSS (Сеул)",
                              "9 октября, 13:17 по Сеулу (07:17 МСК)" in render_post(_ray(auction_end_at="20261009131700"), "auction", RATES).text))
        for value in (None, "", "скоро"):
            results.append(_check(f"дедлайн: {value!r} -> строки нет", "⏳" not in render_post(_ray(auction_end_at=value), "auction", RATES).text))

        # ── десятичная запятая ────────────────────────────────────────────
        half = render_post(_ray(price=12_500_000, cap_price=None), "auction", RATES).text
        results.append(_check("десятичная запятая: «12,5 млн ₩», без капа — без «до … млн ₩»",
                              "при ставке 12,5 млн ₩ (итог зависит от торгов)" in half and "до 13" not in half))
        results.append(_check("в тексте нигде нет десятичной точки в литрах и миллионах",
                              not re.search(r"\d\.\d\s*(л|млн)", t) and not re.search(r"\d\.\d\s*(л|млн)", half)))

        # ── название и хэштеги ────────────────────────────────────────────
        with_brand = render_post(_ray(brand="Kia", model="Ray"), "auction", RATES).text
        results.append(_check("хэштег марки из brand+model, если заданы", with_brand.strip().endswith("#kiaray")))

        # ── ошибки и предупреждения ───────────────────────────────────────
        results.append(_check("неизвестный режим -> PostRenderError", _raises(lambda: render_post(_ray(), "reserve", RATES)) is not None))
        results.append(_check("нет цены -> PostRenderError «не хватает данных»",
                              "не хватает данных" in (_raises(lambda: render_post(_ray(price=None), "auction", RATES)) or "")))
        results.append(_check("НЕИЗВЕСТНЫЙ корейский текст в названии -> PostRenderError",
                              "корейский" in (_raises(lambda: render_post(_ray(title="Kia K5 스페셜에디션"), "auction", RATES)) or "")))
        results.append(_check("известный корейский трим (노블레스) переводится: заголовок «Kia K5 Noblesse», ошибки нет",
                              render_post(_lead_lot(brand="Kia", model="K5", trim="노블레스"), "lead", RATES).text.startswith("🔥 Kia K5 Noblesse —")))
        results.append(_check("lead без фото -> PostRenderError «нет фото»",
                              "нет фото" in (_raises(lambda: render_post(_lead_lot(photos=[]), "lead", RATES)) or "")))
        results.append(_check("история со словом «утильсбор» -> текст не проходит формат канала",
                              "формат канала" in (_raises(lambda: render_post(_ray(history="утильсбор 5 200 ₽"), "auction", RATES)) or "")))
        results.append(_check("build_lead_post() для неизвестного корейского названия -> None (прежний контракт)",
                              build_lead_post(_lead_lot(brand="Kia", model="K5", trim="스페셜에디션"), RATES) is None))
        suv = render_post(_lead_lot(brand="Volkswagen", model="Touareg", trim="3.0 TDI", cc=2967, hp=286, fuel_code="diesel",
                                     price=40_000_000, year=2022, month=6), "lead", RATES)
        results.append(_check("мощность 286 л.с.: в предупреждениях коммерческий утильсбор, в тексте поста его нет",
                              any("КОММЕРЧЕСКИЙ" in w for w in suv.warnings) and "утильсбор" not in suv.text.lower()))
        onbid = render_post(_lead_lot(source="onbid"), "lead", RATES).text
        results.append(_check("OnBid: оговорка про стартовую цену в той же строке, формат канала соблюдён",
                              "₽ (расчёт от стартовой цены аукциона — итоговая может быть выше)" in onbid and bool(validate_post_format(onbid))))
        long_post = render_post(_ray(history="х" * 1100), "auction", RATES)
        results.append(_check("подпись длиннее 1024 символов -> предупреждение, не ошибка",
                              any("1024" in w for w in long_post.warnings) and len(long_post.text) > 1024))
        fallback = render_post(_ray(), "auction", {**RATES, "_source": "fallback"})
        results.append(_check("запасные курсы ЦБ -> предупреждение в RenderedPost.warnings",
                              any("курсы ЦБ недоступны" in w for w in fallback.warnings)))

        # ── источники полей ──────────────────────────────────────────────
        every = [ray, render_post(_lead_lot(), "lead", RATES), render_post(_lead_lot(source="onbid"), "lead", RATES)]
        used = {name for post_ in every for name in post_.used_fields}
        results.append(_check("каждое поле поста описано в FIELD_SOURCES (откуда берётся)", used <= set(pr.FIELD_SOURCES)))
        results.append(_check("в FIELD_SOURCES нет описаний неиспользуемых полей", set(pr.FIELD_SOURCES) <= used | {"year_note", "price_note", "history", "deadline"}))
    finally:
        for name, value in saved.items():
            setattr(config, name, value)

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
