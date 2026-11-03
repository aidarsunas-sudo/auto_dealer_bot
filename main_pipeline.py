# -*- coding: utf-8 -*-
"""
main_pipeline.py — сквозной автоматический пайплайн для лидогенерирующих
постов (Telegram-only, канал @auto_generals).

    сбор (4 источника без логина) → дедуп/фильтр актуальности/пробега →
    пре-фильтр publishable (цена/фото/заголовок) →
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
import json
import logging
import time
import traceback
from datetime import datetime

import config
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "publishing"))
import post_journal  # noqa: E402  единый журнал: история перед генерацией
from target_collector import collect_target_model_cars
from broad_collector import collect_broad_cars
from published_log import (
    load_log, save_log, select_candidates, mark_posted,
    recently_posted_models, recent_posted_brand_sequence,
)
from candidate_diversity import diversify, filter_recent_models, limit_consecutive_brand, check_source_health, MAX_PER_MODEL
from lead_post_builder import build_lead_post
from post_text_builder import prepare_post_fields, HANGUL_RE, ALLOWED_TITLE_RE
import lot_reel_script
from agent_publisher import publish_post
from rates import fetch_cbr_rates
from notifier import notify_error
import vk_publisher
from models_to_watch import sync_to_target_models
from used_car_scraper import enrich_photos_from_detail
import yandex_queue
import registry
from yandex_publisher import send_yandex_draft
from yandex_digest_builder import record_published_lot
from telegram_publisher import send_to_telegram

FRESHNESS_DAYS = 14
REGISTRY_REFRESH_DAYS = 7  # глубина сверки реестра published.jsonl с каналом в начале прогона
POST_LIMIT = 5
DIVERSIFY_BUFFER = 5  # diversify() вернёт до POST_LIMIT * DIVERSIFY_BUFFER кандидатов

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


def _prefilter_publishable(candidates: list) -> list:
    """Убирает кандидатов, которые заведомо провалят build_lead_post():
    нет cc/hp/fuel_code/price для расчёта цены, фото меньше MIN_PHOTOS_PER_POST,
    или нечитаемый заголовок (пустой / корейский / недопустимые символы).

    Проверка заголовка повторяет ту же логику, что prepare_post_fields()
    (HANGUL_RE / ALLOWED_TITLE_RE из post_text_builder.py — один источник
    правды на regex). Это нужно, чтобы filter_recent_models() ниже по
    пайплайну работал ТОЛЬКО с publishable кандидатами и его fallback
    ("все недавние → используем всех") срабатывал корректно.

    Пустой список = все отсеяны."""
    min_photos = config.MIN_PHOTOS_PER_POST
    result = []
    reasons: dict[str, int] = {}
    for car in candidates:
        photos = car.get("photos") or []
        if len(photos) < min_photos:
            reasons["photos<min"] = reasons.get("photos<min", 0) + 1
            continue
        if not all([car.get("price"), car.get("cc"), car.get("hp"), car.get("fuel_code")]):
            missing = [f for f in ("price", "cc", "hp", "fuel_code") if not car.get(f)]
            key = "missing:" + "+".join(missing)
            reasons[key] = reasons.get(key, 0) + 1
            continue
        parts = []
        for v in (car.get("brand"), car.get("model"), car.get("trim")):
            v = (v or "").strip()
            if v and v not in parts:
                parts.append(v)
        title = " ".join(parts)
        if not title or HANGUL_RE.search(title) or not ALLOWED_TITLE_RE.match(title):
            reasons["bad_title"] = reasons.get("bad_title", 0) + 1
            continue
        result.append(car)
    if reasons:
        logging.getLogger("main_pipeline").info(f"Пре-фильтр причины отсева: {reasons}")
    return result


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

    # Дешёвая проверка VK_TOKEN В САМОМ НАЧАЛЕ прогона (см. vk_publisher.
    # check_token_health()) — не блокирует Telegram (та же логика "одно не
    # мешает другому", что и у agent_publisher.publish_post()), но делает
    # битый токен ВИДНЫМ сразу явным логом/уведомлением админу, а не
    # тонущим в ретраях загрузки фото где-то в середине прогона — именно
    # так терялись прошлые инциденты с токеном (см. FINAL_STATE.md).
    if not args.dry_run:
        vk_ok, vk_reason = vk_publisher.check_token_health()
        if not vk_ok:
            log.error(f"VK_TOKEN не рабочий — публикация в VK в этом прогоне будет проваливаться: {vk_reason}")
            notify_error(f"⚠️ VK_TOKEN не прошёл проверку перед прогоном main_pipeline: {vk_reason}")
        else:
            log.info("VK_TOKEN проверен — рабочий" if config.VK_ENABLED else "VK не настроен, пропускаю")

    # Реестр published.jsonl сверяем с публичной страницей канала ДО выбора
    # кандидатов (источник правды — сам канал, см. registry.py): так посты,
    # вышедшие вручную с прошлого прогона (без lot_id), попадают в защиту от дублей
    # (agent_publisher.publish_post -> registry.find_duplicate). Сбой сети не
    # должен останавливать прогон — работаем по сохранённому в git реестру.
    try:
        rows = registry.reconcile(days=REGISTRY_REFRESH_DAYS, apply=not args.dry_run)
        changed = sum(1 for status, _ in rows if status in ("new", "update"))
        log.info(f"registry: сверка с каналом за {REGISTRY_REFRESH_DAYS} сут.: постов {len(rows)}, "
                 f"{'записано' if not args.dry_run else 'нашлось бы'} {changed}")
    except Exception as error:
        log.warning(f"registry: не удалось сверить реестр с каналом ({error}) — "
                    f"работаю по сохранённому published.jsonl")

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

    # "Тихая смерть" источника (см. FINAL_STATE.md/аудит 2026-07-20:
    # kbchachacha/charancha молчали 13-14 дней незамеченными) — теперь
    # видна СРАЗУ, не только по факту отсутствия постов через две недели.
    check_source_health(cars, log)

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

    # Обогащение фото: на GHA IP kbchachacha отдаёт 1 фото в карусели
    # списка вместо 4 (анти-бот деградация). Detail-страница при этом
    # работает и содержит 15-20 фото в JSON-LD. Дозагружаем ДО пре-фильтра,
    # чтобы лоты не отсеялись по MIN_PHOTOS_PER_POST.
    kb_cars = [c for c in candidates
               if "kbchachacha.com" in (c.get("url") or "")
               and len(c.get("photos") or []) < config.MIN_PHOTOS_PER_POST]
    if kb_cars:
        enriched_count = enrich_photos_from_detail(kb_cars)
        log.info(f"Обогащение фото kbchachacha: {enriched_count}/{len(kb_cars)} лотов дозагружены")

    # Пре-фильтр: убираем кандидатов, которые заведомо не пройдут валидацию
    # (нет данных для цены, мало фото, нечитаемый заголовок).
    # ВАЖНО: выполняется ДО filter_recent_models, чтобы fallback фильтра
    # недавних моделей работал корректно — если ВСЕ publishable кандидаты
    # окажутся недавними моделями, они будут опубликованы (лучше повтор
    # модели, чем тишина в канале на несколько дней).
    before_prefilter = len(candidates)
    publishable = _prefilter_publishable(candidates)
    if not publishable:
        log.warning(f"Пре-фильтр: убраны ВСЕ {before_prefilter} кандидатов — "
                     f"fallback на исходный список (все провалят валидацию)")
        notify_error(
            f"⚠️ main_pipeline: пре-фильтр убрал ВСЕ {before_prefilter} кандидатов "
            f"(нет данных для цены / мало фото / нечитаемый заголовок у каждого). "
            f"Используем fallback, но это системная проблема.")
        publishable = candidates
    elif len(publishable) < before_prefilter:
        log.info(f"Пре-фильтр: убрано {before_prefilter - len(publishable)} из {before_prefilter} "
                  f"кандидатов (нет данных для цены / < {config.MIN_PHOTOS_PER_POST} фото / "
                  f"нечитаемый заголовок)")

    # Модели, опубликованные за последние RECENT_MODEL_DAYS дней, — убираем
    # среди publishable, если есть альтернативы. filter_recent_models
    # возвращает все publishable без изменений, если после удаления недавних
    # не осталось ни одного — лучше повтор модели, чем пустой прогон.
    recent_models = recently_posted_models(pub_log, config.RECENT_MODEL_DAYS)
    before_recent_filter = len(publishable)
    candidates = filter_recent_models(publishable, recent_models)
    if len(candidates) < before_recent_filter:
        log.info(f"Фильтр недавних моделей: убрано {before_recent_filter - len(candidates)} "
                  f"из {before_recent_filter} кандидатов (за последние "
                  f"{config.RECENT_MODEL_DAYS} дн.): {recent_models}")
    elif recent_models and len(candidates) == before_recent_filter:
        log.info(f"Фильтр недавних моделей: все {before_recent_filter} publishable "
                  f"кандидатов — недавние, используем всех (fallback)")

    # Без этого шага все POST_LIMIT постов могут оказаться одной моделью с
    # одного источника (см. разбор бага: при первом запуске у всех кандидатов
    # почти одинаковый first_seen, и порядок вырождается в порядок сбора,
    # где один источник может отдать десятки объявлений одной модели подряд).
    # DIVERSIFY_BUFFER: берём с запасом, чтобы цикл публикации мог попробовать
    # больше кандидатов, если первые не пройдут валидацию (фото/цена/данные).
    candidates = diversify(candidates, post_limit * DIVERSIFY_BUFFER, MAX_PER_MODEL)

    # Ещё один слой поверх diversify(): та же МАРКА (Hyundai Avante, потом
    # Hyundai Casper — diversify() это пропускает, там развод только по
    # моделям) не больше config.MAX_SAME_BRAND_IN_A_ROW раз подряд. Считает
    # и хвост уже ФАКТИЧЕСКИ опубликованных постов из предыдущих прогонов —
    # не только внутри этого списка (см. recent_posted_brand_sequence()).
    recent_brands = recent_posted_brand_sequence(pub_log, config.MAX_SAME_BRAND_IN_A_ROW)
    candidates = limit_consecutive_brand(candidates, recent_brands, config.MAX_SAME_BRAND_IN_A_ROW)

    posted_count = 0
    duplicates_skipped = 0
    recent_journal_models = post_journal.recent_focus_models(config.RECENT_MODEL_DAYS)
    for car in candidates:
        if posted_count >= post_limit:
            log.info(f"Достигнут лимит {post_limit} постов за запуск — останавливаюсь")
            break

        # История единого журнала ПЕРЕД генерацией: модель, бывшая фокусом
        # поста за последние RECENT_MODEL_DAYS дней (в любой цепочке проекта:
        # лидоген/SEO/ручные публикации), — пропускаем, не генерируя текст.
        model_key = f"{car.get('brand', '')} {car.get('model', '')}".strip().lower()
        if model_key and model_key in recent_journal_models:
            log.info(f"Пропуск (единый журнал): {car.get('brand', '')} {car.get('model', '')} "
                     f"уже в канале за последние {config.RECENT_MODEL_DAYS} дн.")
            continue

        post_text = build_lead_post(car, rates)
        if post_text is None:
            continue  # не хватает данных для честной цены под ключ — пропускаем

        # fields — те же данные, что build_lead_post() уже посчитал внутри
        # себя через prepare_post_fields(), но наружу отдаёт только готовый
        # текст — пересчитываем отдельно (дёшево, чистая функция без
        # сайд-эффектов) специально для video_generator.py (см.
        # agent_publisher.publish_post(): fields нужен ТОЛЬКО для видео).
        fields, _ = prepare_post_fields(car, rates)

        # Гейт (фото/название/год) — внутри publish_post() самой, см.
        # agent_publisher.py. PublishBlockedError ловится общим except ниже
        # — одна отклонённая машина не должна останавливать весь прогон.
        try:
            ok = publish_post(car, post_text, dry_run=args.dry_run, fields=fields)
        except registry.DuplicatePostError as error:
            # Лот уже в канале (реестр published.jsonl) — штатный пропуск, не сбой:
            # не считается за "ошибку публикации" и не поднимает тревогу "0 постов".
            duplicates_skipped += 1
            log.info(f"Пропущено как дубль {car.get('brand', '?')} {car.get('model', '?')}: {error}")
            continue
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
                # Яндекс-витрина — отдельный поток (см. CLAUDE.md "Контент-потоки"):
                # лот только копится в очереди здесь, отправка — раз в
                # config.YANDEX_MIN_INTERVAL_DAYS, отдельным шагом в конце прогона
                # (см. _dispatch_yandex_draft ниже).
                yandex_queue.enqueue_lot(car)
                # Дайджест — НЕЗАВИСИМЫЙ накопитель от очереди выше (см.
                # yandex_digest_builder.py): лоты сюда не расходуются при
                # выборке, только чистятся по возрасту. Отправка — раз в
                # день, отдельным workflow (run_yandex_digest.py), не отсюда.
                record_published_lot(car)
            # Сценарий ролика «Разбор лота» — за флагом SCRIPT_ENABLED (по
            # умолчанию выключен), по умолчанию dry-run: только лог, ничего
            # не публикуется. Гейт — внутри generate_script_for_car()
            # (validate_post_before_publish -> ValidatedCar). Сбой не должен
            # ломать публикацию — try/except, как у Яндекс-шага.
            if config.SCRIPT_ENABLED:
                try:
                    script = lot_reel_script.generate_script_for_car(
                        car, fields, dry_run=args.dry_run or config.SCRIPT_DRY_RUN)
                    if script:
                        log.info("Сценарий ролика (dry-run, не опубликован): "
                                  + json.dumps(script, ensure_ascii=False))
                except Exception as error:
                    log.error(f"lot_reel_script: сбой генерации сценария: {error}")
            posted_count += 1
            log.info(f"[{posted_count}/{post_limit}] Опубликовано: {car.get('brand', '')} "
                      f"{car.get('model', '')} {car.get('trim', '')}")
            time.sleep(2)

    save_log(pub_log)

    # Guard от "тихого" сбоя: пайплайн честно нашёл кандидатов, но не
    # опубликовал НИ ОДНОГО — раньше это тонуло в логе незамеченным (см.
    # инцидент 2026-08: бот молчал 4+ дня, прежде чем кто-то заметил).
    # Не касается dry_run (там publish_post() ничего не публикует по
    # определению — это ожидаемо, не сбой) и не касается "кандидатов не
    # нашлось вообще" (early return выше — это нормальный тихий период,
    # не аномалия).
    if duplicates_skipped:
        log.info(f"Пропущено как дубли (реестр published.jsonl): {duplicates_skipped}")
    if candidates and posted_count == 0 and duplicates_skipped == 0 and not args.dry_run:
        notify_error(
            f"⚠️ main_pipeline: собрано {len(candidates)} кандидат(ов) на публикацию, "
            f"но опубликовано 0 постов за этот прогон. Похоже на тихий сбой "
            f"(валидатор/цена/данные режут всех подряд) — см. logs/main_pipeline.log."
        )

    _dispatch_yandex_draft(rates, log, args.dry_run)

    log.info(f"=== Готово: опубликовано {posted_count} постов ===")


def _dispatch_yandex_draft(rates: dict, log: logging.Logger, dry_run: bool) -> None:
    """
    Отдельный шаг в конце прогона (см. CLAUDE.md "Контент-потоки"): если с
    последней отправки прошло >= config.YANDEX_MIN_INTERVAL_DAYS, забирает
    ОДИН лучший лот из очереди (yandex_queue.pick_best_lot()) и шлёт
    Яндекс-черновик в служебный чат.

    Ошибка здесь НЕ должна ронять весь прогон main_pipeline — лидоген (TG/VK)
    к этому моменту уже отработал и не должен зависеть от Яндекс-потока
    (тот же принцип, что у VK в agent_publisher.publish_post()). При сбое —
    не только лог, но и явное предупреждение в служебный чат: молчаливый
    отказ было бы легко не заметить месяцами (черновики и так редкие, раз в
    YANDEX_MIN_INTERVAL_DAYS).
    """
    try:
        if not yandex_queue.due_for_send():
            log.info("yandex_queue: рано — интервал ещё не прошёл")
            return

        car, queue_left = yandex_queue.pick_best_lot()
        if car is None:
            log.info("yandex_queue: очередь пуста (или все лоты протухли)")
            return

        today = datetime.now().strftime("%d.%m.%Y")
        extra_header = f"{today} · Черновик для Яндекса · в очереди ещё {queue_left} лотов"

        ok, reason, _ = send_yandex_draft(car, rates, dry_run=dry_run, extra_header=extra_header)

        if not ok:
            log.error(f"yandex_queue: черновик не отправлен: {reason}")
            if not dry_run:
                send_to_telegram(f"⚠️ yandex draft failed: {reason}",
                                  chat_id=config.YANDEX_DRAFTS_CHAT_ID)
            return

        if not dry_run:
            yandex_queue.mark_sent(car)
        log.info(f"yandex_queue: черновик отправлен ({car.get('brand', '')} {car.get('model', '')}), "
                  f"в очереди осталось {queue_left}")
    except Exception as error:
        log.error(f"yandex_queue: непредвиденная ошибка dispatch: {error}")
        if not dry_run:
            try:
                send_to_telegram(f"⚠️ yandex draft failed: {error}", chat_id=config.YANDEX_DRAFTS_CHAT_ID)
            except Exception:
                pass


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
