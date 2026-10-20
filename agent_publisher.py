# -*- coding: utf-8 -*-
"""
agent_publisher.py — "агент-публикатор" в мультиагентном пайплайне (orchestrator.py)
и в main_pipeline.py.

publish_post() — ЕДИНСТВЕННАЯ функция во всём проекте, которая имеет право
вызывать telegram_publisher/vk_publisher (проверено grep — прямых вызовов
send_to_telegram/send_photo_to_telegram/send_media_group_to_telegram/
send_to_vk больше нигде нет). Именно поэтому жёсткий гейт
validate_post_before_publish() стоит ЗДЕСЬ, первым делом внутри самой
функции, а не отдельным вызовом в каждом из вызывающих скриптов
(main.py/main_pipeline.py/publish_now.py) — так его нельзя ни забыть
добавить в одном из них, ни обойти. См. PublishBlockedError.

Без фото — send_to_telegram() (как раньше), с фото — sendPhoto/
sendMediaGroup в зависимости от количества. Telegram — ОСНОВНОЙ канал, от
него зависит возвращаемое значение (и, дальше по цепочке, дедуп в
published_log.json).

Если объявление заявлено с фото, но прикрепить их не удалось (битая
ссылка/сбой сети) — публикация БЛОКИРУЕТСЯ целиком (не откатывается на
текст без фото). Раньше было наоборот ("никогда не блокировать пост"),
но это давало неполные посты — изменено по явному указанию 2026-07-06.

VK (vk_publisher.py) — ВТОРОЙ канал, best-effort по отношению к Telegram:
публикуется тем же текстом/фото сразу вслед за Telegram, и его успех/
неудача НЕ влияет на результат Telegram (см. publish_post() ниже). Но
внутри самого VK действует та же защита — если хотя бы одно фото не
прикрепилось, VK-пост тоже не публикуется (см. vk_publisher.send_to_vk()).
Если это и заблокировало пост (мало фото), а видео (VIDEO_POST_MODE=
"same") было готово — publish_post() пробует спасти хотя бы видео,
опубликовав его отдельным постом (см. ниже, инцидент 2026-09-24).
Вызов send_to_vk() здесь дополнительно обёрнут в try/except (2026-07-17) —
защита от НЕОЖИДАННЫХ ошибок (не сетевых/API, те уже ловятся внутри
vk_publisher.py и возвращают False), чтобы вообще ничего из VK не могло
прервать publish_post() после того, как Telegram уже отправлен.
Отключить канал целиком — VK_ENABLED=false в .env (см. config.py).
Никакой отдельной логики поиска/расчёта цены здесь нет — text/photos уже
готовы на входе, те же самые для обоих каналов.

send_video_to_telegram() — использует метод Bot API sendVideo. Сейчас её
никто не вызывает (scraper.py не собирает видео с сайтов дилеров) — это
задел на будущее, точка расширения.
"""

import logging
import os
import shutil

import requests

import config
import video_generator
import video_state
from telegram_publisher import send_to_telegram, send_photo_to_telegram, send_media_group_to_telegram
from vk_publisher import send_to_vk
from post_validator import (validate_post_before_publish, validate_new_catalog_post,
                            PublishBlockedError, ValidatedCar)

logger = logging.getLogger("agent_publisher")


def _generate_video_safe(car: ValidatedCar, fields: dict) -> dict | None:
    """
    Обёртка над video_generator.generate_videos() — видео строго
    необязательное дополнение к посту (текст/фото важнее и от него не
    зависят), поэтому ЛЮБАЯ ошибка здесь (включая совсем неожиданную,
    не только сетевую/ffmpeg) должна просто пропустить видео, а не
    сломать publish_post() целиком. generate_videos() сама возвращает
    None на предвиденные причины (нет ffmpeg, нет фото и т.п.) — этот
    try/except ловит то, что она не предвидела сама.
    """
    if not video_generator.ffmpeg_available():
        logger.info("Видео: ffmpeg не найден в PATH — пропускаю (см. video_generator.py)")
        return None
    try:
        return video_generator.generate_videos(car, fields)
    except Exception as error:
        logger.error(f"Видео: неожиданная ошибка генерации ({error}) — пропускаю")
        return None


