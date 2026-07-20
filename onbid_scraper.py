# -*- coding: utf-8 -*-
"""
onbid_scraper.py — боевой модуль для OnBid (onbid.co.kr), гос. площадка
изъятого/невостребованного имущества, раздел "차량" (транспорт).

Используется В orchestrator.py (автоматика по расписанию) — сайт полностью
публичный, логин не нужен вообще (в отличие от 5 дилерских площадок).

Поиск по target_models.json — свободный текстовый поиск, резолвер кодов
марки/модели НЕ нужен (в отличие от kbchachacha/Encar): поля формы
srchVhcMkr/srchVhcMdlNm принимают обычный текст. ВАЖНО: srchVhcMkr нужен на
корейском (подтверждено вживую: "Audi" -> 0 результатов, "아우디" -> находит),
а вот название модели (K3, Q3, Tiguan) работает как есть, латиницей — на
сайте так и хранится.

Раздел "차량" смешивает легковые авто со спецтехникой (ctgrNm может быть
"화물차"/"중기"/"지게차" и т.п.) — фильтруем по ctgrNm, оставляя только
легковые категории.
"""

import logging
import re
import time

import requests

from brand_names_ko import to_korean
from model_specs import find_spec

logger = logging.getLogger("onbid_scraper")

LIST_URL = (
    "https://www.onbid.co.kr/op/cltrpbancinf/clbtcltrclg/cltrclbtcltrclg/"
    "CltrClbtCltrClgController/inqCltrClbtVhcClg.do"
)
PAGE_UNIT = 50
MAX_PAGES = 10  # защитный потолок

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

BASE_FORM = {
    "srchBidPerdBgngDt": "", "srchBidPerdEndDt": "", "srchPvctYn": "", "srchArrayCtgrId": "",
    "srchArrayRgn": "", "srchWordType": "0001", "srchSortType": "ASC", "srchLowstBidBgngPrc": "",
    "srchLowstBidEndPrc": "", "srchApslEvlBgngAmt": "", "srchApslEvlEndAmt": "",
    "cltrScrnGrpCd": "", "cltrPrptDivCd": "", "onbidCltrno": "", "onbidPbancNo": "", "pbctNo": "",
    "pbctCdtnNo": "", "srchLowstBidBgng": "", "srchApslEvlAmtType": "001", "rtnListUrl": "",
    "searchCltrMnmtNoYn": "N", "test": "", "pinHistory": "", "srchCltrType": "0002",
    "srchPrptType": ["0007", "0010", "0005", "0003", "0013", "0002", "0011", "0006", "0008", "0004"],
    "srchDspsMthod": "", "checkMobileUsg": "on", "checkMobileRgn": "on", "calOpbdBgdd": "",
    "calOpbdEndd": "", "srchBidDivType": "", "srchBidPerdType": "0002", "calBidPerdBgngDt": "",
    "calhBidPerdEndDt": "", "srchBidMthod": "", "scr02_011": "0", "srchApslEvlBgngType": "",
    "srchApslEvlEndType": "", "scr02_01": "0", "srchLowstBidBgngType": "", "srchLowstBidEndType": "",
    "srchLdarType": "ALL", "srchMinLdar": "", "srchMaxLdar": "", "srchBldSqmsType": "ALL",
    "srchMinBldLdar": "", "srchMaxBldLdar": "", "srchMvastMkr": "", "srchMvastMdlNm": "",
    "srchMvastMnftYr": "", "srchMvastMbs": "", "srchScrtNm": "", "srchItmNm": "", "srchStkKind": "",
    "srchDsplFinCo": "", "srchNplKind": "", "hdorAstCfmtnBgdd": "", "hdorAstCfmtnEndd": "",
    "srchVhcMkr": "", "srchVhcMdlNm": "", "srchVhcYrmdl": "", "srchDrvDstcType": "ALL",
    "srchDrvDstcBgng": "", "srchDrvDstcEnd": "",
    "srchFuelType": ["0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008", "0099"],
    "srchAlcYnType": "", "srchUsbdNftType": "ALL", "srchUsbdNftBgng": "", "srchUsbdNftEnd": "",
    "srchCltrNm": "", "srchCltrMnmtNo": "", "srchOrgNm": "",
}

