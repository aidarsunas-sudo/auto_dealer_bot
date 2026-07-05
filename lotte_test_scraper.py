# -*- coding: utf-8 -*-
"""
lotte_test_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает список
объявлений с Lotte Auto Auction (lotteautoauction.net), раздел
"출품리스트" (список машин на предстоящих торгах).

Требует логин — используются учётные данные из credentials.json (ключ
"lotte"). Не печатает пароль никуда.

Что уже подтверждено в разведке (см. предыдущую сессию):
- Логин: поля #userId/#userPwd, кнопка — div.btn-login (НЕ форма submit,
  обычный клик по div с JS-обработчиком).
- Список объявлений — таблица table.tbl-t02, доступна через
  fn_MovePage('1010200') с главной страницы после логина.
- Пагинация — fnSearch(номер_страницы), по 20 объявлений на страницу.
- Детальная карточка (fnPopupCarView) и Excel-экспорт — НЕ включены в
  этот скрипт: не удалось стабильно поймать их содержимое из-за
  сетевой нестабильности при разведке, это отдельная задача на будущее.
- Сайт временами медленно грузится/подвисает — все сетевые шаги обёрнуты
  в повторные попытки, а не однократный вызов.

Запуск:
    python lotte_test_scraper.py
"""

import json
import logging
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("lotte_test")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_lotte"
SESSION_PATH = Path(__file__).resolve().parent / "lotte_session.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MAX_PAGES = 5  # тестовый масштаб — не весь рынок (см. предыдущий этап с K5/Sonata)


def goto_retry(page, url, tries=5, wait="domcontentloaded", timeout=30000):
    """Сайт Lotte нередко подвисает на загрузке — пробуем несколько раз,
    прежде чем сдаться, вместо одной попытки."""
    for attempt in range(tries):
        try:
            page.goto(url, wait_until=wait, timeout=timeout)
            return True
        except Exception as error:
            logger.warning(f"goto попытка {attempt + 1}/{tries} не удалась: {error}")
            time.sleep(4)
    return False


def wait_for_condition(page, js_expr: str, timeout: int = 20, interval: float = 1.0) -> bool:
    """Ждём, пока JS-выражение не станет истинным (вместо фиксированной паузы,
    которая на нестабильном сайте иногда оказывается слишком короткой)."""
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


def login(page, creds: dict) -> bool:
    if not goto_retry(page, "https://www.lotteautoauction.net/"):
        logger.error("Не удалось загрузить главную страницу Lotte")
        return False

    try:
        page.fill("#userId", creds["id"], timeout=15000)
        page.fill("#userPwd", creds["password"], timeout=15000)
        time.sleep(1)
        page.click(".btn-login", timeout=15000)
    except Exception as error:
        logger.error(f"Ошибка при вводе логина/пароля: {error}")
        return False

    time.sleep(5)
    try:
        body = page.inner_text("body", timeout=15000)
    except Exception as error:
        logger.error(f"Не удалось прочитать страницу после логина: {error}")
        return False

    return "로그아웃" in body


def open_exhibition_list(page) -> bool:
    if not goto_retry(page, "https://www.lotteautoauction.net/hp/auct/cmm/viewMain.do"):
        return False

    if not wait_for_condition(page, "typeof fn_MovePage !== 'undefined'", timeout=20):
        logger.error("fn_MovePage не определилась вовремя")
        return False

    try:
        page.evaluate("fn_MovePage('1010200')")
    except Exception as error:
        logger.error(f"Ошибка вызова fn_MovePage: {error}")
        return False

    return wait_for_condition(page, "document.body.innerText.includes('출품리스트')", timeout=15)


