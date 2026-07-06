# -*- coding: utf-8 -*-
"""
main_pipeline.py — сквозной автоматический пайплайн для лидогенерирующих
постов (Telegram-only, канал @auto_generals).

    сбор (4 источника без логина) → дедуп/фильтр актуальности/пробега →
    фильтр недавно опубликованных моделей → round-robin разнообразие →
    расчёт цены под ключ → генерация поста по внешнему шаблону → публикация

Два независимых режима поиска (см. config.SEARCH_MODE — 'targeted' |
'broad' | 'both', дефолт 'both'):
- targeted — конкретные модели из target_models.json (target_collector.py).
- broad    — любые модели с hp<=max_hp (см. broad_search_config.json,
             broad_collector.py) — порог льготного утильсбора для физлиц,
             максимум разнообразия контента, без привязки к брендам.
В режиме 'both' результаты обоих режимов складываются в один пул ДО
diversify() — round-robin по (brand, model) работает одинаково для машин
из любого режима, без искусственного приоритета одного над другим.

Это ОТДЕЛЬНАЯ точка входа от orchestrator.py: orchestrator.py публикует
обзорные посты по WATCHLIST/USED_CAR_SEARCHES через мультиагентную цепочку
(agent_analyst → agent_copywriter → agent_editor), а main_pipeline.py —
лидогенерирующим постом с CTA на @korea_auto_kazan_bot. target_models.json
обрабатывается ТОЛЬКО здесь (не в orchestrator.py) — иначе одна и та же
машина публиковалась бы в канал дважды в разных форматах.

Запускается точно так же, как main.py/orchestrator.py:

    python main_pipeline.py
    python main_pipeline.py --dry-run

Расписание: каждые 6 часов (см. cron/GitHub Actions). Свежесть объявления —
14 дней от даты, когда МЫ САМИ его впервые увидели (published_log.json;
не все источники отдают дату размещения на сайте). Лимит — 5 постов за
один запуск (см. FRESHNESS_DAYS / POST_LIMIT ниже).
"""

import argparse
import logging
import time
import traceback

import config
from target_collector import collect_target_model_cars
from broad_collector import collect_broad_cars
from published_log import load_log, save_log, select_candidates, mark_posted, recently_posted_models
from candidate_diversity import diversify, filter_recent_models, MAX_PER_MODEL
from lead_post_builder import build_lead_post
from agent_publisher import publish_post
from rates import fetch_cbr_rates
from notifier import notify_error
from models_to_watch import sync_to_target_models

FRESHNESS_DAYS = 14
POST_LIMIT = 5

LOG_FILE_PATH = "logs/main_pipeline.log"


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("main_pipeline")