CAR_KINDS = ("used", "new_catalog")


def publish_post(car: dict, text: str, dry_run: bool = False,
                  fields: dict | None = None, car_kind: str = "used") -> bool:
    """
    Публикует готовый текст поста в Telegram (основной канал) и, следом,
    в VK (второй канал, best-effort — см. докстринг файла).

    Первым делом прогоняет car через гейт: для б/у (car_kind="used", по умолчанию) —
    validate_post_before_publish() (фото/название/год/пробег/цена), для новых авто из
    каталога дилера (car_kind="new_catalog") — validate_new_catalog_post() (то же без
    фото и года). Обхода гейта нет: прежний параметр skip_validation удалён.
    Нарушение — PublishBlockedError, HTTP-запрос к Telegram/VK не делается
    вообще. Это ЕДИНСТВЕННОЕ место в проекте, где стоит эта проверка —
    вызывающий код (main.py/main_pipeline.py/publish_now.py) НЕ должен
    дублировать вызов validate_post_before_publish() сам.

    Args:
        car: словарь объявления (brand/model/trim/year/photos/url/...) —
             используется ТОЛЬКО для валидации и чтобы достать photos;
             сам текст поста уже готов заранее (см. text).
        text: готовый текст поста.
        dry_run: не публиковать по-настоящему, только показать в консоли.
        car_kind: "used" (по умолчанию) — полный гейт; "new_catalog" — новые авто из
            дилерского каталога (main.py: process_one_page, WATCHLIST): там car["year"]
            намеренно всегда None и сайт дилера не отдаёт фото (см. scraper.py,
            age_bucket_for_new_catalog_car()) — это другой класс объявлений, для него
            отдельный гейт validate_new_catalog_post(), а не отключение проверок.
        fields: словарь из post_text_builder.prepare_post_fields() (та же
            структура, что использует lead_post_builder.py для текста) —
            используется для видео (title/year/mileage_km/engine_display/
            transmission/total_rub/cta_bot, см. video_generator.py) И для
            проверок цены в гейте (total_rub_raw, см. post_validator.
            check_price_absolute()/check_price_relative()). Если не передан
            (None) — видео не генерируется вообще, даже при VIDEO_ENABLED=
            true, а проверки цены молча пропускаются (вызывающий код должен
            сам передавать fields — см. main.py/main_pipeline.py/test_post.py).
            Публикация текста/фото от этого не зависит.

    Returns:
        True при успехе в Telegram (VK публикуется независимо и не влияет
        на этот результат). Если объявление заявлено с фото, но прикрепить
        их не удалось (например, битая ссылка на изображение) — публикация
        БЛОКИРУЕТСЯ целиком (False), а не откатывается на текст без фото.

    Raises:
        PublishBlockedError: гейт не пройден (см. car_kind выше).
        ValueError: неизвестный car_kind.
            Вызывающий код должен ловить её на уровне цикла по машинам
            (продолжить со следующей), а не давать упасть всему прогону.
    """
    if car_kind not in CAR_KINDS:
        raise ValueError(f"car_kind должен быть одним из {CAR_KINDS}, получено {car_kind!r}")
    gate = validate_post_before_publish if car_kind == "used" else validate_new_catalog_post
    validated, reason = gate(car, fields)
    if validated is None:
        identifier = car.get("url") or car.get("external_id") or "?"
        raise PublishBlockedError(f"Пост {identifier} заблокирован: {reason}")
    # ValidatedCar (не голый dict) — дальше по функции, включая видео/
    # озвучку (_generate_video_safe -> video_generator.generate_videos()),
    # используется именно эта проверенная обёртка (см. её докстринг в
    # post_validator.py про то, зачем это физическое разделение типов).
    car = validated

    photos = car.get("photos") or []

    # Видео — ДО отправки текста/фото, а не после: в режиме VIDEO_POST_MODE=
    # "same" горизонтальный ролик должен уйти В ТОТ ЖЕ ВЫЗОВ send_to_vk(),
    # что и фото (VK не даёт дописать вложение в уже опубликованный пост
    # постфактум) — поэтому видео должно быть готово ДО первого send_to_vk.
    # Не блокирует и не задерживает Telegram/VK при любой накладке — см.
    # _generate_video_safe() (никогда не поднимает исключение).
    videos = None
    if fields is not None and config.VIDEO_ENABLED and not dry_run:
        if video_state.should_generate_video_for_this_post(config.VIDEO_EVERY_N_POSTS):
            videos = _generate_video_safe(car, fields)

    video_name = f"{fields.get('title', '')} {fields.get('year', '')}".strip() if fields else ""
    inline_video = videos.get("horizontal") if (videos and config.VIDEO_POST_MODE == "same") else None

    if not photos:
        ok = send_to_telegram(text, dry_run=dry_run)
    elif len(photos) == 1:
        ok = send_photo_to_telegram(photos[0], text, dry_run=dry_run)
    else:
        ok = send_media_group_to_telegram(photos, text, dry_run=dry_run)

    if not ok:
        logger.error("Публикация в Telegram заблокирована — не удалось прикрепить фото")

    try:
        if not send_to_vk(text, photos, dry_run=dry_run, video_path=inline_video, video_name=video_name):
            logger.warning("Не удалось опубликовать в VK — Telegram это не затрагивает")
            # Инцидент 2026-09-24 (разбор архитектуры, без live-теста): в
            # режиме VIDEO_POST_MODE="same" видео уходит В ТОМ ЖЕ вызове
            # send_to_vk(), что и фото — если фото не набрали
            # MIN_PHOTOS_FOR_POST, send_to_vk() возвращает False ДО того,
            # как дойти до прикрепления video_path (см. vk_publisher.py),
            # и готовое видео пропадает вместе с постом, хотя видео тут
            # само по себе ни при чём. Раз основной пост не вышел —
            # пробуем спасти хотя бы видео отдельным постом (та же логика,
            # что и у VIDEO_POST_MODE="separate" в _publish_video_extra()
            # ниже, но для его действительно-запланированного повтора там
            # проверка config.VIDEO_POST_MODE == "separate" не сработает,
            # поэтому дублируем здесь, а не переиспользуем).
            if inline_video is not None:
                logger.info("VK: пробую опубликовать видео отдельным постом, раз основной пост не вышел")
                try:
                    caption_vk = text if text else video_name
                    if not send_to_vk(caption_vk, photos=None, dry_run=dry_run,
                                       video_path=inline_video, video_name=video_name):
                        logger.warning("VK: не удалось опубликовать видео и отдельным постом тоже")
                except Exception as error:
                    logger.error(f"VK: неожиданная ошибка при резервной публикации видео ({error})")
    except Exception as error:
        logger.error(f"VK: неожиданная ошибка при публикации ({error}) — Telegram это не затрагивает")

    if videos:
        try:
            _publish_video_extra(videos, video_name, text, dry_run)
        except Exception as error:
            logger.error(f"Видео: неожиданная ошибка при публикации ({error}) — на основной пост не влияет")
        finally:
            # generate_videos() без явного output_dir кладёт файлы во
            # временную папку и сама её не чистит при успехе (чистит
            # только при полном провале) — прибираем здесь, после того
            # как всё уже отправлено.
            tmp_dir = next(iter(videos.values())).parent
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return ok