def parse_current_page(page) -> list:
    """Разбирает таблицу table.tbl-t02 в структурированный список машин."""
    rows_html = page.eval_on_selector_all(
        "table.tbl-t02 tr",
        """trs => trs.map(tr => {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
            return cells;
        })""",
    )

    cars = []
    for cells in rows_html:
        # Строка с данными о машине — 8 колонок: номер, лейн, госномер,
        # название, год, пробег, цвет, оценка, цена (заголовок/пустые пропускаем).
        if len(cells) < 8:
            continue
        if not cells[0].isdigit():
            continue

        lot_number, lane, plate, title, year_raw, mileage_raw, color, grade, price_raw = (
            cells + [""] * (9 - len(cells))
        )[:9]
        # В ячейке названия иногда встречается доп. строка (например, тип
        # КПП "수동") через перенос строки — сводим к одной строке с пробелами.
        title = " ".join(title.split())

        year = int(year_raw) if year_raw.isdigit() else None

        mileage_digits = re.sub(r"[^\d]", "", mileage_raw)
        mileage_km = int(mileage_digits) if mileage_digits else None

        price_digits = re.sub(r"[^\d]", "", price_raw)
        starting_price_krw = int(price_digits) * 10000 if price_digits else None  # 만원 -> 원

        cars.append({
            "lot_number": lot_number,
            "lane": lane,
            "plate": plate,
            "title": title,
            "year": year,
            "mileage_km": mileage_km,
            "color": color,
            "grade": grade,
            "starting_price_krw": starting_price_krw,
        })

    return cars


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    creds = json.load(open("credentials.json", encoding="utf-8"))["lotte"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context_kwargs = {"user_agent": USER_AGENT}
        if SESSION_PATH.exists():
            context_kwargs["storage_state"] = str(SESSION_PATH)
        ctx = browser.new_context(**context_kwargs)
        page = ctx.new_page()

        # Пробуем переиспользовать сохранённую сессию; если она уже истекла —
        # логинимся заново (сайт довольно быстро "забывает" сессию).
        already_in = False
        if SESSION_PATH.exists():
            if goto_retry(page, "https://www.lotteautoauction.net/hp/auct/cmm/viewMain.do"):
                try:
                    already_in = "로그아웃" in page.inner_text("body", timeout=15000)
                except Exception:
                    already_in = False

        if not already_in:
            logger.info("Сессия не найдена/истекла — логинюсь заново")
            if not login(page, creds):
                logger.error("Логин не удался, прекращаю работу")
                browser.close()
                return
            ctx.storage_state(path=str(SESSION_PATH))
        else:
            logger.info("Переиспользую сохранённую сессию")

        if not open_exhibition_list(page):
            logger.error("Не удалось открыть список объявлений")
            browser.close()
            return

        all_cars = []
        seen_lot_numbers = set()
        last_first_lot = None
        for page_num in range(1, MAX_PAGES + 1):
            if page_num > 1:
                try:
                    page.evaluate(f"fnSearch({page_num})")
                except Exception as error:
                    logger.error(f"Не удалось перейти на страницу {page_num}: {error}")
                    break
                # Ждём, пока таблица реально перерисуется — проверяем не просто
                # "есть строки", а что первый номер лота отличается от прошлой
                # страницы (иначе можем разобрать ту же самую страницу дважды).
                check_js = (
                    "(() => { const tds = document.querySelectorAll('table.tbl-t02 tr td'); "
                    f"return tds.length > 0 && tds[0].innerText.trim() !== '{last_first_lot or ''}'; }})()"
                )
                wait_for_condition(page, check_js, timeout=15)
                time.sleep(2)

            cars = parse_current_page(page)
            if cars:
                last_first_lot = cars[0]["lot_number"]
            # защита от дублей, если пагинация не сработала и вернула ту же страницу
            cars = [c for c in cars if c["lot_number"] not in seen_lot_numbers]
            for c in cars:
                seen_lot_numbers.add(c["lot_number"])
            if not cars:
                logger.warning(f"Страница {page_num}: пусто, останавливаюсь")
                break

            logger.info(f"Страница {page_num}: найдено {len(cars)} машин")
            all_cars.extend(cars)

        out_path = OUTPUT_DIR / "lotte_listing_test.json"
        out_path.write_text(json.dumps(all_cars, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"=== Готово: сохранено {len(all_cars)} машин в {out_path} ===")

        browser.close()


if __name__ == "__main__":
    run()
