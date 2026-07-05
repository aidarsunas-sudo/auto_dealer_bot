# -*- coding: utf-8 -*-
"""
kb_detail_scraper.py — добирает с ДЕТАЛЬНОЙ страницы объявления kbchachacha
то, чего нет в списке результатов поиска (used_car_scraper.py): ВСЕ фото
галереи и данные страховой истории (ДТП/потоп/тотальная гибель/смена
владельца).

Это НОВЫЙ, отдельный модуль — used_car_scraper.py (который уже используется
в боевом main.py через orchestrator.py) не тронут. Так безопаснее: если в
этом модуле что-то не заработает, это не сломает существующий рабочий путь.

Как это устроено:
Страница объявления (car/detail.kbc?carSeq=...) рендерится сервером сразу
(без JavaScript), но по факту содержит блок <script type="application/ld+json">
(schema.org/Product) — там уже готовые name (с годом выпуска), offers.price
и полный список фото без мусора (логотипов/заглушек). Используем его как
основной источник. Историю ДТП/потопа/владельцев там нет — берём отдельно
из пар <dt>/<dd> на странице (см. get_review в agent_editor.py — тот же
подход был проверен и на heydealer_bot/kb_parser.py в предыдущей сессии).
"""

import json
import logging
import re
import time

from playwright.sync_api import sync_playwright

logger = logging.getLogger("kb_detail_scraper")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Соответствие корейских меток истории ДТП/потопа/владельцев ключам словаря.
_HISTORY_LABEL_MAP = {
    "전손이력": "total_loss_history",
    "침수이력": "flood_damage_history",
    "용도이력": "usage_history",
    "소유자변경": "owner_changes",
}


def fetch_car_detail(url: str, screenshot: bool = False) -> dict:
    """
    Открывает детальную страницу объявления kbchachacha и достаёт:
    - car_name, year, price_krw — из JSON-LD (надёжнее regex по тексту)
    - photos — ВСЕ фото из JSON-LD (обычно 15-20 штук на объявление)
    - accident_status ("사고있음"/"사고없음"), total_loss_history,
      flood_damage_history, usage_history, owner_changes — страховая история

    Возвращает словарь с этими полями (какие удалось найти — отсутствующие
    ключи просто не появятся в результате, вызывающий код не должен
    рассчитывать, что все поля обязательно будут).
    """
    detail: dict = {"url": url}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT, locale="ko-KR")

        # На этом сайте page.goto иногда не успевает дождаться события
        # "load"/"networkidle" из-за фоновой сетевой активности (аналитика
        # и т.п.) — таймаут не значит, что страница не загрузилась. Ловим
        # его и всё равно читаем DOM (это уже проверено в прошлой сессии).
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as error:
            logger.warning(f"Таймаут при загрузке {url}, читаю DOM как есть ({error})")
        time.sleep(4)

        if screenshot:
            safe_name = re.sub(r"[^\w]+", "_", url)[-60:]
            page.screenshot(path=f"kb_debug_{safe_name}.png")

        # ── JSON-LD — основной источник имени/года/цены/фото ──
        try:
            ld_el = page.query_selector('script[type="application/ld+json"]')
            if ld_el:
                ld = json.loads(ld_el.inner_text())
                raw_name = str(ld.get("name", "")).strip()
                year_match = re.search(r"\((\d{4})년형\)", raw_name)
                if year_match:
                    detail["year"] = year_match.group(1)
                    raw_name = raw_name[: year_match.start()].strip()
                if raw_name:
                    detail["car_name"] = raw_name

                offers = ld.get("offers") or {}
                if offers.get("price"):
                    detail["price_krw"] = int(offers["price"])

                images = [u for u in (ld.get("image") or []) if isinstance(u, str) and u.startswith("http")]
                if images:
                    detail["photos"] = images
        except Exception as error:
            logger.warning(f"Не удалось разобрать JSON-LD на {url}: {error}")

        content = page.inner_text("body")

        # ── Страховая история: пары <dt>/<dd> ──
        try:
            pairs = page.eval_on_selector_all(
                "dl",
                """dls => dls.map(dl => ({
                    dts: Array.from(dl.querySelectorAll('dt')).map(e => e.innerText.trim()),
                    dds: Array.from(dl.querySelectorAll('dd')).map(e => e.innerText.trim()),
                }))""",
            )
            for pair in pairs:
                for dt_text, dd_text in zip(pair["dts"], pair["dds"]):
                    key = _HISTORY_LABEL_MAP.get(dt_text)
                    if key:
                        detail[key] = dd_text
        except Exception as error:
            logger.warning(f"Не удалось извлечь историю ДТП (dt/dd) на {url}: {error}")

        # ── "사고있음"/"사고없음" — отдельная строка сразу после метки "보험사고정보" ──
        try:
            body_lines = [line.strip() for line in content.split("\n") if line.strip()]
            if "보험사고정보" in body_lines:
                idx = body_lines.index("보험사고정보")
                if idx + 1 < len(body_lines):
                    detail["accident_status"] = body_lines[idx + 1]
        except Exception as error:
            logger.warning(f"Не удалось извлечь статус аварийности на {url}: {error}")

        browser.close()

    return detail
