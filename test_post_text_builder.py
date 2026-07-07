# -*- coding: utf-8 -*-
"""
test_post_text_builder.py — самотест post_text_builder.py (в стиле
test_post_validator.py — python test_post_text_builder.py, без pytest).

Цель — доказать, что "price alert" ("Цена изменилась", main.py:
process_used_car_search) и "lead post" (main_pipeline.py) больше НЕ могут
разойтись в поведении, потому что оба вызывают ОДНУ И ТУ ЖЕ
post_text_builder.prepare_post_fields() (см. её докстринг про инцидент
2026-07, когда именно расхождение между build_lead_post и
build_price_alert_post позволило корейскому тексту/car_seq-в-заголовке/
отсутствию фото уйти в канал через "Цена изменилась", хотя lead-путь уже
был исправлен).

Кейсы:
    1. Точная форма старого бага (car["model"] = сырой корейский title,
       car["trim"] = car_seq числом, без фото) — ОБА builder'а должны
       отклонить (None).
    2. Та же машина, но в ИСПРАВЛЕННОЙ форме (см. used_car_scraper.py:
       model = человекочитаемое имя, trim = "", фото есть) — ОБА builder'а
       должны успешно построить текст, БЕЗ корейских символов внутри.
"""

import sys

from customs_calculator import DEFAULT_RATES
from lead_post_builder import build_lead_post
from price_alert_builder import build_price_alert_post
from post_validator import HANGUL_RE


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


RATES = dict(DEFAULT_RATES)

# Точная форма реального прод-бага (Mercedes GLB X247, carSeq=28528478) —
# КАК ОНА БЫЛА ДО фикса used_car_scraper.py: model — сырой корейский title,
# trim — car_seq числом (строкой), photos — отсутствует вовсе.
BROKEN_SHAPE_CAR = {
    "brand": "",
    "model": "벤츠 GLB(X247) 200 d",
    "trim": "28528478",
    "external_id": "28528478",
    "year": 2022,
    "mileage_km": 70785,
    "cc": 1950,
    "hp": 150,
    "fuel_code": "diesel",
    "price": 44900000,
    "specs": "1950 см³, 150 л.с.",
    "url": "https://www.kbchachacha.com/public/car/detail.kbc?carSeq=28528478",
    "source": "kbchachacha",
    # "photos" отсутствует вовсе — как и было у used_car_scraper.py раньше
}

# Та же машина в ИСПРАВЛЕННОЙ форме (см. used_car_scraper.py: model = spec.label,
# trim = "", photos заполнены) — то, что должно получаться СЕЙЧАС.
FIXED_SHAPE_CAR = {
    **BROKEN_SHAPE_CAR,
    "model": "Mercedes-Benz GLB 200d",
    "trim": "",
    "photos": [f"https://example.com/{i}.jpg" for i in range(5)],
}


def run() -> None:
    results = []

    # 1. Старая форма бага — ОБА builder'а должны отклонить.
    lead_broken = build_lead_post(BROKEN_SHAPE_CAR, RATES)
    ok1 = lead_broken is None
    results.append(ok1)
    _safe_print(f"[{'OK' if ok1 else 'FAIL'}] build_lead_post() на сломанной форме -> "
                f"{'None (отклонено)' if lead_broken is None else 'ПОСТ СОЗДАН — БАГ'}")

    price_alert_broken = build_price_alert_post(BROKEN_SHAPE_CAR, "updated", RATES)
    ok2 = price_alert_broken is None
    results.append(ok2)
    _safe_print(f"[{'OK' if ok2 else 'FAIL'}] build_price_alert_post() на сломанной форме -> "
                f"{'None (отклонено)' if price_alert_broken is None else 'ПОСТ СОЗДАН — БАГ'}")

    # 2. Исправленная форма (used_car_scraper.py после фикса) — ОБА должны пройти,
    # и без единого корейского символа в тексте.
    lead_fixed = build_lead_post(FIXED_SHAPE_CAR, RATES)
    ok3 = lead_fixed is not None and not HANGUL_RE.search(lead_fixed)
    results.append(ok3)
    _safe_print(f"[{'OK' if ok3 else 'FAIL'}] build_lead_post() на исправленной форме -> "
                f"{'построен, без хангыля' if ok3 else 'ПРОВАЛ'}")

    price_alert_fixed = build_price_alert_post(FIXED_SHAPE_CAR, "updated", RATES)
    ok4 = (price_alert_fixed is not None and not HANGUL_RE.search(price_alert_fixed)
           and "Цена изменилась" in price_alert_fixed)
    results.append(ok4)
    _safe_print(f"[{'OK' if ok4 else 'FAIL'}] build_price_alert_post() на исправленной форме -> "
                f"{'построен, без хангыля, содержит \"Цена изменилась\"' if ok4 else 'ПРОВАЛ'}")

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
        print()
        print("--- Текст build_price_alert_post() на исправленной форме ---")
        _safe_print(price_alert_fixed)
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
