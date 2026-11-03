# -*- coding: utf-8 -*-
"""
rates.py — получение свежих курсов валют с сайта ЦБ РФ.

Зачем это нужно?
Калькулятору таможни (customs_calculator.py) нужны курсы валют
(вона -> рубль, евро -> рубль и т.д.), чтобы правильно пересчитать
цену машины и таможенные платежи в рубли. Курсы меняются каждый день,
поэтому мы стараемся получить свежие значения перед каждым расчётом.

Если получить свежие курсы не удалось (нет интернета, сайт недоступен) —
используем сохранённые "запасные" значения из customs_calculator.py,
чтобы программа не падала, а просто работала чуть менее точно.
"""

import logging
import requests

from customs_calculator import DEFAULT_RATES

logger = logging.getLogger("rates")


def fetch_cbr_rates() -> dict:
    """
    Получает свежие курсы валют с сайта cbr-xml-daily.ru (открытый сервис,
    публикующий официальные курсы ЦБ РФ в удобном для программ формате).

    Returns:
        Словарь курсов в формате, который ожидает customs_calculator.calc_full():
        {"krw_rub": ..., "eur_rub": ..., "usd_rub": ..., "cny_rub": ..., "jpy_rub": ...}
        Если запрос не удался — вернёт сохранённые запасные значения
        (DEFAULT_RATES из customs_calculator.py), программа не упадёт. Ключ "_source"
        в результате: "cbr" (свежие) или "fallback" (запасные) — по нему pricing.py
        добавляет предупреждение к цене; на расчёт пошлин ключ не влияет.
    """
    try:
        response = requests.get(
            "https://www.cbr-xml-daily.ru/daily_json.js", timeout=10
        )
        response.raise_for_status()
        data = response.json()
        valute = data["Valute"]

        rates = dict(DEFAULT_RATES)  # начинаем с запасных значений
        rates["eur_rub"] = valute["EUR"]["Value"]
        rates["usd_rub"] = valute["USD"]["Value"]
        if "KRW" in valute:
            # ЦБ публикует курс за 1000 вон, а нам нужен курс за 1 вону
            rates["krw_rub"] = valute["KRW"]["Value"] / valute["KRW"]["Nominal"]
        if "CNY" in valute:
            rates["cny_rub"] = valute["CNY"]["Value"] / valute["CNY"]["Nominal"]
        if "JPY" in valute:
            rates["jpy_rub"] = valute["JPY"]["Value"] / valute["JPY"]["Nominal"] * 100

        logger.info(
            f"Получены свежие курсы ЦБ: EUR={rates['eur_rub']:.2f} "
            f"USD={rates['usd_rub']:.2f} KRW(за 1000)={rates['krw_rub']*1000:.2f}"
        )
        rates["_source"] = "cbr"
        return rates

    except requests.RequestException as error:
        logger.warning(f"Не удалось получить курсы ЦБ ({error}), использую сохранённые значения")
        return {**DEFAULT_RATES, "_source": "fallback"}  # pricing.calculate_price() предупредит о запасных курсах
    except (KeyError, ValueError) as error:
        logger.warning(f"Не удалось разобрать ответ ЦБ ({error}), использую сохранённые значения")
        return {**DEFAULT_RATES, "_source": "fallback"}  # pricing.calculate_price() предупредит о запасных курсах
