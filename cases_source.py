"""
cases_source.py — источник «примеров расчёта» для статей (не для публикации лидогена).

Берёт лоты из data/cars.db, прогоняет их теми же проверками, что и бот
(check_price_vs_batch_median на всей выборке, затем prepare_post_fields и
validate_post_before_publish), считает цену через pricing.calculate_price()
и возвращает готовый Markdown-блок «Пример расчёта на основе лота».

  from cases_source import example_block, example_block_for_topic
  example_block("Audi", "Q3")              # до 3 лотов, страна по умолчанию — Корея
  example_block_for_topic(topic_row)        # строка из wordstat/topics.csv

Нет подходящих лотов (или страна не Корея — в базе только корейские площадки) —
возвращается NO_EXAMPLE. Ничего не выдумываем.

Побочные эффекты проверок бота изолированы: построчные REJECT из post_validator
НЕ пишутся в боевой rejected_posts.log (путь на время вызова подменяется на
os.devnull) — бот и его файлы не затрагиваются.
"""
import json
import logging
import os
import random
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import config
import post_validator
import pricing
import rates as rates_mod
from brand_names_ko import BRAND_KO, from_korean
from model_specs import find_spec
from post_text_builder import HANGUL_RE, _build_title, prepare_post_fields

DB_PATH = Path(__file__).resolve().parent / "data" / "cars.db"
NO_EXAMPLE = "[НЕТ ПРИМЕРА]"
MAX_EXAMPLES = 3
COUNTRY_DEFAULT = "Корея"
# Страницы новых авто у дилеров: нет цены в вонах, года и пробега — в примеры не берём.
EXCLUDED_HOSTS = ("carnoon.co.kr",)

RATES_CACHE = Path(__file__).resolve().parent / "data" / "cbr_rates_cache.json"
_rates_memo: dict | None = None
RATES_ATTEMPTS, RATES_PAUSE_SEC = 3, (2, 5)

log = logging.getLogger("cases_source")


def get_rates() -> dict:
    """Курс ЦБ один раз за запуск (память процесса) и один раз в день (файл RATES_CACHE).
    В файл пишется только живой курс; запасной (_source == "fallback") не кэшируется."""
    global _rates_memo
    if _rates_memo is not None:
        return _rates_memo
    today = date.today().isoformat()
    try:
        cached = json.loads(RATES_CACHE.read_text(encoding="utf-8"))
        if cached.get("date") == today and cached["rates"].get("_source") == "cbr":
            _rates_memo = cached["rates"]
            return _rates_memo
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    for attempt in range(RATES_ATTEMPTS):
        _rates_memo = rates_mod.fetch_cbr_rates()
        if _rates_memo.get("_source") == "cbr":
            break
        if attempt < RATES_ATTEMPTS - 1:
            pause = random.uniform(*RATES_PAUSE_SEC)
            log.warning(f"курс ЦБ не получен (попытка {attempt + 1}/{RATES_ATTEMPTS}), повтор через {pause:.1f} с")
            time.sleep(pause)
    if _rates_memo.get("_source") == "cbr":
        try:
            RATES_CACHE.write_text(json.dumps({"date": today, "rates": _rates_memo}), encoding="utf-8")
        except OSError as e:
            log.warning(f"не удалось записать кэш курса: {e}")
    return _rates_memo


class _ConfigView:
    """Копия config только для post_validator: переопределяет перечисленные поля,
    остальное берёт у настоящего config. Глобальный config и окружение не затрагиваются."""

    def __init__(self, **overrides):
        self._overrides = overrides

    def __getattr__(self, name):
        return self._overrides[name] if name in self._overrides else getattr(config, name)


