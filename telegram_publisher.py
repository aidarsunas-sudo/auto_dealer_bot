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
import os
import sys
import requests

import config

logger = logging.getLogger("telegram_publisher")

# Telegram: подпись к фото/альбому — максимум 1024 символа (у обычного
# текстового сообщения лимит другой, 4096) — если текст поста длиннее,
# аккуратно обрезаем, а не падаем с ошибкой API.
CAPTION_MAX_LENGTH = 1024

# Telegram: альбом (sendMediaGroup) — от 2 до 10 файлов за раз.
MEDIA_GROUP_MAX_ITEMS = 10


def _safe_caption(text: str) -> str:
    if len(text) <= CAPTION_MAX_LENGTH:
        return text
    return text[: CAPTION_MAX_LENGTH - 1] + "…"


def _safe_print(text: str) -> None:
    """
    dry_run печатает реальный текст поста — у broad-режима без совпадения с
    target_models.json там может остаться необработанный корейский текст
    (title с сайта-источника). На консоли с не-UTF8 кодировкой (например,
    cp1251 на русской Windows) обычный print() в этом случае падает с
    UnicodeEncodeError — тест локально "падает" ещё до реальной отправки в
    Telegram (которая всегда кодирует JSON как UTF-8 и этой проблемы не
    имеет). Здесь — не даём кодировке консоли ломать тестовый прогон.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def send_document_to_telegram(document: str, caption: str = "", dry_run: bool = False,
                              chat_id: str | None = None, parse_mode: str | None = None) -> bool:
    """
    Отправляет ЛОКАЛЬНЫЙ файл (например, HTML-отчёт) как документ — Bot API
    sendDocument. Нужен, чтобы прислать самодостаточный report.html: получатель
    открывает его одним файлом, со встроенными фото и кликабельными ссылками.

    Сделано по образцу agent_publisher.send_video_to_telegram (тот же приём
    multipart-загрузки локального файла), но с параметром chat_id — отчёты
    мониторинга уходят в служебный чат (config.ADMIN_CHAT_ID), а не в
    публичный канал лидогена.

    Args:
        document: путь к локальному файлу либо прямая ссылка/file_id.
        caption: подпись (лимит Telegram — 1024 символа, обрезается).
        dry_run: ничего не отправлять, только напечатать.
        chat_id: адресат; по умолчанию config.TELEGRAM_CHAT_ID.
        parse_mode: "HTML" — если в подписи есть разметка/ссылки.

    Returns:
        True при успехе (или dry_run), False при любой ошибке. Исключение
        не поднимает — как и остальные отправки в этом проекте.
    """
    chat_id = chat_id if chat_id is not None else config.TELEGRAM_CHAT_ID

    if dry_run:
        print("=" * 50)
        print(f"ТЕСТОВЫЙ РЕЖИМ (документ не отправлен). document={document}")
        _safe_print(caption)
        print("=" * 50)
        return True

    api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendDocument"
    data = {"chat_id": chat_id, "caption": _safe_caption(caption)}
    if parse_mode:
        data["parse_mode"] = parse_mode

    is_local = (not document.startswith(("http://", "https://"))) and os.path.exists(document)
    try:
        if is_local:
            with open(document, "rb") as fh:
                response = requests.post(api_url, data=data, files={"document": fh}, timeout=120)
        else:
            data["document"] = document
            response = requests.post(api_url, data=data, timeout=60)
        if response.status_code == 200:
            logger.info("Документ отправлен в Telegram")
            return True
        logger.error(f"Telegram sendDocument вернул {response.status_code}: {response.text[:300]}")
        return False
    except Exception as error:
        logger.error(f"Не удалось отправить документ в Telegram: {error}")
        return False


def send_to_telegram(text: str, dry_run: bool = False, chat_id: str | None = None,
                     parse_mode: str | None = None) -> bool:
    """
    Отправляет текстовое сообщение в Telegram-канал.

    Args:
        text: готовый текст поста (см. post_builder.build_telegram_post()).
        dry_run: если True — ничего никуда не отправляет, просто печатает
                 текст в консоль. Полезно для тестирования, чтобы не
                 засорять канал, пока проверяешь, что всё работает правильно.
        chat_id: куда слать. По умолчанию (None) — config.TELEGRAM_CHAT_ID
                 (публичный канал лидогена), как и раньше — все существующие
                 вызовы не меняют поведение. Передают явно, например,
                 config.YANDEX_DRAFTS_CHAT_ID — служебный чат для черновиков
                 Яндекс-витрины (см. yandex_publisher.py), никогда не тот же
                 канал, что публичный лидоген.

    Returns:
        True, если сообщение успешно отправлено (или это был dry_run).
        False, если что-то пошло не так.
    """
    chat_id = chat_id if chat_id is not None else config.TELEGRAM_CHAT_ID

    if dry_run:
        print("=" * 50)
        print("ТЕСТОВЫЙ РЕЖИМ (ничего не отправлено). Вот что получилось бы:")
        print("=" * 50)
        _safe_print(text)
        print("=" * 50)
        return True

    # Собираем адрес запроса. config.BOT_TOKEN — твой секретный токен
    # из файла .env, который мы загрузили в config.py.
    telegram_api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"

    # Это данные, которые мы отправляем Telegram: в какой чат/канал писать
    # и какой текст. chat_id может быть как "@имя_канала", так и числовым ID.
    request_data = {
        "chat_id": chat_id,
        "text": text,
    }
    # parse_mode нужен, когда в тексте есть разметка — например, кликабельные
    # <a href> ссылки в сводке мониторинга. По умолчанию None: существующие
    # вызовы (лидоген) шлют обычный текст и ведут себя как раньше.
    if parse_mode:
        request_data["parse_mode"] = parse_mode

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


def send_photo_to_telegram(photo_url: str, caption: str, dry_run: bool = False) -> bool:
    """
    Отправляет ОДНО фото с подписью (caption = текст поста) методом sendPhoto.
    photo_url — прямая ссылка на изображение (Telegram сам её скачает),
    локальные файлы сюда не передаются (все источники отдают только URL).
    """
    caption = _safe_caption(caption)

    if dry_run:
        print("=" * 50)
        print(f"ТЕСТОВЫЙ РЕЖИМ (фото не отправлено). photo={photo_url}")
        print("=" * 50)
        _safe_print(caption)
        print("=" * 50)
        return True

    telegram_api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendPhoto"
    request_data = {"chat_id": config.TELEGRAM_CHAT_ID, "photo": photo_url, "caption": caption}

    try:
        response = requests.post(telegram_api_url, json=request_data, timeout=30)
    except requests.RequestException as error:
        logger.error(f"Не удалось отправить фото в Telegram: {error}")
        return False

    if response.ok:
        logger.info("✅ Фото с подписью успешно отправлено в Telegram")
        return True
    logger.error(f"❌ Telegram вернул ошибку (sendPhoto): {response.status_code} — {response.text}")
    return False


def send_media_group_to_telegram(photo_urls: list, caption: str, dry_run: bool = False,
                                 chat_id: str | None = None) -> bool:
    """
    Отправляет НЕСКОЛЬКО фото одним альбомом (sendMediaGroup) — первое фото
    с подписью (caption = текст поста), остальные без подписи (так их
    видит Telegram-канал — одна подпись на весь альбом, не на каждое фото).

    chat_id: куда слать — как и у send_to_telegram(), по умолчанию (None)
        config.TELEGRAM_CHAT_ID (существующие вызовы не меняют поведение).
        yandex_digest_builder.py передаёт config.YANDEX_DRAFTS_CHAT_ID —
        служебный чат, не публичный канал лидогена.
    """
    chat_id = chat_id if chat_id is not None else config.TELEGRAM_CHAT_ID
    photo_urls = photo_urls[:MEDIA_GROUP_MAX_ITEMS]
    caption = _safe_caption(caption)

    if dry_run:
        print("=" * 50)
        print(f"ТЕСТОВЫЙ РЕЖИМ (альбом из {len(photo_urls)} фото не отправлен)")
        print("=" * 50)
        _safe_print(caption)
        print("=" * 50)
        return True

    telegram_api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMediaGroup"
    media = []
    for i, url in enumerate(photo_urls):
        item = {"type": "photo", "media": url}
        if i == 0:
            item["caption"] = caption
        media.append(item)

    request_data = {"chat_id": chat_id, "media": media}

    try:
        response = requests.post(telegram_api_url, json=request_data, timeout=30)
    except requests.RequestException as error:
        logger.error(f"Не удалось отправить альбом в Telegram: {error}")
        return False

    if response.ok:
        logger.info(f"✅ Альбом из {len(photo_urls)} фото успешно отправлен в Telegram")
        return True
    logger.error(f"❌ Telegram вернул ошибку (sendMediaGroup): {response.status_code} — {response.text}")
    return False
