# -*- coding: utf-8 -*-
"""
glovis_scraper.py — search_target_model() адаптер для Glovis Autobell
(autobell.co.kr, "스마트옥션") для model_search.py.

В отличие от AJ, у Glovis есть server-side фильтры по году/пробегу и
свободнотекстовый поиск (searchTerm) — используем их напрямую, полный обход
каталога не нужен.

ВАЖНО — accident_free НЕДОСТУПЕН: эндпоинт с историей ДТП/диагностикой для
этой площадки ещё не найден (см. glovis_test_scraper.py) — car["accident_free"]
всегда None (неизвестно). Если запрошен --accident-free, model_search.py
консервативно ИСКЛЮЧИТ все объявления Glovis (не проверить — не значит без
ДТП, тот же принцип, что и для OnBid).

ВАЖНО — фото: только ОДНО фото на объявление (delePhtUrl), и то не для всех —
пути вида "MPARK_BATCH_INSERT/..." не разрешаются ни на одном проверенном CDN
(см. build_photo_url), для них photos будет пустым.

ВАЖНО — логин: сайт требует реальный (платный) брокерский аккаунт. Модуль
НИКОГДА не логинится сам по себе молча — см. login_gate.py. Если сохранённая
сессия (glovis_session.json) невалидна/истекла, поднимается LoginRequiredError
— model_search.py требует явного подтверждения пользователя на ЭТОТ запуск
(--confirm-login glovis) перед живым Playwright-логином с
credentials.json["glovis"].

Логин/сессия/построение URL фото — перенесены как есть из
glovis_test_scraper.py (уже проверено вживую), только обёрнуто в общий
интерфейс search_target_model(target, log) -> list.
"""

import json
import logging
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from brand_names_ko import model_to_korean, to_korean
from engine_filter import FUEL_NAME_KO, engine_fuel_hint
from login_gate import LoginRequiredError
from model_specs import find_spec

SESSION_PATH = Path(__file__).resolve().parent / "glovis_session.json"
CREDENTIALS_PATH = Path(__file__).resolve().parent / "credentials.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LIST_API_URL = "https://autobell.co.kr/api/buycar/selectGeneralListLogin.do"
COUNT_PER_PAGE = 30
MAX_PAGES = 20

BASE_SEARCH_BODY = {
    "isMobile": "N", "cho": ["1", "2"], "carType": [],
    "crMnfcCd": "", "crMnfcNm": "", "crMdlCd": "", "crMdlNm": "",
    "crDtlMdlCd": [], "crDtlMdlNm": [], "crClsCd": [], "crClsNm": [],
    "crDtlClsCd": [], "crDtlClsNm": [], "selectedModels": [],
    "yearRange": {"min": 1990, "max": 2027, "labelmax": "최대", "labelmin": "최소"},
    "distanceRange": {"min": 0, "max": None, "labelmax": "최대", "labelmin": "최소"},
    "priceRange": {"min": 0, "max": 9999999, "labelmax": "최대", "labelmin": "최소"},
    "loc": [], "carOption": [], "color": [], "fuel": [], "mss": [], "svc": [],
    "brndGrpTp": "", "brndGrpId": "", "brndCmpnId": "", "mrktCmplxSeqNo": "", "acdt": [],
    "searchTerm": "", "currentPage": 1, "countPerPage": COUNT_PER_PAGE, "countPerMore": COUNT_PER_PAGE,
    "type": "normal", "viewType": "0", "tab": "0", "subTab": "", "detailTab": "",
    "listType": "card", "homeSvc": "N", "ewInsuSvc": "N", "order": "upd_dt", "selected": [],
    "selectedList": [], "advConditionStack": [], "smpConditionStack": [], "filterTab": "0",
}


def _login_and_save_session(creds: dict, log: logging.Logger) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT)
        page = ctx.new_page()
        try:
            page.goto("https://autobell.co.kr/autoAuction/auctionGate", wait_until="domcontentloaded", timeout=30000)
        except Exception as error:
            log.warning(f"glovis: goto warning: {error}")
        time.sleep(3)

        page.click("text=로그인", timeout=8000)
        time.sleep(2)
        page.fill("#user-id_common", creds["id"])
        page.fill("#user-pw", creds["password"])
        page.keyboard.press("Enter")
        time.sleep(3)

        body = page.inner_text("body", timeout=15000)
        if "로그아웃" not in body:
            browser.close()
            raise RuntimeError("glovis: логин не удался — проверь credentials.json")

        ctx.storage_state(path=str(SESSION_PATH))
        browser.close()


def _build_requests_session() -> requests.Session:
    session_data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    cookies = {c["name"]: c["value"] for c in session_data["cookies"] if "autobell.co.kr" in c["domain"]}

    sess = requests.Session()
    sess.cookies.update(cookies)
    sess.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Authorization": cookies["accessToken"],
    })
    return sess


