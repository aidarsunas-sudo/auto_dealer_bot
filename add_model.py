# -*- coding: utf-8 -*-
"""
add_model.py — единая команда для добавления новой модели "на постоянку":
пишет ОДНОВРЕМЕННО в target_models.json (активный поиск каждый прогон
main_pipeline.py, targeted-режим) и в model_specs.py (расчёт см³/л.с. для
цены — используется И targeted, И broad режимами).

Раньше эти два файла приходилось править порознь и не забывать про оба —
именно так K5 попал в model_specs.py, но не в target_models.json, и
оказался доступен только broad-режиму (ненадёжно, не гарантированно).

Пример использования:
    python add_model.py --brand Kia --model K5 --engine "2.0" \
        --cc 1999 --hp 160 --fuel petrol \
        --label "Kia K5 (DL3) 2.0 Gasoline" \
        --source "danawa.com + carnoon.co.kr"

    # с явным переопределением диапазона лет/пробега (иначе — дефолты, см. ниже)
    python add_model.py --brand Audi --model A4 --engine "2.0 TDI" \
        --cc 1968 --hp 150 --fuel diesel \
        --source "carnoon.co.kr" \
        --year-from 2020 --year-to 2022 --mileage-max 50000

Дефолты (если не переопределены явно):
    --year-from  = текущий год - 3
    --year-to    = текущий год
    --mileage-max = config.MAX_MILEAGE_KM

--source ОБЯЗАТЕЛЕН — по правилам model_specs.py, только проверенные данные.

Конфликт match: если новый набор ключевых слов (match) пересекается с уже
существующей записью (одно — подмножество другого, включая точное
совпадение) — скрипт ОСТАНАВЛИВАЕТСЯ с описанием конфликта. Если после
проверки вы уверены, что это осознанное решение (как K3 vs K3 GT — обе
записи специально пересекаются, and find_spec() умеет разрешать именно
такой случай через специфичность) — подтвердите флагом --force.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import config

TARGET_MODELS_PATH = Path(__file__).resolve().parent / "target_models.json"
MODEL_SPECS_PATH = Path(__file__).resolve().parent / "model_specs.py"
INSERT_MARKER = "    # --- Добавляй новые модели ниже по этому же образцу ---"

ALLOWED_FUELS = ("petrol", "diesel", "hybrid_p", "hybrid_d", "elec")


def _safe_print(text: str) -> None:
    """
    На Windows-консоли с не-UTF8 кодировкой (например, cp1251 на русской
    Windows) обычный print() падает с UnicodeEncodeError на эмодзи (✅/❌/⚠️/ℹ️)
    — та же проблема, что была исправлена в telegram_publisher.py при
    аудите. Здесь — то же решение, чтобы add_model.py тоже не падал у
    пользователя при обычном запуске из своей консоли.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def parse_args() -> argparse.Namespace:
    current_year = datetime.now().year
    parser = argparse.ArgumentParser(
        description="Добавить модель одновременно в target_models.json и model_specs.py",
    )
    parser.add_argument("--brand", required=True, help="Например: Kia")
    parser.add_argument("--model", required=True, help="Например: K5")
    parser.add_argument("--engine", default="", help='Например: "2.0 TDI" — используется и как '
                                                       "фильтр в target_models.json, и как часть "
                                                       "ключевых слов для model_specs.py")
    parser.add_argument("--cc", type=int, required=True, help="Объём двигателя, см³")
    parser.add_argument("--hp", type=int, required=True, help="Мощность, л.с.")
    parser.add_argument("--fuel", required=True, choices=ALLOWED_FUELS)
    parser.add_argument("--source", required=True,
                         help="Откуда взяты данные (официальный дилерский каталог/сайт "
                              "производителя предпочтительно) — обязательно для аудита")
    parser.add_argument("--label", default=None,
                         help="Человекочитаемое имя для лога (по умолчанию собирается "
                              "из brand+model+engine)")
    parser.add_argument("--match", default=None,
                         help='Явный набор ключевых слов через запятую (например "k5,2.0"), '
                              "переопределяет автоматически собранный из model+engine")
    parser.add_argument("--year-from", type=int, default=current_year - 3)
    parser.add_argument("--year-to", type=int, default=current_year)
    parser.add_argument("--mileage-max", type=int, default=config.MAX_MILEAGE_KM)
    parser.add_argument("--force", action="store_true",
                         help="Добавить, даже если найден конфликт match с уже существующей записью")
    parser.add_argument("--spec-only", action="store_true",
                         help="Добавить ТОЛЬКО в model_specs.py (для broad-режима) — НЕ добавлять "
                              "в target_models.json. Для случаев, когда модель нужна только для "
                              "распознавания hp в широком поиске, без гарантированного активного "
                              "поиска этой конкретной модели каждый прогон.")
    return parser.parse_args()


def build_match_tuple(model: str, engine: str, override: str | None) -> tuple:
    if override:
        return tuple(part.strip().lower() for part in override.split(",") if part.strip())
    tokens = [model.strip().lower()]
    tokens += [tok.lower() for tok in engine.split() if tok.strip()]
    return tuple(tokens)


