# -*- coding: utf-8 -*-
"""
vk_publisher.py — публикация готового поста на стену сообщества VK (второй
канал вывода, рядом с Telegram — см. agent_publisher.py).

Текст поста сюда приходит уже готовым (из lead_post_builder.py) — здесь
НЕТ отдельной логики генерации текста, только доставка того же текста в VK.

Главное отличие от Telegram: VK не принимает внешнюю ссылку на фото
напрямую — сначала нужно самим скачать фото по ссылке (сайт-источник
объявления), потом загрузить его на сервер VK, и только потом прикрепить
к посту. Три шага на каждое фото:
    1. photos.getWallUploadServer — спросить у VK, куда загружать
    2. POST самого файла на полученный адрес — VK возвращает {photo,server,hash}
    3. photos.saveWallPhoto — сохранить фото на стене, получить attachment-ID

В отличие от Telegram (там альбом — это отдельные "прикреплённые" фото,
подпись только у первого), в VK ОДИН пост может содержать текст + сразу
несколько фото-вложений — переделывать под "первое/остальные" не нужно.
"""

import logging
import sys
import time
from urllib.parse import urlparse

import requests

import config

logger = logging.getLogger("vk_publisher")

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
MAX_PHOTOS_PER_POST = 10  # ограничение VK на число вложений в одном посте

# Инцидент 2026-07-17 (GitHub Actions, прогон test_post.py): фото с
# ci.encar.com скачивались "успешно" (HTTP 200), но VK отвечал на
# photos.saveWallPhoto ошибкой 100 "photo is undefined" — сервер загрузки
# VK получал пустой/не-картиночный файл. Локально (домашняя сеть) та же
# самая ссылка скачивалась нормально — значит дело было не в самом
# encar.com, а в том, что requests.get() без заголовков (стандартный
# User-Agent библиотеки requests) палится анти-бот/hotlink-защитой сайта
# на IP облачных раннеров GitHub Actions и получает вместо картинки
# страницу-заглушку/редирект с кодом 200, но без реального изображения.
# Фикс — качать с браузерными заголовками и Referer сайта-источника, и
# явно проверять, что реально получили (content-type, размер), а не
# только HTTP-статус, с несколькими попытками на случай единичного сбоя.
_DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_MIN_PHOTO_BYTES = 10 * 1024  # 10 КБ — меньше почти наверняка не фото, а заглушка/ошибка
_DOWNLOAD_RETRIES = 3

# Referer, которого ждёт анти-hotlink защита каждого источника — сама CDN-
# ссылка (например, ci.encar.com) обычно не подходит, нужен домен
# основного сайта. По умолчанию (незнакомый источник) — Referer на сам же
# CDN-домен фото, это безопаснее, чем вообще без Referer.
_REFERER_BY_PHOTO_HOST = {
    "ci.encar.com": "https://www.encar.com/",
    "fem.encar.com": "https://www.encar.com/",
}