def _session_is_valid() -> bool:
    if not SESSION_PATH.exists():
        return False
    try:
        sess = _build_requests_session()
        resp = sess.post(LIST_API_URL, json={**BASE_SEARCH_BODY, "countPerPage": 1}, timeout=15)
        return resp.status_code == 200
    except Exception:
        return False


def _ensure_session(target: dict, log: logging.Logger) -> requests.Session:
    if not _session_is_valid():
        if not target.get("allow_fresh_login"):
            raise LoginRequiredError(
                "glovis: сохранённая сессия отсутствует/истекла — нужен живой логин на "
                "реальный аккаунт. Подтверди явно на этот запуск: model_search.py ... "
                "--confirm-login glovis"
            )
        log.info("glovis: сессия истекла/отсутствует, логинюсь заново (подтверждено пользователем)")
        creds = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))["glovis"]
        _login_and_save_session(creds, log)
    else:
        log.info("glovis: переиспользую сохранённую сессию")
    return _build_requests_session()


def build_photo_url(path_raw: str | None) -> str | None:
    """См. докстринг файла — только "/picture/..." пути реально разрешаются."""
    if not path_raw:
        return None
    if path_raw.startswith("/picture/"):
        return f"https://static.glovis.net{path_raw}"
    return None


def _normalize_item(item: dict, target: dict) -> dict:
    photo_url = build_photo_url(item.get("delePhtUrl"))
    trim = " ".join(str(x) for x in (item.get("clsNm"), item.get("dtlClsNm")) if x) or (item.get("crNm") or "")
    year = int(item["frmYyyy"]) if str(item.get("frmYyyy") or "").isdigit() else None

    title_for_spec = f"{target['brand']} {target['model']} {target.get('engine', '')} {trim}"
    spec = find_spec(title_for_spec, year)

    return {
        "brand": target["brand"],
        "model": target["model"],
        "trim": trim,
        "external_id": item.get("dlrPrdId") or item.get("crId"),
        "year": year,
        "mileage_km": item.get("drvDist"),
        "cc": spec.cc if spec else None,
        "hp": spec.hp if spec else None,
        "fuel_code": spec.fuel if spec else None,
        "specs": f"{spec.cc} см³, {spec.hp} л.с." if spec else "",
        "price": (item.get("slAmt") or 0) * 10000,  # 만원 -> вон
        "url": f"https://autobell.co.kr/autoAuction/auctionGate?prdId={item.get('dlrPrdId')}",
        "source": "glovis",
        "photos": [photo_url] if photo_url else [],
        "accident_free": None,  # см. докстринг файла — эндпоинт ещё не найден
        "damage_notes": None,
        "fuel_ko": item.get("fuelNm"),
        "color": None,  # структурного поля цвета в ответе не найдено
    }


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на Glovis Autobell по целевой модели. Использует
    server-side фильтры (searchTerm, yearRange, distanceRange) вместо
    полного обхода каталога. Возвращает список, совместимый с общей схемой
    model_search.py.
    """
    sess = _ensure_session(target, log)

    brand_ko = to_korean(target["brand"])
    model_ko = model_to_korean(target["model"])
    search_term = f"{brand_ko} {target['model']}".strip()

    year_from = target.get("year_from", 1990)
    year_to = target.get("year_to", 2027)
    mileage_max = target.get("mileage_max")

    body = dict(BASE_SEARCH_BODY)
    body["searchTerm"] = search_term
    body["yearRange"] = {"min": year_from, "max": year_to, "labelmax": "최대", "labelmin": "최소"}
    if mileage_max is not None:
        body["distanceRange"] = {"min": 0, "max": mileage_max, "labelmax": "최대", "labelmin": "최소"}

    fuel_hint = engine_fuel_hint(target.get("engine"))

    brand_candidates = [target["brand"].strip().lower(), brand_ko.lower()]
    model_candidates = [target["model"].strip().lower(), model_ko.lower()]

    all_items = []
    current_page = 1
    total_raw = 0
    while current_page <= MAX_PAGES:
        body["currentPage"] = current_page
        try:
            resp = sess.post(LIST_API_URL, json=body, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            log.error(f"glovis: страница {current_page}: ошибка запроса ({error})")
            break

        product_list = data.get("data", {}).get("prdLst", [])
        total_count = data.get("data", {}).get("totalCount", 0)
        if not product_list:
            break

        total_raw += len(product_list)
        for item in product_list:
            title = f"{item.get('mnfcNm', '')} {item.get('mdlNm', '')} {item.get('crNm', '')}".lower()
            if not any(b in title for b in brand_candidates):
                continue
            if not any(m in title for m in model_candidates):
                continue
            if fuel_hint is not None and item.get("fuelNm") != FUEL_NAME_KO.get(fuel_hint):
                continue
            all_items.append(_normalize_item(item, target))

        if total_raw >= total_count:
            break
        current_page += 1
        time.sleep(0.5)

    log.info(f"glovis: просмотрено {total_raw}, {target['brand']} {target['model']} -> "
              f"найдено {len(all_items)} (после фильтров)")
    return all_items
