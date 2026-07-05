# -*- coding: utf-8 -*-
"""
post_generator.py — генерирует ПРОДАЮЩИЙ, красивый пост для Telegram
через Claude API, вместо простой подстановки цифр в шаблон
(тем же самым, но без "искры", занимается post_builder.py).

Как это устроено и почему именно так:

1. ВСЕ ЦИФРЫ (цена, объём, мощность, пробег) мы считаем САМИ, заранее,
   через calculate_price_details() из post_builder.py — точно так же,
   как в шаблонном генераторе. Claude НЕ придумывает цифры — только
   красиво их подаёт. Это важно: если довериться модели в самих расчётах,
   она может ошибиться (или "договорить" правдоподобную, но неверную
   цифру) — а тут реальные деньги клиента, ошибаться нельзя.

2. Мы отправляем Claude уже готовый, точный набор фактов о машине и
   просим ТОЛЬКО оформить их в живой, продающий текст с эмодзи —
   это и есть "умная генерация", но без риска для точности.

3. Если API недоступен (нет ключа, нет интернета, кончились деньги на
   счету) — функция автоматически откатывается на обычный шаблонный
   post_builder.build_telegram_post(), чтобы бот продолжал работать
   даже без Claude API.

Установка библиотеки:
    pip install anthropic
"""

import logging

import config
from post_builder import calculate_price_details, build_telegram_post

logger = logging.getLogger("post_generator")

FUEL_LABELS = {
    "petrol": "бензин",
    "diesel": "дизель",
    "hybrid_p": "гибрид",
    "hybrid_d": "гибрид",
    "elec": "электро",
}

# Системный промпт — задаёт Claude "роль" и общие правила оформления.
# Вынесен отдельной константой, чтобы легко было подправить стиль
# (например, сделать текст более сдержанным или, наоборот, ярче),
# не копаясь в остальном коде функции.
SYSTEM_PROMPT = """Ты — копирайтер автомобильного канала в Telegram (Pick+ Auto,
доставка авто из Кореи в Казань). Пишешь короткие продающие посты о
конкретных автомобилях.

Правила:
- Используй ТОЛЬКО те факты, которые тебе дали в сообщении — ничего не
  придумывай и не добавляй от себя (характеристики, цену, комплектацию).
- НИКОГДА не меняй и не округляй указанную цену — используй её ровно
  такой, какая дана.
- Стиль: живой, дружелюбный, в меру эмоциональный, без канцелярита.
  Уместны эмодзи (2-6 штук на пост), но не перегружай ими текст.
- Длина: 4-8 строк, не считая контактов в конце.
- В конце ВСЕГДА добавляй контакты ровно в этом виде:
  📩 @korea_auto_kazan_bot
  📢 @auto_generals
- Не используй markdown-разметку (**жирный**, _курсив_) — Telegram обычный
  текст показывает лучше, чем недообработанный markdown.
- Пиши только сам текст поста, без вступления вроде "Вот пост:" и без
  пояснений после."""


def _build_user_prompt(car: dict, price_details: dict) -> str:
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

    return "Напиши продающий пост про эту машину:\n\n" + "\n".join(f"- {f}" for f in facts)


def generate_smart_post(car: dict, rates: dict) -> str:
    """
    Генерирует продающий текст поста через Claude API. При любой проблеме
    (нет ключа, сбой сети, ошибка API) — автоматически откатывается на
    обычный шаблонный генератор (post_builder.build_telegram_post), чтобы
    публикация постов не останавливалась из-за недоступности Claude API.

    Args:
        car: словарь с данными о машине (тот же формат, что и для
             post_builder.build_telegram_post).
        rates: курсы валют для расчёта таможни.

    Returns:
        Готовый текст поста.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.info("ANTHROPIC_API_KEY не задан — использую шаблонный генератор")
        return build_telegram_post(car, rates)

    try:
        price_details = calculate_price_details(car, rates)
    except Exception as error:
        logger.warning(f"Не удалось посчитать цену для умного поста ({error}) — использую шаблон")
        return build_telegram_post(car, rates)

    if price_details is None:
        # Не хватает данных для расчёта — Claude тут не поможет, честнее
        # отдать это шаблонному генератору (он умеет вежливо написать
        # "цена уточняется").
        logger.info("Не хватает данных для расчёта цены — использую шаблонный генератор")
        return build_telegram_post(car, rates)

    try:
        import anthropic  # импортируем здесь, а не в начале файла — чтобы
                           # программа не падала при импорте post_generator.py,
                           # если библиотека anthropic вдруг не установлена
                           # и Claude API вообще не используется

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

        user_prompt = _build_user_prompt(car, price_details)

        response = client.messages.create(
            model="claude-sonnet-5",  # хорошее качество текста за разумные деньги;
                                        # для экономии на больших объёмах можно
                                        # попробовать "claude-haiku-4-5-20251001"
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        post_text = response.content[0].text.strip()

        logger.info(
            f"Пост сгенерирован через Claude API "
            f"({response.usage.input_tokens} вх. + {response.usage.output_tokens} исх. токенов)"
        )
        return post_text

    except ImportError:
        logger.warning("Библиотека 'anthropic' не установлена (pip install anthropic) — использую шаблон")
        return build_telegram_post(car, rates)
    except Exception as error:
        # Сюда попадают любые ошибки Claude API: неверный ключ, кончились
        # деньги на счету, сбой сети, превышен лимит запросов и т.п.
        # Не роняем публикацию — просто используем шаблонный текст.
        logger.warning(f"Ошибка Claude API ({error}) — использую шаблонный генератор")
        return build_telegram_post(car, rates)