def _referer_for(photo_url: str) -> str:
    host = urlparse(photo_url).netloc
    if host in _REFERER_BY_PHOTO_HOST:
        return _REFERER_BY_PHOTO_HOST[host]
    parsed = urlparse(photo_url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def _safe_print(text: str) -> None:
    """
    Текст поста содержит эмодзи — на Windows-консоли с не-UTF8 кодировкой
    (например, cp1251) обычный print() падает с UnicodeEncodeError, как уже
    было исправлено в telegram_publisher.py/add_model.py/model_specs.py.
    То же решение здесь.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def is_configured() -> bool:
    return bool(config.VK_ENABLED and config.VK_TOKEN and config.VK_OWNER_ID)


def _api_call(method: str, params: dict, timeout: int = 20) -> dict | None:
    """
    Общий вызов метода VK API. Возвращает словарь из поля "response",
    или None при сетевой ошибке/ошибке VK API (уже залогировано внутри).
    """
    request_params = {**params, "access_token": config.VK_TOKEN, "v": API_VERSION}
    try:
        response = requests.post(f"{API_BASE}/{method}", data=request_params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as error:
        logger.error(f"VK API {method}: сетевая ошибка ({error})")
        return None
    except ValueError as error:
        logger.error(f"VK API {method}: не удалось разобрать ответ ({error})")
        return None

    if "error" in data:
        error_info = data["error"]
        logger.error(f"VK API {method}: ошибка {error_info.get('error_code')} — {error_info.get('error_msg')}")
        return None

    return data.get("response")


def _download_photo(photo_url: str) -> bytes | None:
    """
    Скачивает фото с браузерными заголовками (User-Agent + Referer сайта-
    источника, см. _REFERER_BY_PHOTO_HOST) и проверяет, что реально
    получили картинку, а не HTTP 200 с пустой/не-картиночной заглушкой
    (см. докстринг про инцидент 2026-07-17) — статус, content-type И
    размер (> _MIN_PHOTO_BYTES). При неудаче — до _DOWNLOAD_RETRIES
    попыток, с логом причины на каждой.
    """
    headers = {
        "User-Agent": _DOWNLOAD_USER_AGENT,
        "Referer": _referer_for(photo_url),
    }

    for attempt in range(1, _DOWNLOAD_RETRIES + 1):
        if attempt > 1:
            time.sleep(1)
        try:
            response = requests.get(photo_url, headers=headers, timeout=20)
        except requests.RequestException as error:
            logger.warning(f"VK: попытка {attempt}/{_DOWNLOAD_RETRIES} скачать {photo_url} — "
                            f"сетевая ошибка ({error})")
            continue

        content_type = response.headers.get("content-type", "")
        size = len(response.content)

        if response.status_code != 200:
            logger.warning(f"VK: попытка {attempt}/{_DOWNLOAD_RETRIES} скачать {photo_url} — "
                            f"HTTP {response.status_code}")
            continue
        if not content_type.startswith("image/"):
            logger.warning(f"VK: попытка {attempt}/{_DOWNLOAD_RETRIES} скачать {photo_url} — "
                            f"content-type {content_type!r} (не image/*), размер {size} байт")
            continue
        if size <= _MIN_PHOTO_BYTES:
            logger.warning(f"VK: попытка {attempt}/{_DOWNLOAD_RETRIES} скачать {photo_url} — "
                            f"слишком маленький файл ({size} байт, ожидалось > {_MIN_PHOTO_BYTES})")
            continue

        return response.content

    logger.error(f"VK: не удалось скачать пригодное фото {photo_url} за {_DOWNLOAD_RETRIES} попыток(и)")
    return None


def _upload_one_photo(photo_url: str, group_id: int) -> str | None:
    """
    Три шага загрузки одного фото на стену VK (см. докстринг файла).
    Возвращает attachment-ID вида "photo{owner_id}_{id}", или None при
    любой ошибке (сеть, битая ссылка на фото, отказ VK) — вызывающий код
    просто пропускает это фото, не отправку целиком.
    """
    upload_server = _api_call("photos.getWallUploadServer", {"group_id": group_id})
    if upload_server is None:
        return None
    upload_url = upload_server.get("upload_url")
    if not upload_url:
        logger.error("VK: photos.getWallUploadServer не вернул upload_url")
        return None

    photo_bytes = _download_photo(photo_url)
    if photo_bytes is None:
        return None

    try:
        upload_response = requests.post(
            upload_url,
            # Поле строго "photo" (так его ждёт VK) — имя файла с
            # расширением .jpg и явный content-type, как в ручной проверке
            # curl -F "photo=@file.jpg;type=image/jpeg", которая гарантированно
            # отрабатывает (см. FINAL_STATE.md раздел 9).
            files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
            timeout=30,
        )
        upload_response.raise_for_status()
        upload_result = upload_response.json()
    except (requests.RequestException, ValueError) as error:
        logger.error(f"VK: не удалось загрузить фото {photo_url} на сервер VK ({error})")
        return None

    saved = _api_call("photos.saveWallPhoto", {
        "group_id": group_id,
        "photo": upload_result.get("photo"),
        "server": upload_result.get("server"),
        "hash": upload_result.get("hash"),
    })
    if not saved:
        # _api_call уже залогировал код/текст ошибки VK API — здесь
        # дополнительно печатаем то, что реально вернул upload-сервер
        # (server/photo/hash), чтобы при "photo is undefined" сразу было
        # видно, пустой ли это ответ или что-то более конкретное.
        logger.error(f"VK: ответ upload-сервера для {photo_url}: "
                     f"server={upload_result.get('server')!r}, "
                     f"photo={upload_result.get('photo')!r}, "
                     f"hash={upload_result.get('hash')!r}")
        return None

    saved_photo = saved[0]
    return f"photo{saved_photo['owner_id']}_{saved_photo['id']}"


def send_to_vk(text: str, photos: list | None = None, dry_run: bool = False) -> bool:
    """
    Публикует пост на стену сообщества VK (config.VK_OWNER_ID).

    Args:
        text: готовый текст поста (тот же самый, что уходит в Telegram).
        photos: список URL фото (может быть пустым/None — тогда пост без фото).
        dry_run: не публиковать по-настоящему, только показать в консоли.

    Returns:
        True при успехе (или dry_run, или VK не настроен — см. is_configured()).
        False при ошибке публикации ИЛИ если заявлено хотя бы одно фото, но
        прикрепить удалось не все — пост в этом случае НЕ публикуется
        (даже без фото/с частью фото), чтобы не выходил пост с "как получится"
        набором фото. Не блокирует Telegram — см. agent_publisher.publish_post().
    """
    if not is_configured():
        if not config.VK_ENABLED:
            logger.info("VK: VK_ENABLED=false в .env — публикация в VK пропущена")
        else:
            logger.info("VK: VK_TOKEN/VK_OWNER_ID не заданы в .env — публикация в VK пропущена")
        return True

    photos = (photos or [])[:MAX_PHOTOS_PER_POST]

    if dry_run:
        print("=" * 50)
        print(f"VK ТЕСТОВЫЙ РЕЖИМ (ничего не отправлено). Фото: {len(photos)}")
        print("=" * 50)
        _safe_print(text)
        print("=" * 50)
        return True

    owner_id = int(config.VK_OWNER_ID)
    group_id = abs(owner_id)

    attachments = []
    for photo_url in photos:
        attachment = _upload_one_photo(photo_url, group_id)
        if attachment is None:
            logger.error(f"VK: не удалось прикрепить фото {photo_url} — публикация заблокирована "
                         f"(не выходит пост с неполным набором фото)")
            return False
        attachments.append(attachment)

    post_params = {"owner_id": owner_id, "message": text, "from_group": 1}
    if attachments:
        post_params["attachments"] = ",".join(attachments)

    result = _api_call("wall.post", post_params, timeout=30)
    if result is None:
        return False

    post_id = result.get("post_id")
    logger.info(f"VK: пост опубликован (post_id={post_id})")
    return True