def _publish_video_extra(videos: dict, video_name: str, text: str, dry_run: bool) -> None:
    """
    Досылает то, что НЕ уместилось в основной вызов send_to_vk() выше:
        - Telegram: sendVideo горизонтальным роликом (Telegram не даёт
          подмешать видео в уже отправленный sendMediaGroup постфактум —
          отдельное сообщение сразу следом, без подписи в режиме "same"
          — читается как продолжение поста; с полным текстом в режиме
          "separate" — самостоятельный пост).
        - VK: если VIDEO_POST_MODE=separate — отдельный wall.post только
          с горизонтальным видео (в режиме "same" он уже ушёл вместе с
          фото внутри publish_post()).
        - VK: вертикальная версия НЕ загружается (см. ниже) — раньше
          планировалось "обычным видео, Клипы оформить вручную", но
          предположение "в Клипы само не попадёт" не подтвердилось на
          практике (инцидент 2026-09-24: пользователь обнаружил в
          разделе Клипы сообщества видео с прошлых тестовых прогонов —
          VK сам вложил туда вертикальный ролик, загруженный через
          video.save, без текста/цены/ссылки на бота, 0 просмотров кроме
          нашего собственного — т.е. VK API САМ публично выкладывает
          вертикальное видео сообщества как Клип, вопреки старому
          комментарию). Публиковать туда "осиротевший" ролик без
          контекста хуже, чем не публиковать вообще — поэтому загрузку
          отключили; video_generator.py продолжает генерировать vertical
          (используется как есть — не трогаем), просто здесь его не
          отправляем.
    """
    horizontal = videos.get("horizontal")

    if horizontal:
        caption = text if config.VIDEO_POST_MODE == "separate" else ""
        if not send_video_to_telegram(str(horizontal), caption=caption, dry_run=dry_run):
            logger.warning("Видео: не удалось отправить горизонтальный ролик в Telegram")

        if config.VIDEO_POST_MODE == "separate":
            try:
                caption_vk = text if text else video_name
                if not send_to_vk(caption_vk, photos=None, dry_run=dry_run,
                                   video_path=horizontal, video_name=video_name):
                    logger.warning("Видео: не удалось опубликовать горизонтальный ролик отдельным постом в VK")
            except Exception as error:
                logger.error(f"VK: неожиданная ошибка при публикации видео-поста ({error})")

    # Вертикальная версия в VK больше НЕ загружается — см. докстринг
    # функции выше (инцидент 2026-09-24, "осиротевшие" Клипы без текста/
    # цены/ссылки). videos.get("vertical") здесь намеренно не используется.


