# -*- coding: utf-8 -*-
"""
heydealer_scraper.py — search_target_model() адаптер для личного кабинета
брокера HeyDealer (dealer.heydealer.com, "경매장") для model_search.py.

У площадки нет server-side фильтра по марке/модели (см. heydealer_test_scraper.py)
— тянем весь текущий аукционный зал и фильтруем по тексту на своей стороне,
как у AJ.

Фото — уже полная галерея прямо в списке (detail.image_urls), доп. запрос
не нужен.

Статус ДТП — НЕ булево поле, а свободнотекстовые теги (auction.tags).
ВАЖНО (найдено вживую 2026-07-07): префикс "완무" (완전무사고) НЕ гарантирует
отсутствие страховых выплат — встречаются теги вида "완무 (보험3건 · 360만원)"
(3 страховых случая на 360만원, при этом "완무"!). Ориентируемся на число
"보험N건" в тексте тега: accident_free=True только при "보험0건", иначе False
(независимо от префикса "완무"/"유사고"). Если числа в тегах нет вообще —
accident_free=None (неизвестно), не гадаем. См. _accident_free_from_tags().

ВАЖНО — логин: сайт требует реальный (платный) брокерский аккаунт. Модуль
НИКОГДА не логинится сам по себе молча — см. login_gate.py. Если сохранённая
сессия (heydealer_dealer_session.json) невалидна/истекла, поднимается
LoginRequiredError — model_search.py требует явного подтверждения
пользователя на ЭТОТ запуск (--confirm-login heydealer) перед живым
Playwright-логином с credentials.json["heydealer"].

Логин/сессия — перенесены как есть из heydealer_test_scraper.py (уже
проверено вживую), только обёрнуто в общий интерфейс
search_target_model(target, log) -> list.
"""

import json
import logging
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
import requests

from brand_names_ko import model_to_korean
from login_gate import LoginRequiredError
from model_specs import find_spec

SESSION_PATH = Path(__file__).resolve().parent / "heydealer_dealer_session.json"
CREDENTIALS_PATH = Path(__file__).resolve().parent / "credentials.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CARS_API_URL = "https://api.heydealer.com/v2/dealers/web/cars/"
MAX_PAGES = 50


