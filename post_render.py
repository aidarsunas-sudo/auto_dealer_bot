# -*- coding: utf-8 -*-
"""
post_render.py — ЕДИНЫЙ шаблон текста поста канала @auto_generals:
render_post(lot, mode) с режимами
    lead     бронирование («уже можно забронировать») — автопайплайн main_pipeline.py;
             прежний lead_post_template.txt перенесён сюда;
    auction  торги («торги идут, участвуем за вас») — лоты HeyDealer и другие аукционы.

Структура поста (блоки, которых нет в данных, не выводятся — ничего не выдумываем):
    🔥 {название} — {хук режима}!

    📋 Характеристики:
    📅 Год: {год}{примечание}
    🔧 Двигатель: {1,0 л (998 см³), 76 л.с., бензин}
    ⚙️ КПП: {…}
    🛣 Пробег: {…}

    💰 Стоимость под ключ в Казани: {цена} ₽{пояснение про ставку — только auction}

    📄 История: {…}                       ← только если история передана (иначе блока нет,
                                             слово «чистая» не пишется никогда)
    ⏳ Торги завершаются {Сеул} (… МСК).  ← только если известно время окончания; ВСЕГДА
                                             в двух поясах
    👉 Пишите мне — посчитаю точный расчёт под ваш бюджет: @korea_auto_kazan_bot
    {хэштеги — один набор для всех}

Единые правила: десятичная запятая («1,0 л», «12,5 млн ₩»), одни и те же названия
блоков, один призыв писать, один набор хэштегов (#автоизкореи #подзаказ #казань #марка+модель).
Готовый текст ВСЕГДА проходит post_format_schema.validate_post_format() — формат нельзя
нарушить незаметно (ссылка на источник, построчная разбивка расходов и слова «утильсбор»,
«ЕТС» в тексте поста запрещены).

ОТКУДА БЕРЁТСЯ КАЖДОЕ ПОЛЕ — см. FIELD_SOURCES ниже (она же проверяется тестом: каждое
поле, попавшее в пост, обязано быть описано).
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from post_builder import calculate_price_details
from post_format_schema import validate_post_format
from post_text_builder import CTA_BOT, _build_hashtags, prepare_post_fields

logger = logging.getLogger("post_render")

MODES = ("lead", "auction")
CAPTION_LIMIT = 1024  # лимит подписи Telegram к фото/альбому
KST = timezone(timedelta(hours=9))   # корейские площадки отдают время без пояса — это Сеул
MSK = timezone(timedelta(hours=3))   # Москва без перехода на летнее время
_MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря")

HOOKS = {"lead": "уже можно забронировать", "auction": "торги идут, участвуем за вас"}
HOOK_EMOJI = "🔥"
CTA_TEXT = "Пишите мне — посчитаю точный расчёт под ваш бюджет"
PRICE_LABEL = "Стоимость под ключ в Казани"
# OnBid — гос. площадка аукционов: car["price"] там — СТАРТОВАЯ цена торгов, итог может быть выше.
# Префикс «Стоимость под ключ в Казани:» оставляем (его требует post_format_schema), оговорка — в конце строки.
ONBID_PRICE_NOTE = " (расчёт от стартовой цены аукциона — итоговая может быть выше)"

# Откуда берётся каждое поле поста. Ключ — имя блока; значение — источник по режимам.
FIELD_SOURCES = {
    "hook": "режим render_post: lead -> «уже можно забронировать», auction -> «торги идут, участвуем за вас»; константа HOOKS",
    "title": "lot['title'] (если задано вручную, например для HeyDealer с корейским названием), иначе brand + model + trim "
             "(post_text_builder._build_title); проверка на хангыль/недопустимые символы — prepare_post_fields",
    "year": "lot['year'] (lead: год выпуска из объявления площадки; auction: manufactured_date лота HeyDealer, год)",
    "year_note": "lot['year_note'] — необязательное ручное примечание, напр. « (модельный год 2023)»",
    "engine": "lot['cc'], lot['hp'], lot['fuel_code'] -> post_text_builder.format_engine_line() (десятичная запятая); "
              "lead: поля из объявления площадки/справочника model_specs.py; auction: offer_config лота",
    "transmission": "lot['transmission'] (lead: поле площадки, у kbchachacha/OnBid нет -> «не указано»; "
                    "auction: offer_config); корейские значения переводятся post_text_builder._translate_transmission",
    "mileage": "lot['mileage_km'] (lead: объявление; auction: detail.mileage лота HeyDealer)",
    "price": "pricing.calculate_price() от lot['price'] (₩): lead — цена объявления, auction — ориентир ставки "
             "(offer_config.base_krw); параметры формулы — config.py (KOREA_COSTS_PCT, BANK_COMMISSION_PCT, "
             "LOGISTICS_RUB, BROKER_FEE_RUB, MARGIN_RUB, IMPORTER_TYPE), ставки пошлин — customs_rates.py, курсы — rates.py (ЦБ)",
    "price_note": "auction: lot['price'] и lot['cap_price'] (₩, ориентир и верхняя ставка) -> «при ставке X млн ₩ … до Y млн ₩ — до Z ₽»; "
                  "lead: нет, кроме OnBid (source == 'onbid') — оговорка «расчёт от стартовой цены аукциона» (ONBID_PRICE_NOTE)",
    "history": "lot['history'] — готовая строка; lead: обычно нет данных -> блока нет; auction: offer_config.post_history "
               "(сводка из carhistory/осмотра HeyDealer, составляется вручную). Нет данных — блок не выводится",
    "deadline": "lot['auction_end_at']: HeyDealer auction.end_at (время без пояса = Сеул), OnBid bid_deadline и т.п.; "
                "форматируется всегда в двух поясах (Сеул и МСК); нет значения — строки нет",
    "cta": "константа CTA_TEXT + post_text_builder.CTA_BOT (@korea_auto_kazan_bot)",
    "hashtags": "post_text_builder._build_hashtags(brand, model) — #автоизкореи #подзаказ #казань #марка+модель; "
                "если brand/model не заданы (ручной title) — первые два слова названия",
}


class PostRenderError(Exception):
    """Пост собрать нельзя (нет названия/цены/данных или текст не прошёл формат канала)."""


@dataclass
class RenderedPost:
    text: str
    fields: dict
    warnings: list = field(default_factory=list)
    used_fields: tuple = ()


# ── время окончания торгов ────────────────────────────────────────────────

def parse_deadline(value) -> Optional[datetime]:
    """datetime / 'YYYY-MM-DD HH:MM[:SS]' / ISO / 'YYYYMMDDHHMM[SS]' -> aware datetime.
    Время без пояса считается корейским (Сеул, UTC+9). Не распарсилось -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d{12}(\d{2})?", text):
            try:
                parsed = datetime.strptime(text, "%Y%m%d%H%M%S" if len(text) == 14 else "%Y%m%d%H%M")
            except ValueError:
                return None
        else:
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def format_deadline(deadline: datetime) -> str:
    """'9 октября, 13:17 по Сеулу (07:17 МСК)'. Если дата в Москве другая — она указывается."""
    seoul, moscow = deadline.astimezone(KST), deadline.astimezone(MSK)
    msk_part = f"{moscow:%H:%M} МСК"
    if moscow.date() != seoul.date():
        msk_part = f"{moscow.day} {_MONTHS_RU[moscow.month - 1]}, {msk_part}"
    return f"{seoul.day} {_MONTHS_RU[seoul.month - 1]}, {seoul:%H:%M} по Сеулу ({msk_part})"