@contextmanager
def _quiet_validator(require_photos: bool):
    """Не писать в боевой rejected_posts.log и не шуметь в stderr от проверок бота;
    require_photos=False снимает ТОЛЬКО порог фото внутри post_validator на время вызова."""
    old_path, old_level, old_cfg = (post_validator.REJECTED_LOG_PATH, post_validator.logger.level,
                                    post_validator.config)
    post_validator.REJECTED_LOG_PATH = Path(os.devnull)
    post_validator.logger.setLevel(logging.CRITICAL)
    if not require_photos:
        post_validator.config = _ConfigView(MIN_PHOTOS_PER_POST=0)
    try:
        yield
    finally:
        post_validator.REJECTED_LOG_PATH = old_path
        post_validator.logger.setLevel(old_level)
        post_validator.config = old_cfg


def _brand_from_name(model_text: str) -> tuple[str, str] | None:
    """Колонка марки пуста: марка — первое слово названия, если оно ОДНОЗНАЧНО совпало с известной
    маркой (латиница — ключи BRAND_KO, корейский — через from_korean). Возвращает (марка, остаток названия)."""
    first, _, rest = (model_text or "").strip().partition(" ")
    rest = rest.strip()
    if not first or not rest:
        return None
    found = {from_korean(BRAND_KO[k]) for k in BRAND_KO if k == first.lower()}
    found |= {b for b in [from_korean(first)] if b != first}  # корейская марка из словаря
    return (found.pop(), rest) if len(found) == 1 else None


def _load_cars() -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT brand, model, trim, external_id, year, mileage_km, price, cc, hp, fuel_code, specs, url "
            "FROM cars")]
    finally:
        con.close()
    rows = [r for r in rows if not any(h in (r["url"] or "") for h in EXCLUDED_HOSTS)]
    for r in rows:
        if not (r["brand"] or "").strip():
            split = _brand_from_name(r["model"])
            if split:
                r["brand"], r["model"] = split  # только в памяти, cars.db не меняется
        try:
            r["year"] = int(r["year"]) if r["year"] not in (None, "") else None
        except ValueError:
            r["year"] = None
        r["brand"] = r["brand"] or ""
        r["photos"] = []  # в cars.db фото не хранятся
    return rows


def _fill_from_specs(car: dict) -> None:
    """Объём/мощность/топливо из model_specs.py — только то, чего нет в базе."""
    if car.get("cc") and car.get("hp") and car.get("fuel_code"):
        return
    spec = find_spec(_build_title(car), car.get("year"))
    if spec is None:
        return
    car["cc"] = car.get("cc") or spec.cc
    car["hp"] = car.get("hp") or spec.hp
    car["fuel_code"] = car.get("fuel_code") or spec.fuel


def _matches(car: dict, brand: str | None, model: str | None) -> bool:
    hay = f"{car['brand']} {car['model']} {car.get('trim') or ''}".lower()
    return all(t in hay for t in re.findall(r"[a-zа-яё0-9\-]+", f"{brand or ''} {model or ''}".lower()))


def _named(car: dict) -> bool:
    """Марка и модель определены (непусты) и не на корейском — иначе пример не строим."""
    return all(v and not HANGUL_RE.search(v) for v in (car["brand"].strip(), (car["model"] or "").strip()))


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _render(car: dict, res: "pricing.PriceResult", rates: dict, today: date) -> str:
    name = f"{car['brand']} {car['model']}".strip()
    duties = res.customs_rub - res.util_rub  # таможенный сбор + ЕТС (пошлина)
    util_note = " (льготная ставка для физлица)" if res.util_lgota else ""
    return "\n".join([
        "**Пример расчёта на основе лота**",
        "",
        f"- Автомобиль: {name}, {car['year']} год",
        f"- Пробег: {_fmt(car['mileage_km'])} км",
        f"- Цена в Корее: {_fmt(car['price'])} ₩",
        f"- Таможенные платежи (сбор и пошлина): {_fmt(duties)} ₽",
        f"- Утильсбор: {_fmt(res.util_rub)} ₽{util_note}",
        f"- Итоговая цена в рублях (под ключ в Казани): {_fmt(res.total_rub)} ₽",
        "- Курс ЦБ: " + f"1 000 ₩ = {rates['krw_rub'] * 1000:.2f}".replace(".", ",") + f" ₽, дата расчёта {today:%d.%m.%Y}",
        "",
        f"_Это пример на {today:%d.%m.%Y}: итоговая цена зависит от курса и конкретного лота._",
    ])


