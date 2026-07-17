# -*- coding: utf-8 -*-
"""
config.py — здесь собраны ВСЕ настройки проекта в одном месте.

Зачем отдельный файл для настроек?
Чтобы не разбрасывать "магические числа" и токены по всему коду.
Если завтра понадобится поменять паузу между запросами — меняешь
одну строчку здесь, а не ищешь по всем файлам.

Настоящие секретные значения (токен бота и т.д.) лежат в файле .env
(его у тебя ещё нет — скопируй .env.example в .env и впиши свои данные).
Файл .env специально НЕ публикуется никуда — там секреты.
"""

import os
from dotenv import load_dotenv

# Эта строчка читает файл .env и подставляет его содержимое
# в переменные окружения — как будто ты вручную ввёл их в системе.
load_dotenv()

# os.getenv("ИМЯ", "значение_по_умолчанию") — читает переменную из .env.
# Если такой переменной в .env нет — используется значение по умолчанию
# (второй аргумент). Это подстраховка, чтобы программа не падала сразу,
# если ты забыл что-то прописать.

# --- Telegram ---
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "@auto_generals")

# --- VK ---
# VK_OWNER_ID — отрицательное число для сообщества/группы (см. PLAN_NEXT_CHANNELS.md).
# Если VK_TOKEN пустой ИЛИ VK_ENABLED=false — vk_publisher.py просто не
# публикует в VK (не падает), см. vk_publisher.is_configured().
VK_TOKEN: str = os.getenv("VK_TOKEN", "")
VK_OWNER_ID: str = os.getenv("VK_OWNER_ID", "")
VK_ENABLED: bool = os.getenv("VK_ENABLED", "true").strip().lower() == "true"

# Отдельный чат для уведомлений об ОШИБКАХ (не публичный канал!).
# Проще всего — узнать свой личный chat_id: напиши что угодно боту
# @userinfobot в Telegram, он пришлёт тебе твой ID.
# Если оставить пустым — уведомления об ошибках просто не будут отправляться
# (только запишутся в лог-файл).
ADMIN_CHAT_ID: str = os.getenv("ADMIN_CHAT_ID", "")

# --- Claude API (для "умной" генерации постов, см. post_generator.py) ---
# Получить ключ: console.anthropic.com -> Account Settings -> API Keys.
# Если оставить пустым — post_generator.py сам откатится на обычный
# шаблонный генератор постов (post_builder.py), программа не упадёт.
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# --- Настройки парсинга ---
# float() превращает текст "3" в число 3.0, с которым можно делать математику
REQUEST_DELAY_SECONDS: float = float(os.getenv("REQUEST_DELAY_SECONDS", "3"))

# --- Пути к файлам ---
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/cars.db")
LOG_FILE_PATH: str = os.getenv("LOG_FILE_PATH", "logs/app.log")

# --- Доп. расходы для расчёта цены "под ключ" (в рублях) ---
# Это то, что добавляется сверху к цене авто + таможне: доставка из Кореи,
# услуги брокера, твоя собственная комиссия. Если какая-то статья не нужна —
# просто оставь 0 (или не указывай строку в .env вообще).
LOGISTICS_RUB: float = float(os.getenv("LOGISTICS_RUB", "0"))
BROKER_FEE_RUB: float = float(os.getenv("BROKER_FEE_RUB", "0"))
MARGIN_RUB: float = float(os.getenv("MARGIN_RUB", "0"))

# Комиссия банка за перевод оплаты за саму машину продавцу в Корее — берётся
# ТОЛЬКО от цены авто (price_rub), не от таможни/доставки/брокера/маржи.
BANK_COMMISSION_PCT: float = float(os.getenv("BANK_COMMISSION_PCT", "0.025"))

# --- Кто ввозит (влияет на расчёт утильсбора) ---
# 'phys' — физическое лицо (льготный утильсбор при мощности до 160 л.с.
#          и объёме до 3000 см³), 'legal' — юридическое лицо (коммерческие
#          ставки без льгот).
IMPORTER_TYPE: str = os.getenv("IMPORTER_TYPE", "phys")

