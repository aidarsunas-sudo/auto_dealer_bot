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
import re
import sys
import time
from pathlib import Path
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


_TOKEN_PATTERN = re.compile(r"vk1\.[aA]\.[A-Za-z0-9_\-]{10,}")


def _mask_secret(text: str) -> str:
    """
    Защита на случай, если VK_TOKEN всё же попадёт в текст исключения/URL
    (например, будущая правка случайно вернёт GET с access_token в query —
    см. инцидент со смоук-тестом 2026-07-28, где токен утёк именно так через
    requests.get(params=...) в разовом диагностическом скрипте, НЕ в этом
    файле — здесь все вызовы VK API и так POST с телом, access_token в URL
    не попадает). Заменяет любую подстроку вида "vk1.a...." на маску — из
    предосторожности применяется ко ВСЕМ сообщениям об ошибках ниже, даже
    там, где токен по построению не должен там оказаться.
    """
    return _TOKEN_PATTERN.sub("vk1.a.***MASKED***", text)


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


# Коды ошибок VK API, с которыми мы уже сталкивались вживую на битом/не
# том токене (см. инциденты 2026-07-28/2026-09-15) — распознаём их явно,
# чтобы check_token_health() сразу называл ПРИЧИНУ, а не просто "не работает".
_TOKEN_ERROR_HINTS = {
    5: "токен недействителен/просрочен — нужен новый (см. SETUP_GITHUB_ACTIONS.md, "
       "пользовательский токен через Kate Mobile/аналогичное Standalone-приложение, "
       "НЕ VK ID confidential-приложение — тот выдаёт токен без прав wall/photos/video)",
    27: "токен группового типа (community token) — нужен именно пользовательский",
    15: "токену не хватает прав (scope) на этот метод — переиздать с правами "
        "photos,wall,groups,video,offline",
    # НЕ про токен вообще (инцидент 2026-09-24) — токен рабочий, просто VK
    # временно ограничил частоту вызовов этого метода (слишком много
    # проверок подряд). Проверку стоит повторить позже, не переиздавать токен.
    9: "это флуд-контроль VK (слишком много вызовов подряд), не признак битого токена — "
       "подожди несколько минут и проверь ещё раз, переиздавать токен не нужно",
}