def run() -> None:
    parser = argparse.ArgumentParser(description="Лидогенерирующий пайплайн по target_models.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не публиковать по-настоящему, только показать, что получилось бы",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=POST_LIMIT,
        help=f"Сколько постов максимум публиковать за этот запуск (по умолчанию {POST_LIMIT})",
    )
    args = parser.parse_args()
    post_limit = args.limit

    log = setup_logging()
    log.info("=== Запуск main_pipeline ===")

    config.check_config()

    # Простой список моделей без JSON (models_to_watch.txt) — переносит в
    # target_models.json всё, чего там ещё нет, до того как target_collector.py
    # его прочитает. См. models_to_watch.py/CONTROL.md.
    sync_to_target_models(log)

    rates = fetch_cbr_rates()

    search_mode = config.SEARCH_MODE
    if search_mode not in ("targeted", "broad", "both"):
        log.warning(f"Неизвестный SEARCH_MODE={search_mode!r}, использую 'both'")
        search_mode = "both"
    log.info(f"Режим поиска: {search_mode}")

    cars = []
    if search_mode in ("targeted", "both"):
        cars += collect_target_model_cars(log)
    if search_mode in ("broad", "both"):
        cars += collect_broad_cars(log)

    if not cars:
        log.warning("Ничего не найдено ни в одном из активных режимов поиска")
        return

    pub_log = load_log()
    # select_candidates мутирует pub_log (регистрирует first_seen новых
    # объявлений) — сохраняем сразу, чтобы не потерять это, даже если
    # дальше что-то упадёт при генерации постов/публикации.
    candidates = select_candidates(cars, FRESHNESS_DAYS, pub_log, config.MAX_MILEAGE_KM)
    save_log(pub_log)

    log.info(f"Найдено {len(cars)} объявлений, кандидатов на публикацию (новые, не старше "
              f"{FRESHNESS_DAYS} дн., пробег до {config.MAX_MILEAGE_KM} км, ещё не опубликованные): "
              f"{len(candidates)}")

    if not candidates:
        log.info("Публиковать нечего")
        return

    # Модели, опубликованные за последние RECENT_MODEL_DAYS дней, — убираем,
    # если есть альтернативы (fallback внутри filter_recent_models: если
    # ВСЕ кандидаты окажутся недавно опубликованными моделями — разрешаем
    # повтор, а не пропускаем прогон целиком).
    recent_models = recently_posted_models(pub_log, config.RECENT_MODEL_DAYS)
    before_recent_filter = len(candidates)
    candidates = filter_recent_models(candidates, recent_models)
    if len(candidates) < before_recent_filter:
        log.info(f"Отфильтровано {before_recent_filter - len(candidates)} кандидатов недавно "
                  f"опубликованных моделей (за последние {config.RECENT_MODEL_DAYS} дн.): {recent_models}")

    # Без этого шага все POST_LIMIT постов могут оказаться одной моделью с
    # одного источника (см. разбор бага: при первом запуске у всех кандидатов
    # почти одинаковый first_seen, и порядок вырождается в порядок сбора,
    # где один источник может отдать десятки объявлений одной модели подряд).
    candidates = diversify(candidates, post_limit, MAX_PER_MODEL)

    posted_count = 0
    for car in candidates:
        if posted_count >= post_limit:
            log.info(f"Достигнут лимит {post_limit} постов за запуск — останавливаюсь")
            break

        post_text = build_lead_post(car, rates)
        if post_text is None:
            continue  # не хватает данных для честной цены под ключ — пропускаем

        try:
            ok = publish_post(post_text, photos=car.get("photos"), dry_run=args.dry_run)
        except Exception as error:
            log.error(f"Не удалось опубликовать {car.get('brand', '?')} {car.get('model', '?')}: {error}")
            continue

        if ok:
            # dry_run: publish_post() ничего не отправляет и всегда возвращает
            # True — если пометить URL опубликованным и здесь, следующий
            # РЕАЛЬНЫЙ прогон навсегда пропустит эту машину, хотя пост
            # никогда не выходил в канал. Дедуп обновляем только для
            # настоящей публикации.
            if not args.dry_run:
                mark_posted(pub_log, car["url"], car.get("brand"), car.get("model"))
            posted_count += 1
            log.info(f"[{posted_count}/{post_limit}] Опубликовано: {car.get('brand', '')} "
                      f"{car.get('model', '')} {car.get('trim', '')}")
            time.sleep(2)

    save_log(pub_log)
    log.info(f"=== Готово: опубликовано {posted_count} постов ===")


def main() -> None:
    """Тот же контракт, что и у main.py/orchestrator.py: любая непойманная
    ошибка логируется, отправляется уведомление, процесс завершается с кодом
    ошибки (важно для cron/GitHub Actions)."""
    try:
        run()
    except Exception as error:
        error_details = traceback.format_exc()
        logging.getLogger("main_pipeline").critical(f"КРИТИЧЕСКАЯ ОШИБКА:\n{error_details}")
        notify_error(f"Критическая ошибка, main_pipeline остановился:\n{error}\n\n"
                      f"Подробности — в logs/main_pipeline.log")
        raise


if __name__ == "__main__":
    main()
