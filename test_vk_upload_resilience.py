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


def _check_flood_control_backoff() -> bool:
    """
    Инцидент 2026-09-24: _upload_one_photo() ждал одну и ту же 1 секунду
    между ЛЮБЫМИ ретраями, включая флуд-контроль (код ошибки 9) — окно
    флуд-контроля VK для "тяжёлых" методов ощутимо дольше, из-за чего один
    пойманный флуд-контроль каскадом проваливал ВСЕ 4 фото поста разом
    (до 12 вызовов photos.getWallUploadServer подряд с секундными паузами).
    Проверяет: поймав код 9 на 1-й попытке, перед 2-й попыткой ждёт именно
    _FLOOD_CONTROL_BACKOFF_SEC (не обычную 1 секунду) — без реальной сети
    и без реального ожидания (мокаем time.sleep).
    """
    import time as time_module

    original_api_call = vk._api_call_with_code
    original_download = vk._download_photo
    original_requests_post = vk.requests.post
    original_sleep = time_module.sleep

    sleep_calls = []
    api_call_log = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    def fake_api_call_with_code(method, params, timeout=20):
        api_call_log.append(method)
        if method == "photos.getWallUploadServer":
            if len([m for m in api_call_log if m == "photos.getWallUploadServer"]) == 1:
                return None, vk._FLOOD_CONTROL_CODE  # 1-я попытка — флуд-контроль
            return {"upload_url": "https://upload.example.com/fake"}, None  # 2-я — успех
        if method == "photos.saveWallPhoto":
            return [{"owner_id": 1, "id": 999}], None
        raise AssertionError(f"неожиданный метод в тесте: {method}")

    class _FakeUploadResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"photo": "x", "server": 1, "hash": "y"}

    try:
        vk._api_call_with_code = fake_api_call_with_code
        vk._download_photo = lambda url: b"fake-jpeg-bytes"
        vk.requests.post = lambda *a, **kw: _FakeUploadResponse()
        time_module.sleep = fake_sleep

        result = vk._upload_one_photo("https://example.com/photo.jpg", group_id=1)

        passed = (
            result == "photo1_999"
            and len(sleep_calls) == 1
            and sleep_calls[0] == vk._FLOOD_CONTROL_BACKOFF_SEC
        )
        status = "OK" if passed else "FAIL"
        _safe_print(f"[{status}] После флуд-контроля пауза перед повтором = "
                    f"{sleep_calls} (ожидалось [{vk._FLOOD_CONTROL_BACKOFF_SEC}]), "
                    f"результат={result!r}")
        return passed
    finally:
        vk._api_call_with_code = original_api_call
        vk._download_photo = original_download
        vk.requests.post = original_requests_post
        time_module.sleep = original_sleep


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

    # Инцидент 2026-09-24: флуд-контроль должен ждать дольше обычного ретрая.
    results.append(_check_flood_control_backoff())

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