def check_token_health() -> tuple[bool, str]:
    """
    Дешёвая проверка VK_TOKEN ПЕРЕД тем, как пайплайн потратит время на
    сбор/генерацию контента — чтобы битый токен был виден сразу явным
    сообщением (см. notifier.notify_error() у вызывающего кода), а не
    тонул в ретраях загрузки фото глубоко внутри публикации (именно так
    была обнаружена как минимум половина инцидентов с токеном в этом
    проекте — см. FINAL_STATE.md). Ничего не публикует, только спрашивает
    photos.getWallUploadServer — тот же метод, что первым падает при
    реальной публикации, так что бьёт точно в то же место.

    Returns:
        (True, "") — VK не настроен (см. is_configured()) или токен рабочий.
        (False, reason) — токен нерабочий, reason — человекочитаемая причина.
    """
    if not is_configured():
        return True, ""  # не настроен — не наша забота здесь, см. is_configured()

    group_id = abs(int(config.VK_OWNER_ID))
    try:
        resp = requests.post(f"{API_BASE}/photos.getWallUploadServer", data={
            "group_id": group_id, "access_token": config.VK_TOKEN, "v": API_VERSION,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as error:
        return False, f"сетевая ошибка при проверке токена: {_mask_secret(str(error))}"
    except ValueError as error:
        return False, f"не удалось разобрать ответ VK API при проверке токена: {_mask_secret(str(error))}"

    if "error" in data:
        code = data["error"].get("error_code")
        msg = data["error"].get("error_msg", "")
        hint = _TOKEN_ERROR_HINTS.get(code)
        reason = f"VK API photos.getWallUploadServer: ошибка {code} — {msg}"
        if hint:
            reason += f" ({hint})"
        return False, reason

    if not (data.get("response") or {}).get("upload_url"):
        return False, f"неожиданный ответ VK API (нет upload_url): {data}"

    return True, ""


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
        logger.error(f"VK API {method}: сетевая ошибка ({_mask_secret(str(error))})")
        return None
    except ValueError as error:
        logger.error(f"VK API {method}: не удалось разобрать ответ ({_mask_secret(str(error))})")
        return None

    if "error" in data:
        error_info = data["error"]
        logger.error(f"VK API {method}: ошибка {error_info.get('error_code')} — {error_info.get('error_msg')}")
        return None

    return data.get("response")


def _api_call_with_code(method: str, params: dict, timeout: int = 20) -> tuple:
    """
    Как _api_call(), но дополнительно возвращает КОД ошибки VK API (или
    None при сетевой/JSON-ошибке, когда кода просто нет). _api_call() саму
    не трогаем — её простой контракт "dict или None" используется в
    ~10 местах, менять рискованно. Этот хелпер — для мест, которым важно
    ОТЛИЧИТЬ флуд-контроль (код 9) от прочих сбоев, чтобы решить, ждать ли
    дольше перед повтором (см. _upload_one_photo() и инцидент 2026-09-24 —
    короткая пауза между ретраями сама провоцировала каскад флуд-контроля
    на всех 4 фото поста разом, см. FINAL_STATE.md).

    Returns:
        (response_dict, None) — успех.
        (None, error_code) — ошибка VK API с известным кодом.
        (None, None) — сетевая/JSON-ошибка (кода нет).
    """
    request_params = {**params, "access_token": config.VK_TOKEN, "v": API_VERSION}
    try:
        response = requests.post(f"{API_BASE}/{method}", data=request_params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as error:
        logger.error(f"VK API {method}: сетевая ошибка ({_mask_secret(str(error))})")
        return None, None
    except ValueError as error:
        logger.error(f"VK API {method}: не удалось разобрать ответ ({_mask_secret(str(error))})")
        return None, None

    if "error" in data:
        error_info = data["error"]
        code = error_info.get("error_code")
        logger.error(f"VK API {method}: ошибка {code} — {error_info.get('error_msg')}")
        return None, code

    return data.get("response"), None


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


# Инцидент 2026-07-17 (продовый прогон, пост Chevrolet Trax 1.2 ACTIV):
# _download_photo() скачал фото УСПЕШНО (прошёл проверки content-type/
# размера), но upload-сервер VK на photos.saveWallPhoto ответил ошибкой 100
# "photo is undefined" — upload-сервер VK вернул photo='' (пустая строка).
# Это НЕ то же самое, что уже пофикшенная анти-hotlink проблема (там мы
# вообще не получали картинку) — здесь VK-сторона (upload-сервер/
# saveWallPhoto) сама единично споткнулась на вполне валидном файле.
# Раньше единичный сбой ЛЮБОГО шага (не только скачивания) на ОДНОМ фото
# блокировал публикацию всего поста в VK целиком. Теперь: до
# _UPLOAD_RETRIES попыток на КАЖДЫЙ шаг загрузки (не только скачивание —
# upload_url запрашивается заново на каждой попытке, upload-сервер VK
# бывает нестабилен на повторных POST с тем же upload_url), и если фото
# так и не прикрепилось — не блокируем весь пост, а пропускаем именно это
# фото (см. send_to_vk() ниже: публикуем с тем, что получилось, если
# итоговых фото >= config.MIN_PHOTOS_FOR_POST).
_UPLOAD_RETRIES = 3

# Код ошибки VK "Flood control". Инцидент 2026-09-24: пауза между
# ретраями была одинаковой (1с) для ЛЮБОЙ ошибки — для флуд-контроля
# этого мало (окно ощутимо дольше), из-за чего один пойманный флуд-
# контроль каскадом проваливал ВСЕ последующие попытки на ВСЕХ 4 фото
# поста разом (до 12 вызовов photos.getWallUploadServer подряд с
# секундными паузами — ни разу не дав лимиту VK реально сброситься),
# см. FINAL_STATE.md. Теперь при этом коде — отдельная, заметно более
# длинная пауза, вместо той же секунды, что и для обычных сбоев.
_FLOOD_CONTROL_CODE = 9
_FLOOD_CONTROL_BACKOFF_SEC = 12


def _upload_one_photo(photo_url: str, group_id: int) -> str | None:
    """
    Три шага загрузки одного фото на стену VK (см. докстринг файла), с
    ретраями ВСЕГО пайплайна (не только скачивания — см. _UPLOAD_RETRIES).
    Возвращает attachment-ID вида "photo{owner_id}_{id}", или None, если ни
    одна из попыток не сработала — вызывающий код пропускает это фото, не
    всю отправку (см. send_to_vk()).
    """
    wait_before_next = 1  # обычная пауза; вырастет до _FLOOD_CONTROL_BACKOFF_SEC при коде 9 ниже

    for attempt in range(1, _UPLOAD_RETRIES + 1):
        if attempt > 1:
            time.sleep(wait_before_next)
        wait_before_next = 1  # сбрасываем — увеличим заново, только если СНОВА поймаем флуд-контроль

        upload_server, error_code = _api_call_with_code("photos.getWallUploadServer", {"group_id": group_id})
        if upload_server is None:
            if error_code == _FLOOD_CONTROL_CODE:
                wait_before_next = _FLOOD_CONTROL_BACKOFF_SEC
            continue
        upload_url = upload_server.get("upload_url")
        if not upload_url:
            logger.error("VK: photos.getWallUploadServer не вернул upload_url")
            continue

        photo_bytes = _download_photo(photo_url)
        if photo_bytes is None:
            return None  # _download_photo сама уже исчерпала свои ретраи — пересматривать нечего

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
            logger.warning(f"VK: попытка {attempt}/{_UPLOAD_RETRIES} загрузить {photo_url} "
                            f"на сервер VK — {error}")
            continue

        saved, save_error_code = _api_call_with_code("photos.saveWallPhoto", {
            "group_id": group_id,
            "photo": upload_result.get("photo"),
            "server": upload_result.get("server"),
            "hash": upload_result.get("hash"),
        })
        if not saved:
            if save_error_code == _FLOOD_CONTROL_CODE:
                wait_before_next = _FLOOD_CONTROL_BACKOFF_SEC
            # _api_call_with_code уже залогировал код/текст ошибки VK API —
            # здесь дополнительно печатаем то, что реально вернул
            # upload-сервер (server/photo/hash), чтобы при "photo is
            # undefined" сразу было видно, пустой ли это ответ или что-то
            # более конкретное.
            logger.warning(f"VK: попытка {attempt}/{_UPLOAD_RETRIES}, ответ upload-сервера "
                            f"для {photo_url}: server={upload_result.get('server')!r}, "
                            f"photo={upload_result.get('photo')!r}, "
                            f"hash={upload_result.get('hash')!r}")
            continue

        saved_photo = saved[0]
        return f"photo{saved_photo['owner_id']}_{saved_photo['id']}"

    logger.error(f"VK: не удалось прикрепить фото {photo_url} за {_UPLOAD_RETRIES} попыток(и)")
    return None


def _video_save(group_id: int, name: str, description: str) -> dict | None:
    """
    Как _api_call(), но для video.save отдельно распознаёт ошибки 15/204 —
    у VK это типичные коды "нет доступа" именно на video.save, когда у
    токена нет права 'video' (тот же класс проблемы, что уже был с
    photos.getWallUploadServer на community-токене без права 'photos' —
    см. FINAL_STATE.md раздел 9). Отдельная функция (не через _api_call()),
    чтобы не менять его контракт возврата (используется во многих местах
    как "dict или None") ради одного специфичного случая.
    """
    request_params = {
        "name": name, "description": description,
        "wallpost": 0,  # публикуем сами через wall.post с attachments, не даём VK создать свой пост
        "group_id": group_id,
        "access_token": config.VK_TOKEN, "v": API_VERSION,
    }
    try:
        response = requests.post(f"{API_BASE}/video.save", data=request_params, timeout=20)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as error:
        logger.error(f"VK API video.save: сетевая ошибка ({_mask_secret(str(error))})")
        return None
    except ValueError as error:
        logger.error(f"VK API video.save: не удалось разобрать ответ ({_mask_secret(str(error))})")
        return None

    if "error" in data:
        error_info = data["error"]
        code = error_info.get("error_code")
        if code in (15, 204):
            logger.error(
                f"VK API video.save: ошибка {code} — {error_info.get('error_msg')}. "
                f"Похоже, у VK_TOKEN нет права 'video' — нужен user-токен с правами "
                f"photos,wall,groups,video (см. FINAL_STATE.md раздел 9 про Kate Mobile "
                f"— тот же способ получения токена, просто отметь дополнительно video "
                f"при выпуске)."
            )
        else:
            logger.error(f"VK API video.save: ошибка {code} — {error_info.get('error_msg')}")
        return None

    return data.get("response")


def upload_video_attachment(video_path, group_id: int, name: str, description: str = "") -> str | None:
    """
    Загружает видеофайл на стену VK (video.save -> POST файла на upload_url).
    Возвращает attachment-ID вида "video{owner_id}_{id}" (можно передавать
    в attachments у wall.post вперемешку с photo{}_{} — VK ОК со смешанными
    вложениями в одном посте), или None при ошибке.

    Про VK Клипы: у VK нет документированного публичного API-метода для
    загрузки видео именно в Клипы от имени сообщества — video.save грузит
    ОБЫЧНОЕ видео (в раздел "Видео" группы + как вложение к посту).
    Вертикальный формат отсюда МОЖНО прикрепить к посту как обычное видео
    (пост будет выглядеть нормально в ленте), но в сами Клипы он
    автоматически не попадёт — это осознанно, выдумывать обходной путь
    через недокументированные методы не стали (см. video_generator.py
    докстринг и FINAL_STATE.md) — Клипы придётся заливать вручную через
    приложение/сайт VK.
    """
    save_result = _video_save(group_id, name, description)
    if save_result is None:
        return None
    upload_url = save_result.get("upload_url")
    if not upload_url:
        logger.error("VK: video.save не вернул upload_url")
        return None

    try:
        with open(video_path, "rb") as f:
            upload_response = requests.post(
                upload_url,
                files={"video_file": (Path(video_path).name, f, "video/mp4")},
                timeout=300,  # видео в разы тяжелее фото — 30с (как у фото) мало
            )
        upload_response.raise_for_status()
    except (requests.RequestException, OSError) as error:
        logger.error(f"VK: не удалось загрузить видео {video_path} на сервер VK ({error})")
        return None

    owner_id = save_result.get("owner_id")
    video_id = save_result.get("video_id")
    if owner_id is None or video_id is None:
        logger.error(f"VK: video.save не вернул owner_id/video_id ({save_result!r})")
        return None

    logger.info(f"VK: видео загружено (video{owner_id}_{video_id})")
    return f"video{owner_id}_{video_id}"


def send_to_vk(text: str, photos: list | None = None, dry_run: bool = False,
               video_path=None, video_name: str = "") -> bool:
    """
    Публикует пост на стену сообщества VK (config.VK_OWNER_ID).

    Args:
        text: готовый текст поста (тот же самый, что уходит в Telegram).
        photos: список URL фото (может быть пустым/None — тогда пост без фото).
        dry_run: не публиковать по-настоящему, только показать в консоли.
        video_path: локальный путь к готовому видеофайлу (см.
            video_generator.py) — прикрепляется к ЭТОМУ ЖЕ посту, вперемешку
            с фото-вложениями ("режим same" в agent_publisher.py). Для
            режима "отдельным постом" видео публикуется через отдельный
            вызов send_to_vk() без photos, см. agent_publisher.py.
        video_name: название видео в VK (video.save name) — обязательно,
            если передан video_path.

    Returns:
        True при успехе (или dry_run, или VK не настроен — см. is_configured()).
        Отдельные фото могут не прикрепиться (после ретраев, см.
        _upload_one_photo()) — пост всё равно публикуется с тем, что
        получилось, ЕСЛИ успешных фото >= config.MIN_PHOTOS_FOR_POST
        (добавлено 2026-07-17, инцидент Chevrolet Trax 1.2 ACTIV — раньше
        одно не прикрепившееся фото блокировало весь пост). False — если
        успешных фото меньше порога, ИЛИ ошибка самого wall.post. Не
        блокирует Telegram — см. agent_publisher.publish_post().
        Видео — МЯГКОЕ дополнение (в отличие от фото): если прикрепить его
        не удалось, пост всё равно публикуется без видео (текст/фото важнее,
        видео просто необязательный бонус) — только предупреждение в лог.
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
        print(f"VK ТЕСТОВЫЙ РЕЖИМ (ничего не отправлено). Фото: {len(photos)}, "
              f"видео: {'да, ' + str(video_path) if video_path else 'нет'}")
        print("=" * 50)
        _safe_print(text)
        print("=" * 50)
        return True

    owner_id = int(config.VK_OWNER_ID)
    group_id = abs(owner_id)

    # Инцидент 2026-07-17 (Chevrolet Trax 1.2 ACTIV): раньше ЛЮБОЕ
    # не прикрепившееся фото блокировало публикацию поста в VK целиком,
    # даже если остальные фото загрузились нормально (VK-сторона иногда
    # единично спотыкается на валидном файле — см. _upload_one_photo()).
    # Теперь: собираем что получилось, публикуем с частичным набором, если
    # успешных >= config.MIN_PHOTOS_FOR_POST — полный отказ только ниже
    # порога (см. её докстринг в config.py).
    attachments = []
    failed_photos = []
    for photo_url in photos:
        attachment = _upload_one_photo(photo_url, group_id)
        if attachment is None:
            failed_photos.append(photo_url)
        else:
            attachments.append(attachment)

    if failed_photos:
        logger.warning(f"VK: не удалось прикрепить {len(failed_photos)}/{len(photos)} фото "
                        f"после ретраев: {failed_photos}")

    if len(attachments) < config.MIN_PHOTOS_FOR_POST:
        logger.error(f"VK: успешных фото {len(attachments)} < порога MIN_PHOTOS_FOR_POST="
                     f"{config.MIN_PHOTOS_FOR_POST} — публикация заблокирована")
        return False

    if failed_photos:
        logger.info(f"VK: публикую с {len(attachments)}/{len(photos)} фото "
                     f"(порог MIN_PHOTOS_FOR_POST={config.MIN_PHOTOS_FOR_POST} пройден)")

    if video_path is not None:
        video_attachment = upload_video_attachment(video_path, group_id, video_name)
        if video_attachment is None:
            logger.warning(f"VK: не удалось прикрепить видео {video_path} — публикую пост "
                            f"без видео (текст/фото это не блокирует)")
        else:
            attachments.append(video_attachment)

    post_params = {"owner_id": owner_id, "message": text, "from_group": 1}
    if attachments:
        post_params["attachments"] = ",".join(attachments)

    result = _api_call("wall.post", post_params, timeout=30)
    if result is None:
        return False

    post_id = result.get("post_id")
    logger.info(f"VK: пост опубликован (post_id={post_id})")
    return True
