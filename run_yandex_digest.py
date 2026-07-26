# -*- coding: utf-8 -*-
"""
run_yandex_digest.py — точка входа для дневного Яндекс-дайджеста (см.
yandex_digest_builder.py). ОТДЕЛЬНЫЙ от main_pipeline.py прогон (своё
расписание — раз в сутки, а не раз в 6 часов, см.
.github/workflows/yandex_digest.yml) — main_pipeline.py только копит лоты в
пул (record_published_lot()), сама отправка дайджеста происходит здесь.

config.YANDEX_DIGEST_ENABLED — двойной гейт (тот же приём, что у VIDEO_ENABLED
в main_pipeline.yml): по умолчанию false (локально дайджест не уйдёт никуда
случайно), workflow явно взводит "true" в своём env — так что даже если
workflow запустят вручную БЕЗ этой переменной, скрипт всё равно тихо
завершится, не отправив ничего.

Запуск:
    python run_yandex_digest.py            # реальная отправка (если ENABLED)
    python run_yandex_digest.py --dry-run   # только показать, что получилось бы
    python run_yandex_digest.py --force     # отправить, даже если сегодня уже
                                            # отправляли (обходит суточный гейт
                                            # already_sent_today(), см.
                                            # yandex_digest_builder.run_digest())
"""

import argparse
import logging
import sys

import config
from rates import fetch_cbr_rates
from notifier import notify_error
from yandex_digest_builder import run_digest

LOG_FILE_PATH = "logs/yandex_digest.log"


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("run_yandex_digest")


def main() -> int:
    parser = argparse.ArgumentParser(description="Дневной Яндекс-дайджест (см. yandex_digest_builder.py)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Не отправлять по-настоящему, только показать, что получилось бы")
    parser.add_argument("--force", action="store_true",
                        help="Отправить, даже если дайджест сегодня уже отправлялся "
                             "(обходит суточный гейт already_sent_today())")
    args = parser.parse_args()

    log = setup_logging()
    log.info("=== Запуск run_yandex_digest ===")

    if not config.YANDEX_DIGEST_ENABLED:
        log.info("YANDEX_DIGEST_ENABLED=false — выхожу без отправки (см. .env/.github/workflows/yandex_digest.yml)")
        return 0

    try:
        rates = fetch_cbr_rates()
        ok, reason = run_digest(rates, dry_run=args.dry_run, force=args.force)
    except Exception as error:
        log.critical(f"КРИТИЧЕСКАЯ ОШИБКА: {error}", exc_info=True)
        notify_error(f"Критическая ошибка, run_yandex_digest остановился:\n{error}\n\n"
                     f"Подробности — в logs/yandex_digest.log")
        return 1

    if ok:
        log.info("=== Дайджест отправлен ===" if not args.dry_run else "=== Дайджест собран (dry-run) ===")
        return 0

    # "Лотов недостаточно"/"пул пуст" — штатный день без дайджеста, "уже
    # отправлялся сегодня" — штатная защита от дубля (see already_sent_today())
    # — ни то, ни другое не ошибка. Отличаем по фрагменту причины, чтобы не
    # слать тревожное уведомление на обычный штатный пропуск.
    log.info(f"=== Дайджест не отправлен: {reason} ===")
    benign = ("лотов недостаточно" in reason or "пул пуст" in reason
              or "уже отправлялся сегодня" in reason)
    if not args.dry_run and not benign:
        notify_error(f"Яндекс-дайджест не отправлен: {reason}\n\nПодробности — в logs/yandex_digest.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
