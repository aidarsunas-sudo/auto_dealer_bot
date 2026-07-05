# -*- coding: utf-8 -*-
"""
orchestrator.py — АЛЬТЕРНАТИВНАЯ точка входа с мультиагентным пайплайном.

Запускается точно так же, как main.py:

    python orchestrator.py
    python orchestrator.py --dry-run

Чем это отличается от main.py?
main.py публикует пост сразу через один вызов generate_smart_post(). Здесь
каждая найденная машина проходит через цепочку из 5 агентов:

    agent_parser → agent_analyst → agent_copywriter → agent_editor → agent_publisher

    1. agent_parser    — собирает свежие данные (как main.py, но отдельно).
    2. agent_analyst   — решает, какой ТИП поста уместен: обычный обзор,
                         акцент на акцию/скидку, или "горячее предложение"
                         (самый эмоциональный, не чаще раза в день).
    3. agent_copywriter — пишет пост в выбранном тоне через Claude API.
    4. agent_editor    — второй вызов Claude, проверяет черновик; если
                         не одобрен — один повтор с учётом замечаний.
    5. agent_publisher — публикует готовый пост в Telegram.

main.py при этом НЕ удаляется и не меняется по логике — это two параллельных
пути, чтобы можно было сравнить результат. Ошибка любого агента на одной
машине не должна останавливать обработку остальных машин в очереди.
"""

import argparse
import logging
import time
import traceback

import config
from database import get_connection, save_car, get_car_id, get_last_price, add_post_history
from agent_parser import collect_all_cars
from agent_analyst import analyze_car
from agent_copywriter import write_post
from agent_editor import review_post
from agent_publisher import publish_post
from rates import fetch_cbr_rates
from notifier import notify_error
from watchlist import WATCHLIST, USED_CAR_SEARCHES


def setup_logging() -> logging.Logger:
    """Тот же формат логирования, что и в main.py — в файл и на экран."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_FILE_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("orchestrator")


def process_car(conn, car: dict, rates: dict, dry_run: bool, log: logging.Logger) -> bool:
    """
    Проводит одну машину через всю цепочку агентов. Возвращает True, если
    обработка прошла успешно (даже если публиковать было нечего — машина
    не изменилась), False — если сохранение в базу не удалось.
    """
    external_id = car.get("external_id") or car.get("trim")

    # Достаём старую цену ДО save_car() — save_car() перезаписывает цену на
    # месте и истории не хранит, так что после сохранения старую цену уже
    # не узнать.
    try:
        old_price = get_last_price(conn, car["brand"], car["model"], external_id)
        status = save_car(conn, car)
    except Exception as error:
        log.error(f"orchestrator: не удалось сохранить машину {car.get('brand', '?')} "
                  f"{car.get('model', '?')} {car.get('trim', '?')}: {error}")
        return False

    if status == "unchanged":
        return True

    car_id = get_car_id(conn, car["brand"], car["model"], external_id)
    log.info(f"[{status}] {car['brand']} {car['model']} {car.get('trim', '')}")

    # --- agent_analyst: выбираем тип поста ---
    try:
        post_type = analyze_car(conn, car, status, old_price, car_id, log)
    except Exception as error:
        log.warning(f"agent_analyst упал целиком, использую 'обзор': {error}")
        post_type = "обзор"

    # --- agent_copywriter: пишем черновик ---
    try:
        post_text, price_details = write_post(car, rates, post_type)
    except Exception as error:
        log.error(f"agent_copywriter упал, пропускаю машину: {error}")
        return True

    # --- agent_editor: проверяем черновик, при отклонении — один повтор ---
    approved = True
    feedback = ""
    rewrite_count = 0
    try:
        approved, feedback = review_post(post_text, car, price_details, post_type)
    except Exception as error:
        log.warning(f"agent_editor упал, публикую без проверки: {error}")
        approved, feedback = True, f"editor упал: {error}"

    if not approved:
        log.info(f"agent_editor отклонил черновик ({feedback}) — пробую переписать один раз")
        try:
            post_text, price_details = write_post(car, rates, post_type, editor_feedback=feedback)
            rewrite_count = 1
            approved, feedback = review_post(post_text, car, price_details, post_type)
        except Exception as error:
            log.warning(f"Повторная попытка copywriter/editor упала, публикую как есть: {error}")
            approved = False

    if not approved:
        log.warning("опубликовано без одобрения редактора")

    # --- agent_publisher: публикуем ---
    try:
        publish_post(post_text, dry_run=dry_run)
    except Exception as error:
        log.error(f"agent_publisher упал: {error}")
        return True

    # --- журнал публикаций ---
    try:
        if car_id is not None:
            add_post_history(conn, car_id, post_type, approved, rewrite_count)
    except Exception as error:
        log.warning(f"Не удалось записать post_history: {error}")

    time.sleep(2)
    return True


def run() -> None:
    """Один прогон мультиагентного пайплайна (без обработки критичных сбоев —
    см. main(), которая оборачивает этот вызов в try/except)."""
    parser = argparse.ArgumentParser(description="Мультиагентный пайплайн парсинга и публикации объявлений об авто")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не публиковать по-настоящему, только показать, что получилось бы",
    )
    args = parser.parse_args()

    log = setup_logging()
    log.info("=== Запуск orchestrator ===")

    config.check_config()

    rates = fetch_cbr_rates()

    failed_sources = []

    with get_connection() as conn:
        cars = collect_all_cars(WATCHLIST, USED_CAR_SEARCHES, log)

        if not cars:
            log.warning("Ничего не найдено ни по одному источнику")

        for car in cars:
            try:
                ok = process_car(conn, car, rates, args.dry_run, log)
                if not ok:
                    failed_sources.append(f"{car.get('brand', '?')} {car.get('model', '?')} {car.get('trim', '?')}")
            except Exception as error:
                # Дополнительная страховка: даже если process_car сам не
                # перехватил что-то — одна сломанная машина не должна
                # останавливать обработку остальных.
                log.error(f"orchestrator: необработанная ошибка при обработке машины: {error}")
                failed_sources.append(f"{car.get('brand', '?')} {car.get('model', '?')} {car.get('trim', '?')}")

    log.info("=== Готово ===")

    if failed_sources:
        summary = "Не удалось обработать:\n" + "\n".join(f"• {s}" for s in failed_sources)
        log.warning(summary)
        notify_error(summary)


def main() -> None:
    """Точка входа. См. main.py — тот же контракт: любая непойманная ошибка
    логируется, отправляется уведомление, и процесс завершается с кодом
    ошибки (важно для GitHub Actions)."""
    try:
        run()
    except Exception as error:
        error_details = traceback.format_exc()
        logging.getLogger("orchestrator").critical(f"КРИТИЧЕСКАЯ ОШИБКА:\n{error_details}")
        notify_error(f"Критическая ошибка, orchestrator остановился:\n{error}\n\n"
                      f"Подробности — в logs/app.log")
        raise


if __name__ == "__main__":
    main()
