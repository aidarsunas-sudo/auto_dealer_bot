# -*- coding: utf-8 -*-
"""
heydealer_test_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает
список объявлений с личного кабинета брокера HeyDealer (dealer.heydealer.com,
раздел "경매장" — аукционный зал).

Требует логин — используются учётные данные из credentials.json (ключ
"heydealer"). Не печатает пароль никуда.

Что уже подтверждено в разведке:
- Логин — обычная Django-сессия: POST на api.heydealer.com/v2/dealers/web/login/,
  после успеха сервер выставляет куку sessionid (+ csrftoken). Дальше все
  запросы к api.heydealer.com можно делать ПРОСТЫМ requests.Session с этими
  куками — JWT/localStorage-токен, который тоже присутствует на странице,
  для чтения списка машин не нужен.
- Кнопка логина продублирована в DOM (десктоп/мобильная вёрстка) с
  одинаковым классом — обычный Playwright .click() иногда попадает по
  невидимому дублю, поэтому кликаем через JS по видимому элементу.
- Список объявлений — чистый GET-эндпоинт:
  https://api.heydealer.com/v2/dealers/web/cars/?page=N&type=auction&
      is_subscribed=false&is_retried=false&is_previously_bid=false&order=default
  Ответ — JSON-список, КАЖДЫЙ элемент уже содержит ПОЛНУЮ галерею фото
  (detail.image_urls) — отдельный запрос на детальную карточку не нужен
  (в отличие от kbchachacha/Encar/Glovis).
- Статус аварийности — не отдельное булево поле, а текстовые теги в
  auction.tags, например "완무 (보험0건)" (без ДТП, 0 страховых случаев),
  "1인소유" (один владелец). Разбираем как есть, без попытки угадать
  все возможные варианты тегов.
- Цена — auction.desired_price (в 만원, может быть null, если это лот
  без обозначенной "желаемой цены" — тогда ориентируемся на bids_count/
  highest_bid, которые тоже часто null, если сам аккаунт не участвовал
  в торгах по этому лоту).

Запуск:
    python heydealer_test_scraper.py
"""

import json
import logging
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("heydealer_test")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_heydealer"
SESSION_PATH = Path(__file__).resolve().parent / "heydealer_dealer_session.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CARS_API_URL = "https://api.heydealer.com/v2/dealers/web/cars/"
MAX_PAGES = 5  # тестовый масштаб — не весь рынок


def login_and_save_session(creds: dict) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        try:
            page.goto("https://dealer.heydealer.com", wait_until="domcontentloaded", timeout=30000)
        except Exception as error:
            logger.warning(f"goto warning: {error}")
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
            raise RuntimeError("Логин не удался — проверь credentials.json")

        ctx.storage_state(path=str(SESSION_PATH))
        browser.close()


def build_requests_session() -> requests.Session:
    session_data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    cookies = {c["name"]: c["value"] for c in session_data["cookies"] if "heydealer.com" in c["domain"]}

    sess = requests.Session()
    sess.cookies.update(cookies)
    sess.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    return sess


def normalize_item(item: dict) -> dict:
    detail = item.get("detail", {})
    auction = item.get("auction", {})

    desired_price = auction.get("desired_price")
    price_krw = desired_price * 10000 if desired_price else None  # 만원 -> 원

    tags = [t.get("text") for t in auction.get("tags", []) if t.get("text")]

    return {
        "hash_id": item.get("hash_id"),
        "status": item.get("status"),
        "status_display": item.get("status_display"),
        "full_name": detail.get("full_name"),
        "model_part_name": detail.get("model_part_name"),
        "grade_part_name": detail.get("grade_part_name"),
        "year": detail.get("year"),
        "initial_registration_date": detail.get("initial_registration_date"),
        "mileage_km": detail.get("mileage"),
        "location": detail.get("location"),
        "is_pre_inspected": detail.get("is_pre_inspected"),
        "tags": tags,
        "desired_price_krw": price_krw,
        "bids_count": auction.get("bids_count"),
        "auction_end_at": auction.get("end_at"),
        "main_image_url": detail.get("main_image_url"),
        "image_urls": detail.get("image_urls") or [],
    }


def search_all(sess: requests.Session) -> list:
    all_items = []

    for page_num in range(1, MAX_PAGES + 1):
        params = {
            "page": page_num,
            "type": "auction",
            "is_subscribed": "false",
            "is_retried": "false",
            "is_previously_bid": "false",
            "order": "default",
        }
        try:
            resp = sess.get(CARS_API_URL, params=params, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            logger.error(f"Страница {page_num}: ошибка запроса ({error})")
            break

        if not data:
            logger.info(f"Страница {page_num}: пусто, останавливаюсь")
            break

        all_items.extend(normalize_item(item) for item in data)
        logger.info(f"Страница {page_num}: получено {len(data)} машин")
        time.sleep(0.5)

    return all_items


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    creds = json.load(open("credentials.json", encoding="utf-8"))["heydealer"]

    need_login = True
    if SESSION_PATH.exists():
        try:
            sess = build_requests_session()
            test_resp = sess.get(CARS_API_URL, params={"page": 1, "type": "auction"}, timeout=15)
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

    out_path = OUTPUT_DIR / "heydealer_listing_test.json"
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    photo_counts = [len(it["image_urls"]) for it in items]
    avg_photos = sum(photo_counts) / len(photo_counts) if photo_counts else 0
    logger.info(f"=== Готово: сохранено {len(items)} машин в {out_path} (в среднем {avg_photos:.0f} фото на машину) ===")


if __name__ == "__main__":
    run()
