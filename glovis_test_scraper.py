# -*- coding: utf-8 -*-
"""
glovis_test_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает список
объявлений с Glovis Autobell (autobell.co.kr, "스마트옥션").

Требует логин — используются учётные данные из credentials.json (ключ
"glovis"). Не печатает пароль никуда.

Что уже подтверждено в разведке:
- Логин через Playwright: клик "로그인" в меню -> поля #user-id_common /
  #user-pw -> Enter.
- Сайт использует JWT-авторизацию: после логина токен лежит в куке
  "accessToken" (домен .autobell.co.kr). Дальше можно обращаться К API
  НАПРЯМУЮ через requests (без браузера) — просто передавая эту куку +
  заголовок Authorization с тем же значением.
- Реальный поиск объявлений — POST-эндпоинт
  https://autobell.co.kr/api/buycar/selectGeneralListLogin.do
  Тело запроса — большой JSON с фильтрами (марка/модель/год/пробег/цена/
  топливо/регион/аварийность и т.д.), пагинация через "currentPage".
  Ответ — чистый JSON с уже готовыми полями (марка, модель, комплектация,
  год, пробег, топливо, цена, регион, дата обновления).

ЧТО НЕ РЕАЛИЗОВАНО (сознательно отложено на потом):
- Полные фото объявления — поле "delePhtUrl" содержит только относительный
  путь (например "MPARK_BATCH_INSERT/6061500/6061500603R1.jpg"), не удалось
  подобрать домен CDN (несколько вариантов проверены — 404), а перехват
  реального запроса картинки через скроллинг страницы завис локально.
  Нужно будет отдельно найти правильный домен/эндпоинт.
- Детальная карточка (диагностика/история повреждений) — отдельный
  эндпоинт для этого ещё не найден.

Запуск:
    python glovis_test_scraper.py
"""

import json
import logging
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("glovis_test")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_glovis"
SESSION_PATH = Path(__file__).resolve().parent / "glovis_session.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LIST_API_URL = "https://autobell.co.kr/api/buycar/selectGeneralListLogin.do"
COUNT_PER_PAGE = 15
MAX_PAGES = 10  # тестовый масштаб — не весь рынок

# Пустой шаблон тела запроса — те же поля, что реально отправляет сайт
# при поиске (подсмотрено через перехват сетевого запроса в браузере).
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


