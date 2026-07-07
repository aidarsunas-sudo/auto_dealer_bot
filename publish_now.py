# -*- coding: utf-8 -*-
"""
publish_now.py — публикация ПРЯМО СЕЙЧАС, одной командой, без ожидания
расписания main_pipeline.py.

    python publish_now.py "Hyundai Sonata"
    python publish_now.py "Hyundai Sonata" --dry-run

Сам ищет объявления по указанной марке+модели на тех же 4 источниках без
логина, что и main_pipeline.py (kbchachacha, Encar, Charancha, OnBid),
без ожидания расписания. Из найденного берёт самое подходящее (свежее,
не старше 14 дней, пробег в пределах обычного лимита, ещё не
опубликованное) и публикует пост ТЕМ ЖЕ кодом и по тому же шаблону, что
и автоматические посты (lead_post_builder.py) — отдельного форматирования
текста здесь нет.

Дедуп общий с main_pipeline.py — после реальной публикации машина
помечается в published_log.json, поэтому автоматический поиск её потом не
продублирует (и наоборот).

Необязательные фильтры (по умолчанию — как в остальном проекте):
    python publish_now.py "Hyundai Sonata" --mileage-max 30000 --year-from 2023

Второй режим — если аргумент указывает на существующий .json-файл, он
трактуется как готовая запись машины (например, из model_search_results/,
где объявления уже проверены на ДТП и с фото) и публикуется как есть, без
нового поиска:
    python publish_now.py model_search_results/hyundai_sonata_.../encar_42317887.json
"""

import argparse
import json
import logging
from pathlib import Path

import config
from target_collector import NOLOGIN_TARGET_ADAPTERS
from published_log import load_log, save_log, select_candidates, mark_posted
from main_pipeline import FRESHNESS_DAYS
from lead_post_builder import build_lead_post
from post_validator import PublishBlockedError
from agent_publisher import publish_post
from add_model import YEAR_FROM_DEFAULT, YEAR_TO_DEFAULT
from rates import fetch_cbr_rates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("publish_now")


def find_candidates(brand: str, model: str, year_from: int, year_to: int,
                     mileage_max: int, pub_log: dict) -> list:
    target = {"brand": brand, "model": model, "year_from": year_from, "year_to": year_to}

    cars = []
    for source_name, search_fn in NOLOGIN_TARGET_ADAPTERS.items():
        logger.info(f"Ищу {brand} {model} на {source_name}...")
        try:
            found = search_fn(target, logger)
        except Exception as error:
            logger.error(f"{source_name}: ошибка поиска ({error}) — пропускаю источник")
            continue
        logger.info(f"{source_name}: найдено {len(found)}")
        cars.extend(found)

    if not cars:
        return []

    candidates = select_candidates(cars, FRESHNESS_DAYS, pub_log, mileage_max)

    # Из подходящих — самое свежее по пробегу (меньше пробег = привлекательнее
    # объявление), неизвестный пробег уходит в конец списка, а не в начало.
    candidates.sort(key=lambda c: c.get("mileage_km") if c.get("mileage_km") is not None else 10**9)
    return candidates


def pick_publishable(candidates: list, rates: dict) -> tuple:
    """
    Идёт по кандидатам от самого привлекательного и берёт ПЕРВОГО, для
    которого получается честно посчитать цену под ключ. Некоторые
    объявления (обычно с kbchachacha) не показывают объём/мощность
    двигателя напрямую — они распознаются по модели в model_specs.py; если
    для этой модели там ещё нет записи, у таких объявлений нет ни цены, ни
    честного поста, и их надо пропустить, а не выдумывать цифры.

    Returns: (car, post_text) первого подходящего, либо (None, None), если
    ни для одного из кандидатов не хватило данных.
    """
    skipped = 0
    for car in candidates:
        post_text = build_lead_post(car, rates)
        if post_text is None:
            skipped += 1
            continue
        if skipped:
            logger.info(f"Пропущено {skipped} объявлений раньше в списке — не хватало данных "
                        f"о двигателе (нет записи в model_specs.py для этой модели)")
        return car, post_text
    return None, None


