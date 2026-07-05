# -*- coding: utf-8 -*-
"""
main.py — ГЛАВНЫЙ файл проекта. Именно его ты запускаешь командой:

    python main.py

Что он делает, по шагам:
    1. Проверяет, что все настройки в порядке (config.check_config())
    2. Идёт по списку страниц дилеров, которые нужно проверить (WATCHLIST)
    3. Для каждой страницы: скачивает HTML -> достаёт данные о машинах
    4. Каждую найденную машину сравнивает с базой данных:
       - если такой машины ещё не было -> запоминаем + публикуем пост
       - если была, но цена изменилась -> обновляем + публикуем пост
       - если всё то же самое -> ничего не делаем (не спамим канал)
    5. Пишет в лог, что было сделано

Как это запустить в первый раз:
    1. Установи библиотеки:  pip install requests beautifulsoup4 python-dotenv
    2. Скопируй .env.example в .env и впиши туда свой токен бота
    3. Запусти:  python main.py --dry-run
       (--dry-run значит "ничего не публиковать взаправду, только показать,
       что получилось бы" — полезно для первой проверки)
    4. Если всё выглядит правильно — запусти уже по-настоящему:
       python main.py
"""

import argparse
import logging
import time
import traceback

import config
from database import get_connection, save_car
from scraper import fetch_html, parse_car_page
from used_car_scraper import parse_all_pages as parse_used_cars
from post_generator import generate_smart_post
from telegram_publisher import send_to_telegram
from rates import fetch_cbr_rates
from notifier import notify_error
from watchlist import WATCHLIST, USED_CAR_SEARCHES


