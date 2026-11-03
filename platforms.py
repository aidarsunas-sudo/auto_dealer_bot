# -*- coding: utf-8 -*-
"""
platforms.py — публикация видео лота в Instagram (Reels) и YouTube (Shorts) через upload-post.com.
Telegram отправляется напрямую (telegram_media), здесь его нет.

Ключи — только из окружения (.env / секреты): UPLOAD_POST_API_KEY, UPLOAD_POST_USER (имя профиля).
Значения нигде не печатаются.

Правила:
  * площадка не подключена / нет ключа / исчерпан лимит — это НЕ ошибка: публикация идёт на остальные,
    для этой площадки возвращается статус pending_manual (в реестр пишется он же);
  * сетевая ошибка при отправке — тоже pending_manual с причиной (пост не считаем опубликованным);
  * подписи — из того же текста поста; для YouTube отдельное название <=100 знаков и #Shorts.
HTTP подменяется параметром session (тесты не ходят в сеть).
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("platforms")

API_BASE = "https://api.upload-post.com/api"
SUPPORTED = ("telegram", "instagram", "youtube")
VIA_UPLOAD_POST = ("instagram", "youtube")
YOUTUBE_TITLE_LIMIT = 100
INSTAGRAM_CAPTION_LIMIT = 2200
YOUTUBE_DESCRIPTION_LIMIT = 5000
# Дневные капы площадок (документация upload-post) и месячная квота Free-тарифа — для предупреждений
DAILY_CAPS = {"instagram": 50, "youtube": 10}


class PlatformError(Exception):
    pass


@dataclass
class PlatformStatus:
    channel: str
    ready: bool
    reason: str = ""          # почему не готова (для pending_manual)
    remaining: Optional[int] = None


@dataclass
class UploadStatus:
    configured: bool
    connected: dict = field(default_factory=dict)   # {"instagram": True, "youtube": False}
    remaining: Optional[int] = None                 # остаток публикаций в месяц по тарифу (None — неизвестно)
    plan: str = ""
    error: str = ""


@dataclass
class PlatformResult:
    channel: str
    status: str               # published | pending_manual
    url: Optional[str] = None
    reason: str = ""


def parse_platforms(value: Optional[str]) -> list:
    """'telegram,instagram' -> ['telegram','instagram'] (порядок и уникальность сохраняются)."""
    names = [p.strip().lower() for p in (value or "telegram").split(",") if p.strip()]
    unknown = [p for p in names if p not in SUPPORTED]
    if unknown:
        raise PlatformError(f"неизвестные площадки: {', '.join(unknown)} (доступны: {', '.join(SUPPORTED)})")
    return list(dict.fromkeys(names)) or ["telegram"]


def _credentials() -> tuple:
    return os.getenv("UPLOAD_POST_API_KEY", "").strip(), os.getenv("UPLOAD_POST_USER", "").strip()


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Apikey {api_key}"}


def _find_number(data, names: tuple) -> Optional[int]:
    """Ищет в произвольном JSON первое целое по одному из имён ключа (формат ответа у сервиса меняется)."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in names and isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        for value in data.values():
            found = _find_number(value, names)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_number(value, names)
            if found is not None:
                return found
    return None


def _connected_from_profiles(payload, user: str) -> dict:
    """Из ответа /uploadposts/users: подключённые соцсети профиля user -> {"instagram": True, ...}."""
    profiles = payload.get("profiles") if isinstance(payload, dict) else payload
    for profile in profiles or []:
        if str(profile.get("username", "")).lower() != user.lower():
            continue
        accounts = profile.get("social_accounts") or {}
        return {name: bool(accounts.get(name)) for name in VIA_UPLOAD_POST}
    return {}


def check_status(session=None) -> UploadStatus:
    """Только чтение (ничего не публикует): ключ, подключённые Instagram/YouTube, остаток запросов."""
    api_key, user = _credentials()
    if not api_key or not user:
        return UploadStatus(False, error="не заданы UPLOAD_POST_API_KEY / UPLOAD_POST_USER")
    if session is None:
        import requests as session
    status = UploadStatus(True)
    try:
        me = session.get(f"{API_BASE}/uploadposts/me", headers=_headers(api_key), timeout=30)
        if me.status_code == 401:
            return UploadStatus(True, error="ключ upload-post отклонён (401)")
        if me.ok:
            body = me.json()
            status.remaining = _find_number(body, ("remaining", "remaining_uploads", "uploads_remaining"))
            if status.remaining is None:
                limit = _find_number(body, ("limit", "upload_limit", "monthly_limit"))
                used = _find_number(body, ("used", "usage", "uploads_used", "current_usage"))
                if limit is not None and used is not None:
                    status.remaining = max(limit - used, 0)
            status.plan = str(body.get("plan") or body.get("plan_name") or "") if isinstance(body, dict) else ""
        users = session.get(f"{API_BASE}/uploadposts/users", headers=_headers(api_key), timeout=30)
        if users.ok:
            status.connected = _connected_from_profiles(users.json(), user)
        else:
            status.error = f"список профилей: HTTP {users.status_code}"
    except Exception as error:
        status.error = f"upload-post недоступен ({type(error).__name__})"
    return status


