# -*- coding: utf-8 -*-
"""
scraper.py — здесь код, который ходит на сайт дилера и достаёт данные
о машинах (марка, модель, характеристики, цена).

Как это работает "на пальцах":
1. Мы отправляем запрос на страницу сайта (как будто открыли её в браузере)
2. Сайт присылает нам HTML-код страницы (тот же код, что видит браузер
   ДО того, как отрисовать красивую картинку)
3. Мы используем библиотеку BeautifulSoup, чтобы "прочитать" этот HTML
   и вытащить из него нужный текст (цену, объём двигателя и т.д.)

Сайт для примера: carnoon.co.kr — агрегатор, который публикует официальные
прайс-листы и характеристики BMW, Kia, Renault и других марок в Корее.
Он не блокирует автоматические запросы (в отличие от некоторых других
сайтов), поэтому с него удобно начинать.
"""

import re
import time
import logging

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger("scraper")

# Соответствие корейских слов "тип топлива" -> наши коды для показа человеку.
FUEL_TRANSLATIONS = {
    "휘발유": "бензин",
    "가솔린": "бензин",
    "경유": "дизель",
    "디젤": "дизель",
    "전기": "электро",
    "하이브리드": "гибрид",
}

# То же самое, но в кодах, которые понимает customs_calculator.py
# (там свои внутренние обозначения типов топлива для расчёта утильсбора).
FUEL_CODES = {
    "휘발유": "petrol",
    "가솔린": "petrol",
    "경유": "diesel",
    "디젤": "diesel",
    "전기": "elec",
    "하이브리드": "hybrid_p",
}


# Session — как "постоянное соединение" с сайтом: сохраняет куки между
# запросами (некоторые сайты требуют cookie, полученный на первом запросе,
# чтобы отвечать нормально на последующие — без этого могут отвечать
# ошибкой или блокировать).
_session = requests.Session()
_session.headers.update(config.REQUEST_HEADERS)


