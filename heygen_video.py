# -*- coding: utf-8 -*-
"""
heygen_video.py — генерация ролика с ИИ-аватаром через HeyGen API (аналог
custom MCP-коннектора, который использовался в Claude): тот же API, тот же
поток «сгенерировать → ждать → скачать».

Секреты ТОЛЬКО из .env (auto_dealer_bot):
  HEYGEN_API_KEY     — обязателен (https://app.heygen.com/settings?nav=API)
  HEYGEN_AVATAR_ID   — необязателен; без него: --list-avatars и выбор
  HEYGEN_VOICE_ID    — необязателен; без него: --list-voices и выбор

Использование:
  python heygen_video.py --list-avatars          # список аватаров на выбор
  python heygen_video.py --list-voices           # список голосов (ru сначала)
  python heygen_video.py --avatar-id ID --voice-id ID --text-file script.txt \
      --out output/lynkco06.mp4                  # генерация + скачивание
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.heygen.com"
KEY = os.getenv("HEYGEN_API_KEY", "")
W, H = 1080, 1920  # вертикаль под Telegram/Shorts/Reels
POLL_SEC = 15
POLL_TIMEOUT_SEC = 1200


class HeyGenError(Exception):
    pass


def _headers() -> dict:
    if not KEY:
        raise HeyGenError("HEYGEN_API_KEY не задан в .env")
    return {"X-Api-Key": KEY, "Content-Type": "application/json"}


def _get(path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=_headers(), params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict) -> dict:
    r = requests.post(f"{API_BASE}{path}", headers=_headers(), json=payload, timeout=120)
    if r.status_code >= 400:
        raise HeyGenError(f"POST {path} -> {r.status_code}: {r.text[:400]}")
    return r.json()


def list_avatars() -> list:
    """Все доступные аватары (постранично)."""
    out, page, size = [], 1, 100
    while True:
        data = _get("/v2/avatars", {"page": page, "size": size}).get("data", {})
        batch = data.get("avatars") or []
        out += batch
        if len(batch) < size:
            return out
        page += 1


def list_voices() -> list:
    data = _get("/v2/voices").get("data", {})
    return data.get("voices") or []


def generate(avatar_id: str, voice_id: str, text: str, caption: str | None = None) -> str:
    """Запуск генерации -> video_id. Формат фиксирован 1080x1920."""
    payload = {
        "video_inputs": [{
            "character": {"avatar_id": avatar_id, "avatar_style": "normal"},
            "voice": {"voice_id": voice_id, "input_text": text, "speed": 1.0},
        }],
        "dimension": {"width": W, "height": H},
    }
    if caption:
        payload["caption"] = caption
    data = _post("/v2/video/generate", payload).get("data", {})
    video_id = data.get("video_id")
    if not video_id:
        raise HeyGenError(f"API не вернул video_id: {json.dumps(data)[:300]}")
    return video_id


def wait_done(video_id: str, timeout: int = POLL_TIMEOUT_SEC) -> str:
    """Поллинг video_status.get до completed; вернуть URL MP4."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _get("/v1/video_status.get", {"video_id": video_id}).get("data", {})
        status = data.get("status")
        if status == "completed":
            url = data.get("video_url")
            if not url:
                raise HeyGenError(f"completed без video_url: {json.dumps(data)[:300]}")
            return url
        if status == "failed":
            raise HeyGenError(f"генерация провалилась: {json.dumps(data)[:400]}")
        time.sleep(POLL_SEC)
    raise HeyGenError(f"таймаут {timeout} с, статус не стал completed")


def download(url: str, out_path: str) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-avatars", action="store_true", help="показать аватаров и выйти")
    ap.add_argument("--list-voices", action="store_true", help="показать голоса и выйти")
    ap.add_argument("--avatar-id", default=os.getenv("HEYGEN_AVATAR_ID", ""))
    ap.add_argument("--voice-id", default=os.getenv("HEYGEN_VOICE_ID", ""))
    ap.add_argument("--text-file", help="файл с текстом озвучки (UTF-8)")
    ap.add_argument("--out", default="output/heygen_video.mp4")
    args = ap.parse_args()

    try:
        if args.list_avatars:
            for i, a in enumerate(list_avatars(), 1):
                print(f"{i:3d}. {a.get('avatar_id')} | {a.get('avatar_name')} | "
                      f"{(a.get('preview_image_url') or '')[:80]}")
            return
        if args.list_voices:
            voices = list_voices()
            voices.sort(key=lambda v: 0 if v.get("language") == "Russian" else 1)
            for i, v in enumerate(voices, 1):
                print(f"{i:3d}. {v.get('voice_id')} | {v.get('language')} | "
                      f"{v.get('name')} | {v.get('gender')}")
            return

        if not args.avatar_id or not args.voice_id or not args.text_file:
            ap.error("нужны --avatar-id, --voice-id и --text-file "
                     "(или HEYGEN_AVATAR_ID/HEYGEN_VOICE_ID в .env)")
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
        print(f"текст: {len(text)} знаков; генерация 1080x1920...")
        video_id = generate(args.avatar_id, args.voice_id, text)
        print(f"video_id={video_id}; ждём рендер (проверка каждые {POLL_SEC} с)...")
        url = wait_done(video_id)
        path = download(url, args.out)
        print(f"готово: {path} ({path.stat().st_size / 1024 / 1024:.1f} МБ)")
    except HeyGenError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
