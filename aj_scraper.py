# -*- coding: utf-8 -*-
"""
aj_scraper.py — search_target_model() адаптер для AJ/AutoHub Auction
(sellcarauction.co.kr / autohubauction.co.kr) для model_search.py.

В отличие от kbchachacha/Encar/Charancha (там есть makerCode/classCode или
похожие server-side коды марки/модели), у AJ фильтрации по марке/модели на
сервере нет вообще — только полная постраничная выдача всего каталога
(entry/list/paging), отсортированная по entryNo. Поэтому тянем ВСЕ страницы
и фильтруем по тексту carNm (марка+модель) на своей стороне — тот же приём,
что browse_broad() у kbchachacha/charancha использует для "широкого" режима,
только здесь это единственный режим.

Топливо — редкий случай: AJ отдаёт его структурированным полем (fuelNm), как
Encar/Charancha, а не только в свободном тексте.

ВАЖНО — логин: сайт требует реальный (платный) брокерский аккаунт. Модуль
НИКОГДА не логинится сам по себе молча — см. login_gate.py. Если сохранённая
сессия (aj_session.json) невалидна/истекла (JWT живёт 24 часа), поднимается
LoginRequiredError — model_search.py требует явного подтверждения
пользователя на ЭТОТ запуск (--confirm-login aj) перед живым
Playwright-логином с credentials.json["aj"].

Логин/сессия/пагинация — перенесены как есть из aj_test_scraper.py (уже
проверено вживую), только обёрнуто в общий интерфейс
search_target_model(target, log) -> list, как у остальных источников.
"""

import json
import logging
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from playwright.sync_api import sync_playwright

from brand_names_ko import model_to_korean, to_korean
from engine_filter import FUEL_NAME_KO, engine_fuel_hint
from login_gate import LoginRequiredError
from model_specs import find_spec

SESSION_PATH = Path(__file__).resolve().parent / "aj_session.json"
CREDENTIALS_PATH = Path(__file__).resolve().parent / "credentials.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LIST_API_URL = "https://api.ahsellcar.co.kr/auction/external/rest/api/v1/entry/list/paging"
PAGE_SIZE = 100
MAX_PAGES = 50  # защитный потолок (на момент разведки площадка ~1600-2000 машин)


def _login_and_save_session(creds: dict, log: logging.Logger) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT)
        page = ctx.new_page()
        try:
            page.goto("https://www.autohubauction.co.kr/", wait_until="load", timeout=45000)
        except Exception as error:
            log.warning(f"aj: goto warning: {error}")
        time.sleep(6)  # сайт на Next.js, меню дорисовывается уже после события 'load'

        page.click("text=로그인", timeout=8000)
        time.sleep(3)
        page.fill('input[placeholder="아이디 입력"]', creds["id"])
        page.fill('input[placeholder="비밀번호 입력"]', creds["password"])
        time.sleep(1)
        page.keyboard.press("Enter")
        time.sleep(4)

        body = page.inner_text("body", timeout=15000)
        if "로그아웃" not in body:
            browser.close()
            raise RuntimeError("aj: логин не удался — проверь credentials.json")

        ctx.storage_state(path=str(SESSION_PATH))
        browser.close()


def _get_access_token() -> str:
    session_data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    for cookie in session_data.get("cookies", []):
        if cookie["name"] == "user-info":
            user_info = json.loads(unquote(cookie["value"]))
            return user_info["accessToken"]
    raise RuntimeError("aj: кука user-info не найдена в сохранённой сессии")


def _build_requests_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {_get_access_token()}",
    })
    return sess


def _session_is_valid() -> bool:
    if not SESSION_PATH.exists():
        return False
    try:
        sess = _build_requests_session()
        resp = sess.post(
            LIST_API_URL,
            json={"tenant": "1", "pageSize": 1, "pageIndex": 1, "sort": "desc", "sortBy": "entryNo"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _ensure_session(target: dict, log: logging.Logger) -> requests.Session:
    if not _session_is_valid():
        if not target.get("allow_fresh_login"):
            raise LoginRequiredError(
                "aj: сохранённая сессия отсутствует/истекла — нужен живой логин на реальный "
                "аккаунт. Подтверди явно на этот запуск: model_search.py ... --confirm-login aj"
            )
        log.info("aj: сессия истекла/отсутствует, логинюсь заново (подтверждено пользователем)")
        creds = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))["aj"]
        _login_and_save_session(creds, log)
    else:
        log.info("aj: переиспользую сохранённую сессию")
    return _build_requests_session()


