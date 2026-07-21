# -*- coding: utf-8 -*-
"""
lotte_scraper.py — search_target_model() адаптер для Lotte Auto Auction
(lotteautoauction.net, "출품리스트") для model_search.py.

У площадки нет REST API вообще — список объявлений только через парсинг
HTML-таблицы (table.tbl-t02) после навигации по JS-функциям сайта
(fn_MovePage/fnSearch). Год/пробег/цвет уже разобраны в отдельные поля
(в отличие от AJ/HeyDealer, где всё в одном заголовке) — фильтруем по title
только марку/модель.

ВАЖНО — accident_free/фото НЕДОСТУПНЫ: детальная карточка (fnPopupCarView,
где может быть история ДТП/фото) не реализована (см. lotte_test_scraper.py —
сознательно отложено из-за нестабильности сайта при разведке). car["accident_free"]
всегда None, car["photos"] всегда []. При --accident-free/--min-photos
model_search.py консервативно исключит все объявления Lotte (тот же принцип,
что для OnBid/Glovis — не проверить не значит "подходит").

ВАЖНО — логин и стабильность: сайт нередко подвисает при загрузке — все
сетевые шаги (goto/пагинация) обёрнуты в повторные попытки/ожидание условия,
не одиночный вызов (перенесено как есть из lotte_test_scraper.py). Модуль
НИКОГДА не логинится сам по себе молча — см. login_gate.py. Если сохранённая
сессия (lotte_session.json) невалидна/истекла, поднимается LoginRequiredError
— model_search.py требует явного подтверждения пользователя на ЭТОТ запуск
(--confirm-login lotte) перед живым логином с credentials.json["lotte"].
"""

import json
import logging
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from brand_names_ko import model_to_korean, to_korean
from login_gate import LoginRequiredError
from model_specs import find_spec

SESSION_PATH = Path(__file__).resolve().parent / "lotte_session.json"
CREDENTIALS_PATH = Path(__file__).resolve().parent / "credentials.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MAX_PAGES = 30


def _goto_retry(page, url, tries=5, wait="domcontentloaded", timeout=30000, log=None) -> bool:
    for attempt in range(tries):
        try:
            page.goto(url, wait_until=wait, timeout=timeout)
            return True
        except Exception as error:
            if log:
                log.warning(f"lotte: goto попытка {attempt + 1}/{tries} не удалась: {error}")
            time.sleep(4)
    return False


def _wait_for_condition(page, js_expr: str, timeout: int = 20, interval: float = 1.0) -> bool:
    elapsed = 0.0
    while elapsed < timeout:
        try:
            if page.evaluate(js_expr):
                return True
        except Exception:
            pass
        time.sleep(interval)
        elapsed += interval
    return False


def _login(page, creds: dict, log: logging.Logger) -> bool:
    if not _goto_retry(page, "https://www.lotteautoauction.net/", log=log):
        log.error("lotte: не удалось загрузить главную страницу")
        return False

    try:
        page.fill("#userId", creds["id"], timeout=15000)
        page.fill("#userPwd", creds["password"], timeout=15000)
        time.sleep(1)
        page.click(".btn-login", timeout=15000)
    except Exception as error:
        log.error(f"lotte: ошибка при вводе логина/пароля: {error}")
        return False

    time.sleep(5)
    try:
        body = page.inner_text("body", timeout=15000)
    except Exception as error:
        log.error(f"lotte: не удалось прочитать страницу после логина: {error}")
        return False

    return "로그아웃" in body


def _open_exhibition_list(page, log: logging.Logger) -> bool:
    if not _goto_retry(page, "https://www.lotteautoauction.net/hp/auct/cmm/viewMain.do", log=log):
        return False
    if not _wait_for_condition(page, "typeof fn_MovePage !== 'undefined'", timeout=20):
        log.error("lotte: fn_MovePage не определилась вовремя")
        return False
    try:
        page.evaluate("fn_MovePage('1010200')")
    except Exception as error:
        log.error(f"lotte: ошибка вызова fn_MovePage: {error}")
        return False
    return _wait_for_condition(page, "document.body.innerText.includes('출품리스트')", timeout=15)


def _parse_current_page(page) -> list:
    rows_html = page.eval_on_selector_all(
        "table.tbl-t02 tr",
        """trs => trs.map(tr => {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
            return cells;
        })""",
    )

    cars = []
    for cells in rows_html:
        if len(cells) < 8:
            continue
        if not cells[0].isdigit():
            continue

        lot_number, lane, plate, title, year_raw, mileage_raw, color, grade, price_raw = (
            cells + [""] * (9 - len(cells))
        )[:9]
        title = " ".join(title.split())
        year = int(year_raw) if year_raw.isdigit() else None

        mileage_digits = re.sub(r"[^\d]", "", mileage_raw)
        mileage_km = int(mileage_digits) if mileage_digits else None

        price_digits = re.sub(r"[^\d]", "", price_raw)
        starting_price_krw = int(price_digits) * 10000 if price_digits else None  # 만원 -> вон

        cars.append({
            "lot_number": lot_number, "lane": lane, "plate": plate, "title": title,
            "year": year, "mileage_km": mileage_km, "color": color, "grade": grade,
            "starting_price_krw": starting_price_krw,
        })

    return cars


