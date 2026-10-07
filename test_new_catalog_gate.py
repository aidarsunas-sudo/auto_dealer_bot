# -*- coding: utf-8 -*-
"""
test_new_catalog_gate.py — самотест validate_new_catalog_post() и параметра car_kind у publish_post()
(python test_new_catalog_gate.py, без pytest и без сети: ВСЕ отправщики Telegram/VK подменены).

Лот — как его отдаёт scraper.parse_car_page() для каталога новых авто дилера (carnoon.co.kr, WATCHLIST):
year=None, фото нет, цена в вонах, объём и мощность числами, ссылка на страницу.
"""

import ast
import tempfile
from pathlib import Path

import agent_publisher
import config
import post_validator as pv
from post_validator import PublishBlockedError, ValidatedCar, validate_new_catalog_post

ROOT = Path(__file__).resolve().parent


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def _new_car(**kw) -> dict:
    car = {"brand": "Kia", "model": "Sportage", "trim": "Prestige", "year": None, "cc": 1598, "hp": 180, "fuel_code": "petrol",
           "price": 30_000_000, "specs": "1598 см³, 180 л.с.", "url": "https://www.carnoon.co.kr/newcar/vehicle/11722"}
    car.update(kw)
    return car


def _raises(exc, fn) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


def run() -> None:
    results = []
    tmp = tempfile.TemporaryDirectory()
    real_log = pv.REJECTED_LOG_PATH
    pv.REJECTED_LOG_PATH = Path(tmp.name) / "rejected_posts.log"
    sent = []
    names = ("send_to_telegram", "send_photo_to_telegram", "send_media_group_to_telegram", "send_to_vk")
    originals = {n: getattr(agent_publisher, n) for n in names}
    try:
        # ── валидный лот каталога ────────────────────────────────────────
        validated, reason = validate_new_catalog_post(_new_car(), {"total_rub_raw": 4_000_000})
        results.append(_check("валидный новый авто из каталога: год None, фото и пробега нет — гейт пропускает (ValidatedCar)",
                              isinstance(validated, ValidatedCar) and reason == ""))
        results.append(_check("без fields (цену под ключ не передали) — остальные проверки всё равно идут",
                              validate_new_catalog_post(_new_car())[0] is not None))

        # ── отказы ───────────────────────────────────────────────────────
        cases = [
            ("пустое название", _new_car(brand="", model="", trim=""), "пустое название"),
            ("корейский текст в названии (неизвестное слово)", _new_car(trim="스페셜에디션"), "корейские"),
            ("недопустимые символы в названии", _new_car(trim="<b>x</b>"), "недопустимые"),
            ("нет цены (None)", _new_car(price=None), "нет цены"),
            ("цена 0", _new_car(price=0), "нет цены"),
            ("отрицательная цена", _new_car(price=-5), "нет цены"),
            ("нет объёма двигателя", _new_car(cc=None), "объём"),
            ("нет мощности", _new_car(hp=None), "мощность"),
            ("нет ссылки на страницу каталога", _new_car(url=""), "ссылки"),
        ]
        for label, car, fragment in cases:
            got, why = validate_new_catalog_post(car, {"total_rub_raw": 4_000_000})
            results.append(_check(f"отказ: {label}", got is None and fragment in why))
        too_cheap, why = validate_new_catalog_post(_new_car(), {"total_rub_raw": config.PRICE_MIN_RUB - 1})
        too_dear, why2 = validate_new_catalog_post(_new_car(), {"total_rub_raw": config.PRICE_MAX_RUB + 1})
        results.append(_check("отказ: итоговая цена под ключ ниже PRICE_MIN_RUB и выше PRICE_MAX_RUB",
                              too_cheap is None and too_dear is None and "вне допустимого" in why and "вне допустимого" in why2))
        log_text = pv.REJECTED_LOG_PATH.read_text(encoding="utf-8")
        results.append(_check("каждый отказ записан в rejected_posts.log с причиной", log_text.count("\tREJECT\t") >= len(cases) + 2
                              and "нет цены в вонах" in log_text))

        # ── publish_post(car_kind=...) ───────────────────────────────────
        for name in names:
            setattr(agent_publisher, name, lambda *a, **k: sent.append("send") or True)
        fields = {"total_rub_raw": 4_000_000, "title": "Kia Sportage Prestige", "year": "не указан"}
        sent.clear()
        ok = agent_publisher.publish_post(_new_car(), "текст", fields=fields, car_kind="new_catalog", skip_duplicate_check=True)
        results.append(_check("publish_post(car_kind='new_catalog'): лот без года и фото проходит свой гейт и публикуется", ok is True and "send" in sent))
        sent.clear()
        results.append(_check("новый авто с битыми данными блокируется (PublishBlockedError), ничего не отправлено",
                              _raises(PublishBlockedError, lambda: agent_publisher.publish_post(_new_car(cc=None), "текст", fields=fields,
                                                                                               car_kind="new_catalog", skip_duplicate_check=True))
                              and sent == []))
        results.append(_check("тот же лот как «used» (полный гейт) блокируется: нет фото и года — старая строгость не ослаблена",
                              _raises(PublishBlockedError, lambda: agent_publisher.publish_post(_new_car(), "текст", fields=fields, skip_duplicate_check=True))
                              and sent == []))
        results.append(_check("неизвестный car_kind -> ValueError", _raises(ValueError, lambda: agent_publisher.publish_post(_new_car(), "т", car_kind="used_new"))))
        results.append(_check("параметра skip_validation больше нет: попытка передать его -> TypeError",
                              _raises(TypeError, lambda: agent_publisher.publish_post(_new_car(), "т", skip_validation=True))))

        # ── статическая проверка: обхода гейта в коде нет (через ast: вызовы и параметры, не докстринги)
        offenders = []
        skip_prefixes = ("archive", "avito_sold", "telegram_car_monitor", "test_")
        for path in ROOT.glob("*.py"):
            if path.name.startswith(skip_prefixes):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and any(k.arg == "skip_validation" for k in node.keywords):
                    offenders.append(f"{path.name}:{node.lineno} (вызов)")
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                        a.arg == "skip_validation" for a in node.args.args + node.args.kwonlyargs):
                    offenders.append(f"{path.name}:{node.lineno} (параметр функции {node.name})")
        results.append(_check("в коде проекта нет ни одного вызова или параметра skip_validation", offenders == []))
        main_src = (ROOT / "main.py").read_text(encoding="utf-8")
        results.append(_check("main.py: ветка WATCHLIST использует car_kind='new_catalog'", 'car_kind="new_catalog"' in main_src))
    finally:
        for name, fn in originals.items():
            setattr(agent_publisher, name, fn)
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