def login_and_save_session(creds: dict) -> None:
    """Логинится через браузер один раз и сохраняет сессию (куки, включая
    accessToken) — дальше её можно переиспользовать через requests."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT)
        page = ctx.new_page()
        try:
            page.goto("https://autobell.co.kr/autoAuction/auctionGate", wait_until="domcontentloaded", timeout=30000)
        except Exception as error:
            logger.warning(f"goto warning: {error}")
        time.sleep(3)

        page.click("text=로그인", timeout=8000)
        time.sleep(2)
        page.fill("#user-id_common", creds["id"])
        page.fill("#user-pw", creds["password"])
        page.keyboard.press("Enter")
        time.sleep(3)

        body = page.inner_text("body", timeout=15000)
        if "로그아웃" not in body:
            raise RuntimeError("Логин не удался — проверь credentials.json")

        ctx.storage_state(path=str(SESSION_PATH))
        browser.close()


def get_access_token() -> str:
    """Достаёт JWT из куки accessToken сохранённой сессии."""
    if not SESSION_PATH.exists():
        raise FileNotFoundError("Сессия не найдена — сначала login_and_save_session()")

    session_data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    for cookie in session_data.get("cookies", []):
        if cookie["name"] == "accessToken":
            return cookie["value"]
    raise RuntimeError("Кука accessToken не найдена в сохранённой сессии")


def build_requests_session() -> requests.Session:
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


def normalize_item(item: dict) -> dict:
    return {
        "dlr_prd_id": item.get("dlrPrdId"),
        "cr_id": item.get("crId"),
        "title": item.get("crNm"),
        "plate": item.get("crNo"),
        "brand": item.get("mnfcNm"),
        "model": item.get("mdlNm"),
        "cls": item.get("clsNm"),
        "detail_cls": item.get("dtlClsNm"),
        "year": item.get("frmYyyy"),
        "mileage_km": item.get("drvDist"),
        "transmission": item.get("mss"),
        "fuel": item.get("fuelNm"),
        "price_krw": (item.get("slAmt") or 0) * 10000,  # slAmt в 만원 -> вон
        "location": item.get("locNm"),
        "updated_at": item.get("updDt"),
        "photo_url": build_photo_url(item.get("delePhtUrl")),
        "photo_path_raw": item.get("delePhtUrl"),
        "description": item.get("oneLineDesc"),
    }


def build_photo_url(path_raw: str | None) -> str | None:
    """
    Строит рабочую ссылку на фото. Подтверждено: путь вида
    "/picture/dlr/prd/carImg/{id}/normal/thumb/{hash}_M" (обычные объявления
    дилеров) реально отдаётся напрямую с static.glovis.net — родного CDN
    Hyundai Glovis (сайт сам грузит его через прокси-ресайзер
    img.autobell.co.kr, но и прямая ссылка на static.glovis.net работает).

    "MPARK_BATCH_INSERT/..." (похоже, старый импорт из отдельной системы,
    заметная часть объявлений на площадке) НЕ разрешён — ни один
    проверенный CDN-домен не отдаёт эти файлы напрямую (see: разведка
    в этой сессии). Для таких путей возвращаем None — вызывающий код
    решает сам, что делать (пропустить фото, показать заглушку и т.п.).
    """
    if not path_raw:
        return None
    if path_raw.startswith("/picture/"):
        return f"https://static.glovis.net{path_raw}"
    return None  # MPARK_BATCH_INSERT/... и прочие нераспознанные форматы


def search_all(sess: requests.Session, search_term: str = "") -> list:
    """Постранично собирает результаты поиска (searchTerm пустой = все машины)."""
    all_items = []
    current_page = 1

    while current_page <= MAX_PAGES:
        body = dict(BASE_SEARCH_BODY)
        body["searchTerm"] = search_term
        body["currentPage"] = current_page

        try:
            resp = sess.post(LIST_API_URL, json=body, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            logger.error(f"Страница {current_page}: ошибка запроса ({error})")
            break

        product_list = data.get("data", {}).get("prdLst", [])
        total_count = data.get("data", {}).get("totalCount", 0)

        if not product_list:
            logger.info(f"Страница {current_page}: пусто, останавливаюсь")
            break

        all_items.extend(normalize_item(item) for item in product_list)
        logger.info(f"Страница {current_page}: получено {len(product_list)} (всего в поиске: {total_count})")

        if len(all_items) >= total_count:
            break
        current_page += 1
        time.sleep(0.5)

    return all_items


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    creds = json.load(open("credentials.json", encoding="utf-8"))["glovis"]

    need_login = True
    if SESSION_PATH.exists():
        try:
            sess = build_requests_session()
            # быстрая проверка — если токен ещё жив, запрос пройдёт без ошибки авторизации
            test_resp = sess.post(LIST_API_URL, json={**BASE_SEARCH_BODY, "countPerPage": 1}, timeout=15)
            need_login = test_resp.status_code != 200
        except Exception:
            need_login = True

    if need_login:
        logger.info("Сессия отсутствует/истекла — логинюсь заново")
        login_and_save_session(creds)
    else:
        logger.info("Переиспользую сохранённую сессию")

    sess = build_requests_session()
    items = search_all(sess, search_term="")

    out_path = OUTPUT_DIR / "glovis_listing_test.json"
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"=== Готово: сохранено {len(items)} машин в {out_path} ===")


if __name__ == "__main__":
    run()
