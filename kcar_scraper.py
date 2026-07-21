# -*- coding: utf-8 -*-
"""
kcar_scraper.py — search_target_model() адаптер для K Car Auction
(kcarauction.com, "위클리옥션") для model_search.py.

В отличие от AJ/Glovis/HeyDealer, здесь список объявлений отдаёт структурные
поля марки/модели (MNUFTR_NM/MODEL_NM), а не только "сырой" заголовок —
фильтруем по ним напрямую, без AJ-style полнотекстового угадывания. Топливо
(FUEL_TYPE_NM) и цвет (EXTERIOR_COLOR_NM) — тоже готовые структурные поля.

ДТП/повреждения — JINDAN_EX_LIST: список повреждённых панелей человеческим
языком, либо буквально "무사고" (без ДТП), если повреждений нет. Это
единственный источник среди новых 4 адаптеров, где сигнал ДТП НЕ составной
(в отличие от HeyDealer, где "완무" мог сочетаться со скрытыми страховыми
выплатами) — используем напрямую, но это НЕ перепроверено против отдельного
источника (в отличие от Peugeot/HeyDealer, где именно перепроверка вскрыла
проблему) — сохраняем осторожность при интерпретации.

ВАЖНО — сессия: сайт использует form-data (не JSON) для поиска, и сам
тестовый скрипt каждый раз логинился заново через Playwright, БЕЗ
переиспользования сессии (см. kcar_test_scraper.py). Здесь это исправлено —
сохраняем/переиспользуем storage_state как у остальных логин-адаптеров;
если сессия невалидна, поднимается LoginRequiredError (см. login_gate.py) —
model_search.py требует явного подтверждения на ЭТОТ запуск
(--confirm-login kcar) перед живым логином + обязательным разовым
согласием ("동의") с credentials.json["kcar"].

ВАЖНО — фото: только THUMBNAIL (одно превью), полная галерея (EX_CNT
показывает сколько всего) — отдельный эндпоинт для неё не найден (см.
kcar_test_scraper.py, сознательно отложено, как и MPARK-формат у Glovis).

Запрос к API выполняется через Playwright page.request.post() (не requests) —
подтверждено вживую, что form-data POST так и задумывался (см. тестовый
скрипт); отдельно requests.Session с теми же куками не проверялся.
"""

import json
import logging
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from brand_names_ko import model_to_korean, to_korean
from engine_filter import FUEL_NAME_KO, engine_fuel_hint
from login_gate import LoginRequiredError
from model_specs import find_spec

SESSION_PATH = Path(__file__).resolve().parent / "kcar_session.json"
CREDENTIALS_PATH = Path(__file__).resolve().parent / "credentials.json"

IMAGE_BASE_URL = "https://www.kcarauction.com/auction/IMAGE_UPLOAD/CAR/"
LIST_URL = "https://www.kcarauction.com/kcar/auction/getAuctionCarList_ajax.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PAGE_CNT = 50
MAX_PAGES = 20


def _login_and_accept_terms(page, creds: dict, log: logging.Logger) -> bool:
    try:
        page.goto("https://www.kcarauction.com/kcar/user/user_login.do", wait_until="domcontentloaded", timeout=30000)
    except Exception as error:
        log.warning(f"kcar: goto warning: {error}")
    time.sleep(3)

    page.fill("#user_id", creds["id"])
    page.fill("#user_pw", creds["password"])
    time.sleep(1)
    page.keyboard.press("Enter")
    time.sleep(4)

    body = page.inner_text("body", timeout=15000)
    if "로그아웃" not in body:
        return False

    # Разовое согласие — без него список объявлений не откроется.
    page.evaluate("""
        () => {
            const btns = Array.from(document.querySelectorAll('button'));
            const b = btns.find(x => x.innerText.trim() === '동의' && x.offsetParent !== null);
            if (b) b.click();
        }
    """)
    time.sleep(2)
    return True


def _empty_search_form(page_num: int = 1) -> dict:
    start_rnum = (page_num - 1) * PAGE_CNT + 1
    return {
        "AUC_TYPE": "weekly", "MNUFTR_CD": "", "MODEL_GRP_CD": "", "MODEL_CD": "",
        "PAGE_CNT": str(PAGE_CNT), "START_RNUM": str(start_rnum), "ORDER": "",
        "OPTION_CD": "", "FORM_YR_ST": "", "FORM_YR_ED": "", "AUC_START_PRC_ST": "",
        "AUC_START_PRC_ED": "", "MILG_ST": "", "MILG_ED": "", "CNO": "", "FUEL_CD": "",
        "GBOX_DCD": "", "COLOR_CD": "", "SRC_OPT": "weekly", "CAR_TYPE": "", "CARMD_CD": "",
        "PAGE_TYPE": "wCfm", "LANE_TYPE": "A", "TO_DATE": "", "FROM_DATE": "",
        "CAR_STAT_CD": "",
    }


def _probe_session_valid(page) -> bool:
    try:
        resp = page.request.post(LIST_URL, form=_empty_search_form(1), timeout=15000)
        data = resp.json()
        return isinstance(data.get("CAR_LIST"), list)
    except Exception:
        return False