# --- Режим поиска для main_pipeline.py ---
# 'targeted' — только конкретные модели из target_models.json.
# 'broad'    — только широкий поиск любых моделей с hp<=max_hp (см.
#              broad_search_config.json) — максимум разнообразия контента.
# 'both'     — оба режима сразу, результаты смешиваются в один пул перед
#              round-robin отбором (см. candidate_diversity.py). Дефолт.
SEARCH_MODE: str = os.getenv("SEARCH_MODE", "both")

# Финальный фильтр по пробегу для main_pipeline.py (select_candidates) —
# применяется независимо от режима поиска (targeted/broad), поверх любых
# per-модельных mileage_max из target_models.json/broad_search_config.json.
MAX_MILEAGE_KM: int = int(os.getenv("MAX_MILEAGE_KM", "40000"))

# Сколько дней модель (brand+model) считается "недавно опубликованной" —
# main_pipeline.py избегает повторно постить ту же модель в этот период,
# если есть альтернативы (см. candidate_diversity.filter_recent_models).
# 3 дня — при ~5-6 доступных hp<=160 моделях сейчас и 4 прогонах в сутки
# большее значение (например, 7) почти всегда исчерпывало бы все модели
# за 1-2 прогона, и откат "разрешить повтор" стал бы нормой, а не
# исключением. Можно увеличивать по мере роста model_specs.py.
RECENT_MODEL_DAYS: int = int(os.getenv("RECENT_MODEL_DAYS", "3"))

# --- Валидация поста перед публикацией (жёсткий код-гейт, см. post_validator.py) ---
# Детерминированная проверка, НЕ промпт-инструкция агенту — см. докстринг
# post_validator.py про то, почему код, а не LLM-промпт.
MIN_PHOTOS_PER_POST: int = int(os.getenv("MIN_PHOTOS_PER_POST", "3"))
# 2022-2023 — коарс-фильтр по ГОДУ производства (не модельному году сайта).
# Точная граница "прошло 3/5 лет" считается по МЕСЯЦУ в age_calculator.py
# (см. его докстринг) — этот диапазон лишь грубый предфильтр кандидатов,
# не подменяет точный расчёт таможенной категории.
MIN_YEAR: int = int(os.getenv("MIN_YEAR", "2022"))
MAX_YEAR: int = int(os.getenv("MAX_YEAR", "2023"))

# Максимально допустимая наценка объявления над заводской ценой той же
# комплектации (сейчас проверяется только для Encar, см. encar_scraper.
# fetch_origin_price_krw()/post_validator.check_price_sanity()) — 1.3 = до
# 30% наценки (обычная практика за срочную доступность без ожидания заказа)
# допустимо, выше — REJECT. Инцидент 2026-07: Chevrolet Trax по цене +95%
# от заводской — расчёт таможни был верным, а вот исходная цена — нет.
MAX_MARKUP_OVER_ORIGIN: float = float(os.getenv("MAX_MARKUP_OVER_ORIGIN", "1.3"))

# --- Заголовки для запросов к сайтам ---
# Это "представление" нашей программы сайту — мы говорим "я обычный браузер
# Chrome", чтобы сайт не отказывал нам в ответе. Без этого некоторые сайты
# блокируют запросы (или даже падают с ошибкой 500), если видят, что
# запрос не похож на настоящий браузер — слишком мало заголовков.
REQUEST_HEADERS: dict = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,ru;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def check_config() -> None:
    """
    Проверяет, что все обязательные настройки заданы, и если нет —
    сразу и понятно об этом сообщает (вместо непонятной ошибки где-то
    посреди работы программы).

    Вызывается один раз в самом начале main.py.
    """
    problems = []

    if not BOT_TOKEN:
        problems.append(
            "BOT_TOKEN не задан. Открой файл .env и впиши токен бота "
            "(получить у @BotFather в Telegram)."
        )

    if problems:
        print("❌ Проблемы с настройками:")
        for p in problems:
            print(f"   - {p}")
        print("\nСоздай файл .env на основе .env.example и заполни его.")
        raise SystemExit(1)
