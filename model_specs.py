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

import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class ModelSpec:
    match: tuple                      # подстроки для поиска в title (все должны совпасть)
    cc: int                            # объём, см³
    hp: int                             # мощность, л.с.
    fuel: str                           # 'petrol' | 'diesel' | 'hybrid_p' | 'hybrid_d' | 'elec' | 'lpg'
    label: str                           # человекочитаемое имя для лога
    source: str                           # откуда взяты данные (для проверки/аудита)
    year_min: Optional[int] = None         # включительно, год выпуска
    year_max: Optional[int] = None         # включительно, год выпуска
    # (мин, макс) ожидаемой цены "под ключ" в рублях для этой модели — доп.
    # рубеж защиты в post_validator.check_price_relative() ПОВЕРХ абсолютного
    # PRICE_MIN_RUB/PRICE_MAX_RUB (см. config.py): абсолютный диапазон нужен
    # широким, чтобы не резать честные дорогие/дешёвые модели, а этот —
    # узкий, специфичный для конкретной модели (инцидент 2026-07-17: пост
    # Chevrolet Trax ~5 млн ₽ был бы формально ВНУТРИ разумного абсолютного
    # диапазона, но абсурден для бюджетного кроссовера под льготный
    # утильсбор — только модельный диапазон способен это поймать).
    # None — проверка не блокирует (нет данных для сравнения, не гадаем, тот
    # же принцип, что и у check_price_sanity()) — заполняется постепенно.
    price_range_rub: Optional[tuple] = None
    # Цена нового автомобиля в Корее, ₩ — доп. рубеж защиты в
    # post_validator.check_price_vs_new_krw(). В ОТЛИЧИЕ от price_range_rub
    # (итоговая цена ПОД КЛЮЧ в рублях) — это сырая корейская цена ОБЪЯВЛЕНИЯ
    # (car["price"], до перевода в рубли и добавления таможни/логистики).
    # В отличие от check_price_sanity() (живая заводская цена ИМЕННО этой
    # комплектации через encar_scraper.fetch_origin_price_krw() — точнее, но
    # только Encar и только когда дилер заполнил поле) — этот справочник
    # работает для ЛЮБОГО источника и не зависит от сети.
    # ⚠️ ВАЖНО (исправлено 2026-07-21 — первая версия анкорилась на БАЗОВЫЙ
    # трим и ложно резала честные старшие комплектации): сюда кладётся цена
    # нового ТОП-трима для этого match (Signature/топовая комплектация, не
    # Trendy/база) — один match может покрывать несколько тримов одного
    # мотора (напр. match=('k5','2.0') — Trendy/Prestige/Noblesse/Signature
    # сразу), и цена ТОПОВОГО трима — это уже потолок для всех них. Именно
    # поэтому check_price_vs_new_krw() сравнивает БЕЗ допуска (не умножает
    # на MAX_MARKUP_OVER_ORIGIN, как check_price_sanity()) — выше цены
    # нового топового трима легитимного объяснения нет.
    # None — проверка не блокирует, а логирует WARNING (не гадаем молча,
    # см. докстринг check_price_vs_new_krw()) — заполняется постепенно.
    new_price_max_krw: Optional[int] = None


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

    # ─── BMW X6 xDrive40i 3.0 G (G06 LCI; добавлено для отчёта bmw_x6_report) ──
    # Корейская спека — 375 л.с. (48V MHEV), не путать с EU 381 л.с.: на
    # таможенные бакеты обе версии попадают одинаково (>320 л.с.).
    ModelSpec(
        match=("x6", "40i"),
        cc=2998, hp=375, fuel="petrol",
        label="BMW X6 xDrive40i (G06 LCI)",
        source="bikecaronline.com/kr — BMW X6 xDrive40i South Korea (2998 см³, 375 л.с.)",
    ),

    # ─── BMW X5 (G05 LCI; добавлено для отчёта bmw_x5_report) ───────────────
    # Те же силовые установки, что у G06: B57 (30d) и B58 (40i, KR 375 л.с.).
    ModelSpec(
        match=("x5", "30d"),
        cc=2993, hp=298, fuel="diesel",
        label="BMW X5 xDrive30d (G05 LCI)",
        source="carnoon.co.kr (2026) — B57, та же установка, что X6 xDrive30d LCI",
        year_min=2023,
    ),
    ModelSpec(
        match=("x5", "40i"),
        cc=2998, hp=375, fuel="petrol",
        label="BMW X5 xDrive40i (G05 LCI)",
        source="BMW of Loveland — 2026 X5 xDrive40i 375 hp; B58, тот же, что X6 xDrive40i KR",
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
        # ВАЖНО: label — ТОЛЬКО человекочитаемое имя машины, ничего больше.
        # Дев-заметка "почему нужна отдельная запись" (корейское название
        # сайта) — в комментарии выше, а не в label: раньше была прямо в
        # label и утекла в публичный пост как есть (см. FINAL_STATE.md,
        # тот же баг найден и на Chevrolet Trax).
        match=("티구안", "tdi"),
        cc=1968, hp=150, fuel="diesel",
        label="VW Tiguan 2.0 TDI",
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

    # ─── Kia K5 (3-е поколение, DL3) 2.0 Gasoline (Smartstream G2.0 MPi,
    # атмосферный, не путать с 1.6T GT-line/гибридом) ───────────────────────
    # ⚠️ В прайс-листе Pick+ Auto у K5 стоит пометка "уточнить у брокера" —
    # у модели в Корее несколько силовых установок (2.0 MPi/1.6T GT-line/
    # 2.0 HEV), match ниже покрывает только 2.0 MPi. Перед публикацией
    # конкретного объявления сверяй реальную комплектацию у брокера, а не
    # полагайся вслепую на автоматическое распознавание по match.
    ModelSpec(
        match=("k5", "2.0"),
        cc=1999, hp=160, fuel="petrol",
        label="Kia K5 (DL3) 2.0 Gasoline (Smartstream G2.0 MPi)",
        source="danawa.com + carnoon.co.kr — официальные технические характеристики "
               "(Smartstream G2.0 MPi, единый мотор для всех тримов 2.0 бензин, "
               "включая Signature — комплектация не влияет на двигатель). "
               "⚠️ Уточнить у брокера (пометка из прайс-листа Pick+ Auto) — "
               "перед конкретной сделкой подтвердить реальную комплектацию.",
        # Цена ТОП-трима (Signature), названа пользователем в сессии
        # 2026-07-21 — требует проверки по официальному прайс-листу
        # kia.com/carnoon.co.kr. Первая версия (2026-07-21, до правки) стояла
        # на цене БАЗОВОГО Trendy (27 630 000 ₩) — ложно резала бы честные
        # Prestige/Noblesse/Signature дороже базового; исправлено на потолок
        # по топ-триму, см. комментарий у поля new_price_max_krw.
        # Найдено вживую: encar #42247766 (K5 2.0, BadgeDetail "노블레스"/
        # Noblesse) — цена объявления 37 900 000 ₩ ВЫШЕ даже этой цены
        # топ-трима — однозначная аномалия, регрессия в test_post_validator.py.
        new_price_max_krw=34_690_000,
    ),


    # ─── Hyundai Avante 1.6 G (добавлено через add_model.py) ────────────────────
    ModelSpec(
        match=('avante', '1.6'),
        cc=1598, hp=123, fuel='petrol',
        label='Hyundai Avante 1.6 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Avante 1.6 G (добавлено через add_model.py) ────────────────────
    ModelSpec(
        match=('아반떼', '1.6'),
        cc=1598, hp=123, fuel='petrol',
        label='Hyundai Avante 1.6 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Avante Hybrid 1.6 G+E (добавлено через add_model.py) ───────────
    ModelSpec(
        match=('avante', '하이브리드'),
        cc=1580, hp=141, fuel='hybrid_p',
        label='Hyundai Avante Hybrid 1.6 G+E',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Avante Hybrid 1.6 G+E (добавлено через add_model.py) ───────────
    ModelSpec(
        match=('아반떼', '하이브리드'),
        cc=1580, hp=141, fuel='hybrid_p',
        label='Hyundai Avante Hybrid 1.6 G+E',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Casper 1.0 G (добавлено через add_model.py) ────────────────────
    ModelSpec(
        match=('casper', '1.0'),
        cc=998, hp=76, fuel='petrol',
        label='Hyundai Casper 1.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Casper 1.0 G (добавлено через add_model.py) ────────────────────
    ModelSpec(
        match=('캐스퍼', '1.0'),
        cc=998, hp=76, fuel='petrol',
        label='Hyundai Casper 1.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Kona 2.0 G (добавлено через add_model.py) ──────────────────────
    ModelSpec(
        match=('kona', '2.0'),
        cc=1999, hp=149, fuel='petrol',
        label='Hyundai Kona 2.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Kona 2.0 G (добавлено через add_model.py) ──────────────────────
    ModelSpec(
        match=('코나', '2.0'),
        cc=1999, hp=149, fuel='petrol',
        label='Hyundai Kona 2.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Venue 1.6 G (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('venue', '1.6'),
        cc=1598, hp=123, fuel='petrol',
        label='Hyundai Venue 1.6 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Venue 1.6 G (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('베뉴', '1.6'),
        cc=1598, hp=123, fuel='petrol',
        label='Hyundai Venue 1.6 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Morning 1.0 G (добавлено через add_model.py) ───────────────────────
    ModelSpec(
        match=('morning', '1.0'),
        cc=998, hp=76, fuel='petrol',
        label='Kia Morning 1.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Morning 1.0 G (добавлено через add_model.py) ───────────────────────
    ModelSpec(
        match=('모닝', '1.0'),
        cc=998, hp=76, fuel='petrol',
        label='Kia Morning 1.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Niro HEV 1.6 G (добавлено через add_model.py) ──────────────────────
    ModelSpec(
        match=('niro', '하이브리드'),
        cc=1580, hp=105, fuel='hybrid_p',
        label='Kia Niro HEV 1.6 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Niro HEV 1.6 G (добавлено через add_model.py) ──────────────────────
    ModelSpec(
        match=('니로', '하이브리드'),
        cc=1580, hp=105, fuel='hybrid_p',
        label='Kia Niro HEV 1.6 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Soul 2.0 G (добавлено через add_model.py) ──────────────────────────
    ModelSpec(
        match=('soul', '2.0'),
        cc=1999, hp=147, fuel='petrol',
        label='Kia Soul 2.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Soul 2.0 G (добавлено через add_model.py) ──────────────────────────
    ModelSpec(
        match=('쏘울', '2.0'),
        cc=1999, hp=147, fuel='petrol',
        label='Kia Soul 2.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Ray 1.0 G (добавлено через add_model.py) ───────────────────────────
    ModelSpec(
        match=('ray', '1.0'),
        cc=998, hp=76, fuel='petrol',
        label='Kia Ray 1.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Ray 1.0 G (добавлено через add_model.py) ───────────────────────────
    ModelSpec(
        match=('레이', '1.0'),
        cc=998, hp=76, fuel='petrol',
        label='Kia Ray 1.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Seltos 1.6 D (добавлено через add_model.py) ────────────────────────
    ModelSpec(
        match=('seltos', '디젤'),
        cc=1582, hp=136, fuel='diesel',
        label='Kia Seltos 1.6 Diesel',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Seltos 1.6 D (добавлено через add_model.py) ────────────────────────
    ModelSpec(
        match=('셀토스', '디젤'),
        cc=1582, hp=136, fuel='diesel',
        label='Kia Seltos 1.6 Diesel',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Seltos 2.0 G (добавлено через add_model.py) ────────────────────────
    ModelSpec(
        match=('seltos', '2.0'),
        cc=1999, hp=149, fuel='petrol',
        label='Kia Seltos 2.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Seltos 2.0 G (добавлено через add_model.py) ────────────────────────
    ModelSpec(
        match=('셀토스', '2.0'),
        cc=1999, hp=149, fuel='petrol',
        label='Kia Seltos 2.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Kia Sportage 2.0 G (QL, до 2021) (добавлено через add_model.py) ────────
    ModelSpec(
        match=('스포티지', '2.0', '가솔린'),
        cc=1999, hp=152, fuel='petrol',
        label='Kia Sportage (до 2021) 2.0 Gasoline',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── BMW 1 Series 118d 2.0 D (добавлено через add_model.py) ─────────────────
    ModelSpec(
        match=('118d',),
        cc=1995, hp=150, fuel='diesel',
        label='BMW 1 Series 118d',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── BMW X1 xDrive18d 2.0 D (добавлено через add_model.py) ──────────────────
    ModelSpec(
        match=('x1', '18d'),
        cc=1995, hp=150, fuel='diesel',
        label='BMW X1 xDrive18d',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── BMW X2 xDrive18d 2.0 D (добавлено через add_model.py) ──────────────────
    ModelSpec(
        match=('x2', '18d'),
        cc=1995, hp=150, fuel='diesel',
        label='BMW X2 xDrive18d',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── BMW X4 xDrive20i 2.0 G (G02; добавлено для отчёта bmw_x4_report) ──────
    # ВАЖНО: 20i — это 184 л.с. (135 кВт), НЕ путать с 30i (185 кВт/252 л.с.).
    ModelSpec(
        match=('x4', '20i'),
        cc=1998, hp=184, fuel='petrol',
        label='BMW X4 xDrive20i (G02)',
        source='velmax.uz (корейский X4 xDrive20i 2025 — 184 л.с.), справочники H&R/eBay (135 кВт/184 л.с.)',
    ),

    # ─── Mercedes-Benz A-Class 180 1.3 G (добавлено через add_model.py) ─────────
    # ВАЖНО: match — ("a180","가솔린"), а не просто ("a180",) — "a180" это
    # substring внутри "a180d" (дизель, см. запись ниже), без второго
    # ключевого слова дизельные объявления ошибочно попадали бы и сюда
    # (специфичность tie-break в find_spec() тут не спасает — он сравнивает
    # НАБОРЫ ключевых слов как целые строки, а не подстроки друг в друге).
    ModelSpec(
        match=('a180', '가솔린'),
        cc=1332, hp=136, fuel='petrol',
        label='Mercedes-Benz A180',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Mercedes-Benz A180d 2.0 D — по данным пользователя, для корейского
    # рынка используется версия 2.0 D (не европейская 1.5D) ─────────────────
    ModelSpec(
        match=('a180d',),
        cc=1950, hp=116, fuel='diesel',
        label='Mercedes-Benz A180d (корейский рынок, 2.0 D)',
        source='прайс-лист Pick+ Auto (внутренний документ компании) — '
               'подтверждено пользователем: корейский рынок отличается от '
               'европейского 1.5D',
    ),

    # ─── Mercedes-Benz A-Class 200d 2.0 D (добавлено через add_model.py) ────────
    ModelSpec(
        match=('a200d',),
        cc=1950, hp=150, fuel='diesel',
        label='Mercedes-Benz A200d',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Audi Q2 1.5 G TFSI (добавлено через add_model.py) ──────────────────────
    ModelSpec(
        match=('q2',),
        cc=1498, hp=150, fuel='petrol',
        label='Audi Q2 35 TFSI',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Citroen C3 Aircross 1.2 G (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('c3', 'aircross', '1.2'),
        cc=1199, hp=128, fuel='petrol',
        label='Citroen C3 Aircross 1.2 PureTech',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Citroen C3 Aircross 1.5 D (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('c3', 'aircross', '1.5'),
        cc=1499, hp=118, fuel='diesel',
        label='Citroen C3 Aircross 1.5 BlueHDi',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Volkswagen Golf 2.0 D TDI (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('golf', 'tdi'),
        cc=1968, hp=150, fuel='diesel',
        label='Volkswagen Golf 2.0 TDI',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Volkswagen Jetta 1.5 G TSI (добавлено через add_model.py) ──────────────
    ModelSpec(
        match=('jetta', 'tsi'),
        cc=1498, hp=158, fuel='petrol',
        label='Volkswagen Jetta 1.5 TSI',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Volkswagen T-Roc 2.0 D TDI (добавлено через add_model.py) ──────────────
    ModelSpec(
        match=('t-roc', 'tdi'),
        cc=1968, hp=150, fuel='diesel',
        label='Volkswagen T-Roc 2.0 TDI',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Casper Turbo 1.0 G Turbo (добавлено через add_model.py) ────────
    ModelSpec(
        match=('casper', '1.0', '터보'),
        cc=998, hp=100, fuel='petrol',
        label='Hyundai Casper 1.0 Gasoline Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Casper Turbo 1.0 G Turbo (добавлено через add_model.py) ────────
    ModelSpec(
        match=('캐스퍼', '1.0', '터보'),
        cc=998, hp=100, fuel='petrol',
        label='Hyundai Casper 1.0 Gasoline Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),


    # ─── Chevrolet Trax Crossover 1.2 Turbo — единственный мотор для этого
    # поколения в Корее (нет риска перепутать с другой силовой установкой,
    # как было у Sonata) — два варианта ключевых слов, как у VW Tiguan
    # выше: kbchachacha показывает модель ТОЛЬКО по-корейски ("트랙스").
    ModelSpec(
        match=('trax', '1.2'),
        cc=1198, hp=139, fuel='petrol',
        label='Chevrolet Trax Crossover 1.2 Turbo',
        source='Официальные технические характеристики Chevrolet Trax Crossover '
               '(chevrolet.co.kr) — 1.2 Turbo GDI, единственный мотор для этого '
               'поколения в Корее',
        # ОЦЕНКА по формуле calc_full() для правдоподобного диапазона цены
        # объявления (18-38 млн ₩, обе возрастные категории u3/35), НЕ
        # внешний прайс-лист — требует уточнения от пользователя. Добавлено
        # 2026-07-17 после инцидента: пост Chevrolet Trax ~5 млн ₽ ушёл в
        # канал (см. FINAL_STATE.md/аудит), этот диапазон его бы отклонил.
        price_range_rub=(1_200_000, 3_600_000),
    ),
    ModelSpec(
        # ВАЖНО: label — ТОЛЬКО человекочитаемое имя, ничего больше (см.
        # аналогичный фикс у VW Tiguan выше — дев-заметка "корейское
        # название сайта" раньше была прямо в label и утекла в реальный
        # пост в проде, см. FINAL_STATE.md).
        match=('트랙스', '1.2'),
        cc=1198, hp=139, fuel='petrol',
        label='Chevrolet Trax Crossover 1.2 Turbo',
        source='Официальные технические характеристики Chevrolet Trax Crossover '
               '(chevrolet.co.kr) — 1.2 Turbo GDI, единственный мотор для этого '
               'поколения в Корее',
        price_range_rub=(1_200_000, 3_600_000),
    ),
    # ─── Peugeot 2008 (P24, 2020~) — те же PSA/Stellantis моторы, что и
    # Citroen C3 Aircross (см. записи выше), но НЕ идентичная мощность на
    # дизеле: 2008 1.5 BlueHDi — 130 л.с. (иная тюнировка ECU для этой
    # модели у Peugeot Korea, не 118 л.с. как у C3 Aircross 1.5 BlueHDi —
    # исправлено 2026-07-17, старое значение 118 было ошибочным, см.
    # FINAL_STATE.md). Уточняй бэдж комплектации в объявлении перед
    # доверием к расчёту — если реального бэджа "PureTech"/"BlueHDi" в
    # названии нет, find_spec() не сработает намеренно (см. match).
    ModelSpec(
        match=("2008", "puretech"),
        cc=1199, hp=131, fuel="petrol",
        label="Peugeot 2008 1.2 PureTech",
        source="официальные тех.характеристики Peugeot Korea — тот же мотор, "
               "что и Citroen C3 Aircross 1.2 PureTech (см. запись выше)",
    ),
    ModelSpec(
        match=("2008", "bluehdi"),
        cc=1499, hp=130, fuel="diesel",
        label="Peugeot 2008 1.5 BlueHDi",
        source="прайс-лист Pick+ Auto (внутренний документ компании) — "
               "130 л.с., отличается от Citroen C3 Aircross 1.5 BlueHDi "
               "(118 л.с.) несмотря на общую платформу, подтверждено "
               "пользователем 2026-07-17 (было ошибочно указано 118)",
    ),

    # ─── Audi RS6 / RS7 (C8, 2020~) — единственный мотор для обоих кузовов
    # (Avant/Sportback) в этом поколении: EA825 4.0 TFSI V8 биturbo, 600 PS —
    # нет других силовых установок, риска перепутать нет.
    ModelSpec(
        match=("rs6",),
        cc=3996, hp=600, fuel="petrol",
        label="Audi RS6 Avant 4.0 TFSI V8",
        source="официальные тех.характеристики Audi (audi.co.kr) — EA825 4.0 "
               "TFSI, единственный мотор для RS6 (C8)",
    ),
    ModelSpec(
        match=("rs7",),
        cc=3996, hp=600, fuel="petrol",
        label="Audi RS7 Sportback 4.0 TFSI V8",
        source="официальные тех.характеристики Audi (audi.co.kr) — EA825 4.0 "
               "TFSI, единственный мотор для RS7 (C8)",
    ),


    # ─── Hyundai Sonata 2.0 (добавлено через add_model.py) ──────────────────────
    # ⚠️ В прайс-листе Pick+ Auto у Sonata тоже стоит пометка "уточнить у
    # брокера" (см. аналогичную заметку у K5 выше) — у Sonata в Корее
    # несколько силовых установок (2.0 MPi/1.6T/2.0 LPi/гибрид), match
    # ниже покрывает только 2.0 MPi — 2.0 LPi см. отдельную запись ниже.
    ModelSpec(
        match=('sonata', '2.0'),
        cc=1999, hp=160, fuel='petrol',
        label='Hyundai Sonata The Edge (DN8) 2.0 Gasoline (Smartstream G2.0 MPi)',
        source='carnoon.co.kr — SONATA The Edge, официальный дилерский каталог. '
               '⚠️ Уточнить у брокера (пометка из прайс-листа Pick+ Auto) — '
               'перед конкретной сделкой подтвердить реальную комплектацию.',
    ),

    # ─── Hyundai Sonata 2.0 (добавлено через add_model.py) ──────────────────────
    ModelSpec(
        match=('쏘나타', '2.0'),
        cc=1999, hp=160, fuel='petrol',
        label='Hyundai Sonata The Edge (DN8) 2.0 Gasoline (Smartstream G2.0 MPi)',
        source='carnoon.co.kr — SONATA The Edge, официальный дилерский каталог',
    ),

    # ─── Kia Tasman 2.5 (добавлено через add_model.py) ──────────────────────────
    ModelSpec(
        match=('tasman', '2.5'),
        cc=2497, hp=281, fuel='petrol',
        label='Kia Tasman 2.5 Turbo Gasoline',
        source='carnoon.co.kr — Tasman, официальный дилерский каталог; единственный мотор (нет дизеля/гибрида)',
    ),

    # ─── Kia Tasman 2.5 (добавлено через add_model.py) ──────────────────────────
    ModelSpec(
        match=('타스만', '2.5'),
        cc=2497, hp=281, fuel='petrol',
        label='Kia Tasman 2.5 Turbo Gasoline',
        source='carnoon.co.kr — Tasman, официальный дилерский каталог; единственный мотор (нет дизеля/гибрида)',
    ),

    # ─── Hyundai IONIQ 5 Long Range AWD (добавлено через add_model.py) ──────────
    ModelSpec(
        match=('ioniq', '5', 'awd'),
        cc=0, hp=325, fuel='elec',
        label='Hyundai IONIQ 5 Long Range AWD (77.4 кВтч, 239 кВт)',
        source='hyundai.com/kr — IONIQ 5, официальный дилерский каталог (2023+, 77.4 кВтч)',
    ),

    # ─── Kia EV9 Long Range AWD (добавлено через add_model.py) ──────────────────
    # ВАЖНО: официальное название трима у Kia — "4WD", а не "AWD" (в отличие
    # от IONIQ 5/GV60, где Hyundai/Genesis используют именно "AWD") —
    # проверено на carnoon.co.kr ("롱레인지 7인승 4WD" и т.д.), match ниже
    # исправлен под реальное название, иначе он никогда бы не сработал.
    ModelSpec(
        match=('ev9', '4wd'),
        cc=0, hp=385, fuel='elec',
        label='Kia EV9 Long Range AWD Earth (283 кВт)',
        source='kia.com/kr — EV9, официальный дилерский каталог',
    ),

    # ─── Genesis GV60 Standard AWD (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('gv60', 'awd'),
        cc=0, hp=318, fuel='elec',
        label='Genesis GV60 Standard AWD (84 кВтч, 234 кВт)',
        source='genesis.com/kr — GV60, официальный дилерский каталог',
    ),

    # ─── BYD SEALION 7 (добавлено через add_model.py) ───────────────────────────
    ModelSpec(
        match=('sealion', '7'),
        cc=0, hp=313, fuel='elec',
        label='BYD SEALION 7 (RWD, 82.5 кВтч, 230 кВт)',
        source='bydauto.kr — SEALION 7, официальный дилерский каталог (запуск 2025-09)',
    ),

    # ─── Hyundai IONIQ 5 Long Range AWD (добавлено через add_model.py) ──────────
    ModelSpec(
        match=('아이오닉', '5', 'awd'),
        cc=0, hp=325, fuel='elec',
        label='Hyundai IONIQ 5 Long Range AWD (77.4 кВтч, 239 кВт)',
        source='hyundai.com/kr — IONIQ 5, официальный дилерский каталог (2023+, 77.4 кВтч)',
    ),

    # ─── BYD SEALION 7 (добавлено через add_model.py) ───────────────────────────
    ModelSpec(
        match=('씨라이언', '7'),
        cc=0, hp=313, fuel='elec',
        label='BYD SEALION 7 (RWD, 82.5 кВтч, 230 кВт)',
        source='bydauto.kr — SEALION 7, официальный дилерский каталог (запуск 2025-09)',
    ),

    # ─── Kia Sportage 1.6 D (до 2021) (добавлено через add_model.py) ────────────
    ModelSpec(
        match=('스포티지', '1.6', '디젤'),
        cc=1598, hp=136, fuel='diesel',
        label='Kia Sportage (до 2021) 1.6 Diesel',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Sonata 2.0 LPi (добавлено через add_model.py) ──────────────────
    ModelSpec(
        match=('sonata', 'lpi'),
        cc=1999, hp=146, fuel='lpg',
        label='Hyundai Sonata 2.0 LPi',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Hyundai Sonata 2.0 LPi (добавлено через add_model.py) ──────────────────
    ModelSpec(
        match=('쏘나타', 'lpi'),
        cc=1999, hp=146, fuel='lpg',
        label='Hyundai Sonata 2.0 LPi',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Renault QM6 2.0 (добавлено через add_model.py) ─────────────────────────
    ModelSpec(
        match=('qm6', '2.0'),
        cc=1998, hp=144, fuel='petrol',
        label='Renault QM6 2.0 GDe',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Renault QM6 2.0 LPe (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('qm6', 'lpe'),
        cc=1998, hp=140, fuel='lpg',
        label='Renault QM6 2.0 LPe',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Renault SM6 1.3 (добавлено через add_model.py) ─────────────────────────
    ModelSpec(
        match=('sm6', '1.3'),
        cc=1332, hp=156, fuel='petrol',
        label='Renault SM6 1.3 TCe Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Renault SM6 2.0 LPe (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('sm6', 'lpe'),
        cc=1998, hp=140, fuel='lpg',
        label='Renault SM6 2.0 LPe',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Chevrolet Malibu 1.35 (добавлено через add_model.py) ───────────────────
    ModelSpec(
        match=('malibu', '1.35'),
        cc=1350, hp=156, fuel='petrol',
        label='Chevrolet Malibu 1.35 Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Chevrolet Malibu 1.35 (добавлено через add_model.py) ───────────────────
    ModelSpec(
        match=('말리부', '1.35'),
        cc=1350, hp=156, fuel='petrol',
        label='Chevrolet Malibu 1.35 Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Chevrolet Spark 1.0 (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('spark', '1.0'),
        cc=999, hp=75, fuel='petrol',
        label='Chevrolet Spark 1.0',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Chevrolet Spark 1.0 (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('스파크', '1.0'),
        cc=999, hp=75, fuel='petrol',
        label='Chevrolet Spark 1.0',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Chevrolet Trax 1.35 (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('trax', '1.35'),
        cc=1350, hp=156, fuel='petrol',
        label='Chevrolet Trax Crossover 1.35 Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
        # ОЦЕНКА по calc_full(), см. комментарий у 1.2 Turbo варианта выше —
        # тот же принцип и повод (аудит 2026-07-17).
        price_range_rub=(1_300_000, 3_800_000),
    ),

    # ─── Chevrolet Trax 1.35 (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('트랙스', '1.35'),
        cc=1350, hp=156, fuel='petrol',
        label='Chevrolet Trax Crossover 1.35 Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
        price_range_rub=(1_300_000, 3_800_000),
    ),

    # ─── Chevrolet Trailblazer 1.2 (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('trailblazer', '1.2'),
        cc=1200, hp=137, fuel='petrol',
        label='Chevrolet Trailblazer 1.2 Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Chevrolet Trailblazer 1.2 (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('트레일블레이저', '1.2'),
        cc=1200, hp=137, fuel='petrol',
        label='Chevrolet Trailblazer 1.2 Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Chevrolet Trailblazer 1.4 (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('trailblazer', '1.4'),
        cc=1400, hp=155, fuel='petrol',
        label='Chevrolet Trailblazer 1.4 Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Chevrolet Trailblazer 1.4 (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('트레일블레이저', '1.4'),
        cc=1400, hp=155, fuel='petrol',
        label='Chevrolet Trailblazer 1.4 Turbo',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Mini Countryman 1.5 (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('countryman', '1.5'),
        cc=1499, hp=136, fuel='petrol',
        label='Mini Countryman 1.5',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Mini Countryman 1.5 (добавлено через add_model.py) ─────────────────────
    ModelSpec(
        match=('컨트리맨', '1.5'),
        cc=1499, hp=136, fuel='petrol',
        label='Mini Countryman 1.5',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Mini Clubman 1.5 (добавлено через add_model.py) ────────────────────────
    ModelSpec(
        match=('clubman', '1.5'),
        cc=1499, hp=136, fuel='petrol',
        label='Mini Clubman 1.5',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Mini Clubman 1.5 (добавлено через add_model.py) ────────────────────────
    ModelSpec(
        match=('클럽맨', '1.5'),
        cc=1499, hp=136, fuel='petrol',
        label='Mini Clubman 1.5',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Mini Convertible 1.5 (добавлено через add_model.py) ────────────────────
    ModelSpec(
        match=('mini', 'convertible', '1.5'),
        cc=1499, hp=136, fuel='petrol',
        label='Mini Convertible 1.5',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Mini Convertible 1.5 (добавлено через add_model.py) ────────────────────
    ModelSpec(
        match=('미니', '컨버터블', '1.5'),
        cc=1499, hp=136, fuel='petrol',
        label='Mini Convertible 1.5',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Peugeot 3008 1.2 PureTech (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('3008', 'puretech'),
        cc=1199, hp=131, fuel='petrol',
        label='Peugeot 3008 1.2 PureTech',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Peugeot 3008 1.5 BlueHDi (добавлено через add_model.py) ────────────────
    ModelSpec(
        match=('3008', 'bluehdi'),
        cc=1499, hp=130, fuel='diesel',
        label='Peugeot 3008 1.5 BlueHDi',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Peugeot 5008 1.2 PureTech (добавлено через add_model.py) ───────────────
    ModelSpec(
        match=('5008', 'puretech'),
        cc=1199, hp=131, fuel='petrol',
        label='Peugeot 5008 1.2 PureTech',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Peugeot 5008 1.5 BlueHDi (добавлено через add_model.py) ────────────────
    ModelSpec(
        match=('5008', 'bluehdi'),
        cc=1499, hp=130, fuel='diesel',
        label='Peugeot 5008 1.5 BlueHDi',
        source='прайс-лист Pick+ Auto (внутренний документ компании)',
    ),

    # ─── Audi A6 40 TDI quattro (добавлено через add_model.py) ──────────────────
    ModelSpec(
        match=('a6', '40', 'tdi'),  # "quattro" убран из match: в Корее дизель A6 C8
        # продаётся ТОЛЬКО в Quattro (нет FWD-варианта), а само слово может быть
        # "quattro"/"콰트로"/вообще опущено в заголовке площадки — не нужно как
        # дискриминатор и рискует ложноотрицательным совпадением
        cc=1968, hp=204, fuel='diesel',
        label='Audi A6 (C8) 40 TDI quattro (2.0 TDI)',
        source='carlab.co.kr + danawa.com — офиц. запуск Audi Korea апрель 2020: 2.0L R4 TDI, 204 л.с., 7-ст. S tronic, MHEV Plus',
    ),

    # ─── Audi A6 45 TDI (добавлено через add_model.py) ──────────────────────────
    ModelSpec(
        match=('a6', '45', 'tdi'),
        cc=2967, hp=231, fuel='diesel',
        label='Audi A6 (C8) 45 TDI quattro (3.0 V6 TDI)',
        source='danawa.com — офиц. запуск Audi Korea апрель 2020: 3.0L V6 TDI, 231 л.с., 8-ст. типтроник, quattro',
    ),
    # --- Добавляй новые модели ниже по этому же образцу ---
]


def model_has_privileged_hp_variant(brand: str, model: str, max_hp: int) -> bool:
    """
    True, если среди ИЗВЕСТНЫХ конфигураций этой модели (brand+model, БЕЗ
    привязки к конкретному триму/мотору конкретного лота) есть хотя бы одна
    запись в SPECS с hp <= max_hp. Используется yandex_post_validator.py:
    для лота с hp > max_hp (льготный утильсбор не положен, см.
    customs_calculator.MAX_HP_FOR_PHYS_LGOTA) — блокировать публикацию
    имеет смысл ТОЛЬКО если для этой же модели известна версия в пределах
    льготы (тогда высокий hp у конкретного лота — повод сверить комплектацию
    у брокера, см. пример K5: 2.0 MPi/160 л.с. в SPECS, но у модели есть и
    1.6T GT-line с более высокой мощностью — см. комментарий у записи K5).
    Если у модели ВООБЩЕ нет варианта в пределах льготы (все её моторы
    мощнее max_hp, или модель не отслеживается в SPECS вовсе) — блокировать
    нечего, не гадаем (тот же принцип "нет данных — не блокируем", что и у
    post_validator.check_price_relative()).

    Ищем по label (человекочитаемое "Kia K5 (DL3) 2.0 Gasoline ..."), а не
    по match (тот заточен под точное распознавание ОДНОГО конкретного трима
    по сырой строке объявления, не под "все известные версии модели").
    """
    brand_l = (brand or "").strip().lower()
    model_l = (model or "").strip().lower()
    if not brand_l or not model_l:
        return False
    return any(
        brand_l in spec.label.lower() and model_l in spec.label.lower() and spec.hp <= max_hp
        for spec in SPECS
    )


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


def _safe_print(text: str) -> None:
    """
    "см³"/другие спецсимволы падают с UnicodeEncodeError на Windows-консоли
    с не-UTF8 кодировкой (например, cp1251) — та же проблема, что чинили в
    telegram_publisher.py/add_model.py. add_model.py сам советует запустить
    этот самотест следующим шагом — он не должен падать на той же консоли.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


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
            _safe_print(f"{title!r} (год={year}) -> {spec.label} | {spec.cc}см³ / {spec.hp}лс / {spec.fuel}")
        else:
            _safe_print(f"{title!r} (год={year}) -> НЕ НАЙДЕНО (нужна проверка вручную)")