def _normalize_item(item: dict, target: dict) -> dict:
    thumbnail = item.get("THUMBNAIL")
    photo_url = f"{IMAGE_BASE_URL}{thumbnail}" if thumbnail else None

    diagnosis_raw = item.get("JINDAN_EX_LIST", "") or ""
    diagnosis_items = [p for p in diagnosis_raw.split(",") if p]
    has_damage = diagnosis_items not in ([], ["무사고"])

    trim = item.get("CAR_NM") or item.get("MODEL_NM") or ""
    year_raw = item.get("FORM_YR")
    year = int(year_raw) if str(year_raw or "").isdigit() else None

    title_for_spec = f"{target['brand']} {target['model']} {target.get('engine', '')} {trim}"
    spec = find_spec(title_for_spec, year)

    price = item.get("AUC_START_HOPE") or item.get("AUC_STRT_HOPE") or item.get("AUC_STRT_PRC") or 0

    return {
        "brand": target["brand"],
        "model": target["model"],
        "trim": trim,
        "external_id": item.get("CAR_ID"),
        "year": year,
        "mileage_km": item.get("MILG"),
        "cc": spec.cc if spec else None,
        "hp": spec.hp if spec else None,
        "fuel_code": spec.fuel if spec else None,
        "specs": f"{spec.cc} см³, {spec.hp} л.с." if spec else "",
        "price": price,
        "url": f"https://www.kcarauction.com/kcar/auction/car_detail.do?carId={item.get('CAR_ID')}",
        "source": "kcar",
        "photos": [photo_url] if photo_url else [],
        "accident_free": not has_damage,
        "damage_notes": ", ".join(diagnosis_items) if has_damage else None,
        "color": item.get("EXTERIOR_COLOR_NM"),
        "fuel_ko": item.get("FUEL_TYPE_NM"),
    }


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на K Car Auction ("위클리옥션") по целевой модели.
    Полный обход текущего аукциона (нет проверенных кодов MNUFTR_CD/
    MODEL_CD для server-side фильтра) + фильтрация по структурным полям
    MNUFTR_NM/MODEL_NM на своей стороне. Возвращает список, совместимый с
    общей схемой model_search.py.

    target["allow_fresh_login"] must be True для живого логина при
    невалидной сессии — иначе поднимается login_gate.LoginRequiredError.
    """
    creds = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        session_valid = False
        if SESSION_PATH.exists():
            ctx = browser.new_context(user_agent=USER_AGENT, storage_state=str(SESSION_PATH))
            page = ctx.new_page()
            session_valid = _probe_session_valid(page)
            if not session_valid:
                ctx.close()

        if not session_valid:
            if not target.get("allow_fresh_login"):
                browser.close()
                raise LoginRequiredError(
                    "kcar: сохранённая сессия отсутствует/истекла — нужен живой логин на "
                    "реальный аккаунт (+ обязательное согласие). Подтверди явно на этот "
                    "запуск: model_search.py ... --confirm-login kcar"
                )
            log.info("kcar: сессия истекла/отсутствует, логинюсь заново (подтверждено пользователем)")
            creds = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))["kcar"]
            ctx = browser.new_context(user_agent=USER_AGENT)
            page = ctx.new_page()
            if not _login_and_accept_terms(page, creds, log):
                browser.close()
                raise RuntimeError("kcar: логин не удался — проверь credentials.json")
            ctx.storage_state(path=str(SESSION_PATH))
        else:
            log.info("kcar: переиспользую сохранённую сессию")

        # ВАЖНО (найдено вживую 2026-07-07): MNUFTR_NM — корейское название
        # марки ("기아", не "Kia") — без перевода через to_korean() ни одно
        # совпадение не находилось (проверено на реальном K5 в списке).
        brand_candidates = [target["brand"].strip().lower(), to_korean(target["brand"]).lower()]
        model_candidates = [target["model"].strip().lower(), model_to_korean(target["model"]).lower()]
        fuel_hint = engine_fuel_hint(target.get("engine"))
        # Цвет — фильтруется централизованно в model_search.py::enrich_and_filter
        # (car["color"] уже заполнен из EXTERIOR_COLOR_NM ниже), здесь не нужно.

        matched = []
        total_raw = 0
        for page_num in range(1, MAX_PAGES + 1):
            try:
                resp = page.request.post(LIST_URL, form=_empty_search_form(page_num), timeout=25000)
                data = resp.json()
            except Exception as error:
                log.error(f"kcar: страница {page_num}: ошибка запроса ({error})")
                break

            car_list = data.get("CAR_LIST", [])
            if not car_list:
                break
            total_raw += len(car_list)

            for item in car_list:
                mnuftr = (item.get("MNUFTR_NM") or "").lower()
                model_nm = (item.get("MODEL_NM") or "").lower()
                car_nm = (item.get("CAR_NM") or "").lower()
                if not any(b in mnuftr for b in brand_candidates):
                    continue
                if not any(m in model_nm or m in car_nm for m in model_candidates):
                    continue
                if fuel_hint is not None and item.get("FUEL_TYPE_NM") != FUEL_NAME_KO.get(fuel_hint):
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

    log.info(f"kcar: просмотрено {total_raw}, {target['brand']} {target['model']} -> "
              f"найдено {len(matched)} (после фильтров)")
    return matched
