# -*- coding: utf-8 -*-
"""
yandex_publisher.py — отправка готового черновика Яндекс/Дзен-поста (см.
yandex_post_builder.build_yandex_post()) в служебный Telegram-чат для
ручного копирования в кабинет Яндекса (у Яндекс Карт нет API публикации —
см. правило в CLAUDE.md). НИКОГДА не отправляет в config.TELEGRAM_CHAT_ID
(публичный канал лидогена) — см. YandexDraftBlockedError ниже.
"""

import logging

import config
from telegram_publisher import send_to_telegram
from yandex_post_builder import build_yandex_post
from yandex_post_validator import validate_yandex_post

logger = logging.getLogger("yandex_publisher")


class YandexDraftBlockedError(Exception):
    """
    Поднимается, если config.YANDEX_DRAFTS_CHAT_ID совпадает с
    config.TELEGRAM_CHAT_ID — защита от того, чтобы черновик Яндекс-витрины
    (может быть недосмотренным перед публикацией, это ЧЕРНОВИК) случайно не
    улетел в публичный канал лидогена. Проверяется прямо в точке отправки,
    не где-то выше по стеку — см. post_validator.PublishBlockedError про то,
    почему так надёжнее.
    """


def format_draft_message(post: dict, test_marker: bool = False, extra_header: str | None = None) -> str:
    prefix = "[ТЕСТ] " if test_marker else ""
    header = f"{extra_header}\n\n" if extra_header else ""
    return f"{header}{prefix}{post['title']}\n\n{post['text']}\n\n[статус: {post['stock_status']}]"


def send_yandex_draft(
    car: dict,
    rates: dict,
    stock_status: str | None = None,
    dry_run: bool = False,
    test_marker: bool = False,
    extra_header: str | None = None,
) -> tuple[bool, str, str | None]:
    """
    Args:
        extra_header: необязательная строка, добавляется ПЕРЕД заголовком
            (своя пустая строка-разделитель) — main_pipeline.py использует
            для "дата · Черновик для Яндекса · в очереди ещё N лотов"
            (см. yandex_queue.py). test_yandex_post.py её не передаёт.

    Returns:
        (True, "", message) — отправлено (или dry_run) успешно, message —
            то, что фактически ушло в чат.
        (False, reason, None) — не отправлено (нет данных / не прошло
            валидацию / чат не настроен) — reason человекочитаемый.
    """
    if config.YANDEX_DRAFTS_CHAT_ID == config.TELEGRAM_CHAT_ID:
        raise YandexDraftBlockedError(
            "config.YANDEX_DRAFTS_CHAT_ID совпадает с config.TELEGRAM_CHAT_ID "
            "(публичный канал лидогена) — отправка заблокирована. Впишите "
            "отдельный YANDEX_DRAFTS_CHAT_ID (или ADMIN_CHAT_ID) в .env."
        )
    if not config.YANDEX_DRAFTS_CHAT_ID:
        reason = "YANDEX_DRAFTS_CHAT_ID пуст — впишите ADMIN_CHAT_ID (или отдельный chat_id) в .env"
        logger.error(reason)
        return False, reason, None

    post, reason = build_yandex_post(car, rates, stock_status=stock_status)
    if post is None:
        return False, reason, None

    result = validate_yandex_post(post)
    if not result:
        reason = "; ".join(result.errors)
        logger.error(f"Яндекс-черновик отклонён валидатором: {reason}")
        return False, reason, None

    message = format_draft_message(post, test_marker=test_marker, extra_header=extra_header)
    ok = send_to_telegram(message, dry_run=dry_run, chat_id=config.YANDEX_DRAFTS_CHAT_ID)
    if not ok:
        return False, "Telegram вернул ошибку при отправке — см. лог telegram_publisher", None
    return True, "", message