def example_block(brand: str | None = None, model: str | None = None,
                  country: str = COUNTRY_DEFAULT, rates: dict | None = None,
                  require_photos: bool = False) -> str:
    """Блок(и) «Пример расчёта на основе лота» (до MAX_EXAMPLES) или NO_EXAMPLE.

    require_photos=False (по умолчанию) — в cars.db фото нет, поэтому порог фото в
    validate_post_before_publish снимается ТОЛЬКО на время вызовов отсюда; True — как в боте.
    Остальные проверки (медиана партии, цена, пробег, год, полнота данных) всегда включены.
    """
    if country.strip().lower() != COUNTRY_DEFAULT.lower():
        return NO_EXAMPLE  # в базе только корейские площадки
    try:
        cars = _load_cars()
    except sqlite3.Error as e:
        log.error(f"cars.db недоступна: {e}")
        return NO_EXAMPLE

    rates = rates or get_rates()
    if rates.get("_source") == "fallback":
        log.warning("курс ЦБ недоступен, запасной курс для статей не используем")
        return NO_EXAMPLE
    today = date.today()
    params = pricing.PricingParams.from_config()

    chosen, seen = [], set()
    with _quiet_validator(require_photos):
        # групповой гейт по медиане — на ВСЕЙ выборке, как в target_collector/broad_collector
        kept = post_validator.check_price_vs_batch_median(cars, log, "cases_source")
        pool = [c for c in kept if _named(c) and _matches(c, brand, model)
                and c.get("price") and c.get("mileage_km") is not None]
        pool.sort(key=lambda c: (-(c["year"] or 0), c["mileage_km"]))
        for car in pool:
            key = (car["brand"], car["model"], car["year"], car["mileage_km"], car["price"])
            if key in seen:
                continue
            seen.add(key)
            if HANGUL_RE.search(_build_title(car)):
                car = {**car, "trim": ""}  # в блоке комплектации нет — корейская не должна блокировать
            _fill_from_specs(car)
            fields, reason = prepare_post_fields(car, rates, require_photos=False)
            if fields is None:
                log.info(f"{car.get('url')}: пропуск ({reason})")
                continue
            validated, reason = post_validator.validate_post_before_publish(car, fields)
            if validated is None:
                log.info(f"{car.get('url')}: пропуск ({reason})")
                continue
            res = pricing.calculate_price(validated, rates, params, today)
            if res is None:
                continue
            chosen.append(_render(validated, res, rates, today))
            if len(chosen) == MAX_EXAMPLES:
                break
    return "\n\n".join(chosen) if chosen else NO_EXAMPLE


def example_block_for_topic(topic: dict, rates: dict | None = None) -> str:
    """По строке wordstat/topics.csv: марка/модель — латинские слова запроса, которые есть в базе;
    страна — только по главному запросу: Китай, если в нём китай/китайск/changan/чанган; Корея — если есть
    корея/кореи/корейск или нет ни тех, ни других слов."""
    text = f"{topic.get('главный запрос', '')} {topic.get('сопутствующие', '')}".lower()
    main = (topic.get("главный запрос") or "").lower()
    country = "Китай" if re.search(r"китай|китайск|changan|чанган", main) else COUNTRY_DEFAULT
    words = set(re.findall(r"[a-z0-9\-]+", text))
    try:
        cars = _load_cars()
    except sqlite3.Error:
        return NO_EXAMPLE
    brands = {c["brand"].lower() for c in cars if c["brand"]}
    brand = next((w for w in words if w in brands), None)
    model = None
    if brand:
        models = {t for c in cars if c["brand"].lower() == brand for t in re.findall(r"[a-z0-9\-]+", c["model"].lower())}
        model = " ".join(sorted(words & models)) or None
    return example_block(brand, model, country, rates)
