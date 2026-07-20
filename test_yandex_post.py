# -*- coding: utf-8 -*-
"""
test_yandex_post.py — РУЧНОЙ прогон Яндекс-витрины (yandex_post_builder.py/
yandex_post_validator.py/yandex_publisher.py). НЕ юнит-тест (не входит в
автоматический CI) — реально отправляет сообщения в config.YANDEX_DRAFTS_CHAT_ID:

    1. Короткий пинг — подтвердить, что служебный чат подключён и токен/id
       рабочие, ДО того как слать сам черновик поста.
    2. Один тестовый черновик по стаб-лоту (Kia K5, статус по умолчанию —
       under_order, как у всех лотов пайплайна) — текст помечен "[ТЕСТ] ".

Использование:
    python test_yandex_post.py            # реальная отправка
    python test_yandex_post.py --dry-run   # только печать в консоль
"""

import sys

import config
from customs_calculator import DEFAULT_RATES
from telegram_publisher import send_to_telegram
from yandex_publisher import YandexDraftBlockedError, send_yandex_draft

# Стаб-лот — НЕ реальное объявление (пользователь предпочёл не давать
# данные реального лота для первого прогона), помечен external_id
# "test-yandex-stub-001", чтобы не пересечься со own_stock.json реальных лотов.
STUB_CAR = {
    "brand": "Kia",
    "model": "K5",
    "trim": "1.6T",
    "external_id": "test-yandex-stub-001",
    "year": 2021,
    "mileage_km": 45000,
    "cc": 1600,
    "hp": 150,  # <= 160 л.с. — реальный K5 1.6T, льготный утильсбор (см. CLAUDE.md)
    "fuel_code": "petrol",
    "price": 28000000,
    "specs": "климат-контроль, камера заднего вида, подогрев сидений",
    "photos": [f"https://example.com/test-{i}.jpg" for i in range(3)],
    "source": "test",
}


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def run(dry_run: bool = False) -> None:
    if not config.YANDEX_DRAFTS_CHAT_ID:
        print("YANDEX_DRAFTS_CHAT_ID пуст — впишите ADMIN_CHAT_ID (или отдельный "
              "chat_id) в .env и повторите прогон.")
        raise SystemExit(1)

    print("=== 1. Проверка подключения служебного чата ===")
    ping_ok = send_to_telegram(
        "[ТЕСТ] Служебный чат подключён — yandex_publisher готов к прогону.",
        dry_run=dry_run,
        chat_id=config.YANDEX_DRAFTS_CHAT_ID,
    )
    if not ping_ok:
        print("Пинг НЕ отправлен — см. лог telegram_publisher выше. Прерываю.")
        raise SystemExit(1)
    print("OK" + (" (dry-run — см. текст выше)" if dry_run else " — пинг отправлен"))

    print()
    print("=== 2. Тестовый черновик Яндекс-поста (статус: under_order по умолчанию) ===")
    rates = dict(DEFAULT_RATES)
    try:
        ok, reason, message = send_yandex_draft(
            STUB_CAR, rates, dry_run=dry_run, test_marker=True,
        )
    except YandexDraftBlockedError as error:
        print(f"ЗАБЛОКИРОВАНО: {error}")
        raise SystemExit(1)

    if not ok:
        print(f"Черновик НЕ отправлен: {reason}")
        raise SystemExit(1)

    print("OK" + (" (dry-run — см. текст выше)" if dry_run else " — черновик отправлен"))
    print()
    print("--- Текст, который ушёл в служебный чат ---")
    _safe_print(message)


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
