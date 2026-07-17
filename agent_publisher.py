# -*- coding: utf-8 -*-
"""
agent_publisher.py — "агент-публикатор" в мультиагентном пайплайне (orchestrator.py)
и в main_pipeline.py.

publish_post() — ЕДИНСТВЕННАЯ функция во всём проекте, которая имеет право
вызывать telegram_publisher/vk_publisher (проверено grep — прямых вызовов
send_to_telegram/send_photo_to_telegram/send_media_group_to_telegram/
send_to_vk больше нигде нет). Именно поэтому жёсткий гейт
validate_post_before_publish() стоит ЗДЕСЬ, первым делом внутри самой
функции, а не отдельным вызовом в каждом из вызывающих скриптов
(main.py/main_pipeline.py/publish_now.py) — так его нельзя ни забыть
добавить в одном из них, ни обойти. См. PublishBlockedError.

Без фото — send_to_telegram() (как раньше), с фото — sendPhoto/
sendMediaGroup в зависимости от количества. Telegram — ОСНОВНОЙ канал, от
него зависит возвращаемое значение (и, дальше по цепочке, дедуп в
published_log.json).

Если объявление заявлено с фото, но прикрепить их не удалось (битая
ссылка/сбой сети) — публикация БЛОКИРУЕТСЯ целиком (не откатывается на
текст без фото). Раньше было наоборот ("никогда не блокировать пост"),
но это давало неполные посты — изменено по явному указанию 2026-07-06.

VK (vk_publisher.py) — ВТОРОЙ канал, best-effort по отношению к Telegram:
публикуется тем же текстом/фото сразу вслед за Telegram, и его успех/
неудача НЕ влияет на результат Telegram (см. publish_post() ниже). Но
внутри самого VK действует та же защита — если хотя бы одно фото не
прикрепилось, VK-пост тоже не публикуется (см. vk_publisher.send_to_vk()).
Вызов send_to_vk() здесь дополнительно обёрнут в try/except (2026-07-17) —
защита от НЕОЖИДАННЫХ ошибок (не сетевых/API, те уже ловятся внутри
vk_publisher.py и возвращают False), чтобы вообще ничего из VK не могло
прервать publish_post() после того, как Telegram уже отправлен.
Отключить канал целиком — VK_ENABLED=false в .env (см. config.py).
Никакой отдельной логики поиска/расчёта цены здесь нет — text/photos уже
готовы на входе, те же самые для обоих каналов.

send_video_to_telegram() — использует метод Bot API sendVideo. Сейчас её
никто не вызывает (scraper.py не собирает видео с сайтов дилеров) — это
задел на будущее, точка расширения.
"""

import logging
import os

import requests

import config
from telegram_publisher import send_to_telegram, send_photo_to_telegram, send_media_group_to_telegram
from vk_publisher import send_to_vk
from post_validator import validate_post_before_publish, PublishBlockedError

logger = logging.getLogger("agent_publisher")


def publish_post(car: dict, text: str, dry_run: bool = False, skip_validation: bool = False) -> bool:
    """
    Публикует готовый текст поста в Telegram (основной канал) и, следом,
    в VK (второй канал, best-effort — см. докстринг файла).

    Первым делом (кроме одного явного исключения ниже) прогоняет car через
    validate_post_before_publish() — фото/название/год, см. post_validator.py.
    Нарушение — PublishBlockedError, HTTP-запрос к Telegram/VK не делается
    вообще. Это ЕДИНСТВЕННОЕ место в проекте, где стоит эта проверка —
    вызывающий код (main.py/main_pipeline.py/publish_now.py) НЕ должен
    дублировать вызов validate_post_before_publish() сам.

    Args:
        car: словарь объявления (brand/model/trim/year/photos/url/...) —
             используется ТОЛЬКО для валидации и чтобы достать photos;
             сам текст поста уже готов заранее (см. text).
        text: готовый текст поста.
        dry_run: не публиковать по-настоящему, только показать в консоли.
        skip_validation: пропустить гейт. ЕДИНСТВЕННЫЙ законный случай —
            новые авто из дилерского каталога (main.py: process_one_page,
            WATCHLIST) — там car["year"] намеренно всегда None и сайт
            дилера вообще не отдаёт фото (см. scraper.py,
            calculate_price_details: age_bucket_for_new_catalog_car()) —
            это не брак данных, а другой класс объявлений. Для ЛЮБОГО
            источника б/у авто — оставлять False.

    Returns:
        True при успехе в Telegram (VK публикуется независимо и не влияет
        на этот результат). Если объявление заявлено с фото, но прикрепить
        их не удалось (например, битая ссылка на изображение) — публикация
        БЛОКИРУЕТСЯ целиком (False), а не откатывается на текст без фото.

    Raises:
        PublishBlockedError: гейт не пройден (см. skip_validation выше).
            Вызывающий код должен ловить её на уровне цикла по машинам
            (продолжить со следующей), а не давать упасть всему прогону.
    """
    if not skip_validation:
        valid, reason = validate_post_before_publish(car)
        if not valid:
            identifier = car.get("url") or car.get("external_id") or "?"
            raise PublishBlockedError(f"Пост {identifier} заблокирован: {reason}")

    photos = car.get("photos") or []

    if not photos:
        ok = send_to_telegram(text, dry_run=dry_run)
    elif len(photos) == 1:
        ok = send_photo_to_telegram(photos[0], text, dry_run=dry_run)
    else:
        ok = send_media_group_to_telegram(photos, text, dry_run=dry_run)

    if not ok:
        logger.error("Публикация в Telegram заблокирована — не удалось прикрепить фото")

    try:
        if not send_to_vk(text, photos, dry_run=dry_run):
            logger.warning("Не удалось опубликовать в VK — Telegram это не затрагивает")
    except Exception as error:
        logger.error(f"VK: неожиданная ошибка при публикации ({error}) — Telegram это не затрагивает")

    return ok


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
