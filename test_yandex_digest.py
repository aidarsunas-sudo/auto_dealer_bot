# -*- coding: utf-8 -*-
"""
test_yandex_digest.py — регресс-тест yandex_digest_builder.py (в стиле
test_yandex_post_validator.py/test_post_validator.py — python
test_yandex_digest.py, без pytest, без сети/ffmpeg).

Что проверяет (по ТЗ):
    - build_digest_post() из 4 корректных фикстур-лотов -> текст собирается,
      validate_yandex_digest() его пропускает.
    - validate_yandex_digest() режет мусор в имени (хангыль) в собранном тексте.
    - validate_yandex_digest() режет превышение лимита длины
      (config.YANDEX_DIGEST_MAX_CHARS).
    - build_digest_post() из 1 лота -> дайджест НЕ строится (меньше
      config.YANDEX_DIGEST_MIN_LOTS).
    - select_digest_lots(): окно 24ч -> фолбэк на 48ч при нехватке -> отказ
      при нехватке даже там (на временном пуле, без сети/файлов проекта).
"""

import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import config
import yandex_digest_builder as ydb
from customs_calculator import DEFAULT_RATES
from yandex_post_validator import validate_yandex_digest

RATES = dict(DEFAULT_RATES)


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _check(label: str, ok, expect_ok: bool, detail: str = "") -> bool:
    passed = bool(ok) == expect_ok
    mark = "OK" if passed else "FAIL"
    _safe_print(f"[{mark}] {label}: ok={bool(ok)} (ожидалось {expect_ok}){' — ' + detail if detail else ''}")
    return passed


def _fixture_car(n: int, brand: str, model: str, cc: int, hp: int, price_krw: int) -> dict:
    return {
        "brand": brand, "model": model, "trim": "",
        "external_id": f"test-digest-{n}",
        "url": f"https://example.com/test-digest-{n}",
        "year": 2022 if n % 2 == 0 else 2023,
        "mileage_km": 20000 + n * 1000,
        "cc": cc, "hp": hp, "fuel_code": "petrol",
        "price": price_krw,
        "specs": f"{cc} см³, {hp} л.с.",
        "photos": [f"https://example.com/test-digest-{n}/{i}.jpg" for i in range(3)],
        "source": "test",
    }


FIXTURE_CARS = [
    _fixture_car(1, "Kia", "Sorento", 2199, 202, 58_000_000),
    _fixture_car(2, "Hyundai", "Tucson", 1998, 156, 49_500_000),
    _fixture_car(3, "Renault", "Arkana", 1600, 150, 41_000_000),
    _fixture_car(4, "Chevrolet", "Onix", 1500, 115, 33_000_000),
]


