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

import requests

import config

logger = logging.getLogger("vk_publisher")

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
MAX_PHOTOS_PER_POST = 10  # ограничение VK на число вложений в одном посте


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
    return bool(config.VK_TOKEN and config.VK_OWNER_ID)


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

    try:
        photo_response = requests.get(photo_url, timeout=20)
        photo_response.raise_for_status()
        photo_bytes = photo_response.content
    except requests.RequestException as error:
        logger.error(f"VK: не удалось скачать фото {photo_url}: {error}")
        return None

    try:
        upload_response = requests.post(
            upload_url, files={"photo": ("photo.jpg", photo_bytes)}, timeout=30,
        )
        upload_response.raise_for_status()
        upload_result = upload_response.json()
    except (requests.RequestException, ValueError) as error:
        logger.error(f"VK: не удалось загрузить фото на сервер VK: {error}")
        return None

    saved = _api_call("photos.saveWallPhoto", {
        "group_id": group_id,
        "photo": upload_result.get("photo"),
        "server": upload_result.get("server"),
        "hash": upload_result.get("hash"),
    })
    if not saved:
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
        False только при реальной ошибке публикации — не блокирует Telegram,
        см. agent_publisher.publish_post().
    """
    if not is_configured():
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
        if attachment:
            attachments.append(attachment)
        else:
            logger.warning(f"VK: пропускаю фото, не удалось загрузить: {photo_url}")

    post_params = {"owner_id": owner_id, "message": text, "from_group": 1}
    if attachments:
        post_params["attachments"] = ",".join(attachments)

    result = _api_call("wall.post", post_params, timeout=30)
    if result is None:
        return False

    post_id = result.get("post_id")
    logger.info(f"VK: пост опубликован (post_id={post_id})")
    return True