def _login_and_save_session(creds: dict, log: logging.Logger) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        try:
            page.goto("https://dealer.heydealer.com", wait_until="domcontentloaded", timeout=30000)
        except Exception as error:
            log.warning(f"heydealer: goto warning: {error}")
        time.sleep(3)

        page.fill('input[name="username"]', creds["id"])
        page.fill('input[name="password"]', creds["password"])
        time.sleep(1)

        # Кнопка логина продублирована (десктоп/мобильная вёрстка) с
        # одинаковым классом — кликаем именно видимую копию через JS.
        page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button.css-jwz1sc'));
                for (const b of btns) { if (b.offsetParent !== null) { b.click(); return; } }
            }
        """)
        time.sleep(6)

        body = page.inner_text("body", timeout=15000)
        if "로그아웃" not in body:
            browser.close()
            raise RuntimeError("heydealer: логин не удался — проверь credentials.json")

        ctx.storage_state(path=str(SESSION_PATH))
        browser.close()


def _build_requests_session() -> requests.Session:
    session_data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    cookies = {c["name"]: c["value"] for c in session_data["cookies"] if "heydealer.com" in c["domain"]}

    sess = requests.Session()
    sess.cookies.update(cookies)
    sess.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return sess


def _session_is_valid() -> bool:
    if not SESSION_PATH.exists():
        return False
    try:
        sess = _build_requests_session()
        resp = sess.get(CARS_API_URL, params={"page": 1, "type": "auction"}, timeout=15)
        return resp.status_code == 200
    except Exception:
        return False


def _ensure_session(target: dict, log: logging.Logger) -> requests.Session:
    if not _session_is_valid():
        if not target.get("allow_fresh_login"):
            raise LoginRequiredError(
                "heydealer: сохранённая сессия отсутствует/истекла — нужен живой логин на "
                "реальный аккаунт. Подтверди явно на этот запуск: model_search.py ... "
                "--confirm-login heydealer"
            )
        log.info("heydealer: сессия истекла/отсутствует, логинюсь заново (подтверждено пользователем)")
        creds = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))["heydealer"]
        _login_and_save_session(creds, log)
    else:
        log.info("heydealer: переиспользую сохранённую сессию")
    return _build_requests_session()


_INSURANCE_CLAIMS_RE = re.compile(r"보험\s*(\d+)\s*건")


def _accident_free_from_tags(tags: list) -> tuple:
    """
    ВАЖНО (найдено вживую 2026-07-07, на реальной выборке HeyDealer): тег,
    начинающийся с "완무" (완전무사고), НЕ гарантирует "без страховых выплат"
    — встречаются теги вида "완무 (보험3건 · 360만원)": формально "без
    ДТП по своей вине", но 3 страховых случая на 360만원 реальных выплат.
    "완무" здесь описывает только СОБСТВЕННУЮ аварийность машины, а не
    отсутствие любых страховых случаев вообще.

    Поэтому ориентируемся на число "보험N건" (число страховых случаев) в
    тексте тега, а не на префикс "완무"/"유사고": accident_free=True только
    при явном "보험0건" (ноль случаев). Любое N>0 — accident_free=False,
    независимо от префикса. Если числа в тегах нет вообще — не гадаем,
    возвращаем None (неизвестно).
    """
    for tag in tags:
        match = _INSURANCE_CLAIMS_RE.search(tag)
        if match:
            claims = int(match.group(1))
            return (claims == 0), (None if claims == 0 else tag)
    return None, (", ".join(tags) if tags else None)


def _normalize_item(item: dict, target: dict) -> dict:
    detail = item.get("detail") or {}
    auction = item.get("auction") or {}

    desired_price = auction.get("desired_price")
    price_krw = desired_price * 10000 if desired_price else None  # 만원 -> вон

    tags = [t.get("text") for t in auction.get("tags", []) if t.get("text")]
    accident_free, damage_notes = _accident_free_from_tags(tags)

    trim = " ".join(str(x) for x in (detail.get("model_part_name"), detail.get("grade_part_name")) if x) \
        or (detail.get("full_name") or "")
    year_raw = detail.get("year")
    year = int(year_raw) if str(year_raw or "").isdigit() else None

    title_for_spec = f"{target['brand']} {target['model']} {target.get('engine', '')} {trim}"
    spec = find_spec(title_for_spec, year)

    return {
        "brand": target["brand"],
        "model": target["model"],
        "trim": trim,
        "external_id": item.get("hash_id"),
        "year": year,
        "mileage_km": detail.get("mileage"),
        "cc": spec.cc if spec else None,
        "hp": spec.hp if spec else None,
        "fuel_code": spec.fuel if spec else None,
        "specs": f"{spec.cc} см³, {spec.hp} л.с." if spec else "",
        "price": price_krw or 0,
        "url": f"https://dealer.heydealer.com/auctions/{item.get('hash_id')}",
        "source": "heydealer",
        "photos": detail.get("image_urls") or [],
        "accident_free": accident_free,
        "damage_notes": damage_notes,
        "color": None,  # структурного поля цвета не найдено (см. разведку)
    }


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления в аукционном зале HeyDealer по целевой модели. Полный
    обход текущего зала (нет server-side фильтра по марке/модели) +
    фильтрация по тексту на своей стороне. Возвращает список, совместимый с
    общей схемой model_search.py.
    """
    sess = _ensure_session(target, log)

    # ВАЖНО (найдено вживую 2026-07-07): для отечественных марок (Kia/
    # Hyundai/Genesis) full_name часто НЕ содержит название марки вообще
    # (напр. "K5 하이브리드 3세대 노블레스" без "기아") — это НЕ то же самое,
    # что у AJ/kbchachacha, где марка почти всегда присутствует. Проверка
    # "марка И модель" здесь давала 0 ложноотрицательных совпадений на
    # реальных K5 (13 в зале, 0 найдено) — оставляем проверку ТОЛЬКО по
    # модели (названия моделей типа K5/RS6/G80 сами по себе достаточно
    # специфичны, риск ложных совпадений низкий).
    model_candidates = [target["model"].strip().lower(), model_to_korean(target["model"]).lower()]
    # Нет структурного поля топлива на этой площадке — фильтрацию по
    # engine_fuel_hint(trim) делает сам model_search.py после сбора (тот же
    # приём, что для kbchachacha/OnBid, см. model_search.py::collect_for_source).

    matched = []
    total_raw = 0
    for page_num in range(1, MAX_PAGES + 1):
        params = {
            "page": page_num, "type": "auction", "is_subscribed": "false",
            "is_retried": "false", "is_previously_bid": "false", "order": "default",
        }
        try:
            resp = sess.get(CARS_API_URL, params=params, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            log.error(f"heydealer: страница {page_num}: ошибка запроса ({error})")
            break

        if not data:
            break
        total_raw += len(data)

        for item in data:
            detail = item.get("detail") or {}
            title = f"{detail.get('full_name', '')} {detail.get('model_part_name', '')}".lower()
            if not any(m in title for m in model_candidates):
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

    log.info(f"heydealer: просмотрено {total_raw}, {target['brand']} {target['model']} -> "
              f"найдено {len(matched)} (после фильтров)")
    return matched