def _normalize_item(item: dict, target: dict) -> dict:
    title_for_spec = f"{target['brand']} {target['model']} {target.get('engine', '')} {item['title']}"
    spec = find_spec(title_for_spec, item.get("year"))

    return {
        "brand": target["brand"],
        "model": target["model"],
        "trim": item["title"],
        "external_id": item.get("lot_number"),
        "year": item.get("year"),
        "mileage_km": item.get("mileage_km"),
        "cc": spec.cc if spec else None,
        "hp": spec.hp if spec else None,
        "fuel_code": spec.fuel if spec else None,
        "specs": f"{spec.cc} см³, {spec.hp} л.с." if spec else "",
        "price": item.get("starting_price_krw") or 0,
        "url": "https://www.lotteautoauction.net/hp/auct/cmm/viewMain.do",
        "source": "lotte",
        "photos": [],  # см. докстринг файла — детальная карточка не реализована
        "accident_free": None,  # см. докстринг файла — история ДТП недоступна
        "damage_notes": None,
        "color": item.get("color"),
    }


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на Lotte Auto Auction ("출품리스트") по целевой модели.
    Полный обход текущего списка (нет server-side фильтра по марке/модели)
    + фильтрация по тексту заголовка на своей стороне. Возвращает список,
    совместимый с общей схемой model_search.py.

    target["allow_fresh_login"] must be True для живого логина при
    невалидной сессии — иначе поднимается login_gate.LoginRequiredError.
    """
    brand_candidates = [target["brand"].strip().lower(), to_korean(target["brand"]).lower()]
    model_candidates = [target["model"].strip().lower(), model_to_korean(target["model"]).lower()]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context_kwargs = {"user_agent": USER_AGENT}
        if SESSION_PATH.exists():
            context_kwargs["storage_state"] = str(SESSION_PATH)
        ctx = browser.new_context(**context_kwargs)
        page = ctx.new_page()

        already_in = False
        if SESSION_PATH.exists():
            if _goto_retry(page, "https://www.lotteautoauction.net/hp/auct/cmm/viewMain.do", log=log):
                try:
                    already_in = "로그아웃" in page.inner_text("body", timeout=15000)
                except Exception:
                    already_in = False

        if not already_in:
            if not target.get("allow_fresh_login"):
                browser.close()
                raise LoginRequiredError(
                    "lotte: сохранённая сессия отсутствует/истекла — нужен живой логин на "
                    "реальный аккаунт. Подтверди явно на этот запуск: model_search.py ... "
                    "--confirm-login lotte"
                )
            log.info("lotte: сессия истекла/отсутствует, логинюсь заново (подтверждено пользователем)")
            creds = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))["lotte"]
            if not _login(page, creds, log):
                browser.close()
                raise RuntimeError("lotte: логин не удался — проверь credentials.json")
            ctx.storage_state(path=str(SESSION_PATH))
        else:
            log.info("lotte: переиспользую сохранённую сессию")

        if not _open_exhibition_list(page, log):
            browser.close()
            raise RuntimeError("lotte: не удалось открыть список объявлений")

        matched = []
        total_raw = 0
        seen_lot_numbers = set()
        last_first_lot = None

        for page_num in range(1, MAX_PAGES + 1):
            if page_num > 1:
                try:
                    page.evaluate(f"fnSearch({page_num})")
                except Exception as error:
                    log.error(f"lotte: не удалось перейти на страницу {page_num}: {error}")
                    break
                check_js = (
                    "(() => { const tds = document.querySelectorAll('table.tbl-t02 tr td'); "
                    f"return tds.length > 0 && tds[0].innerText.trim() !== '{last_first_lot or ''}'; }})()"
                )
                _wait_for_condition(page, check_js, timeout=15)
                time.sleep(2)

            cars = _parse_current_page(page)
            if cars:
                last_first_lot = cars[0]["lot_number"]
            cars = [c for c in cars if c["lot_number"] not in seen_lot_numbers]
            for c in cars:
                seen_lot_numbers.add(c["lot_number"])
            if not cars:
                break

            total_raw += len(cars)
            for item in cars:
                title_lower = item["title"].lower()
                if not any(b in title_lower for b in brand_candidates):
                    continue
                if not any(m in title_lower for m in model_candidates):
                    continue

                normalized = _normalize_item(item, target)

                year = normalized.get("year")
                if year is not None and not (target.get("year_from", 0) <= year <= target.get("year_to", 9999)):
                    continue
                mileage_km = normalized.get("mileage_km")
                if target.get("mileage_max") is not None and mileage_km is not None and mileage_km > target["mileage_max"]:
                    continue

                matched.append(normalized)

            time.sleep(0.5)

        browser.close()

    log.info(f"lotte: просмотрено {total_raw}, {target['brand']} {target['model']} -> "
              f"найдено {len(matched)} (после фильтров)")
    return matched