def publish_car(car: dict, post_text: str, pub_log: dict, dry_run: bool, force: bool) -> None:
    existing = pub_log.get(car["url"])
    if existing and existing.get("posted_at") and not force:
        raise SystemExit(
            f"Эта машина уже была опубликована {existing['posted_at']} "
            f"(brand={existing.get('brand')!r}, model={existing.get('model')!r}). "
            f"Если это ошибка (или нужно опубликовать повторно осознанно) — добавь --force."
        )

    logger.info(f"Публикую: {car.get('brand', '')} {car.get('model', '')} "
                f"{car.get('trim', '')} (пробег {car.get('mileage_km', '?')} км, {car.get('url')})")

    # Гейт (фото/название/год) — внутри publish_post() самой, см.
    # agent_publisher.py. Здесь ловим PublishBlockedError отдельно, чтобы
    # дать чистое сообщение в консоли, а не сырой traceback.
    try:
        ok = publish_post(car, post_text, dry_run=dry_run)
    except PublishBlockedError as error:
        raise SystemExit(f"{error} (см. rejected_posts.log)")

    if ok and not dry_run:
        mark_posted(pub_log, car["url"], car.get("brand"), car.get("model"))
        save_log(pub_log)
        logger.info("Опубликовано и отмечено в published_log.json")
    elif ok:
        logger.info("dry-run: пост показан выше, ничего не отправлено и не отмечено")
    else:
        logger.error("Публикация не удалась")


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help='"Марка Модель" (например "Hyundai Sonata") '
                                       "или путь к готовому JSON-файлу из model_search_results/")
    parser.add_argument("--dry-run", action="store_true",
                         help="не публиковать по-настоящему, только показать, что получилось бы")
    parser.add_argument("--force", action="store_true",
                         help="опубликовать, даже если эта машина уже отмечена опубликованной")
    parser.add_argument("--year-from", type=int, default=YEAR_FROM_DEFAULT)
    parser.add_argument("--year-to", type=int, default=YEAR_TO_DEFAULT)
    parser.add_argument("--mileage-max", type=int, default=config.MAX_MILEAGE_KM)
    args = parser.parse_args()

    pub_log = load_log()
    rates = fetch_cbr_rates()

    maybe_path = Path(args.query)
    if args.query.endswith(".json") and maybe_path.exists():
        car = json.loads(maybe_path.read_text(encoding="utf-8"))
        post_text = build_lead_post(car, rates)
        if post_text is None:
            raise SystemExit(
                "Не хватает данных для честного расчёта цены под ключ "
                "(нет price/cc/hp/fuel_code в этой записи) — не публикую."
            )
    else:
        parts = args.query.split(None, 1)
        if len(parts) != 2:
            raise SystemExit('Укажи "Марка Модель" (например "Hyundai Sonata") или путь к .json-файлу')
        brand, model = parts
        logger.info(f"Ищу подходящее объявление: {brand} {model} "
                    f"(год от {args.year_from}, пробег до {args.mileage_max} км)")
        candidates = find_candidates(brand, model, args.year_from, args.year_to, args.mileage_max, pub_log)
        save_log(pub_log)  # select_candidates() уже зарегистрировал first_seen новых машин
        if not candidates:
            raise SystemExit(
                f"Не нашёл подходящих объявлений для {brand} {model} "
                f"(год от {args.year_from}, пробег до {args.mileage_max} км, ещё не опубликованные)."
            )
        car, post_text = pick_publishable(candidates, rates)
        if car is None:
            raise SystemExit(
                f"Нашёл {len(candidates)} объявлений {brand} {model}, но ни для одного не хватило "
                f"данных о двигателе для честного расчёта цены (нужна запись в model_specs.py)."
            )

    publish_car(car, post_text, pub_log, args.dry_run, args.force)


if __name__ == "__main__":
    run()