def run() -> None:
    results = []

    # 1. build_digest_post() из 4 фикстур -> собирается, проходит валидатор.
    post, photos, reason = ydb.build_digest_post(FIXTURE_CARS, RATES)
    results.append(_check("build_digest_post из 4 корректных лотов", post is not None, True, reason))
    if post is not None:
        result = validate_yandex_digest(post, len(photos))
        results.append(_check("validate_yandex_digest пропускает корректный дайджест",
                              result, True, "; ".join(result.errors)))
        _safe_print(f"      лотов в тексте: {post['text'].count(chr(0x2022))}, фото: {len(photos)}, "
                    f"символов: {len(post['title']) + len(post['text'])}")
    else:
        # Не блокируем весь прогон — остальные проверки не зависят от этого.
        post = {"title": "", "text": ""}

    # 2. Мусор в имени (хангыль) -> validate_yandex_digest режет.
    garbage_post = {"title": post["title"], "text": post["text"] + "\n기아 더 뉴 K5"}
    result = validate_yandex_digest(garbage_post, len(photos) if photos else 1)
    results.append(_check("validate_yandex_digest режет хангыль в тексте", result, False))

    # 3. Превышение лимита длины -> режет.
    long_text = post["text"] + ("x" * (config.YANDEX_DIGEST_MAX_CHARS + 100))
    long_post = {"title": post["title"], "text": long_text}
    result = validate_yandex_digest(long_post, len(photos) if photos else 1)
    results.append(_check("validate_yandex_digest режет превышение лимита длины", result, False))

    # 3b. Превышение лимита фото -> режет (тот же профиль, дополнительно).
    result = validate_yandex_digest(post, config.YANDEX_DIGEST_MAX_PHOTOS + 1)
    results.append(_check("validate_yandex_digest режет превышение лимита фото", result, False))

    # 4. Всего 1 лот -> дайджест не строится.
    post_one, photos_one, reason_one = ydb.build_digest_post(FIXTURE_CARS[:1], RATES)
    results.append(_check("build_digest_post из 1 лота -> не строится", post_one is None, True, reason_one))

    # 5. select_digest_lots(): окно 24ч -> фолбэк 48ч -> отказ (на временном
    # пуле, чтобы не трогать настоящий yandex_digest_pool.json проекта).
    with tempfile.TemporaryDirectory(prefix="yandex_digest_test_") as td:
        original_pool, original_history = ydb.POOL_PATH, ydb.HISTORY_PATH
        ydb.POOL_PATH = Path(td) / "pool.json"
        ydb.HISTORY_PATH = Path(td) / "history.json"
        try:
            now = datetime(2026, 7, 23, 19, 0, 0)

            # 2 лота за последние 24ч, 1 ещё за 48ч -> должно хватить только
            # после фолбэка на 48ч (< YANDEX_DIGEST_MIN_FOR_WINDOW=3 в окне 24ч).
            pool = [
                {"car": FIXTURE_CARS[0], "published_at": (now - timedelta(hours=5)).isoformat()},
                {"car": FIXTURE_CARS[1], "published_at": (now - timedelta(hours=20)).isoformat()},
                {"car": FIXTURE_CARS[2], "published_at": (now - timedelta(hours=32)).isoformat()},
            ]
            ydb._save_json(ydb.POOL_PATH, pool)

            lots, reason = ydb.select_digest_lots(now=now)
            results.append(_check("select_digest_lots: фолбэк на 48ч даёт 3 лота",
                                  len(lots) == 3, True, reason))

            # Только 1 лот даже за 48ч -> отказ.
            ydb._save_json(ydb.POOL_PATH, pool[:1])
            lots, reason = ydb.select_digest_lots(now=now)
            results.append(_check("select_digest_lots: 1 лот даже за 48ч -> отказ",
                                  len(lots) == 0, True, reason))

            # 6. Суточный гейт already_sent_today()/run_digest(force=...) —
            # защита от дубля дайджеста при повторном ручном запуске в тот
            # же день (см. scraper-reviewer, пункт 1). Пул нарочно пуст —
            # если гейт не сработает как ожидается, run_digest() дальше сам
            # откажет по "пул пуст", а НЕ дойдёт до реальной отправки в
            # Telegram (сеть в этом тесте не трогаем ни при каком исходе).
            ydb._save_json(ydb.POOL_PATH, [])
            ydb._save_json(ydb.HISTORY_PATH, {"last_digest_at": None, "last_lot_ids": []})
            results.append(_check("already_sent_today(): нет истории -> False",
                                  ydb.already_sent_today(now), False))

            ydb._save_json(ydb.HISTORY_PATH, {"last_digest_at": now.isoformat(), "last_lot_ids": []})
            results.append(_check("already_sent_today(): отправлено сегодня -> True",
                                  ydb.already_sent_today(now), True))

            ok, reason = ydb.run_digest(RATES, force=False, now=now)
            results.append(_check("run_digest(force=False) после отправки сегодня -> блокируется гейтом",
                                  (not ok) and ("сегодня" in reason), True, reason))

            ok, reason = ydb.run_digest(RATES, force=True, now=now)
            results.append(_check("run_digest(force=True) обходит суточный гейт (дальше отказ по пустому пулу, не по гейту)",
                                  (not ok) and ("сегодня" not in reason) and ("пул пуст" in reason), True, reason))

            # dry_run тоже обязан обходить гейт (иначе --dry-run бесполезен
            # в день, когда дайджест уже реально отправляли — а ничего
            # реально не отправляет и дубль создать не может).
            ok, reason = ydb.run_digest(RATES, dry_run=True, force=False, now=now)
            results.append(_check("run_digest(dry_run=True, force=False) тоже обходит суточный гейт",
                                  (not ok) and ("сегодня" not in reason) and ("пул пуст" in reason), True, reason))

            yesterday = now - timedelta(days=1)
            ydb._save_json(ydb.HISTORY_PATH, {"last_digest_at": yesterday.isoformat(), "last_lot_ids": []})
            results.append(_check("already_sent_today(): отправлено вчера -> False",
                                  ydb.already_sent_today(now), False))
        finally:
            ydb.POOL_PATH, ydb.HISTORY_PATH = original_pool, original_history

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
