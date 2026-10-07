# -*- coding: utf-8 -*-
"""
telegram_media.py — отправка в Telegram ЛОКАЛЬНЫХ медиафайлов с возвратом message_id
(нужен реестру published.jsonl; существующие функции telegram_publisher.py шлют по
ссылкам и возвращают только True/False).

    send_album(paths, caption, chat_id)   2-10 фото одним альбомом, подпись на первом
    send_video_file(path, caption, chat_id)  одно видео с подписью

Обе функции:
  - ничего не отправляют при dry_run=True;
  - не печатают и не логируют токен (BOT_TOKEN читается из config);
  - не поднимают исключений наружу: ошибка сети/API -> None и сообщение в лог.
Лимиты Bot API: подпись 1024 символа, альбом 2-10 фото, фото до 10 МБ (сумма сторон до
10 000 px), видео до 50 МБ. Фото пережимаются до MAX_SIDE по длинной стороне (JPEG q85).
"""

import io
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

import config

logger = logging.getLogger("telegram_media")

CAPTION_MAX = 1024
ALBUM_MIN, ALBUM_MAX = 2, 10
VIDEO_MAX_BYTES = 50 * 1024 * 1024
MAX_SIDE = 1600


def jpeg_bytes(path: Path) -> bytes:
    """Фото -> JPEG до MAX_SIDE px по длинной стороне (лимиты Telegram по размеру)."""
    image = Image.open(path).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85, optimize=True)
    return buffer.getvalue()


def probe_video(path: Path) -> dict:
    """ширина/высота/длительность через ffprobe (чтобы вертикальное видео не сплющилось в
    плеере Telegram). Нет ffprobe или сбой — пустой словарь, видео всё равно отправится."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {"width": stream.get("width"), "height": stream.get("height"),
                "duration": round(float(data.get("format", {}).get("duration", 0)))}
    except Exception:
        return {}


def _api(method: str, **kwargs) -> requests.Response:
    return requests.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}", timeout=120, **kwargs)


def send_album(paths: list, caption: str, chat_id: str, dry_run: bool = False) -> Optional[list]:
    """Альбом из локальных фото. Возвращает [message_id, ...] или None при ошибке/некорректных данных."""
    paths = [Path(p) for p in paths]
    if not ALBUM_MIN <= len(paths) <= ALBUM_MAX:
        logger.error(f"альбом должен содержать {ALBUM_MIN}-{ALBUM_MAX} фото, передано {len(paths)}")
        return None
    if len(caption) > CAPTION_MAX:
        logger.error(f"подпись {len(caption)} симв. > {CAPTION_MAX} — не отправляю")
        return None
    if dry_run:
        return []
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN пуст — отправка невозможна")
        return None
    try:
        media, files = [], {}
        for index, path in enumerate(paths):
            key = f"photo{index}"
            files[key] = (path.name, jpeg_bytes(path), "image/jpeg")
            item = {"type": "photo", "media": f"attach://{key}"}
            if index == 0:
                item["caption"] = caption
            media.append(item)
        response = _api("sendMediaGroup", data={"chat_id": chat_id, "media": json.dumps(media)}, files=files)
    except (requests.RequestException, OSError) as error:
        logger.error(f"не удалось отправить альбом: {type(error).__name__}")
        return None
    if response.ok:
        ids = [m["message_id"] for m in response.json()["result"]]
        logger.info(f"✅ альбом из {len(paths)} фото отправлен, message_id {ids}")
        return ids
    logger.error(f"Telegram вернул ошибку (sendMediaGroup): {response.status_code} — {response.text[:300]}")
    return None


def send_video_file(path, caption: str, chat_id: str, dry_run: bool = False) -> Optional[int]:
    """Одно видео из файла с подписью. Возвращает message_id или None."""
    path = Path(path)
    if not path.is_file():
        logger.error(f"файл видео не найден: {path}")
        return None
    if path.stat().st_size > VIDEO_MAX_BYTES:
        logger.error(f"видео {path.stat().st_size / 1024 / 1024:.1f} МБ > 50 МБ (лимит Bot API) — не отправляю")
        return None
    if len(caption) > CAPTION_MAX:
        logger.error(f"подпись {len(caption)} симв. > {CAPTION_MAX} — не отправляю")
        return None
    if dry_run:
        return 0
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN пуст — отправка невозможна")
        return None
    meta = probe_video(path)
    data = {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}
    for key in ("width", "height", "duration"):
        if meta.get(key):
            data[key] = meta[key]
    try:
        with open(path, "rb") as handle:
            response = _api("sendVideo", data=data, files={"video": (path.name, handle, "video/mp4")})
    except (requests.RequestException, OSError) as error:
        logger.error(f"не удалось отправить видео: {type(error).__name__}")
        return None
    if response.ok:
        message_id = response.json()["result"]["message_id"]
        logger.info(f"✅ видео отправлено, message_id {message_id}")
        return message_id
    logger.error(f"Telegram вернул ошибку (sendVideo): {response.status_code} — {response.text[:300]}")
    return None
