# -*- coding: utf-8 -*-
"""
aj_test_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает список
объявлений с AJ / AutoHub Auction (autohubauction.co.kr, он же
sellcarauction.co.kr — один и тот же сайт "오토허브 옥션").

Требует логин — используются учётные данные из credentials.json (ключ
"aj"). Не печатает пароль никуда.

Что уже подтверждено в разведке:
- Логин: клик "로그인" в меню (появляется после ожидания полной
  загрузки главной страницы — сайт на Next.js, "load" происходит
  раньше, чем реально готово меню) -> поля с placeholder "아이디 입력"/
  "비밀번호 입력" -> Enter.
- Сайт — Next.js (App Router, RSC). Из-за этого страница
  /auction/list?...&_rsc=... САМА ПО СЕБЕ содержит только shell
  роутинга, а НЕ данные — легко перепутать с реальным API, но реальные
  данные оттуда не достать. Настоящие данные идут с ОТДЕЛЬНОГО домена:
  api.ahsellcar.co.kr (видно по названию — это старый бренд "AJ셀카").
- Список объявлений — чистый REST-эндпоинт:
  POST https://api.ahsellcar.co.kr/auction/external/rest/api/v1/entry/list/paging
  Тело запроса — JSON: {"tenant":"1","pageSize":100,"pageIndex":N,"sort":"desc","sortBy":"entryNo"}
  Ответ — {"code":"1","message":"성공","data":{"totalRows":N,"totalPages":N,"list":[...]}}
  Каждая машина — плоский объект с двуязычными полями (*_ko/*_en), уже
  включает превью-фото (mainFileUrl) и оценку состояния (inspGrade,
  accidentTag — например "STRUCTURAL").
- Авторизация — JWT в заголовке Authorization: Bearer <accessToken>,
  токен живёт 24 часа, лежит НЕ в localStorage, а в куке "user-info"
  (URL-encoded JSON, поле accessToken внутри).
- Фото (mainFileUrl) отдаются с отдельного домена file.ahsellcar.co.kr
  БЕЗ авторизации — подтверждено HEAD-запросом.

Запуск:
    python aj_test_scraper.py
"""

import json
import logging
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("aj_test")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_aj"
SESSION_PATH = Path(__file__).resolve().parent / "aj_session.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LIST_API_URL = "https://api.ahsellcar.co.kr/auction/external/rest/api/v1/entry/list/paging"
PAGE_SIZE = 100
MAX_PAGES = 5  # тестовый масштаб (не весь рынок — сейчас на площадке ~1694 машины, 17 страниц по 100)


def login_and_save_session(creds: dict) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT)
        page = ctx.new_page()
        try:
            page.goto("https://www.autohubauction.co.kr/", wait_until="load", timeout=45000)
        except Exception as error:
            logger.warning(f"goto warning: {error}")
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
            raise RuntimeError("Логин не удался — проверь credentials.json")

        ctx.storage_state(path=str(SESSION_PATH))
        browser.close()


def get_access_token() -> str:
    session_data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    for cookie in session_data.get("cookies", []):
        if cookie["name"] == "user-info":
            user_info = json.loads(unquote(cookie["value"]))
            return user_info["accessToken"]
    raise RuntimeError("Кука user-info не найдена в сохранённой сессии")


def build_requests_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {get_access_token()}",
    })
    return sess


def normalize_item(item: dict) -> dict:
    return {
        "entry_id": item.get("entryId"),
        "entry_no": item.get("entryNo"),
        "car_id": item.get("carId"),
        "title": item.get("carNm"),
        "plate": item.get("carNo"),
        "year": item.get("carYear"),
        "mileage_km": item.get("mileage"),
        "fuel": item.get("fuelNm"),
        "transmission": item.get("tmNm"),
        "usage": item.get("useageNm"),  # 자가용/렌터카 и т.п.
        "location": item.get("carLocNm"),
        "lane": item.get("laneNm"),
        "start_price_krw": (item.get("startAmt") or 0) * 10000,  # в 만원 -> вон
        "hope_price_krw": (item.get("hopeAmt") or 0) * 10000 if item.get("hopeAmt") else None,
        "auction_date": item.get("aucDate"),
        "auction_status": item.get("aucStatusGrpNm"),
        "inspection_grade": item.get("inspGrade"),
        "accident_tag": item.get("accidentTag"),  # например "STRUCTURAL", null если без ДТП
        "thumbnail_url": item.get("mainFileUrl"),
    }


def search_all(sess: requests.Session) -> list:
    all_items = []

    for page_index in range(1, MAX_PAGES + 1):
        body = {"tenant": "1", "pageSize": PAGE_SIZE, "pageIndex": page_index, "sort": "desc", "sortBy": "entryNo"}
        try:
            resp = sess.post(LIST_API_URL, json=body, timeout=25)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as error:
            logger.error(f"Страница {page_index}: ошибка запроса ({error})")
            break

        data = payload.get("data", {})
        car_list = data.get("list", [])
        total_rows = data.get("totalRows", 0)

        if not car_list:
            logger.info(f"Страница {page_index}: пусто, останавливаюсь")
            break

        all_items.extend(normalize_item(item) for item in car_list)
        logger.info(f"Страница {page_index}: получено {len(car_list)} (всего в поиске: {total_rows})")

        if len(all_items) >= total_rows:
            break
        time.sleep(0.5)

    return all_items


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    creds = json.load(open("credentials.json", encoding="utf-8"))["aj"]

    need_login = True
    if SESSION_PATH.exists():
        try:
            sess = build_requests_session()
            test_resp = sess.post(
                LIST_API_URL, json={"tenant": "1", "pageSize": 1, "pageIndex": 1, "sort": "desc", "sortBy": "entryNo"},
                timeout=15,
            )
            need_login = test_resp.status_code != 200
        except Exception:
            need_login = True

    if need_login:
        logger.info("Сессия отсутствует/истекла — логинюсь заново")
        login_and_save_session(creds)
    else:
        logger.info("Переиспользую сохранённую сессию")

    sess = build_requests_session()
    items = search_all(sess)

    out_path = OUTPUT_DIR / "aj_listing_test.json"
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"=== Готово: сохранено {len(items)} машин в {out_path} ===")


if __name__ == "__main__":
    run()
