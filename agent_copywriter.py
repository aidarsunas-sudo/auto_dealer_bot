# -*- coding: utf-8 -*-
"""
agent_copywriter.py — "агент-копирайтер" в мультиагентном пайплайне (orchestrator.py).

Повторяет тот же отказоустойчивый паттерн, что и post_generator.generate_smart_post()
(см. этот файл для подробного объяснения идеи "цифры считаем сами, Claude только
оформляет"), но:
    1. умеет выбирать один из ТРЁХ системных промптов в зависимости от типа
       поста, который порекомендовал agent_analyst.py ("обзор"/"акция"/
       "горячее предложение") — меняется только тон подачи, а не факты;
    2. умеет учесть замечания agent_editor.py и переписать пост ещё раз
       (параметр editor_feedback).

Это ОТДЕЛЬНЫЙ от post_generator.py генератор — post_generator.py и main.py
не трогаем, чтобы старый путь публикации продолжал работать как раньше.
"""

import logging

import config
from post_builder import calculate_price_details, build_telegram_post

logger = logging.getLogger("agent_copywriter")

FUEL_LABELS = {
    "petrol": "бензин",
    "diesel": "дизель",
    "hybrid_p": "гибрид",
    "hybrid_d": "гибрид",
    "elec": "электро",
}

# Общая часть правил — одинаковая для всех трёх тональностей (жёсткие
# ограничения, которые нельзя нарушать ни в одном из типов поста).
_COMMON_RULES = """Правила:
- Используй ТОЛЬКО те факты, которые тебе дали в сообщении — ничего не
  придумывай и не добавляй от себя (характеристики, цену, комплектацию).
- НИКОГДА не меняй и не округляй указанную цену — используй её ровно
  такой, какая дана.
- Длина: 4-8 строк, не считая контактов в конце.
- В конце ВСЕГДА добавляй контакты ровно в этом виде:
  📩 @korea_auto_kazan_bot
  📢 @auto_generals
- Не используй markdown-разметку (**жирный**, _курсив_) — Telegram обычный
  текст показывает лучше, чем недообработанный markdown.
- Пиши только сам текст поста, без вступления вроде "Вот пост:" и без
  пояснений после."""

SYSTEM_PROMPTS = {
    "обзор": f"""Ты — копирайтер автомобильного канала в Telegram (Pick+ Auto,
доставка авто из Кореи в Казань). Пишешь короткие продающие посты о
конкретных автомобилях.

Тон: спокойный, дружелюбный, информативный, в меру эмоциональный, без
канцелярита. Уместны эмодзи (2-6 штук на пост), но не перегружай ими текст.
Это обычный, нейтральный пост о машине — без нагнетания срочности.

{_COMMON_RULES}""",

    "акция": f"""Ты — копирайтер автомобильного канала в Telegram (Pick+ Auto,
доставка авто из Кореи в Казань). Пишешь короткие продающие посты о
конкретных автомобилях.

Тон: живой, дружелюбный, с явным акцентом на выгоде — подчеркни, что цена
снизилась и это хорошая возможность купить дешевле, чем обычно. Эмодзи (3-6
штук), можно использовать эмодзи со смыслом "скидка/выгода" (💸📉🔥).

{_COMMON_RULES}""",

    "горячее предложение": f"""Ты — копирайтер автомобильного канала в Telegram (Pick+ Auto,
доставка авто из Кореи в Казань). Пишешь короткие продающие посты о
конкретных автомобилях.

Тон: самый эмоциональный и срочный из всех — дай понять, что это
исключительное предложение и раздумывать некогда (в духе "успей забронировать",
"предложение ограничено"). Обязательно включи явный призыв к действию.
Не выдумывай ложных ограничений (типа выдуманного количества оставшихся
единиц) — просто передай ощущение срочности тоном и словами. Эмодзи (3-6
штук), можно использовать яркие (🔥⚡️🚨).

{_COMMON_RULES}""",
}


