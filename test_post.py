# -*- coding: utf-8 -*-
"""
test_post.py — форс-публикация ОДНОГО тестового поста в Telegram и VK,
игнорируя дедупликацию (published_log.json/cars.db не проверяются и не
обновляются) — нужно, чтобы проверить оба канала «в бою» (например, после
смены VK-токена), не дожидаясь, пока подвернётся реальный неопубликованный
кандидат. Используется run_bot.yml через workflow_dispatch input test_post.

    python test_post.py
    python test_post.py --brand Hyundai --model Sonata

Почему не берём "последнюю машину из cars.db" (что было бы проще и быстрее):
таблица cars (см. database.py) НЕ хранит фото вообще (WATCHLIST — каталог
дилера, фото там в принципе нет; для USED_CAR_SEARCHES фото были на
момент скрапинга, но в базу не сохраняются) — а без фото
post_text_builder.prepare_post_fields() блокирует пост ("нет фото") ещё до
Telegram/VK, то есть тест ничего не проверил бы про VK-загрузку фото —
самое важное, что нужно проверить. Поэтому здесь, как и в publish_now.py,
делаем свежий поиск по тем же 4 источникам без логина (target_collector.
NOLOGIN_TARGET_ADAPTERS) — гарантированно с фото.

К тексту готового поста (lead_post_builder.build_lead_post — тот же
builder, что и у обычной публикации) добавляется префикс "[ТЕСТ] ", чтобы
пост был явно отличим от настоящей лидогенерации в канале/группе.

Идёт по кандидатам (от меньшего пробега к большему, как в publish_now.py)
и публикует ПЕРВОГО, для которого получилось и посчитать честную цену, и
пройти гейт validate_post_before_publish() (фото/название/год) — если
кандидат отклонён гейтом (например, аномальная цена — не связано с этим
тестом), пробует следующего, не больше MAX_CANDIDATES_TO_TRY раз, чтобы не
уйти в долгий перебор при неудачном стечении обстоятельств.
"""

import argparse
import logging

import config
from target_collector import NOLOGIN_TARGET_ADAPTERS
from lead_post_builder import build_lead_post
from post_text_builder import prepare_post_fields
from post_validator import PublishBlockedError
from agent_publisher import publish_post
from rates import fetch_cbr_rates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_post")

DEFAULT_BRAND = "Kia"
DEFAULT_MODEL = "K3"
MAX_CANDIDATES_TO_TRY = 10


def find_candidates(brand: str, model: str) -> list:
    target = {"brand": brand, "model": model, "year_from": 2020, "year_to": 2026}
    cars = []
    for source_name, search_fn in NOLOGIN_TARGET_ADAPTERS.items():
        try:
            found = search_fn(target, logger)
        except Exception as error:
            logger.error(f"{source_name}: ошибка поиска ({error}) — пропускаю источник")
            continue
        logger.info(f"{source_name}: найдено {len(found)}")
        cars.extend(found)

    cars = [c for c in cars if c.get("photos")]
    cars.sort(key=lambda c: c.get("mileage_km") if c.get("mileage_km") is not None else 10**9)
    return cars


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--brand", default=DEFAULT_BRAND)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    config.check_config()
    logger.info(f"=== Тестовая публикация (дедуп игнорируется): {args.brand} {args.model} ===")
    rates = fetch_cbr_rates()
    candidates = find_candidates(args.brand, args.model)
    logger.info(f"Кандидатов с фото: {len(candidates)}")

    tried = 0
    for car in candidates:
        if tried >= MAX_CANDIDATES_TO_TRY:
            break

        post_text = build_lead_post(car, rates)
        if post_text is None:
            continue  # не хватает данных о двигателе — не считаем попыткой
        tried += 1

        # fields — для video_generator.py (см. agent_publisher.publish_post):
        # при config.VIDEO_ENABLED=true тест тоже прогоняет генерацию и
        # публикацию видео, а не только текст/фото.
        fields, _ = prepare_post_fields(car, rates)

        try:
            ok = publish_post(car, f"[ТЕСТ] {post_text}", dry_run=False, fields=fields)
        except PublishBlockedError as error:
            logger.warning(f"Кандидат отклонён гейтом публикации ({error}) — пробую следующего")
            continue

        if ok:
            logger.info(f"✅ Тестовый пост опубликован: {car.get('brand')} {car.get('model')} "
                        f"{car.get('trim')} ({car.get('url')})")
            return
        logger.warning("Telegram не подтвердил отправку — пробую следующего кандидата")

    raise SystemExit(
        f"Не нашлось ни одного кандидата {args.brand} {args.model} (из {len(candidates)} с фото, "
        f"проверено {tried}), прошедшего гейт публикации — тестовый пост не отправлен."
    )


if __name__ == "__main__":
    run()