# Только легковые категории — раздел "차량" смешивает их со спецтехникой.
PASSENGER_CATEGORIES = {"승용차", "SUV", "승합차"}


def _parse_int(text) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def _normalize_item(item: dict, target: dict) -> dict | None:
    category = item.get("ctgrNm")
    if category not in PASSENGER_CATEGORIES:
        return None

    title = item.get("onbidCltrNm", "")
    # find_spec ищет по латинским подстрокам ("tiguan", "tdi", "q3") —
    # onbidCltrNm обычно уже латиницей, но на случай другого источника
    # названия подстраховываемся каноническим brand/model/engine из
    # target_models.json (лишний текст не мешает — match — просто substring).
    title_for_spec = f"{target['brand']} {target['model']} {target.get('engine', '')} {title}"
    year_text = item.get("nrtpNm", "")
    year = _parse_int(year_text)
    mileage_km = _parse_int(item.get("drvDstcNm"))
    cc_from_site = _parse_int(item.get("fuelContDsvlmNm"))

    spec = find_spec(title_for_spec, year)
    cc = cc_from_site or (spec.cc if spec else None)
    hp = spec.hp if spec else None
    fuel_code = spec.fuel if spec else None

    external_id = item.get("onbidCltrno")
    price = item.get("lowstBidPrc")

    return {
        "brand": target["brand"],
        "model": target["model"],
        "trim": title,
        "external_id": external_id,
        "year": year,
        "mileage_km": mileage_km,
        "cc": cc,
        "hp": hp,
        "fuel_code": fuel_code,
        "price": price,
        "specs": f"{cc} см³, {hp} л.с." if cc and hp else "",
        "url": f"https://www.onbid.co.kr/op/cltrpbancinf/cltrpbanc/CltrPbancDetail.do?onbidCltrno={external_id}",
        # доп. поля для отчётов (model_search.py) — save_car их не читает
        "source": "onbid",
        "category": category,
        "bid_status": item.get("pbancPbctCltrStatNm"),
        "appraisal_value_krw": item.get("cltrApslEvlAvgAmt"),
    }


