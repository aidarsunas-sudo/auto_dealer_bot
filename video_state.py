# -*- coding: utf-8 -*-
"""
video_state.py — считает успешные посты между прогонами (каждый прогон
main_pipeline.py на GitHub Actions — ОТДЕЛЬНЫЙ процесс на чистой машине,
в памяти между прогонами ничего не остаётся), чтобы video_generator.py
генерировал ролик раз в VIDEO_EVERY_N_POSTS постов, а не для каждого —
см. config.VIDEO_EVERY_N_POSTS.

Тот же принцип, что у published_log.json/target_models_cache.json —
маленький JSON-файл, коммитится обратно в репозиторий в конце прогона
(см. .github/workflows/*.yml, шаг "Сохранить журнал публикаций").
"""

import json
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / "video_state.json"


def _load() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"post_count": 0}


def _save(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def should_generate_video_for_this_post(every_n: int) -> bool:
    """
    Увеличивает общий счётчик успешных постов на 1 и решает, нужно ли
    видео ИМЕННО для этого поста (каждый every_n-й). Вызывать РОВНО один
    раз на пост — см. agent_publisher.publish_post().

    every_n <= 0 — видео выключено этим счётчиком (защита от деления на
    ноль и от опечатки VIDEO_EVERY_N_POSTS=0 в .env).
    """
    if every_n <= 0:
        return False
    state = _load()
    count = state.get("post_count", 0) + 1
    state["post_count"] = count
    _save(state)
    return count % every_n == 0