def send_video_to_telegram(video: str, caption: str = "", dry_run: bool = False) -> bool:
    """
    Отправляет видео в Telegram-канал через Bot API метод sendVideo.

    Args:
        video: локальный путь к файлу, ПРЯМАЯ ссылка на видео (http/https)
               или уже загруженный в Telegram file_id — sendVideo принимает
               любой из этих трёх вариантов.
        caption: подпись к видео (аналог text у send_to_telegram).
        dry_run: как и у send_to_telegram — ничего не отправлять, только
                 напечатать в консоль.

    Returns:
        True при успехе (или dry_run). False при любой ошибке — сеть,
        неверный путь/URL, ошибка API. Никогда не поднимает исключение,
        как и остальные функции отправки в этом проекте.
    """
    if dry_run:
        print("═" * 50)
        print(f"ТЕСТОВЫЙ РЕЖИМ (видео не отправлено). video={video}, caption={caption}")
        print("═" * 50)
        return True

    telegram_api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendVideo"
    is_local_file = not video.startswith("http://") and not video.startswith("https://") and os.path.exists(video)

    try:
        if is_local_file:
            with open(video, "rb") as video_file:
                response = requests.post(
                    telegram_api_url,
                    data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption},
                    files={"video": video_file},
                    timeout=60,
                )
        else:
            # Прямая ссылка на видео или уже загруженный file_id — Telegram
            # сам скачает видео по URL, отдельный файл отправлять не нужно.
            response = requests.post(
                telegram_api_url,
                data={"chat_id": config.TELEGRAM_CHAT_ID, "video": video, "caption": caption},
                timeout=60,
            )
    except requests.RequestException as error:
        logger.error(f"Не удалось отправить видео в Telegram: {error}")
        return False
    except OSError as error:
        logger.error(f"Не удалось открыть файл видео: {error}")
        return False

    if response.ok:
        logger.info("✅ Видео успешно отправлено в Telegram")
        return True

    logger.error(f"❌ Telegram вернул ошибку при отправке видео: {response.status_code} — {response.text}")
    return False