def search_target_model(target: dict, log: logging.Logger) -> list:
    """
    Ищет объявления на OnBid по целевой модели (see target_models.json).
    Свободный текстовый поиск — резолвер кодов не нужен. Возвращает список,
    совместимый с database.save_car (+ доп. поля для отчётов).
    """
    brand_ko = to_korean(target["brand"])
    model_text = target["model"]

    all_items = []
    for page_index in range(1, MAX_PAGES + 1):
        form_data = {
            **BASE_FORM,
            "pageIndex": str(page_index),
            "pageUnit": str(PAGE_UNIT),
            "srchVhcMkr": brand_ko,
            "srchVhcMdlNm": model_text,
        }
        try:
            resp = requests.post(LIST_URL, data=form_data, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            log.error(f"onbid: страница {page_index} для {target['brand']} {target['model']}: {error}")
            break

        items = data.get("cltrInfVOList", [])
        total_count = data.get("totalCount", 0)
        if not items:
            break

        for item in items:
            normalized = _normalize_item(item, target)
            if normalized is None:
                continue
            year = normalized.get("year")
            mileage_km = normalized.get("mileage_km")
            if year is not None and not (target.get("year_from", 0) <= year <= target.get("year_to", 9999)):
                continue
            if target.get("mileage_max") is not None and mileage_km is not None and mileage_km > target["mileage_max"]:
                continue
            all_items.append(normalized)

        if page_index * PAGE_UNIT >= total_count:
            break
        time.sleep(0.5)

    log.info(f"onbid: {target['brand']} {target['model']} -> найдено {len(all_items)} (после фильтров)")
    return all_items


def _normalize_broad_item(item: dict) -> dict | None:
    """Как _normalize_item(), но без target — onbidCltrNm свободный текст
    (не структурированные brand/model), поэтому brand оставляем пустым, а
    полное название — в model (тот же приём, что в used_car_scraper.py для
    kbchachacha: 'brand': '', 'model': title)."""
    category = item.get("ctgrNm")
    if category not in PASSENGER_CATEGORIES:
        return None

    title = item.get("onbidCltrNm", "")
    year_text = item.get("nrtpNm", "")
    year = _parse_int(year_text)
    mileage_km = _parse_int(item.get("drvDstcNm"))
    cc_from_site = _parse_int(item.get("fuelContDsvlmNm"))

    spec = find_spec(title, year)
    cc = cc_from_site or (spec.cc if spec else None)
    hp = spec.hp if spec else None
    fuel_code = spec.fuel if spec else None

    external_id = item.get("onbidCltrno")
    price = item.get("lowstBidPrc")

    # Раз hp уже найден (find_spec совпал) — есть человекочитаемое имя
    # (spec.label, напр. "Kia Ray 1.0 Gasoline"). Используем его вместо
    # сырого текста title, иначе в посте выходит необработанный корейский
    # заголовок (тот же баг, что был у kbchachacha — см. FINAL_STATE.md).
    model_display = spec.label if spec else title
    trim_display = "" if spec else title

    return {
        "brand": "",
        "model": model_display,
        "trim": trim_display,
        "external_id": external_id,
        "year": year,
        "mileage_km": mileage_km,
        "cc": cc,
        "hp": hp,
        "fuel_code": fuel_code,
        "price": price,
        "specs": f"{cc} см³, {hp} л.с." if cc and hp else "",
        "url": f"https://www.onbid.co.kr/op/cltrpbancinf/cltrpbanc/CltrPbancDetail.do?onbidCltrno={external_id}",
        "source": "onbid",
        "category": category,
        "bid_status": item.get("pbancPbctCltrStatNm"),
        "appraisal_value_krw": item.get("cltrApslEvlAvgAmt"),
    }


def browse_broad(config: dict, log: logging.Logger) -> list:
    """
    Широкий поиск (Режим 2, см. broad_collector.py) — srchVhcMkr/
    srchVhcMdlNm оставляем пустыми (уже подтверждено вживую: работает,
    отдаёт все категории вперемешку — фильтруем по PASSENGER_CATEGORIES,
    как обычно). Фильтр по max_hp — см. docstring charancha_scraper.
    browse_broad.
    """
    max_hp = config.get("max_hp")
    min_hp = config.get("min_hp")
    max_pages = config.get("max_pages_per_source", 5)

    all_items = []
    for page_index in range(1, max_pages + 1):
        form_data = {
            **BASE_FORM,
            "pageIndex": str(page_index),
            "pageUnit": str(PAGE_UNIT),
            "srchVhcMkr": "",
            "srchVhcMdlNm": "",
        }
        try:
            resp = requests.post(LIST_URL, data=form_data, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            log.error(f"onbid (broad): страница {page_index}: {error}")
            break

        items = data.get("cltrInfVOList", [])
        total_count = data.get("totalCount", 0)
        if not items:
            break

        for item in items:
            normalized = _normalize_broad_item(item)
            if normalized is None:
                continue

            if normalized.get("hp") is None:
                continue
            if max_hp is not None and normalized["hp"] > max_hp:
                continue
            if min_hp is not None and normalized["hp"] < min_hp:
                continue

            year = normalized.get("year")
            if config.get("year_from") is not None and year is not None and year < config["year_from"]:
                continue
            mileage_km = normalized.get("mileage_km")
            if config.get("mileage_max") is not None and mileage_km is not None and mileage_km > config["mileage_max"]:
                continue
            fuel_types = config.get("fuel_types")
            if fuel_types is not None and normalized.get("fuel_code") not in fuel_types:
                continue

            all_items.append(normalized)

        if page_index * PAGE_UNIT >= total_count:
            break
        time.sleep(0.5)

    log.info(f"onbid (broad): найдено {len(all_items)} машин с hp в [{min_hp or 0}, {max_hp}]")
    return all_items