def plan_platforms(requested: list, status: UploadStatus) -> list:
    """По каждой запрошенной площадке (кроме telegram): готова ли отправка прямо сейчас."""
    plans = []
    for name in requested:
        if name == "telegram":
            continue
        if not status.configured:
            plans.append(PlatformStatus(name, False, status.error or "upload-post не настроен"))
        elif status.error and not status.connected:
            plans.append(PlatformStatus(name, False, status.error))
        elif not status.connected.get(name):
            plans.append(PlatformStatus(name, False, f"{name} не подключён к профилю upload-post"))
        elif status.remaining is not None and status.remaining <= 0:
            plans.append(PlatformStatus(name, False, "исчерпан лимит публикаций upload-post", 0))
        else:
            plans.append(PlatformStatus(name, True, "", status.remaining))
    return plans


# ── подписи ───────────────────────────────────────────────────────────────

def _trim(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def instagram_caption(post_text: str) -> str:
    return _trim(post_text, INSTAGRAM_CAPTION_LIMIT)


def youtube_title(post_text: str, lot_title: str, year: Optional[int] = None) -> str:
    """Отдельное название <=100 знаков с #Shorts: «Название год — под заказ из Кореи #Shorts»."""
    base = f"{lot_title} {year}".strip() if year else lot_title.strip()
    suffix = " — под заказ из Кореи #Shorts"
    title = base + suffix
    if len(title) > YOUTUBE_TITLE_LIMIT:
        title = _trim(base, YOUTUBE_TITLE_LIMIT - len(" #Shorts")) + " #Shorts"
    return title


def youtube_description(post_text: str) -> str:
    text = post_text.strip()
    if "#Shorts" not in text:
        text += "\n\n#Shorts"
    return _trim(text, YOUTUBE_DESCRIPTION_LIMIT)


# ── отправка ──────────────────────────────────────────────────────────────

def _post_url_from(response_json, channel: str) -> Optional[str]:
    """Ссылка на пост из ответа upload-post (формат разный) — первая https-ссылка нужной площадки."""
    needle = {"instagram": "instagram.com", "youtube": "youtu"}[channel]
    found = re.findall(r"https?://[^\s\"'\\]+", str(response_json))
    for url in found:
        if needle in url:
            return url.rstrip("',)}]")
    return None


def publish_video(channel: str, video: Path, *, caption: str, title: str = "", description: str = "",
                  test_mode: bool = False, session=None) -> PlatformResult:
    """Одна площадка. Любая проблема -> pending_manual с причиной, исключения наружу не выходят."""
    if channel not in VIA_UPLOAD_POST:
        raise PlatformError(f"{channel} не публикуется через upload-post")
    api_key, user = _credentials()
    if not api_key or not user:
        return PlatformResult(channel, "pending_manual", reason="не заданы UPLOAD_POST_API_KEY / UPLOAD_POST_USER")
    if session is None:
        import requests as session
    data = {"user": user, "platform[]": channel, "title": caption}
    if channel == "instagram":
        data.update({"media_type": "REELS", "instagram_title": caption})
    else:
        data.update({"title": title, "youtube_title": title, "youtube_description": description or caption,
                     "privacy_status": "private" if test_mode else "public"})
    try:
        with open(video, "rb") as handle:
            response = session.post(f"{API_BASE}/upload", headers=_headers(api_key), data=data,
                                    files={"video": (Path(video).name, handle, "video/mp4")}, timeout=600)
        if response.status_code == 429:
            return PlatformResult(channel, "pending_manual", reason="лимит upload-post (429)")
        if not response.ok:
            return PlatformResult(channel, "pending_manual", reason=f"upload-post вернул HTTP {response.status_code}")
        body = response.json()
    except Exception as error:
        return PlatformResult(channel, "pending_manual", reason=f"сбой отправки ({type(error).__name__})")
    if isinstance(body, dict) and body.get("success") is False:
        return PlatformResult(channel, "pending_manual", reason=str(body.get("message") or body.get("error") or "отказ upload-post")[:200])
    return PlatformResult(channel, "published", url=_post_url_from(body, channel))
