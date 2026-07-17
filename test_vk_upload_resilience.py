# -*- coding: utf-8 -*-
"""
test_vk_upload_resilience.py — самотест vk_publisher.send_to_vk() на
частичный набор фото (без pytest, в стиле остальных self-test'ов проекта —
python test_vk_upload_resilience.py).

Инцидент 2026-07-17 (прод-прогон, пост Chevrolet Trax 1.2 ACTIV): одно фото
с ci.encar.com не прикрепилось на шаге photos.saveWallPhoto (VK вернул
"photo is undefined"), и это заблокировало публикацию ВСЕГО поста в VK —
хотя остальные 3 из 4 фото загрузились нормально. Фикс:
_upload_one_photo() теперь ретраит весь пайплайн загрузки (не только
скачивание), а send_to_vk() публикует с тем, что получилось, если
успешных фото >= config.MIN_PHOTOS_FOR_POST — полный отказ только ниже
порога. Этот тест мокает _upload_one_photo()/_api_call() (без реальной
сети) и проверяет именно эту логику отбора, не саму загрузку.
"""

import sys

import config
import vk_publisher as vk


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _run_case(label: str, photos: list, failing_urls: set, min_photos_for_post: int,
              expect_ok: bool) -> bool:
    original_upload = vk._upload_one_photo
    original_api_call = vk._api_call
    original_min = config.MIN_PHOTOS_FOR_POST
    original_enabled = config.VK_ENABLED
    original_token = config.VK_TOKEN
    original_owner = config.VK_OWNER_ID

    try:
        config.VK_ENABLED = True
        config.VK_TOKEN = "fake-token"
        config.VK_OWNER_ID = "-1"
        config.MIN_PHOTOS_FOR_POST = min_photos_for_post

        vk._upload_one_photo = lambda photo_url, group_id: (
            None if photo_url in failing_urls else f"photo1_{hash(photo_url) % 10000}"
        )
        vk._api_call = lambda method, params, timeout=30: {"post_id": 999}

        ok = vk.send_to_vk("тестовый текст", photos, dry_run=False)
        passed = ok == expect_ok
        status = "OK" if passed else "FAIL"
        _safe_print(f"[{status}] {label}: published={ok} (ожидалось {expect_ok})")
        return passed
    finally:
        vk._upload_one_photo = original_upload
        vk._api_call = original_api_call
        config.MIN_PHOTOS_FOR_POST = original_min
        config.VK_ENABLED = original_enabled
        config.VK_TOKEN = original_token
        config.VK_OWNER_ID = original_owner


def run() -> None:
    photos = [f"https://example.com/{i}.jpg" for i in range(4)]
    results = []

    # Регрессия самого инцидента: 1 из 4 не прикрепилось, порог=3 — публикуем.
    results.append(_run_case(
        "1/4 фото не прикрепилось, порог=3 (регрессия Chevrolet Trax 1.2 ACTIV)",
        photos, failing_urls={photos[3]}, min_photos_for_post=3, expect_ok=True,
    ))

    # Успешных фото ровно на пороге — публикуем.
    results.append(_run_case(
        "Успешных фото ровно на пороге (3 из 4, порог=3)",
        photos, failing_urls={photos[3]}, min_photos_for_post=3, expect_ok=True,
    ))

    # Успешных меньше порога — блокируем.
    results.append(_run_case(
        "2/4 фото не прикрепилось, порог=3 — блокируем",
        photos, failing_urls={photos[2], photos[3]}, min_photos_for_post=3, expect_ok=False,
    ))

    # Ничего не отвалилось — публикуем со всеми 4.
    results.append(_run_case(
        "0/4 фото не прикрепилось — публикуем со всеми",
        photos, failing_urls=set(), min_photos_for_post=3, expect_ok=True,
    ))

    # Все фото отвалились — блокируем в любом случае.
    results.append(_run_case(
        "4/4 фото не прикрепилось — блокируем",
        photos, failing_urls=set(photos), min_photos_for_post=3, expect_ok=False,
    ))

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
