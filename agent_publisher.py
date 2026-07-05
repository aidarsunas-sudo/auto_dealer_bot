# -*- coding: utf-8 -*-
"""
agent_publisher.py — "агент-публикатор" в мультиагентном пайплайне (orchestrator.py).

publish_post() — тонкая обёртка над telegram_publisher.send_to_telegram(),
существует здесь только для единообразия имён агентов в orchestrator.py.

send_video_to_telegram() — новая функция, использующая метод Bot API
sendVideo. Сейчас её никто не вызывает (scraper.py не собирает видео с
сайтов дилеров) — это задел на будущее, точка расширения.
"""

import logging
import os

import requests

import config
from telegram_publisher import send_to_telegram

logger = logging.getLogger("agent_publisher")


def publish_post(text: str, dry_run: bool = False) -> bool:
    """Публикует готовый текст поста — обёртка над telegram_publisher.send_to_telegram()."""
    return send_to_telegram(text, dry_run=dry_run)


def send_video_to_telegram(video: str, caption: str = "", dry_run: bool = False) -> bool:
    """
    Отправляет видео в Telegram-канал через Bot API метод sendVideo.

    Args:
        video: локальный путь к файлу, ПРЯМАЯ ссылка на видео (http/https)
               или уже загруженный в Telegram file_id — sendVideo принимает
               любой из этих трёх вариантов.
        caption: подпись к видео (аналог text у send_to_telegram).
        dry_run: как и у send_to_telegram — ничего не отправлять, только
                 напечатать в консоль.

    Returns:
        True при успехе (или dry_run). False при любой ошибке — сеть,
        неверный путь/URL, ошибка API. Никогда не поднимает исключение,
        как и остальные функции отправки в этом проекте.
    """
    if dry_run:
        print("═" * 50)
        print(f"ТЕСТОВЫЙ РЕЖИМ (видео не отправлено). video={video}, caption={caption}")
        print("═" * 50)
        return True

    telegram_api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendVideo"
    is_local_file = not video.startswith("http://") and not video.startswith("https://") and os.path.exists(video)

    try:
        if is_local_file:
            with open(video, "rb") as video_file:
                response = requests.post(
                    telegram_api_url,
                    data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption},
                    files={"video": video_file},
                    timeout=60,
                )
        else:
            # Прямая ссылка на видео или уже загруженный file_id — Telegram
            # сам скачает видео по URL, отдельный файл отправлять не нужно.
            response = requests.post(
                telegram_api_url,
                data={"chat_id": config.TELEGRAM_CHAT_ID, "video": video, "caption": caption},
                timeout=60,
            )
    except requests.RequestException as error:
        logger.error(f"Не удалось отправить видео в Telegram: {error}")
        return False
    except OSError as error:
        logger.error(f"Не удалось открыть файл видео: {error}")
        return False

    if response.ok:
        logger.info("✅ Видео успешно отправлено в Telegram")
        return True

    logger.error(f"❌ Telegram вернул ошибку при отправке видео: {response.status_code} — {response.text}")
    return False
