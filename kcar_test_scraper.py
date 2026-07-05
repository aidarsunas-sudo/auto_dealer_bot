# -*- coding: utf-8 -*-
"""
kcar_test_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает список
объявлений с K Car Auction (kcarauction.com), раздел "위클리옥션"
(еженедельный аукцион — самый крупный пул, ~380 подтверждённых лотов,
в отличие от "데일리옥션", где на момент разведки конкретный раунд
оказался пустым).

Требует логин — используются учётные данные из credentials.json (ключ
"kcar"). Не печатает пароль никуда.

Что уже подтверждено в разведке:
- Логин: обычная форма #user_id / #user_pw + Enter (без отдельной кнопки).
- ВАЖНО: после логина сайт требует принять разовое согласие (юридическое
  уведомление) — до этого ЛЮБАЯ попытка открыть 데일리옥션/위클리옥션
  редиректит на user_confirm_fail.do с alert("미동의시 진행하실 수
  없습니다."). Согласие — обычная кнопка <button>동의</button>, скрытая
  в DOM, но реально кликабельная сразу после логина (JS её открывает).
  Это разовое действие на сессию, не при каждом запросе.
- Список объявлений — POST-эндпоинт (form-data, не JSON!):
  https://www.kcarauction.com/kcar/auction/getAuctionCarList_ajax.do
  Ответ — JSON с полем CAR_LIST: КАЖДАЯ машина — это ОГРОМНЫЙ плоский
  объект (236 полей!) с посегментной диагностикой кузова (BONNET,
  FRONTDOOR_LEFT, QUARTERPANEL_RIGHT и т.д. — каждое поле либо пусто
  (норм), либо содержит код неисправности) — это даже подробнее, чем
  у Encar. Отдельно есть JINDAN_EX_LIST — текстовый список повреждённых
  панелей человеческим языком (например "앞팬더(우),앞펜더(좌),").
- Фото: THUMBNAIL / THUMBNAIL_MOBILE — относительный путь превью,
  реальный CDN — https://www.kcarauction.com/auction/IMAGE_UPLOAD/CAR/{путь}
  (без авторизации, подтверждено HEAD-запросом). ПОЛНАЯ галерея (EX_CNT
  показывает, СКОЛЬКО фото есть, например 16) — отдельный эндпоинт для
  неё в этой сессии не искали (см. докстринг — сознательно отложено,
  как и MPARK-формат у Glovis).
- Также на странице есть кнопка "엑셀다운로드" (экспорт в Excel) — не
  использовали, но потенциально более простой путь для полного дампа.

Запуск:
    python kcar_test_scraper.py
"""

import json
import logging
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("kcar_test")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_kcar"
IMAGE_BASE_URL = "https://www.kcarauction.com/auction/IMAGE_UPLOAD/CAR/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PAGE_CNT = 18  # такой же page size, что использует сам сайт по умолчанию
MAX_PAGES = 6  # тестовый масштаб


def login_and_accept_terms(page, creds: dict) -> bool:
    try:
        page.goto("https://www.kcarauction.com/kcar/user/user_login.do", wait_until="domcontentloaded", timeout=30000)
    except Exception as error:
        logger.warning(f"goto warning: {error}")
    time.sleep(3)

    page.fill("#user_id", creds["id"])
    page.fill("#user_pw", creds["password"])
    time.sleep(1)
    page.keyboard.press("Enter")
    time.sleep(4)

    body = page.inner_text("body", timeout=15000)
    if "로그아웃" not in body:
        return False

    # Разовое согласие — без него список объявлений не откроется
    # (см. докстринг). Кнопка скрыта в DOM, но кликабельна через JS.
    page.evaluate("""
        () => {
            const btns = Array.from(document.querySelectorAll('button'));
            const b = btns.find(x => x.innerText.trim() === '동의' && x.offsetParent !== null);
            if (b) b.click();
        }
    """)
    time.sleep(2)
    return True


