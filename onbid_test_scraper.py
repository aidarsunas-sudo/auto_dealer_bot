# -*- coding: utf-8 -*-
"""
onbid_test_scraper.py — ТЕСТОВЫЙ скрипт (не боевой код): собирает список
лотов из раздела "차량" (транспорт) на OnBid (onbid.co.kr) — гос.
площадка изъятого/невостребованного имущества (не только авто, но раздел
차량 отдельный).

НЕ требует логина — раздел полностью публичный (вход нужен только для
"나의 온비드", т.е. личных ставок). Не использует Playwright для самого
сбора — только для одноразовой разведки, сам сбор через requests.

Что уже подтверждено в разведке:
- Список лотов — POST-эндпоинт (form-data, не JSON):
  https://www.onbid.co.kr/op/cltrpbancinf/clbtcltrclg/cltrclbtcltrclg/
      CltrClbtCltrClgController/inqCltrClbtVhcClg.do
  Тело — большой набор form-полей фильтра (см. BASE_FORM ниже, скопировано
  из реального запроса браузера), пагинация через pageIndex/pageUnit.
- Ответ — JSON с cltrInfVOList (список лотов) + paginationInfo
  (totalRecordCount, totalPageCount).
- ВАЖНО: раздел "차량" на сайте объединяет и легковые автомобили, и
  спецтехнику/грузовики (ctgrNm может быть "화물차", "중기" и т.п.) —
  при использовании в бою обязательно фильтровать по ctgrNm/ctgrFullNm,
  не считать все записи легковыми авто.
- Фото НЕ разрешены в этой сессии: есть только atchFileLstNo/atchSn
  (ссылки на файловую систему вложений), не прямые URL картинок —
  нужен отдельный эндпоинт для резолва (не искали, как MPARK у Glovis).
- Пробег/объём двигателя/год приходят как ГОТОВЫЙ ТЕКСТ ("102,531km",
  "배기량 2,157cc", "2019"), а не чистые числа — парсим на своей стороне.

Запуск:
    python onbid_test_scraper.py
"""

import json
import logging
import re
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("onbid_test")

OUTPUT_DIR = Path(__file__).resolve().parent / "test_onbid"

LIST_URL = (
    "https://www.onbid.co.kr/op/cltrpbancinf/clbtcltrclg/cltrclbtcltrclg/"
    "CltrClbtCltrClgController/inqCltrClbtVhcClg.do"
)
PAGE_UNIT = 50  # сайт по умолчанию использует 10, увеличиваем для меньшего числа запросов
MAX_PAGES = 5  # тестовый масштаб (сейчас всего 250 лотов — можно и все, но по инструкции: сначала тест)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

# Скопировано из реального запроса браузера (раздел "차량", без доп.
# фильтров — все категории/регионы/топливо включены по умолчанию).
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


def parse_mileage(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def parse_displacement(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def normalize_item(item: dict) -> dict:
    return {
        "onbid_cltrno": item.get("onbidCltrno"),
        "management_no": item.get("scrnIndctCltrMngNo"),
        "title": item.get("onbidCltrNm"),
        "category": item.get("ctgrNm"),
        "category_full": item.get("ctgrFullNm"),  # напр. "자동차 / 화물차" — трактор/грузовик тоже сюда попадает
        "disposal_method": item.get("dspsMthodNm"),  # 매각/임대
        "region": item.get("sidoSgkEmd"),
        "year": item.get("nrtpNm"),
        "mileage_km": parse_mileage(item.get("drvDstcNm")),
        "displacement_cc": parse_displacement(item.get("fuelContDsvlmNm")),
        "appraisal_value_krw": item.get("cltrApslEvlAvgAmt"),
        "min_bid_price_krw": item.get("lowstBidPrc"),
        "bid_status": item.get("pbancPbctCltrStatNm"),
        "bid_deadline": item.get("pbctDdlnDt"),
        "bid_execute_date": item.get("pbctExctDt"),
        "failed_bid_count": item.get("uscbdCnt"),
        "view_count": item.get("inqCnt"),
        "atch_file_lst_no": item.get("atchFileLstNo"),  # см. докстринг — фото не разрешены
    }


def search_all() -> list:
    all_items = []

    for page_index in range(1, MAX_PAGES + 1):
        form_data = {**BASE_FORM, "pageIndex": str(page_index), "pageUnit": str(PAGE_UNIT)}
        try:
            resp = requests.post(LIST_URL, data=form_data, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            logger.error(f"Страница {page_index}: ошибка запроса ({error})")
            break

        items = data.get("cltrInfVOList", [])
        total_count = data.get("totalCount", 0)

        if not items:
            logger.info(f"Страница {page_index}: пусто, останавливаюсь")
            break

        all_items.extend(normalize_item(item) for item in items)
        logger.info(f"Страница {page_index}: получено {len(items)} (всего лотов: {total_count})")

        if len(all_items) >= total_count:
            break
        time.sleep(0.5)

    return all_items


def run() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    items = search_all()

    out_path = OUTPUT_DIR / "onbid_listing_test.json"
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    by_category = {}
    for it in items:
        by_category[it["category"]] = by_category.get(it["category"], 0) + 1
    logger.info(f"=== Готово: сохранено {len(items)} лотов в {out_path} ===")
    logger.info(f"Разбивка по категориям: {by_category}")


if __name__ == "__main__":
    run()