def format_mln(krw: float) -> str:
    """12000000 -> '12', 12500000 -> '12,5' (десятичная запятая)."""
    return f"{krw / 1_000_000:g}".replace(".", ",")


def _fmt_rub(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


# ── сборка ────────────────────────────────────────────────────────────────

def render_post(lot: dict, mode: str, rates: Optional[dict] = None) -> RenderedPost:
    """
    Текст поста по лоту.

    lot: словарь лота (тот же car-словарь, что у автопайплайна): brand/model/trim ИЛИ title,
         year (month), year_note, mileage_km, cc, hp, fuel_code, transmission, price (₩),
         source; для auction ещё cap_price (₩), history (str), auction_end_at.
    mode: "lead" | "auction".
    rates: курсы ЦБ (rates.fetch_cbr_rates()); не заданы — загружаются.

    Возвращает RenderedPost(text, fields, warnings, used_fields). Поднимает PostRenderError,
    если пост собрать нельзя. warnings — предупреждения расчёта цены (утильсбор не
    льготный, запасные курсы…) и длина подписи; публикацию они не блокируют.
    """
    if mode not in MODES:
        raise PostRenderError(f"неизвестный режим {mode!r}, допустимо: {MODES}")
    if rates is None:
        from rates import fetch_cbr_rates
        rates = fetch_cbr_rates()

    title_override = (lot.get("title") or "").strip()
    car = dict(lot, brand=title_override, model="", trim="") if title_override else dict(lot)
    fields, reason = prepare_post_fields(car, rates, require_photos=(mode == "lead"))
    if fields is None:
        raise PostRenderError(reason)

    used = ["hook", "title", "year", "engine", "transmission", "mileage", "price", "cta", "hashtags"]
    warnings = list(fields.get("price_warnings", []))
    total = _fmt_rub(fields["total_rub_raw"])

    # хэштеги: один набор; при ручном названии марку и модель берём из lot либо из первых слов названия
    if title_override and not (lot.get("brand") or lot.get("model")):
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", title_override)
        hashtags = _build_hashtags({"brand": words[0] if words else "", "model": words[1] if len(words) > 1 else ""})
    else:
        hashtags = _build_hashtags(lot) if title_override else fields["hashtags"]

    year_note = lot.get("year_note") or ""
    if year_note:
        used.append("year_note")

    # цена
    if mode == "auction":
        note = f" при ставке {format_mln(lot['price'])} млн ₩ (итог зависит от торгов"
        cap = lot.get("cap_price")
        if cap:
            cap_details = calculate_price_details(dict(car, price=cap), rates)
            if cap_details:
                note += f"; при ставке до {format_mln(cap)} млн ₩ — до {_fmt_rub(cap_details['total_rub'])} ₽"
                warnings += [w for w in cap_details.get("warnings", []) if w not in warnings]
        price_line = f"💰 {PRICE_LABEL}: {total} ₽{note})"
        used.append("price_note")
    else:
        note = ONBID_PRICE_NOTE if lot.get("source") == "onbid" else ""
        price_line = f"💰 {PRICE_LABEL}: {total} ₽{note}"
        if note:
            used.append("price_note")

    lines = [
        f"{HOOK_EMOJI} {fields['title']} — {HOOKS[mode]}!",
        "",
        "📋 Характеристики:",
        f"📅 Год: {fields['year']}{year_note}",
        f"🔧 Двигатель: {fields['engine_line']}",
        f"⚙️ КПП: {fields['transmission']}",
        f"🛣 Пробег: {fields['mileage_km']}",
        "",
        price_line,
    ]

    history = (lot.get("history") or "").strip()
    if history:
        lines += ["", f"📄 История: {history}"]
        used.append("history")

    deadline = parse_deadline(lot.get("auction_end_at"))
    if deadline is not None:
        lines += ["", f"⏳ Торги завершаются {format_deadline(deadline)}."]
        used.append("deadline")

    lines += ["", f"👉 {CTA_TEXT}: {CTA_BOT}", "", hashtags]
    text = "\n".join(lines) + "\n"

    verdict = validate_post_format(text)
    if not verdict:
        raise PostRenderError("текст не прошёл формат канала: " + "; ".join(verdict.errors))
    if len(text.strip()) > CAPTION_LIMIT:
        warnings.append(f"подпись {len(text.strip())} симв. > {CAPTION_LIMIT} (лимит Telegram для фото/альбома) — сократите историю")
    for warning in warnings:
        logger.warning(f"render_post({mode}) {fields['title']}: {warning}")
    return RenderedPost(text=text, fields=fields, warnings=warnings, used_fields=tuple(used))