def normalize_item(item: dict) -> dict:
    thumbnail = item.get("THUMBNAIL")
    photo_url = f"{IMAGE_BASE_URL}{thumbnail}" if thumbnail else None

    # JINDAN_EX_LIST — это НЕ всегда список повреждений: для машин без ДТП
    # там буквально стоит "무사고" (без ДТП) как единственное значение.
    # Различаем через has_damage — считаем повреждением всё, кроме этой пометки.
    diagnosis_raw = item.get("JINDAN_EX_LIST", "")
    diagnosis_items = [p for p in diagnosis_raw.split(",") if p]
    has_damage = diagnosis_items not in ([], ["무사고"])

    return {
        "car_id": item.get("CAR_ID"),
        "title": item.get("CAR_NM"),
        "plate": item.get("CNO"),
        "manufacturer": item.get("MNUFTR_NM"),
        "model": item.get("MODEL_NM"),
        "year": item.get("FORM_YR"),
        "first_reg_date": item.get("FST_REG_DT"),
        "mileage_km": item.get("MILG"),
        "fuel": item.get("FUEL_TYPE_NM"),
        "transmission": item.get("TRANS_MISSION_NM"),
        "color": item.get("EXTERIOR_COLOR_NM"),
        "location": item.get("CAR_LOCT"),
        "auction_start_price_krw": item.get("AUC_STRT_PRC"),
        "auction_hope_price_krw": item.get("AUC_STRT_HOPE"),
        "auction_date": item.get("AUC_STRT_DT"),
        "auction_datetime_range": item.get("AUC_STRT_END_DATETIME"),
        "diagnosis_items": diagnosis_items,
        "has_damage": has_damage,
        "photo_count": item.get("EX_CNT"),
        "thumbnail_url": photo_url,
        "thumbnail_path_raw": thumbnail,
        "vin": item.get("VIN"),
        "note": item.get("NOTE"),
    }


def search_all(page) -> list:
    all_items = []
    seen_ids = set()

    for page_num in range(1, MAX_PAGES + 1):
        start_rnum = (page_num - 1) * PAGE_CNT + 1
        form_data = {
            "AUC_TYPE": "weekly", "MNUFTR_CD": "", "MODEL_GRP_CD": "", "MODEL_CD": "",
            "PAGE_CNT": str(PAGE_CNT), "START_RNUM": str(start_rnum), "ORDER": "",
            "OPTION_CD": "", "FORM_YR_ST": "", "FORM_YR_ED": "", "AUC_START_PRC_ST": "",
            "AUC_START_PRC_ED": "", "MILG_ST": "", "MILG_ED": "", "CNO": "", "FUEL_CD": "",
            "GBOX_DCD": "", "COLOR_CD": "", "SRC_OPT": "weekly", "CAR_TYPE": "", "CARMD_CD": "",
            "PAGE_TYPE": "wCfm", "LANE_TYPE": "A", "TO_DATE": "", "FROM_DATE": "",
            "CAR_STAT_CD": "",
        }

        try:
            resp = page.request.post(
                "https://www.kcarauction.com/kcar/auction/getAuctionCarList_ajax.do",
                form=form_data, timeout=25000,
            )
            data = resp.json()
        except Exception as error:
            logger.error(f"Страница {page_num}: ошибка запроса ({error})")
            break

        car_list = data.get("CAR_LIST", [])
        if not car_list:
            logger.info(f"Страница {page_num}: пусто, останавливаюсь")
            break

        new_items = 0
        for item in car_list:
            car_id = item.get("CAR_ID")
            if car_id in seen_ids:
                continue
            seen_ids.add(car_id)
            all_items.append(normalize_item(item))
            new_items += 1

        logger.info(f"Страница {page_num}: получено {len(car_list)} (новых {new_items})")
        if new_items == 0:
            break
        time.sleep(0.5)

    return all_items


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    creds = json.load(open("credentials.json", encoding="utf-8"))["kcar"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT)
        page = ctx.new_page()

        if not login_and_accept_terms(page, creds):
            logger.error("Логин не удался, прекращаю работу")
            browser.close()
            return

        items = search_all(page)
        browser.close()

    out_path = OUTPUT_DIR / "kcar_listing_test.json"
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"=== Готово: сохранено {len(items)} машин в {out_path} ===")


if __name__ == "__main__":
    run()