def _parse_year(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_item(item: dict, target: dict) -> dict:
    thumbnail = item.get("mainFileUrl")
    accident_tag = item.get("accidentTag")
    price = item.get("hopeAmt") or item.get("startAmt") or 0

    # accidentTag — это КЛАССИФИКАЦИЯ состояния, не признак "есть тег/нет
    # тега" (подтверждено вживую на реальной выборке из 80 Kia K5: значения
    # "CLEAN" (без ДТП), "REPLACE" (замена панели — лёгкий ремонт),
    # "STRUCTURAL" (структурные повреждения), null (не размечено — статус
    # неизвестен, НЕ значит "без ДТП"). Раньше здесь ошибочно стояло
    # accident_free = accident_tag is None — это засчитывало "не размечено"
    # как "без ДТП", что неверно.
    if accident_tag == "CLEAN":
        accident_free = True
    elif accident_tag in ("REPLACE", "STRUCTURAL"):
        accident_free = False
    else:
        accident_free = None

    title = item.get("carNm") or ""
    year = _parse_year(item.get("carYear"))
    title_for_spec = f"{target['brand']} {target['model']} {target.get('engine', '')} {title}"
    spec = find_spec(title_for_spec, year)

    return {
        "brand": target["brand"],
        "model": target["model"],
        "trim": title,
        "external_id": item.get("entryId") or item.get("entryNo"),
        "year": year,
        "mileage_km": item.get("mileage"),
        "cc": spec.cc if spec else None,
        "hp": spec.hp if spec else None,
        "fuel_code": spec.fuel if spec else None,
        "specs": f"{spec.cc} см³, {spec.hp} л.с." if spec else "",
        "price": price * 10000,  # 만원 -> вон
        "url": f"https://www.autohubauction.co.kr/auction/detail?entryNo={item.get('entryNo')}",
        "source": "aj",
        "photos": [thumbnail] if thumbnail else [],
        "accident_free": accident_free,
        "damage_notes": accident_tag if accident_tag and accident_tag != "CLEAN" else None,
        "fuel_ko": item.get("fuelNm"),
        "color": None,  # AJ не отдаёт цвет ни в списке, ни в проверенных полях
    }


def _fetch_all_pages(sess: requests.Session, log: logging.Logger) -> list:
    all_items = []
    for page_index in range(1, MAX_PAGES + 1):
        body = {"tenant": "1", "pageSize": PAGE_SIZE, "pageIndex": page_index, "sort": "desc", "sortBy": "entryNo"}
        try:
            resp = sess.post(LIST_API_URL, json=body, timeout=25)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as error:
            log.error(f"aj: страница {page_index}: ошибка запроса ({error})")
            break

        data = payload.get("data", {})
        car_list = data.get("list", [])
        total_rows = data.get("totalRows", 0)
        if not car_list:
            break

        all_items.extend(car_list)
        if len(all_items) >= total_rows:
            break
        time.sleep(0.3)

    return all_items


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на AJ/AutoHub Auction по целевой модели. Возвращает
    список, совместимый с общей схемой model_search.py (brand/model/trim/
    external_id/year/mileage_km/price/url/source/photos + accident_free).

    target["allow_fresh_login"] must be True для живого логина при истёкшей
    сессии — иначе поднимается login_gate.LoginRequiredError (см. докстринг
    файла).
    """
    sess = _ensure_session(target, log)
    raw_items = _fetch_all_pages(sess, log)
    log.info(f"aj: получено {len(raw_items)} объявлений (весь каталог)")

    brand_candidates = [target["brand"].strip().lower(), to_korean(target["brand"]).lower()]
    model_candidates = [target["model"].strip().lower(), model_to_korean(target["model"]).lower()]
    fuel_hint = engine_fuel_hint(target.get("engine"))

    matched = []
    for item in raw_items:
        title = (item.get("carNm") or "").lower()
        if not any(b in title for b in brand_candidates):
            continue
        if not any(m in title for m in model_candidates):
            continue

        if fuel_hint is not None and item.get("fuelNm") != FUEL_NAME_KO.get(fuel_hint):
            continue

        year = _parse_year(item.get("carYear"))
        if year is not None and not (target.get("year_from", 0) <= year <= target.get("year_to", 9999)):
            continue

        mileage_km = item.get("mileage")
        if target.get("mileage_max") is not None and mileage_km is not None and mileage_km > target["mileage_max"]:
            continue

        matched.append(_normalize_item(item, target))

    log.info(f"aj: {target['brand']} {target['model']} -> найдено {len(matched)} (после фильтров)")
    return matched
