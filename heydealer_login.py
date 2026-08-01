# -*- coding: utf-8 -*-
"""
heydealer_login.py — рабочий логин в дилерский кабинет HeyDealer и сохранение
сессии в heydealer_dealer_session.json.

Зачем отдельный модуль, если есть heydealer_scraper._login_and_save_session():
штатная реализация перестала работать на текущей вёрстке кабинета (проверено
вживую). Две причины, обе воспроизводимы:

  1) page.fill() выставляет .value, но НЕ будит onChange у React — состояние
     компонента не обновляется, и форма уходит на сервер пустой. Лечится
     вводом с клавиатуры: click() + keyboard.type(..., delay=...).
  2) Клик по первой кнопке с классом css-jwz1sc попадает на «딜러 가입신청»
     (регистрация) и уводит на /register/agreement. Нужна именно ВИДИМАЯ
     кнопка с текстом ровно «로그인».

Прямой POST на /v2/dealers/web/login/ не подходит: API отвечает
400 «only pc is allowed» — вход возможен только через браузерный контекст.

Сессия HeyDealer живёт недолго (часы), поэтому автоматическим прогонам
(мониторинг раз в 6 часов, GitHub Actions на чистой VM) логин нужен почти
каждый раз — для этого есть ensure_session().
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSION_PATH = ROOT / "heydealer_dealer_session.json"
CREDENTIALS_PATH = ROOT / "credentials.json"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
LOGIN_URL = "https://dealer.heydealer.com"

logger = logging.getLogger("heydealer_login")

# JS: найти ВИДИМУЮ кнопку с текстом ровно «로그인» и кликнуть.
# Кнопки в вёрстке дублируются (десктоп/мобайл), у невидимой копии
# offsetParent === null — по ней клик не сработает.
_CLICK_LOGIN_JS = """() => {
    const btn = Array.from(document.querySelectorAll('button'))
        .find(b => b.offsetParent !== null && (b.innerText || '').trim() === '로그인');
    if (btn) { btn.click(); return true; }
    return false;
}"""


def login_and_save_session(log: logging.Logger | None = None) -> bool:
    """
    Выполняет живой логин и сохраняет storage_state в SESSION_PATH.

    Returns:
        True — сессия получена и сохранена; False — логин не удался.
        Исключения наружу не выпускает (кроме отсутствия credentials.json).
    """
    log = log or logger
    if not CREDENTIALS_PATH.exists():
        log.error("credentials.json не найден — логин невозможен")
        return False
    creds = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))["heydealer"]

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=USER_AGENT,
                                      viewport={"width": 1400, "height": 900})
            page = ctx.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3)

            # Ввод с клавиатуры — иначе React не увидит значения (см. докстринг).
            page.click('input[name="username"]')
            page.keyboard.type(creds["id"], delay=30)
            page.click('input[name="password"]')
            page.keyboard.type(creds["password"], delay=30)
            time.sleep(0.5)

            filled = page.evaluate(
                """() => ({u: (document.querySelector('input[name=username]')||{}).value?.length || 0,
                           p: (document.querySelector('input[name=password]')||{}).value?.length || 0})""")
            if not filled.get("u") or not filled.get("p"):
                log.error(f"поля формы не заполнились: {filled}")
                browser.close()
                return False

            if not page.evaluate(_CLICK_LOGIN_JS):
                log.error("не найдена видимая кнопка «로그인»")
                browser.close()
                return False
            time.sleep(7)

            names = {c["name"] for c in ctx.cookies()}
            try:
                body = page.inner_text("body", timeout=10000)
            except Exception:
                body = ""
            ok = ("sessionid" in names) or ("로그아웃" in body)
            if ok:
                ctx.storage_state(path=str(SESSION_PATH))
                log.info("heydealer: сессия обновлена и сохранена")
            else:
                log.error("heydealer: логин не удался (нет sessionid / 로그아웃)")
            browser.close()
            return ok
    except Exception as error:
        log.error(f"heydealer: ошибка логина — {error}")
        return False


def ensure_session(log: logging.Logger | None = None) -> bool:
    """
    Гарантирует рабочую сессию: если сохранённая ещё жива — переиспользует,
    иначе логинится заново.

    Для автоматических прогонов (cron/Actions) логин здесь ожидаемый и
    штатный: сессия HeyDealer живёт часы, а на чистой VM её вовсе нет.
    """
    log = log or logger
    try:
        from heydealer_scraper import _session_is_valid
        if _session_is_valid():
            log.info("heydealer: переиспользую сохранённую сессию")
            return True
    except Exception as error:
        log.warning(f"heydealer: проверка сессии не удалась ({error}) — логинюсь заново")
    return login_and_save_session(log)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("OK" if ensure_session() else "FAILED")
