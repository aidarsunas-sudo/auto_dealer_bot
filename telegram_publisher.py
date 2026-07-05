# -*- coding: utf-8 -*-
"""
telegram_publisher.py — здесь код, который отправляет готовый текст
поста в твой Telegram-канал.

Как Telegram-бот вообще может "писать" в канал?
У Telegram есть Bot API — набор простых веб-адресов (URL), на которые
можно отправить запрос, и бот выполнит действие (например, отправит
сообщение). Нам не нужна отдельная библиотека для Telegram — можно
просто отправить обычный HTTP-запрос через requests, как на любой сайт.

Формат такого адреса:
    https://api.telegram.org/bot<ТВОЙ_ТОКЕН>/sendMessage

Мы отправляем на этот адрес данные (текст сообщения, куда его отправить)
методом POST — это как заполнить и отправить веб-форму.
"""

import logging
import requests

import config

logger = logging.getLogger("telegram_publisher")


def send_to_telegram(text: str, dry_run: bool = False) -> bool:
    """
    Отправляет текстовое сообщение в Telegram-канал.

    Args:
        text: готовый текст поста (см. post_builder.build_telegram_post()).
        dry_run: если True — ничего никуда не отправляет, просто печатает
                 текст в консоль. Полезно для тестирования, чтобы не
                 засорять канал, пока проверяешь, что всё работает правильно.

    Returns:
        True, если сообщение успешно отправлено (или это был dry_run).
        False, если что-то пошло не так.
    """
    if dry_run:
        print("═" * 50)
        print("ТЕСТОВЫЙ РЕЖИМ (ничего не отправлено). Вот что получилось бы:")
        print("═" * 50)
        print(text)
        print("═" * 50)
        return True

    # Собираем адрес запроса. config.BOT_TOKEN — твой секретный токен
    # из файла .env, который мы загрузили в config.py.
    telegram_api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"

    # Это данные, которые мы отправляем Telegram: в какой чат/канал писать
    # и какой текст. chat_id может быть как "@имя_канала", так и числовым ID.
    request_data = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
    }

    try:
        # requests.post() отправляет данные на сервер Telegram и ждёт ответа.
        # timeout=30 значит "если за 30 секунд ответа нет — считать, что
        # что-то пошло не так" (иначе программа может зависнуть навсегда).
        response = requests.post(telegram_api_url, json=request_data, timeout=30)

    except requests.RequestException as error:
        # Сюда попадаем, если вообще не получилось связаться с Telegram
        # (например, нет интернета, или Telegram временно недоступен).
        logger.error(f"Не удалось отправить сообщение в Telegram: {error}")
        return False

    if response.ok:
        # response.ok — True, если Telegram ответил "всё хорошо" (код 200).
        logger.info("✅ Сообщение успешно отправлено в Telegram")
        return True
    else:
        # Если Telegram ответил ошибкой (например, неправильный токен,
        # или бот не добавлен в канал как администратор) — покажем, что
        # именно он ответил, чтобы можно было понять причину.
        logger.error(f"❌ Telegram вернул ошибку: {response.status_code} — {response.text}")
        return False
