# -*- coding: utf-8 -*-
"""
test_vk_video_fallback.py — самотест agent_publisher.publish_post() на
резервную публикацию видео отдельным постом в VK (без pytest, в стиле
остальных self-test'ов проекта — python test_vk_video_fallback.py).

Инцидент 2026-09-24 (разбор архитектуры по логам реальных прогонов, без
live-теста — токен временно под флуд-контролем VK): в режиме
VIDEO_POST_MODE="same" видео уходит в ТОМ ЖЕ вызове vk_publisher.
send_to_vk(text, photos, video_path=...), что и фото. Если фото не
набрали config.MIN_PHOTOS_FOR_POST, send_to_vk() возвращает False ДО
того, как дойти до прикрепления video_path (см. vk_publisher.py) — уже
готовое видео пропадало вместе с постом, хотя видео тут ни при чём.
Фикс: publish_post() при таком отказе пробует переопубликовать то же
видео отдельным постом (без фото). Этот тест мокает send_to_vk() (без
реальной сети) и проверяет именно эту резервную логику, не саму загрузку.
"""

import sys
import tempfile
from pathlib import Path

import agent_publisher
import config


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _check_video_rescued_when_main_vk_post_blocked() -> bool:
    original_send_to_vk = agent_publisher.send_to_vk
    original_send_photo = agent_publisher.send_photo_to_telegram
    original_send_video = agent_publisher.send_video_to_telegram
    original_ffmpeg_available = agent_publisher.video_generator.ffmpeg_available
    original_generate_videos = agent_publisher.video_generator.generate_videos
    original_should_generate = agent_publisher.video_state.should_generate_video_for_this_post
    original_video_enabled = config.VIDEO_ENABLED
    original_video_post_mode = config.VIDEO_POST_MODE

    tmp_dir = Path(tempfile.mkdtemp(prefix="vk_video_fallback_test_"))
    fake_horizontal = tmp_dir / "horizontal.mp4"
    vk_calls = []

    def fake_send_to_vk(text, photos=None, dry_run=False, video_path=None, video_name=""):
        vk_calls.append({"photos": photos, "video_path": video_path})
        if len(vk_calls) == 1:
            return False  # 1-й вызов: текст+фото+видео вместе — имитируем "фото не набрали порог"
        return True  # 2-й вызов: резервный, видео отдельным постом — имитируем успех

    try:
        config.VIDEO_ENABLED = True
        config.VIDEO_POST_MODE = "same"

        agent_publisher.send_to_vk = fake_send_to_vk
        agent_publisher.send_photo_to_telegram = lambda photo, text, dry_run=False: True
        agent_publisher.send_video_to_telegram = lambda video, caption="", dry_run=False: True
        agent_publisher.video_generator.ffmpeg_available = lambda: True
        agent_publisher.video_generator.generate_videos = lambda car, fields: {"horizontal": fake_horizontal}
        agent_publisher.video_state.should_generate_video_for_this_post = lambda every_n: True

        car = {"photos": ["https://example.com/1.jpg"], "url": "https://example.com/car/1"}
        fields = {"title": "Kia K5", "year": "2022"}

        ok = agent_publisher.publish_post(car, "тестовый текст", dry_run=False,
                                           skip_validation=True, fields=fields)

        passed = (
            ok is True  # Telegram (единственный, что влияет на возврат) отработал успешно
            and len(vk_calls) == 2
            and vk_calls[0]["photos"] is not None
            and vk_calls[0]["video_path"] == fake_horizontal
            and vk_calls[1]["photos"] is None  # резервный пост — только видео, без фото
            and vk_calls[1]["video_path"] == fake_horizontal
        )
        status = "OK" if passed else "FAIL"
        _safe_print(f"[{status}] Видео переотправлено отдельным постом, когда основной пост в VK "
                    f"заблокирован нехваткой фото: vk_calls={vk_calls}, publish_post={ok}")
        return passed
    finally:
        agent_publisher.send_to_vk = original_send_to_vk
        agent_publisher.send_photo_to_telegram = original_send_photo
        agent_publisher.send_video_to_telegram = original_send_video
        agent_publisher.video_generator.ffmpeg_available = original_ffmpeg_available
        agent_publisher.video_generator.generate_videos = original_generate_videos
        agent_publisher.video_state.should_generate_video_for_this_post = original_should_generate
        config.VIDEO_ENABLED = original_video_enabled
        config.VIDEO_POST_MODE = original_video_post_mode
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _check_no_fallback_when_main_vk_post_succeeds() -> bool:
    """
    Регрессия: если основной пост в VK прошёл нормально (видео уже внутри
    него) — резервный вызов НЕ должен происходить, иначе видео/пост
    задвоится.
    """
    original_send_to_vk = agent_publisher.send_to_vk
    original_send_photo = agent_publisher.send_photo_to_telegram
    original_send_video = agent_publisher.send_video_to_telegram
    original_ffmpeg_available = agent_publisher.video_generator.ffmpeg_available
    original_generate_videos = agent_publisher.video_generator.generate_videos
    original_should_generate = agent_publisher.video_state.should_generate_video_for_this_post
    original_video_enabled = config.VIDEO_ENABLED
    original_video_post_mode = config.VIDEO_POST_MODE

    tmp_dir = Path(tempfile.mkdtemp(prefix="vk_video_fallback_test_"))
    fake_horizontal = tmp_dir / "horizontal.mp4"
    vk_calls = []

    def fake_send_to_vk(text, photos=None, dry_run=False, video_path=None, video_name=""):
        vk_calls.append({"photos": photos, "video_path": video_path})
        return True  # основной пост сразу успешен

    try:
        config.VIDEO_ENABLED = True
        config.VIDEO_POST_MODE = "same"

        agent_publisher.send_to_vk = fake_send_to_vk
        agent_publisher.send_photo_to_telegram = lambda photo, text, dry_run=False: True
        agent_publisher.send_video_to_telegram = lambda video, caption="", dry_run=False: True
        agent_publisher.video_generator.ffmpeg_available = lambda: True
        agent_publisher.video_generator.generate_videos = lambda car, fields: {"horizontal": fake_horizontal}
        agent_publisher.video_state.should_generate_video_for_this_post = lambda every_n: True

        car = {"photos": ["https://example.com/1.jpg"], "url": "https://example.com/car/1"}
        fields = {"title": "Kia K5", "year": "2022"}

        ok = agent_publisher.publish_post(car, "тестовый текст", dry_run=False,
                                           skip_validation=True, fields=fields)

        passed = ok is True and len(vk_calls) == 1
        status = "OK" if passed else "FAIL"
        _safe_print(f"[{status}] Нет резервного вызова, когда основной пост в VK и так успешен: "
                    f"vk_calls={vk_calls}, publish_post={ok}")
        return passed
    finally:
        agent_publisher.send_to_vk = original_send_to_vk
        agent_publisher.send_photo_to_telegram = original_send_photo
        agent_publisher.send_video_to_telegram = original_send_video
        agent_publisher.video_generator.ffmpeg_available = original_ffmpeg_available
        agent_publisher.video_generator.generate_videos = original_generate_videos
        agent_publisher.video_state.should_generate_video_for_this_post = original_should_generate
        config.VIDEO_ENABLED = original_video_enabled
        config.VIDEO_POST_MODE = original_video_post_mode
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run() -> None:
    results = [
        _check_video_rescued_when_main_vk_post_blocked(),
        _check_no_fallback_when_main_vk_post_succeeds(),
    ]

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
