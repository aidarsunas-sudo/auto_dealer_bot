# -*- coding: utf-8 -*-
"""
Справочник объёма двигателя (см³) и мощности (л.с.) по названию модели/трима,
как он приходит с kbchachacha (напр. "벤츠 GLB(X247) 200 d").

ВАЖНО: сюда добавляются ТОЛЬКО проверенные данные (сверенные по официальным
или авторитетным источникам), потому что от объёма и мощности напрямую
зависит коэффициент утильсбора — ошибка тут стоит реальных денег.

Если у модели мощность менялась по поколениям/рестайлингам (как у BMW X6
xDrive30d) — используем year_min/year_max, чтобы не перепутать поколения.
Если для какого-то диапазона лет данные взяты из вторичного источника
(не официальный дилерский каталог) — это указано в source с пометкой "⚠️".

Как добавить новую модель:
    1. Найди характеристики на официальном дилерском каталоге
       (carnoon.co.kr, синхронизирован с прайс-листами производителей —
       предпочтительно), либо на сайте самого производителя (kia.com,
       bmw.co.kr и т.д.), либо на danawa.com (авторитетный агрегатор).
    2. Проверь, не менялась ли мощность в течение периода выпуска —
       если да, добавь несколько записей с year_min/year_max.
    3. Добавь запись в SPECS ниже.

match: список подстрок, которые должны ВСЕ встретиться в названии (без учёта
       регистра), чтобы запись считалась подходящей.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ModelSpec:
    match: tuple                      # подстроки для поиска в title (все должны совпасть)
    cc: int                            # объём, см³
    hp: int                             # мощность, л.с.
    fuel: str                           # 'petrol' | 'diesel' | 'hybrid_p' | 'hybrid_d' | 'elec'
    label: str                           # человекочитаемое имя для лога
    source: str                           # откуда взяты данные (для проверки/аудита)
    year_min: Optional[int] = None         # включительно, год выпуска
    year_max: Optional[int] = None         # включительно, год выпуска


SPECS = [
    # ─── Mercedes-Benz GLB (X247) ──────────────────────────────────────────
    ModelSpec(
        match=("glb", "200"),
        cc=1950, hp=150, fuel="diesel",
        label="Mercedes-Benz GLB 200d",
        source="carnoon.co.kr — GLB-Class, официальный дилерский каталог",
    ),
    ModelSpec(
        match=("glb", "250"),
        cc=1991, hp=224, fuel="petrol",
        label="Mercedes-Benz GLB 250 4Matic",
        source="carnoon.co.kr — GLB-Class, официальный дилерский каталог",
    ),
    ModelSpec(
        match=("glb", "35", "amg"),
        cc=1991, hp=306, fuel="petrol",
        label="Mercedes-Benz GLB 35 AMG 4Matic",
        source="carnoon.co.kr — GLB-Class F/L, официальный дилерский каталог",
    ),

    # ─── BMW X6 (G06) xDrive30d — мощность менялась по рестайлингам! ──────
    ModelSpec(
        match=("x6", "30d"),
        cc=2993, hp=298, fuel="diesel",
        label="BMW X6 xDrive30d (G06 LCI, рестайлинг 2023+)",
        source="carnoon.co.kr — New X6, официальный дилерский каталог (2026)",
        year_min=2023,
    ),
    ModelSpec(
        match=("x6", "30d"),
        cc=2993, hp=286, fuel="diesel",
        label="BMW X6 xDrive30d (G06 до рестайлинга, 2020-2022)",
        source="⚠️ koreausedcar.co.kr (форум/агрегатор, НЕ официальный источник) — "
               "рекомендуется дополнительная проверка перед использованием",
        year_min=2020, year_max=2022,
    ),

    # ─── Kia Sportage (NQ5, 5-е поколение, 2021.07~) ───────────────────────
    # Внимание: три силовые установки при (почти) одном модельном ряде —
    # смотри по наличию "디젤"/"가솔린"/"하이브리드"/"터보" в названии.
    ModelSpec(
        match=("스포티지", "nq5", "디젤"),
        cc=1999, hp=186, fuel="diesel",
        label="Kia Sportage NQ5 2.0 diesel",
        source="danawa.com + namu.wiki — диапазон 1598~1999cc подтверждён, "
               "186 л.с. по данным namu.wiki (сверить точный НEW-R индекс)",
    ),
    ModelSpec(
        match=("스포티지", "nq5", "하이브리드"),
        cc=1598, hp=230, fuel="hybrid_p",
        label="Kia Sportage NQ5 1.6T hybrid",
        source="namu.wiki — Sportage NQ5, официальные тех.характеристики Kia",
    ),
    ModelSpec(
        match=("스포티지", "nq5", "터보"),
        cc=1598, hp=180, fuel="petrol",
        label="Kia Sportage NQ5 1.6T petrol",
        source="namu.wiki — Sportage NQ5, официальные тех.характеристики Kia",
    ),

    # ─── Renault (Samsung) XM3 (LJL) — дизельной версии НЕ существует ─────
    ModelSpec(
        match=("xm3", "1.3"),
        cc=1332, hp=152, fuel="petrol",
        label="Renault XM3 1.3 TCe Turbo",
        source="carnoon.co.kr — XM3, официальный дилерский каталог",
    ),
    ModelSpec(
        match=("xm3", "1.6"),
        cc=1598, hp=123, fuel="petrol",
        label="Renault XM3 1.6 GTe",
        source="carnoon.co.kr — XM3, официальный дилерский каталог",
    ),
    ModelSpec(
        match=("xm3", "하이브리드"),
        cc=1598, hp=143, fuel="hybrid_p",
        label="Renault XM3 1.6 E-TECH Hybrid",
        source="ko.wikipedia.org — XM3, сверено с офиц. пресс-релизом Renault Korea",
    ),

    # ─── Audi Q3 (8U/F3) 35 TDI ─────────────────────────────────────────────
    ModelSpec(
        match=("q3", "tdi"),
        cc=1968, hp=150, fuel="diesel",
        label="Audi Q3 35 TDI",
        source="carnoon.co.kr (прайс-лист) + audi.co.kr, официальные данные",
    ),

    # ─── Volkswagen Tiguan 2.0 TDI ──────────────────────────────────────────
    # Два дубля с одинаковыми цифрами: kbchachacha показывает модель ТОЛЬКО
    # по-корейски ("티구안"), а charancha/encar/onbid — как повезёт (после
    # подстановки target_models.json обычно есть и "tiguan" тоже) — match
    # внутри одной записи это "И" (все подстроки разом), поэтому нужны две
    # записи, а не одна с обоими вариантами.
    ModelSpec(
        match=("tiguan", "tdi"),
        cc=1968, hp=150, fuel="diesel",
        label="VW Tiguan 2.0 TDI",
        source="danawa.com + carnoon.co.kr, официальные данные",
    ),
    ModelSpec(
        match=("티구안", "tdi"),
        cc=1968, hp=150, fuel="diesel",
        label="VW Tiguan 2.0 TDI (корейское название сайта)",
        source="danawa.com + carnoon.co.kr, официальные данные",
    ),

    # ─── Kia K3 (BD) — обычная 1.6 и турбо GT — не путать между собой! ─────
    ModelSpec(
        match=("k3", "gt"),
        cc=1598, hp=204, fuel="petrol",
        label="Kia K3 GT 1.6 turbo",
        source="прайс-лист kia.com + namu.wiki (K3 GT, выпуск 2021-2024)",
    ),
    ModelSpec(
        match=("k3",),
        cc=1598, hp=123, fuel="petrol",
        label="Kia K3 1.6 (база, Смартстрим Гамма-II MPI)",
        source="danawa.com, официальные технические характеристики",
    ),

    # --- Добавляй новые модели ниже по этому же образцу ---
]


def find_spec(title: str, year: Optional[int] = None) -> Optional[ModelSpec]:
    """
    Ищет подходящую запись по названию машины (и году выпуска, если известен
    и модель зависит от поколения). None, если не нашли или не смогли
    однозначно определить поколение.
    """
    if not title:
        return None
    t = title.lower()

    candidates = [spec for spec in SPECS if all(m in t for m in spec.match)]
    if not candidates:
        return None

    if len(candidates) == 1 and candidates[0].year_min is None:
        return candidates[0]

    # Несколько кандидатов может значить не только "разные поколения одной
    # модели" (тогда различаем по году, см. ниже), но и "разные комплектации
    # с пересекающимися ключевыми словами" — например, K3 (match=("k3",)) и
    # K3 GT (match=("k3","gt")): любой заголовок с "K3 GT" формально подходит
    # под ОБА варианта, потому что "k3" — часть более специфичного набора
    # ("k3","gt"). В таком случае, если один кандидат строго специфичнее
    # остальных (его набор ключевых слов — надмножество остальных), выбираем
    # его сразу, не дожидаясь года — это не поколенческая неоднозначность.
    most_specific = max(candidates, key=lambda c: len(c.match))
    if all(
        set(c.match) <= set(most_specific.match)
        for c in candidates
    ) and sum(1 for c in candidates if len(c.match) == len(most_specific.match)) == 1:
        return most_specific

    # Несколько кандидатов (разные поколения) — фильтруем по году
    if year is None:
        return None  # без года не угадываем поколение

    for spec in candidates:
        if spec.year_min is not None and year < spec.year_min:
            continue
        if spec.year_max is not None and year > spec.year_max:
            continue
        return spec

    return None  # год не попал ни в один диапазон


if __name__ == "__main__":
    tests = [
        ("벤츠 GLB(X247) 200 d", None),
        ("벤츠 GLB(X247) 250 4Matic", None),
        ("BMW X6 xDrive30d", None),
        ("BMW X6 xDrive30d", 2024),
        ("BMW X6 xDrive30d", 2021),
        ("BMW X6 30d", 2021),
        ("기아 스포티지 NQ5 2.0 디젤", None),
        ("기아 스포티지 NQ5 1.6 터보 하이브리드", None),
        ("르노 XM3 1.3 터보", None),
        ("기아 스포티지 NQ5", None),  # нет указания на топливо — не найдётся
    ]
    for title, year in tests:
        spec = find_spec(title, year)
        if spec:
            print(f"{title!r} (год={year}) -> {spec.label} | {spec.cc}см³ / {spec.hp}лс / {spec.fuel}")
        else:
            print(f"{title!r} (год={year}) -> НЕ НАЙДЕНО (нужна проверка вручную)")
