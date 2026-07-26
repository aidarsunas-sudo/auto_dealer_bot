# -*- coding: utf-8 -*-
"""
yandex_digest_builder.py — дневной дайджест для Яндекс-витрины: подборка
2-4 лучших лотов, опубликованных за последние сутки, ОДНИМ постом (вместо
одного черновика раз в config.YANDEX_MIN_INTERVAL_DAYS дней — см.
yandex_queue.py, тот поток остаётся отдельным, дайджест НЕ переиспользует
его очередь и не мешает ей).

Откуда лоты — yandex_digest_pool.json:
    Накопительный (НЕ расходуемый при выборке, в отличие от yandex_queue.json)
    журнал полных car-словарей. main_pipeline.py дописывает сюда КАЖДЫЙ
    успешно опубликованный (не dry-run) лот — см. record_published_lot(),
    вызывается рядом с yandex_queue.enqueue_lot(). Лоты старше
    config.YANDEX_DIGEST_POOL_MAX_AGE_DAYS вычищаются при каждой записи; один
    и тот же лот МОЖЕТ попасть в несколько дайджестов подряд идущих дней,
    если всё ещё в окне отбора — единственное явно запрещённое — два дня
    ПОДРЯД (см. yandex_digest_history.json / select_digest_lots()).

Почему не yandex_queue.json: та очередь — FIFO для ОДНОГО черновика раз в
несколько дней, pick_best_lot() физически УДАЛЯЕТ выбранный лот. Дайджесту
нужен другой срез данных ("что опубликовано за последние сутки", независимо
от того, попал ли уже лот в одиночный черновик) — общий накопитель со своей
логикой чистки по возрасту, а не по факту использования.

Отбор (select_digest_lots()):
    1. Лоты за последние config.YANDEX_DIGEST_WINDOW_HOURS часов (24).
    2. Меньше config.YANDEX_DIGEST_MIN_FOR_WINDOW (3) — расширяем окно до
       config.YANDEX_DIGEST_FALLBACK_WINDOW_HOURS (48).
    3. Исключаем лоты из ПРЕДЫДУЩЕГО дайджеста (не два дня подряд).
    4. Меньше config.YANDEX_DIGEST_MIN_LOTS (2) — дайджест сегодня НЕ
       строится, причина — в лог, ничего никуда не уходит.
    5. Берём до config.YANDEX_DIGEST_MAX_LOTS (4, см. её докстринг в
       config.py про лимит фото Яндекс Бизнес), от свежих к старым.

Двойная валидация (build_digest_post() + validate_yandex_digest()):
    - Каждый лот-кандидат ДО включения в текст проходит
      post_validator.validate_post_before_publish() — тот же гейт фото/
      названия/года/пробега/цены, что и у обычного лидогена (см. CLAUDE.md
      "Железные правила публикации"): дайджест не обходит общие лимиты
      качества лота. Не прошедший гейт лот молча пропускается (не весь
      дайджест из-за одного плохого лота).
    - Собранный ТЕКСТ дайджеста целиком проходит
      yandex_post_validator.validate_yandex_digest() — ОТДЕЛЬНЫЙ профиль
      (лимит длины/фото площадки, Telegram-разметка, хангыль, эмодзи), НЕ
      validate_yandex_post() (тот заточен под одиночный in_stock/under_order
      шаблон с фиксированными CTA/ключевыми фразами — дайджесту не подходит).

"Лучший"/"1 лучшее фото" — без формальной метрики качества фото/лота (её
нет ни у одного источника) упрощено до: лоты — по свежести публикации
(новые вперёд), фото — первое из car["photos"] (тот же список, что уже
прошёл через прежние проверки на этапе публикации лида).

Формат поста — под Яндекс Бизнес (без Telegram-разметки, эмодзи умеренно,
маркер списка "•" — не эмодзи):
    {хук}

    • {Марка Модель}, {год}
      Пробег {пробег}, {двигатель}
      Цена под ключ: {цена} ₽

    ... (ещё лоты)

    {CTA}
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import config
from post_text_builder import prepare_post_fields
from post_validator import validate_post_before_publish
from yandex_post_validator import validate_yandex_digest
from telegram_publisher import send_to_telegram, send_media_group_to_telegram

logger = logging.getLogger("yandex_digest_builder")

POOL_PATH = Path(__file__).resolve().parent / "yandex_digest_pool.json"
HISTORY_PATH = Path(__file__).resolve().parent / "yandex_digest_history.json"

HOOK = "Свежие поступления под заказ из Кореи и Китая"
CTA = (
    "Привозим авто под заказ из Кореи и Китая — под ключ: выкуп, доставка, "
    "растаможка, оформление ЭПТС. Пишите или звоните — подберём вариант под "
    "ваш бюджет и модель."
)


class YandexDigestBlockedError(Exception):
    """
    Поднимается, если config.YANDEX_DRAFTS_CHAT_ID совпадает с
    config.TELEGRAM_CHAT_ID — та же защита, что и у
    yandex_publisher.YandexDraftBlockedError, отдельным классом (независимый
    поток, см. докстринг файла), чтобы не тянуть импорт yandex_publisher.py
    только ради одного исключения.
    """


# ─────────────────────────── пул опубликованных лотов ────────────────────────

def _load_json(path: Path, default):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        logger.error(f"{path.name} повреждён ({error}) — использую значение по умолчанию")
        return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_pool() -> list:
    return _load_json(POOL_PATH, [])


def _load_history() -> dict:
    return _load_json(HISTORY_PATH, {"last_digest_at": None, "last_lot_ids": []})


def _lot_id(car: dict) -> Optional[str]:
    return car.get("external_id") or car.get("url")


def record_published_lot(car: dict) -> None:
    """
    Вызывать РЯДОМ с yandex_queue.enqueue_lot() (main_pipeline.py) — после
    КАЖДОЙ успешной (не dry-run) публикации в TG/VK. В отличие от
    yandex_queue.json, отсюда лоты НЕ удаляются при выборке в дайджест,
    только по возрасту (см. докстринг файла).
    """
    identifier = _lot_id(car)
    if not identifier:
        logger.warning("record_published_lot: у лота нет external_id/url — пропускаю "
                       "(не с чем дедупить/датировать)")
        return

    pool = _load_pool()
    cutoff = datetime.now() - timedelta(days=config.YANDEX_DIGEST_POOL_MAX_AGE_DAYS)
    pool = [e for e in pool if datetime.fromisoformat(e["published_at"]) >= cutoff]

    if any(_lot_id(e["car"]) == identifier for e in pool):
        return  # уже в пуле — не дублируем

    pool.append({"car": car, "published_at": datetime.now().isoformat()})
    _save_json(POOL_PATH, pool)


# ─────────────────────────── отбор лотов для дайджеста ───────────────────────

def select_digest_lots(now: Optional[datetime] = None) -> tuple[list, str]:
    """
    Returns:
        (lots, "") — от config.YANDEX_DIGEST_MIN_LOTS до
            config.YANDEX_DIGEST_MAX_LOTS car-словарей, от свежих к старым.
        ([], reason) — дайджест сегодня не строится, reason — для лога (НЕ
            для отправки куда-либо — молчаливый отказ с логом, см. докстринг
            файла).
    """
    now = now or datetime.now()
    pool = _load_pool()
    if not pool:
        return [], "пул пуст — ещё ни один лот не записан (record_published_lot)"

    history = _load_history()
    excluded_ids = {i for i in (history.get("last_lot_ids") or []) if i}

    def _within(hours: int) -> list:
        cutoff = now - timedelta(hours=hours)
        picked = []
        for entry in pool:
            published_at = datetime.fromisoformat(entry["published_at"])
            if published_at < cutoff:
                continue
            if _lot_id(entry["car"]) in excluded_ids:
                continue
            picked.append(entry)
        picked.sort(key=lambda e: e["published_at"], reverse=True)
        return picked

    window_hours = config.YANDEX_DIGEST_WINDOW_HOURS
    picked = _within(window_hours)
    if len(picked) < config.YANDEX_DIGEST_MIN_FOR_WINDOW:
        window_hours = config.YANDEX_DIGEST_FALLBACK_WINDOW_HOURS
        picked = _within(window_hours)
        logger.info(f"yandex_digest: меньше {config.YANDEX_DIGEST_MIN_FOR_WINDOW} лотов за "
                    f"{config.YANDEX_DIGEST_WINDOW_HOURS}ч — расширяю окно до {window_hours}ч "
                    f"({len(picked)} лотов)")

    if len(picked) < config.YANDEX_DIGEST_MIN_LOTS:
        return [], (f"лотов недостаточно даже за {window_hours}ч: {len(picked)} < минимума "
                    f"{config.YANDEX_DIGEST_MIN_LOTS} (исключены как вчерашние: {len(excluded_ids)})")

    cars = [e["car"] for e in picked[:config.YANDEX_DIGEST_MAX_LOTS]]
    return cars, ""


# ─────────────────────────── сборка текста дайджеста ─────────────────────────

def _lot_block(car: dict, fields: dict) -> str:
    title = fields.get("title") or f"{car.get('brand', '')} {car.get('model', '')}".strip()
    year = fields.get("year") or car.get("year") or ""
    mileage = fields.get("mileage_km", "")
    engine = fields.get("engine_display", "")
    total_rub = fields.get("total_rub")

    line1 = f"• {title}, {year}".rstrip(", ")
    line2 = f"  Пробег {mileage}, {engine}".rstrip(", ")
    line3 = f"  Цена под ключ: {total_rub} ₽" if total_rub else "  Цена под ключ: уточняется"
    return "\n".join([line1, line2, line3])


def build_digest_post(cars: list, rates: dict) -> tuple[Optional[dict], list, str]:
    """
    Собирает текст дайджеста + список фото (по 1 на лот, максимум
    config.YANDEX_DIGEST_MAX_PHOTOS).

    Returns:
        ({"title": str, "text": str}, photos, "") — готово к
            yandex_post_validator.validate_yandex_digest() и отправке.
        (None, [], reason) — после гейта лотов осталось меньше
            config.YANDEX_DIGEST_MIN_LOTS (см. докстринг файла — гейт тот
            же, что у обычного лидогена, дайджест его не обходит).
    """
    blocks, photos = [], []
    for car in cars:
        fields, reason = prepare_post_fields(car, rates)
        if fields is None:
            logger.info(f"yandex_digest: лот пропущен ({reason}) — "
                        f"{car.get('brand', '')} {car.get('model', '')}")
            continue

        validated, reason = validate_post_before_publish(car, fields)
        if validated is None:
            logger.info(f"yandex_digest: лот отклонён гейтом ({reason}) — "
                        f"{car.get('brand', '')} {car.get('model', '')}")
            continue

        blocks.append(_lot_block(car, fields))
        car_photos = car.get("photos") or []
        if car_photos:
            photos.append(car_photos[0])

    if len(blocks) < config.YANDEX_DIGEST_MIN_LOTS:
        return None, [], (f"после гейта validate_post_before_publish осталось {len(blocks)} "
                          f"лотов из {len(cars)} < минимума {config.YANDEX_DIGEST_MIN_LOTS}")

    hook = f"{HOOK} — {len(blocks)} авто"
    text = "\n\n".join(blocks) + "\n\n" + CTA
    photos = photos[:config.YANDEX_DIGEST_MAX_PHOTOS]

    return {"title": hook, "text": text}, photos, ""


def already_sent_today(now: Optional[datetime] = None) -> bool:
    """True, если дайджест уже отправлялся СЕГОДНЯ (по history["last_digest_at"])
    — защита от дубля в служебный чат при повторном ручном запуске workflow
    в тот же день (см. run_digest(), параметр force — явный обход этой
    проверки, когда повтор действительно нужен)."""
    now = now or datetime.now()
    last_digest_at = _load_history().get("last_digest_at")
    if not last_digest_at:
        return False
    return datetime.fromisoformat(last_digest_at).date() == now.date()


def mark_digest_sent(cars: list) -> None:
    """Вызывать ПОСЛЕ успешной (не dry-run) отправки — запоминает лоты этого
    дайджеста, чтобы select_digest_lots() исключил их из ЗАВТРАШНЕГО (не два
    дня подряд один и тот же лот)."""
    lot_ids = [i for i in (_lot_id(c) for c in cars) if i]
    _save_json(HISTORY_PATH, {"last_digest_at": datetime.now().isoformat(), "last_lot_ids": lot_ids})


# ─────────────────────────── доставка в служебный чат ────────────────────────

def send_digest(post: dict, photos: list, dry_run: bool = False) -> tuple[bool, str]:
    """
    Отправляет дайджест в config.YANDEX_DRAFTS_CHAT_ID (тот же служебный
    чат, что и одиночные Яндекс-черновики, yandex_publisher.py) ДВУМЯ
    сообщениями:
        1. Текст дайджеста целиком (sendMessage, лимит Telegram — 4096
           символов, с запасом хватает на config.YANDEX_DIGEST_MAX_CHARS=3000).
        2. Фото альбомом (sendMediaGroup) с КОРОТКОЙ подписью-меткой — сам
           текст дайджеста НЕ кладём в подпись альбома: у sendMediaGroup
           подпись режется на 1024 символа (telegram_publisher.
           CAPTION_MAX_LENGTH), а дайджест может быть длиннее — обрезалась
           бы именно та часть, которую нужно скопировать в кабинет Яндекса.
    Оба сообщения помечены "ЯНДЕКС-ДАЙДЖЕСТ", чтобы не путать с одиночными
    черновиками в том же чате.
    """
    if config.YANDEX_DRAFTS_CHAT_ID == config.TELEGRAM_CHAT_ID:
        raise YandexDigestBlockedError(
            "config.YANDEX_DRAFTS_CHAT_ID совпадает с config.TELEGRAM_CHAT_ID "
            "(публичный канал лидогена) — отправка дайджеста заблокирована. "
            "Впишите отдельный YANDEX_DRAFTS_CHAT_ID (или ADMIN_CHAT_ID) в .env."
        )
    if not config.YANDEX_DRAFTS_CHAT_ID:
        return False, "YANDEX_DRAFTS_CHAT_ID пуст — впишите ADMIN_CHAT_ID (или отдельный chat_id) в .env"

    today = datetime.now().strftime("%d.%m.%Y")
    header = f"📋 ЯНДЕКС-ДАЙДЖЕСТ · {today}"
    message = f"{header}\n\n{post['title']}\n\n{post['text']}"

    if not send_to_telegram(message, dry_run=dry_run, chat_id=config.YANDEX_DRAFTS_CHAT_ID):
        return False, "не удалось отправить текст дайджеста"

    if photos:
        if not send_media_group_to_telegram(photos, header, dry_run=dry_run,
                                            chat_id=config.YANDEX_DRAFTS_CHAT_ID):
            return False, "текст отправлен, но альбом фото — нет"

    return True, ""


# ─────────────────────────── оркестрация целиком ─────────────────────────────

def run_digest(rates: dict, dry_run: bool = False, force: bool = False,
               now: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Весь путь дайджеста: суточный гейт -> отбор -> сборка -> валидация ->
    отправка -> дедуп. Используется run_yandex_digest.py (см.
    YANDEX_DIGEST_ENABLED в config.py и .github/workflows/yandex_digest.yml).

    Args:
        force: обойти суточный гейт already_sent_today() — для случаев,
            когда повторная отправка в тот же день действительно нужна
            (см. run_yandex_digest.py --force).
        now: для тестов (см. test_yandex_digest.py) — иначе datetime.now().

    Returns:
        (True, "") — отправлено (или dry_run).
        (False, reason) — не отправлено; reason человекочитаемый, это НЕ
            обязательно ошибка (например, "лотов недостаточно" — штатный
            день без дайджеста, см. select_digest_lots(); "уже отправлялся
            сегодня" — защита от дубля при повторном ручном запуске).

    ВАЖНО: суточный гейт НЕ применяется при dry_run=True — dry-run ничего
    реально не отправляет и не может создать дубль (mark_digest_sent() и
    так вызывается только при not dry_run, см. ниже), поэтому его смысл —
    честно показать, что получилось бы собрать, а не наткнуться на гейт,
    предназначенный для защиты РЕАЛЬНОЙ отправки.
    """
    if not force and not dry_run and already_sent_today(now):
        return False, ("дайджест уже отправлялся сегодня (last_digest_at) — повторный запуск "
                       "в тот же день пропущен (используйте --force, если повтор нужен)")

    cars, reason = select_digest_lots(now=now)
    if not cars:
        return False, reason

    post, photos, reason = build_digest_post(cars, rates)
    if post is None:
        return False, reason

    result = validate_yandex_digest(post, len(photos))
    if not result:
        return False, "; ".join(result.errors)

    ok, reason = send_digest(post, photos, dry_run=dry_run)
    if not ok:
        return False, reason

    if not dry_run:
        mark_digest_sent(cars)

    return True, ""
