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

Модель/источник — переключаемые (см. FALLBACK_TARGETS, 2026-09-15): цель
этого теста — проверить КАНАЛЫ публикации (Telegram/VK), а не конкретную
площадку. Если у запрошенной --brand/--model на данный момент недобор фото
у всех кандидатов (например, конкретный источник временно деградирует на
IP GitHub Actions — см. FINAL_STATE.md, инцидент с kbchachacha), тест сам
переключается на следующую модель из резервного пула вместо того, чтобы
провалиться целиком, так и не дойдя до проверки самих каналов.
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

# Резервный пул — разные модели, исторически покрытые РАЗНЫМИ источниками
# (не все четыре площадки одновременно капризничают), чтобы тест дошёл до
# проверки каналов публикации, даже если у --brand/--model сейчас недобор
# фото на всех источниках сразу. Порядок — от наиболее надёжного.
FALLBACK_TARGETS = [
    ("Kia", "K3"),
    ("Chevrolet", "Trax"),
    ("Renault", "XM3"),
    ("Kia", "K5"),
    ("Hyundai", "Avante"),
]


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

    # Строгий пре-фильтр — НЕ "хоть одно фото", а >= порога гейта
    # (config.MIN_PHOTOS_PER_POST). Раньше пропускали кандидатов с 1-2
    # фото дальше, и они съедали весь бюджет MAX_CANDIDATES_TO_TRY, ни разу
    # не пройдя гейт — до проверки самих каналов (Telegram/VK) дело не
    # доходило (см. 2026-09-15: kbchachacha временно отдавал по 1 фото на
    # IP GitHub Actions — 10 из 10 кандидатов Kia K3 отсеялись впустую).
    before = len(cars)
    cars = [c for c in cars if len(c.get("photos") or []) >= config.MIN_PHOTOS_PER_POST]
    if before != len(cars):
        logger.info(f"Пре-фильтр по фото (>= {config.MIN_PHOTOS_PER_POST}): "
                    f"{before} -> {len(cars)} кандидатов")
    cars.sort(key=lambda c: c.get("mileage_km") if c.get("mileage_km") is not None else 10**9)
    return cars


def _try_target(brand: str, model: str, rates: dict) -> bool:
    """Пробует опубликовать тестовый пост по одной модели. Возвращает True
    при успехе, False — если ни один кандидат этой модели не прошёл гейт
    (вызывающий код тогда переключается на следующую модель из пула)."""
    logger.info(f"=== Тестовая публикация (дедуп игнорируется): {brand} {model} ===")
    candidates = find_candidates(brand, model)
    logger.info(f"Кандидатов с фото (>= {config.MIN_PHOTOS_PER_POST}): {len(candidates)}")

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
            return True
        logger.warning("Telegram не подтвердил отправку — пробую следующего кандидата")

    logger.warning(f"{brand} {model}: ни один из {tried} проверенных кандидатов ({len(candidates)} "
                    f"с достаточным числом фото) не прошёл гейт")
    return False


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--brand", default=DEFAULT_BRAND)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    config.check_config()
    rates = fetch_cbr_rates()

    requested = (args.brand, args.model)
    targets_to_try = [requested] + [t for t in FALLBACK_TARGETS if t != requested]

    for i, (brand, model) in enumerate(targets_to_try, start=1):
        if _try_target(brand, model, rates):
            return
        if i < len(targets_to_try):
            logger.info(f"Переключаюсь на следующую модель из резервного пула "
                        f"({i}/{len(targets_to_try)} испробовано)")

    raise SystemExit(
        f"Не нашлось ни одного кандидата, прошедшего гейт публикации, ни по одной из "
        f"{len(targets_to_try)} испробованных моделей ({', '.join(f'{b} {m}' for b, m in targets_to_try)}) "
        f"— тестовый пост не отправлен."
    )


if __name__ == "__main__":
    run()