def _build_user_prompt(car: dict, price_details: dict, post_type: str) -> str:
    """Собирает текст с фактами о машине, который отправим Claude."""
    brand = car.get("brand", "")
    model = car.get("model", "")
    trim = car.get("trim", "")
    manufacture_year = car.get("year")
    mileage_km = car.get("mileage_km")
    cc = car.get("cc")
    hp = car.get("hp")
    fuel_label = FUEL_LABELS.get(car.get("fuel_code"), car.get("fuel_code"))
    url = car.get("url", "")

    facts = [f"Марка и модель: {brand} {model} {trim}".strip()]

    if manufacture_year:
        facts.append(f"Год выпуска: {manufacture_year}")
    else:
        facts.append("Это НОВЫЙ автомобиль (актуальный модельный год у дилера)")

    if mileage_km is not None:
        facts.append(f"Пробег: {mileage_km:,} км".replace(",", " "))

    if cc and hp:
        facts.append(f"Двигатель: {cc} см³, {hp} л.с., {fuel_label}")

    total_formatted = f"{price_details['total_rub']:,}".replace(",", " ")
    facts.append(f"Цена под ключ в Казани: {total_formatted} ₽ (включает пошлину, ЕТС, "
                  f"утильсбор, доставку и комиссию — это ФИНАЛЬНАЯ цена, используй её как есть)")

    if url:
        facts.append(f"Ссылка на объявление: {url}")

    facts.append(f"Тип поста: {post_type}")

    return "Напиши продающий пост про эту машину:\n\n" + "\n".join(f"- {f}" for f in facts)


def write_post(car: dict, rates: dict, post_type: str, editor_feedback: str = None) -> tuple:
    """
    Генерирует текст поста заданного типа через Claude API. При любой проблеме
    (нет ключа, сбой сети, ошибка API, не хватает данных для расчёта цены) —
    автоматически откатывается на обычный шаблонный генератор
    (post_builder.build_telegram_post), чтобы публикация не останавливалась.

    Args:
        car: словарь с данными о машине.
        rates: курсы валют для расчёта таможни.
        post_type: "обзор" | "акция" | "горячее предложение".
        editor_feedback: если это повторная попытка после замечаний
                         agent_editor.py — сюда передаётся текст замечаний,
                         чтобы Claude их учёл.

    Returns:
        (post_text, price_details) — price_details нужен agent_editor.py для
        сверки цифр (None, если использовался шаблонный откат).
    """
    if not config.ANTHROPIC_API_KEY:
        logger.info("ANTHROPIC_API_KEY не задан — использую шаблонный генератор")
        return build_telegram_post(car, rates), None

    try:
        price_details = calculate_price_details(car, rates)
    except Exception as error:
        logger.warning(f"Не удалось посчитать цену для умного поста ({error}) — использую шаблон")
        return build_telegram_post(car, rates), None

    if price_details is None:
        logger.info("Не хватает данных для расчёта цены — использую шаблонный генератор")
        return build_telegram_post(car, rates), None

    try:
        import anthropic  # лениво — чтобы модуль не падал при импорте, если
                           # библиотека anthropic вдруг не установлена

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

        user_prompt = _build_user_prompt(car, price_details, post_type)
        if editor_feedback:
            user_prompt += (
                "\n\nЗамечания редактора к предыдущей версии, обязательно учти их:\n"
                f"{editor_feedback}"
            )

        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=500,
            system=SYSTEM_PROMPTS.get(post_type, SYSTEM_PROMPTS["обзор"]),
            messages=[{"role": "user", "content": user_prompt}],
        )

        post_text = response.content[0].text.strip()

        logger.info(
            f"agent_copywriter: пост типа '{post_type}' сгенерирован "
            f"({response.usage.input_tokens} вх. + {response.usage.output_tokens} исх. токенов)"
        )
        return post_text, price_details

    except ImportError:
        logger.warning("Библиотека 'anthropic' не установлена (pip install anthropic) — использую шаблон")
        return build_telegram_post(car, rates), price_details
    except Exception as error:
        logger.warning(f"Ошибка Claude API ({error}) — использую шаблонный генератор")
        return build_telegram_post(car, rates), price_details