def check_match_conflicts(new_match: tuple) -> list[str]:
    """
    Возвращает список описаний конфликтов (пустой — конфликтов нет).
    Конфликт — если множество ключевых слов новой записи и существующей
    находятся в отношении подмножества (включая точное совпадение) —
    ровно то, что find_spec() потом придётся разрешать через специфичность
    (см. model_specs.py: K3 vs K3 GT).
    """
    from model_specs import SPECS  # локальный импорт — читаем актуальный SPECS с диска

    new_set = set(new_match)
    conflicts = []
    for spec in SPECS:
        existing_set = set(spec.match)
        if existing_set == new_set:
            conflicts.append(f"ТОЧНОЕ СОВПАДЕНИЕ с {spec.label!r} (match={spec.match})")
        elif existing_set <= new_set or new_set <= existing_set:
            conflicts.append(f"пересечение с {spec.label!r} (match={spec.match})")
    return conflicts


def append_to_target_models(brand: str, model: str, engine: str,
                             year_from: int, year_to: int, mileage_max: int) -> str:
    """
    Возвращает 'added' | 'updated' | 'unchanged'. Ключ существующей записи —
    (brand, model) без учёта регистра; при совпадении СПРАШИВАТЬ не у кого
    (скрипт неинтерактивный) — обновляем найденную запись новыми
    параметрами, не плодим дубли по одному и тому же brand+model.
    """
    data = json.loads(TARGET_MODELS_PATH.read_text(encoding="utf-8")) if TARGET_MODELS_PATH.exists() else []

    new_entry = {
        "brand": brand, "model": model,
        "year_from": year_from, "year_to": year_to, "mileage_max": mileage_max,
    }
    if engine:
        new_entry["engine"] = engine

    for i, entry in enumerate(data):
        if entry.get("brand", "").lower() == brand.lower() and entry.get("model", "").lower() == model.lower():
            if entry == new_entry:
                return "unchanged"
            data[i] = new_entry
            TARGET_MODELS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return "updated"

    data.append(new_entry)
    TARGET_MODELS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "added"


def append_to_model_specs(match: tuple, cc: int, hp: int, fuel: str, label: str, source: str,
                           brand: str, model: str, engine: str) -> None:
    content = MODEL_SPECS_PATH.read_text(encoding="utf-8")
    if INSERT_MARKER not in content:
        raise RuntimeError(f"Не найден маркер вставки в {MODEL_SPECS_PATH.name} — файл менялся вручную?")

    match_repr = ", ".join(repr(m) for m in match)
    if len(match) == 1:
        match_repr += ","  # tuple из одного элемента — обязательная запятая, как у Kia K3 (база)

    header = f"{brand} {model}" + (f" {engine}" if engine else "")
    block = (
        f"\n    # ─── {header} (добавлено через add_model.py) "
        f"{'─' * max(1, 40 - len(header))}\n"
        f"    ModelSpec(\n"
        f"        match=({match_repr}),\n"
        f"        cc={cc}, hp={hp}, fuel={fuel!r},\n"
        f"        label={label!r},\n"
        f"        source={source!r},\n"
        f"    ),\n"
    )

    new_content = content.replace(INSERT_MARKER, block + INSERT_MARKER)
    MODEL_SPECS_PATH.write_text(new_content, encoding="utf-8")


def main() -> None:
    args = parse_args()

    if args.year_from > args.year_to:
        _safe_print(f"❌ --year-from ({args.year_from}) больше --year-to ({args.year_to})")
        sys.exit(1)

    match = build_match_tuple(args.model, args.engine, args.match)

    conflicts = check_match_conflicts(match)
    if conflicts and not args.force:
        _safe_print(f"❌ Конфликт match={match} с уже существующими записями model_specs.py:")
        for c in conflicts:
            _safe_print(f"   - {c}")
        _safe_print("\nЕсли это осознанно (например, специализация вроде K3 vs K3 GT, которую "
              "find_spec() умеет разруливать по специфичности) — повторите команду с флагом --force.")
        sys.exit(1)
    elif conflicts:
        _safe_print(f"⚠️  Продолжаю несмотря на конфликт match={match} (--force):")
        for c in conflicts:
            _safe_print(f"   - {c}")

    label = args.label or f"{args.brand} {args.model} {args.engine}".strip()

    if args.spec_only:
        _safe_print(f"ℹ️  --spec-only: target_models.json НЕ трогаю (модель только для broad-режима)")
    else:
        target_result = append_to_target_models(
            args.brand, args.model, args.engine, args.year_from, args.year_to, args.mileage_max,
        )
        _safe_print(f"✅ target_models.json: запись {target_result}")

    append_to_model_specs(match, args.cc, args.hp, args.fuel, label, args.source,
                          args.brand, args.model, args.engine)

    _safe_print(f"✅ model_specs.py: добавлена запись match={match}, cc={args.cc}, hp={args.hp}, fuel={args.fuel}")
    _safe_print(f"\nПроверьте результат: python model_specs.py (самопроверка) и один тестовый "
          f"прогон main_pipeline.py --dry-run --limit 1")


if __name__ == "__main__":
    main()