def setup_logging() -> logging.Logger:
    """
    Настраивает логирование: все важные события будут записываться
    и в файл (logs/app.log — можно посмотреть позже), и сразу выводиться
    на экран (чтобы видеть прогресс прямо во время работы).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_FILE_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("main")


def process_one_page(url: str, brand: str, model: str, conn, rates: dict, dry_run: bool, log: logging.Logger) -> bool:
    """
    Обрабатывает одну страницу из WATCHLIST целиком: скачивает, парсит,
    сохраняет в базу, публикует новые/изменившиеся находки.

    Вынесено в отдельную функцию, чтобы основной цикл в main() был
    коротким и понятным — "для каждой страницы вызови process_one_page".

    Returns:
        True, если страница обработана успешно (даже если ничего нового
        не нашлось — это не ошибка). False — если страницу не удалось
        загрузить или разобрать (тогда main() учтёт это в сводке ошибок).
    """
    log.info(f"── Проверяю: {brand} {model} ──")

    # Шаг 1: скачиваем HTML страницы
    try:
        html = fetch_html(url)
    except Exception as error:
        # Если даже после всех повторных попыток (см. scraper.fetch_html)
        # не получилось скачать страницу — пропускаем её и идём дальше,
        # а не останавливаем всю программу из-за одной проблемной страницы.
        log.error(f"Пропускаю {brand} {model}: не удалось загрузить страницу ({error})")
        return False

    # Шаг 2: достаём из HTML данные о машинах
    try:
        found_cars = parse_car_page(html, url, brand, model)
    except Exception as error:
        log.error(f"Пропускаю {brand} {model}: ошибка разбора страницы ({error})")
        return False

    if not found_cars:
        log.warning(f"На странице {brand} {model} ничего не найдено")
        return False

    # Шаг 3: для каждой найденной комплектации — сохраняем и, если нужно, публикуем
    for car in found_cars:
        try:
            status = save_car(conn, car)  # вернёт "new" / "updated" / "unchanged"

            if status == "unchanged":
                # Ничего нового — просто идём дальше, не публикуем повторно
                continue

            log.info(f"[{status}] {car['brand']} {car['model']} {car['trim']}")

            post_text = generate_smart_post(car, rates)
            send_to_telegram(post_text, dry_run=dry_run)

            # Небольшая пауза между публикациями, чтобы не отправлять всё
            # одним залпом (и чтобы Telegram не посчитал это спамом).
            time.sleep(2)
        except Exception as error:
            # Ошибка с ОДНОЙ конкретной машиной не должна ронять обработку
            # остальных найденных на этой же странице.
            log.error(f"Ошибка при обработке {car.get('trim', '?')}: {error}")

    return True


def process_used_car_search(search_params: dict, conn, rates: dict, dry_run: bool, log: logging.Logger) -> bool:
    """
    Обрабатывает один поисковый запрос из USED_CAR_SEARCHES: скачивает
    список объявлений (может быть несколько страниц), сохраняет в базу,
    публикует новые/изменившиеся находки.

    Returns:
        True при успешной обработке, False — если запрос не удался.
    """
    log.info(f"── Проверяю б/у авто: {search_params} ──")

    try:
        found_cars = parse_used_cars(search_params)
    except Exception as error:
        log.error(f"Пропускаю поиск {search_params}: не удалось загрузить ({error})")
        return False

    if not found_cars:
        log.warning(f"По запросу {search_params} ничего не найдено")
        return False

    for car in found_cars:
        try:
            status = save_car(conn, car)

            if status == "unchanged":
                continue

            log.info(f"[{status}] {car['model']} (carSeq={car['external_id']})")

            post_text = generate_smart_post(car, rates)
            send_to_telegram(post_text, dry_run=dry_run)

            time.sleep(2)
        except Exception as error:
            log.error(f"Ошибка при обработке объявления {car.get('external_id', '?')}: {error}")

    return True


def run() -> None:
    """Вся логика одного прогона бота (без обработки критичных сбоев —
    см. main(), которая оборачивает этот вызов в try/except)."""
    parser = argparse.ArgumentParser(description="Парсер и публикатор объявлений об авто")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не публиковать по-настоящему, только показать, что получилось бы",
    )
    args = parser.parse_args()

    log = setup_logging()
    log.info("=== Запуск ===")

    # Проверяем настройки ДО начала работы — лучше сразу сказать
    # "у тебя не заполнен .env", чем упасть с непонятной ошибкой
    # где-то посреди обработки 5-й страницы.
    config.check_config()

    # Получаем свежие курсы валют ОДИН раз в начале (а не для каждой
    # машины отдельно) — это быстрее и не создаёт лишнюю нагрузку на
    # сайт с курсами.
    rates = fetch_cbr_rates()

    failed_sources = []  # сюда собираем, что именно не удалось обработать

    with get_connection() as conn:
        for url, brand, model in WATCHLIST:
            ok = process_one_page(url, brand, model, conn, rates, args.dry_run, log)
            if not ok:
                failed_sources.append(f"{brand} {model} ({url})")

        for search_params in USED_CAR_SEARCHES:
            ok = process_used_car_search(search_params, conn, rates, args.dry_run, log)
            if not ok:
                failed_sources.append(f"б/у авто: {search_params}")

    log.info("=== Готово ===")

    # Если что-то не удалось обработать — шлём ОДНО сводное уведомление
    # (а не по одному на каждую проблему, чтобы не заспамить)
    if failed_sources:
        summary = "Не удалось обработать:\n" + "\n".join(f"• {s}" for s in failed_sources)
        log.warning(summary)
        notify_error(summary)


def main() -> None:
    """
    Точка входа. Оборачивает run() в try/except, чтобы ЛЮБАЯ неожиданная
    ошибка (даже та, которую мы не предвидели и не обработали внутри run())
    не осталась незамеченной — придёт уведомление в Telegram, а не просто
    "тихо упавший" процесс, о котором ты узнаешь только через 3 дня, когда
    заметишь, что бот перестал публиковать посты.
    """
    try:
        run()
    except Exception as error:
        error_details = traceback.format_exc()
        # В лог — полная информация (для разбора на компьютере),
        # в Telegram — покороче (чтобы не упереться в лимит длины сообщения).
        logging.getLogger("main").critical(f"КРИТИЧЕСКАЯ ОШИБКА:\n{error_details}")
        notify_error(f"Критическая ошибка, бот остановился:\n{error}\n\n"
                      f"Подробности — в logs/app.log")
        raise  # пробрасываем ошибку дальше, чтобы процесс завершился с кодом ошибки
        # (это важно для GitHub Actions — так он покажет прогон как "failed",
        # а не тихо-зелёный, будто всё в порядке)


# Эта строчка — стандартная практика в Python: код внутри неё выполнится,
# только если файл запущен НАПРЯМУЮ (python main.py), а не когда он
# импортируется как модуль из другого файла. Так main.py можно спокойно
# использовать и как отдельный скрипт, и как часть более крупного проекта.
if __name__ == "__main__":
    main()
