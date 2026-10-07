# -*- coding: utf-8 -*-
"""
test_trim_ru.py — самотест перевода корейских комплектаций (brand_names_ko.TRIM_RU +
translate_trims, применяется в post_text_builder._build_title; python test_trim_ru.py,
без pytest и без сети).

Реальные данные: 8 названий, отсеянных пайплайном 27.09–07.10.2026 с причиной «нет читаемого
названия (корейский текст)» (logs/main_pipeline.log на origin) — это семь слов-комплектаций,
`시그니처` встречается у двух моделей.
"""

import logging
import tempfile
from pathlib import Path

import brand_names_ko as bk
import config
import post_text_builder as ptb
import post_validator as pv
import registry as reg

RATES = {"krw_rub": 0.0625, "eur_rub": 95.33, "usd_rub": 85.0, "cny_rub": 12.0, "jpy_rub": 0.55, "_source": "cbr"}

# (brand, model, корейский трим) -> ожидаемый заголовок; все 8 названий из реального лога
REAL_REJECTED = [
    ("Kia", "K3", "트렌디", "Kia K3 Trendy"),
    ("Kia", "K3", "시그니처", "Kia K3 Signature"),
    ("Kia", "K5", "노블레스", "Kia K5 Noblesse"),
    ("Kia", "K5", "시그니처", "Kia K5 Signature"),
    ("Hyundai", "Sonata", "인스퍼레이션", "Hyundai Sonata Inspiration"),
    ("Hyundai", "Sonata", "익스클루시브", "Hyundai Sonata Exclusive"),
    ("Hyundai", "Sonata", "프리미엄", "Hyundai Sonata Premium"),
    ("Kia", "K3", "프레스티지", "Kia K3 Prestige"),
]


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _car(trim: str, **kw) -> dict:
    year = (config.MIN_YEAR + config.MAX_YEAR) // 2
    car = {"brand": "Kia", "model": "K5", "trim": trim, "year": year, "month": 3, "mileage_km": 24000, "cc": 1999, "hp": 160,
           "fuel_code": "petrol", "price": 25_000_000, "source": "charancha", "url": "https://www.charancha.com/cars/x",
           "photos": [f"https://example.com/{i}.jpg" for i in range(config.MIN_PHOTOS_PER_POST + 1)]}
    car.update(kw)
    return car


def run() -> None:
    results = []
    capture = _Capture()
    bk_logger = logging.getLogger("brand_names_ko")
    bk_logger.addHandler(capture)
    real_log = pv.REJECTED_LOG_PATH
    tmp = tempfile.TemporaryDirectory()
    pv.REJECTED_LOG_PATH = Path(tmp.name) / "rejected_posts.log"
    try:
        results.append(_check("в словаре ровно семь комплектаций из реального лога", len(bk.TRIM_RU) == 7 and
                              set(bk.TRIM_RU) == {"트렌디", "시그니처", "노블레스", "인스퍼레이션", "익스클루시브", "프리미엄", "프레스티지"}))
        for brand, model, trim, expected in REAL_REJECTED:
            title = ptb._build_title({"brand": brand, "model": model, "trim": trim})
            results.append(_check(f"реальное название «{brand} {model} {trim}» -> «{title}»", title == expected))

        # главное: такие лоты больше не отсеиваются
        fields, reason = ptb.prepare_post_fields(_car("노블레스"), RATES)
        results.append(_check("prepare_post_fields: лот «Kia K5 노블레스» теперь собирается (заголовок «Kia K5 Noblesse»)",
                              fields is not None and fields["title"] == "Kia K5 Noblesse" and reason == ""))
        validated, why = pv.validate_post_before_publish(_car("노블레스"), fields)
        results.append(_check("гейт validate_post_before_publish пропускает такой лот (корейских символов в названии нет)",
                              validated is not None and why == ""))
        results.append(_check("хэштег по переведённому названию, без хангыля",
                              "#kia" in fields["hashtags"] and not ptb.HANGUL_RE.search(fields["hashtags"])))

        # неизвестное — не гадаем, но пишем в лог один раз
        capture.messages.clear(); bk._UNKNOWN_TRIMS_LOGGED.clear()
        fields, reason = ptb.prepare_post_fields(_car("스페셜에디션"), RATES)
        results.append(_check("неизвестный трим: лот по-прежнему отсеивается (причина — корейский текст), перевод не выдумывается",
                              fields is None and "корейский" in reason))
        ptb.prepare_post_fields(_car("스페셜에디션"), RATES)
        pv.validate_post_before_publish(_car("스페셜에디션"), None)
        unknown = [m for m in capture.messages if "스페셜에디션" in m]
        results.append(_check("неизвестный трим записан в лог ровно один раз, с подсказкой про TRIM_RU",
                              len(unknown) == 1 and "TRIM_RU" in unknown[0]))
        capture.messages.clear()
        ptb.prepare_post_fields(_car("노블레스"), RATES)
        results.append(_check("известные тримы в лог не попадают", capture.messages == []))

        # смешанное и граничные случаи
        results.append(_check("латинский трим не меняется", ptb._build_title({"brand": "Audi", "model": "Q3", "trim": "35 TFSI"}) == "Audi Q3 35 TFSI"))
        results.append(_check("трим внутри строки заменяется, остальное сохраняется",
                              bk.translate_trims("Kia K5 노블레스 2.0") == "Kia K5 Noblesse 2.0"))
        results.append(_check("пустые значения не падают", bk.translate_trims("") == "" and bk.translate_trims(None) is None
                              and ptb._build_title({}) == ""))
        results.append(_check("повтор бренда/модели в trim по-прежнему схлопывается",
                              ptb._build_title({"brand": "Kia", "model": "K5", "trim": "K5"}) == "Kia K5"))
        results.append(_check("registry.lot_title использует тот же сборщик (сверка «латиница с латиницей»)",
                              reg.lot_title({"brand": "Kia", "model": "K5", "trim": "노블레스"}) == "Kia K5 Noblesse"))
        # реестр: лот с корейским тримом опознаётся как уже опубликованный пост с латинским названием
        records = {1: {"message_id": 1, "model": "Kia K5 Noblesse", "year": 2022, "mileage_km": 24000, "lot_id": None, "lot_url": None,
                       "method": "auto", "template": "lead", "post_url": "https://t.me/auto_generals/1", "posted_at": "2026-10-01T00:00:00+00:00"}}
        results.append(_check("защита от дублей: корейский лот находит уже вышедший пост «Kia K5 Noblesse»",
                              reg.find_duplicate(_car("노블레스", year=2022, url="https://example.com/other"), records) is not None))
    finally:
        bk_logger.removeHandler(capture)
        pv.REJECTED_LOG_PATH = real_log
        tmp.cleanup()

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
