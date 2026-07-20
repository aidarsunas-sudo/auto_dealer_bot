# -*- coding: utf-8 -*-
"""
test_used_car_scraper.py — самотест извлечения фото со страницы списка
kbchachacha (без pytest, в стиле остальных self-test'ов — python
test_used_car_scraper.py).

Разбор "последнего блокера перед публикациями" (2026-07-20): у
kbchachacha targeted/broad-режима (used_car_scraper.py — общий боевой путь
для kbchachacha_target_adapter.py И main.py) НЕ было фото вообще —
find_lead_post()/prepare_post_fields() отклоняли всё на "нет фото".
Детальная страница объявления (car/detail.kbc) защищена reCAPTCHA (не
проходит даже headless-браузер, см. kb_detail_scraper.py) — специально
обходить не пытаемся. Фото берутся прямо со страницы СПИСКА (карусель
превью в каждой карточке) — этот тест на синтетическом HTML-фрагменте,
повторяющем реальную структуру (снято вживую 2026-07-20 на Chevrolet Trax),
без завязки на живую сеть.
"""

import sys

from used_car_scraper import parse_used_car_list


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _area_html(car_seq: str, n_photos: int, title: str = "한국GM 트랙스 크로스오버 1.2 가솔린 2WD RS") -> str:
    """Один блок div.area[data-car-seq] — повторяет реальную структуру
    (снята вживую 2026-07-20), только не все поля (достаточно для теста
    фото + базовых полей)."""
    imgs = "".join(
        f'<span class="item__img"><img alt="" onerror="this.src=\'/images/common/noimage.jpg\';" '
        f'src="https://img.kbchachacha.com/IMG/carimg/l/img10/img2850/{car_seq}_{i}.jpeg?width=720"/></span>'
        for i in range(n_photos)
    )
    return f"""
    <div class="area" data-car-seq="{car_seq}">
      <div class="thumnail owl-carousel">{imgs}</div>
      <div class="con">
        <div class="item">
          <strong class="tit">{title}</strong>
          <div class="data-line"><span>23/05식(24년형)</span><span>153,860km</span><span>경기</span></div>
          <span class="price">1,590<span class="unit">만원</span></span>
        </div>
      </div>
    </div>
    """


def run() -> None:
    results = []

    # 1. Обычная карточка с 4 фото — все извлекаются, реальные CDN-ссылки
    html = f"<html><body>{_area_html('28506522', 4)}</body></html>"
    cars = parse_used_car_list(html)
    passed = len(cars) == 1 and len(cars[0]["photos"]) == 4 and all(
        p.startswith("https://img.kbchachacha.com/") for p in cars[0]["photos"]
    )
    results.append(passed)
    _safe_print(f"[{'OK' if passed else 'FAIL'}] 4 фото извлечены: {cars[0]['photos'] if cars else '(нет карточек)'}")

    # 2. Карточка с 1 фото (не у всех объявлений полная карусель) — не должно падать
    html2 = f"<html><body>{_area_html('28213093', 1)}</body></html>"
    cars2 = parse_used_car_list(html2)
    passed2 = len(cars2) == 1 and len(cars2[0]["photos"]) == 1
    results.append(passed2)
    _safe_print(f"[{'OK' if passed2 else 'FAIL'}] 1 фото — тоже извлекается корректно: {cars2[0]['photos'] if cars2 else '?'}")

    # 3. Карточка совсем без фото (защита от KeyError/падения — просто пустой список)
    html3 = """
    <div class="area" data-car-seq="99999999">
      <div class="con"><div class="item"><strong class="tit">Test No Photo</strong>
      <div class="data-line"><span>23/05식(24년형)</span><span>1km</span><span>x</span></div>
      <span class="price">100<span class="unit">만원</span></span></div></div>
    </div>
    """
    cars3 = parse_used_car_list(f"<html><body>{html3}</body></html>")
    passed3 = len(cars3) == 1 and cars3[0]["photos"] == []
    results.append(passed3)
    _safe_print(f"[{'OK' if passed3 else 'FAIL'}] Без фото — пустой список, не падает: {cars3[0]['photos'] if cars3 else '?'}")

    # 4. Дедуп — если одна и та же ссылка повторится (защита от дублей в вёрстке)
    dup_html = (
        '<div class="area" data-car-seq="1"><div class="thumnail">'
        '<span class="item__img"><img src="https://img.kbchachacha.com/x.jpg?width=720"/></span>'
        '<span class="item__img"><img src="https://img.kbchachacha.com/x.jpg?width=720"/></span>'
        '</div><div class="con"><div class="item"><strong class="tit">Dup Test</strong>'
        '<div class="data-line"><span>23/05식(24년형)</span><span>1km</span><span>x</span></div>'
        '<span class="price">100<span class="unit">만원</span></span></div></div></div>'
    )
    cars4 = parse_used_car_list(f"<html><body>{dup_html}</body></html>")
    passed4 = len(cars4) == 1 and cars4[0]["photos"] == ["https://img.kbchachacha.com/x.jpg?width=720"]
    results.append(passed4)
    _safe_print(f"[{'OK' if passed4 else 'FAIL'}] Дубли ссылок схлопываются: {cars4[0]['photos'] if cars4 else '?'}")

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