def fetch_html(url: str, max_retries: int = 3) -> str:
    """
    Скачивает HTML-код страницы по ссылке. Если запрос не удался (сайт
    не ответил, оборвалось соединение, вернул ошибку сервера и т.п.) —
    пробует ещё раз, до max_retries попыток, с небольшой паузой между ними.

    Это и есть "простая, но надёжная" часть: мы не сдаёмся после первой
    же сетевой заминки (интернет — штука не идеальная), но и не пытаемся
    бесконечно, если сайт реально недоступен.

    Args:
        url: адрес страницы.
        max_retries: сколько раз пробовать, прежде чем сдаться.

    Returns:
        HTML-код страницы как обычный текст.

    Raises:
        requests.RequestException: если все попытки провалились.
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Загружаю {url} (попытка {attempt}/{max_retries})")
            # Используем сессию (с сохранёнными куки) вместо разового запроса
            response = _session.get(url, timeout=15)
            # Если сервер вернул код ошибки (404, 500 и т.п.) — эта строка
            # сама "выбросит" исключение, и мы попадём в except ниже.
            response.raise_for_status()
            # Некоторые сайты неправильно указывают свою кодировку — эта
            # строка позволяет requests самому угадать правильную кодировку
            # (важно для корейского текста, иначе получится "кракозябры").
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text

        except requests.RequestException as error:
            last_error = error
            logger.warning(f"Попытка {attempt} не удалась: {error}")
            if attempt < max_retries:
                # Ждём немного перед следующей попыткой. Не мгновенно —
                # вдруг проблема временная (сайт перегружен на секунду).
                time.sleep(2 * attempt)  # 2 сек, потом 4 сек, потом 6 сек...

    # Если мы дошли до сюда — все попытки провалились.
    # raise поднимает (пробрасывает) ошибку выше, чтобы вызывающий код
    # знал, что что-то пошло не так, и мог соответственно отреагировать.
    raise last_error


def _parse_prices(page_text: str) -> list[dict]:
    """
    Ищет в тексте страницы блоки с ценами. На сайте цена идёт сразу
    после расхода топлива, вот так (каждая часть на своей строке):
        <имя комплектации>
        <тип топлива, необязательно>
        <расход>㎞/ℓ
        <цена>원

    Возвращает список найденных цен: [{"trim": ..., "price": ...}, ...]
    Важно: на странице может быть НЕСКОЛЬКО цен для одной и той же
    комплектации (например, прайс за 2025 и за 2026 год) — мы берём
    первую попавшуюся (сайт обычно перечисляет их от новых к старым).
    """
    pattern = re.compile(
        r"([^\n]{2,60}?)\n(?:(휘발유|경유|전기|하이브리드)\n)?[\d.]*㎞/ℓ\n([\d,]+)원"
    )

    prices = []
    for match in pattern.finditer(page_text):
        trim_name_raw, _fuel, price_raw = match.groups()
        trim_name = trim_name_raw.strip(" -•\t")
        if not trim_name or "년형" in trim_name:
            # Пропускаем случайные совпадения на строках-заголовках
            # вида "2026년형 가솔린 2.0 ..." — это не имя комплектации.
            continue
        try:
            price = int(price_raw.replace(",", ""))
        except ValueError:
            continue
        prices.append({"trim": trim_name, "price": price})

    return prices


def _parse_fuel_types(page_text: str) -> dict:
    """
    Ищет в тексте страницы блоки вида:
        <имя комплектации>
        연료
        <휘발유|경유|전기|하이브리드>
    (это секция "연비" — расход топлива — там явно подписан тип топлива).

    Returns:
        Словарь {имя_комплектации: код_топлива}, код — из FUEL_CODES.
    """
    pattern = re.compile(r"([^\n]{2,60}?)\n연료\n(휘발유|경유|전기|하이브리드)")

    fuel_by_trim = {}
    for match in pattern.finditer(page_text):
        trim_name_raw, fuel_kr = match.groups()
        trim_name = trim_name_raw.strip(" -•\t")
        fuel_by_trim[trim_name] = FUEL_CODES.get(fuel_kr, "petrol")

    return fuel_by_trim


def _find_matching_value(trim_name: str, lookup, key: str = "trim"):
    """
    Вспомогательная функция: ищет значение для данной комплектации в
    списке/словаре, даже если названия не совпадают ДОСЛОВНО (сайт может
    называть одну и ту же комплектацию чуть по-разному в разных разделах
    страницы) — сравниваем только первые 10 символов названия.
    """
    if isinstance(lookup, dict):
        for name, value in lookup.items():
            if name.startswith(trim_name[:10]) or trim_name.startswith(name[:10]):
                return value
        return None
    else:
        for item in lookup:
            name = item[key]
            if name.startswith(trim_name[:10]) or trim_name.startswith(name[:10]):
                return item
        return None


def parse_car_page(html: str, url: str, brand: str, model: str) -> list[dict]:
    """
    Разбирает HTML-код страницы одной модели и достаёт все найденные
    комплектации (тримы) с их характеристиками.

    Как мы ищем данные без знания точной вёрстки сайта:
    страница carnoon.co.kr показывает характеристики в виде подписанных
    пар "название поля -> значение", например:
        엔진형식 (тип двигателя)
        배기량(㏄) (объём, см³)     2,993
        최고출력(PS/rpm) (мощность)  298

    Мы ищем эти корейские подписи в тексте страницы и берём число,
    которое идёт сразу после них. Это НАДЁЖНЕЕ, чем искать по CSS-классам
    (имена классов в вёрстке сайта могут поменяться при любом редизайне,
    а подписи полей — вряд ли).

    Args:
        html: HTML-код страницы (результат fetch_html()).
        url: адрес страницы (сохраним вместе с данными, для ссылки в посте).
        brand: марка авто (BMW, Kia, ...) — передаём явно, чтобы не гадать.
        model: модель авто — тоже передаём явно.

    Returns:
        Список словарей — один словарь на каждую найденную комплектацию.
        Если ничего не нашли — вернёт пустой список (не ошибка, просто
        сигнал "здесь парсить нечего или вёрстка страницы другая").
    """
    # BeautifulSoup(html, "html.parser") — превращает "сырой" HTML-текст
    # в объект, по которому удобно "гулять" и искать нужные части.
    soup = BeautifulSoup(html, "html.parser")

    # get_text(separator="\n") — вытаскивает ВЕСЬ видимый текст страницы,
    # каждый кусочек текста на отдельной строке (как если бы ты выделил
    # всю страницу мышкой и скопировал в блокнот).
    page_text = soup.get_text(separator="\n")

    # re.findall — ищет ВСЕ совпадения по шаблону (регулярному выражению).
    # Шаблон ниже ищет блоки вида:
    #   <название комплектации>
    #   엔진형식
    #   <что угодно>
    #   배기량(㏄)
    #   <число, объём двигателя>
    #   최고출력(PS/rpm)
    #   <число, мощность>
    #
    # \s* означает "любое количество пробелов/переносов строк здесь" —
    # то есть шаблону всё равно, все ли эти подписи идут на отдельных
    # строчках или слитно, главное — что они идут в таком порядке.
    pattern = re.compile(
        r"([^\n]{2,40}?)\s*엔진형식\s*[^\n]{0,40}\s*"
        r"배기량\(㏄\)\s*([\d,]+)\s*"
        r"최고출력\(PS/rpm\)\s*([\d,./~\-]+)"
    )

    found_trims = []

    # Достаём цены и типы топлива один раз для всей страницы — дальше
    # будем сопоставлять их с каждой найденной комплектацией по имени.
    prices = _parse_prices(page_text)
    fuel_by_trim = _parse_fuel_types(page_text)

    for match in pattern.finditer(page_text):
        trim_name_raw, engine_volume_raw, power_raw = match.groups()

        trim_name = trim_name_raw.strip(" -•\t")

        # Убираем запятые из чисел (корейский формат "2,993" -> "2993")
        # и превращаем текст в настоящее число.
        try:
            engine_volume_cc = int(engine_volume_raw.replace(",", ""))
        except ValueError:
            # Если почему-то не смогли превратить в число — пропускаем
            # эту находку, а не роняем всю программу.
            logger.warning(f"Не смог разобрать объём двигателя: {engine_volume_raw!r}")
            continue

        # Мощность иногда идёт как "298", а иногда как "298/5,200-6,250"
        # (мощность/обороты) — нам нужно только первое число.
        power_match = re.match(r"([\d,]+)", power_raw)
        power_hp = int(power_match.group(1).replace(",", "")) if power_match else None

        # Ищем цену и топливо для этой же комплектации (могут не найтись —
        # тогда просто оставляем None, ничего страшного).
        price_entry = _find_matching_value(trim_name, prices)
        price_krw = price_entry["price"] if price_entry else None
        fuel_code = _find_matching_value(trim_name, fuel_by_trim)

        fuel_display = None
        for kr_word, ru_word in FUEL_TRANSLATIONS.items():
            if FUEL_CODES.get(kr_word) == fuel_code:
                fuel_display = ru_word
                break

        found_trims.append({
            "brand": brand,
            "model": model,
            "trim": trim_name,
            "year": None,          # точный год модели пока не определяем —
                                    # можно доработать отдельно при желании
            "cc": engine_volume_cc,        # объём двигателя, см³ (число)
            "hp": power_hp,                 # мощность, л.с. (число)
            "fuel_code": fuel_code,          # код для калькулятора таможни
            "fuel_display": fuel_display,     # "бензин"/"дизель" для поста
            "price": price_krw,                # цена в вонах (число) или None
            "specs": f"{engine_volume_cc} см³, {power_hp} л.с.",
            "url": url,
        })

    if not found_trims:
        logger.warning(
            f"На странице {url} не нашлось ни одной комплектации. "
            "Возможно, изменилась вёрстка сайта, или нужные данные "
            "грузятся через JavaScript (тогда requests их не увидит)."
        )
        # Сохраняем "сырой" текст страницы в отдельный файл — это поможет
        # понять, ПОЧЕМУ парсер ничего не нашёл (можно открыть файл и
        # посмотреть, как реально выглядит текст на этой странице).
        debug_path = "debug_last_failed_page.txt"
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(page_text)
            logger.info(f"Сохранил текст страницы для отладки: {debug_path}")
        except OSError as e:
            logger.warning(f"Не смог сохранить debug-файл: {e}")

    return found_trims


# TODO для дальнейшего развития (когда освоишься с базовой версией):
#   - точнее определять год выпуска конкретного трима (сейчас year=None)
#   - вынести парсинг под конкретного дилера в отдельные файлы
#     (scraper_bmw.py, scraper_kia.py), если вёрстка у разных марок
#     на сайте отличается
