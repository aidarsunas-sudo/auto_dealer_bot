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

# --- Видео (video_generator.py) ---
# Ролик — необязательное дополнение к посту (текст+фото важнее и не
# зависят от него), поэтому по умолчанию ВЫКЛЮЧЕНО явно (в отличие от
# VK_ENABLED) — генерация требует ffmpeg и заметно удлиняет прогон
# (см. video_generator.py, ~1-2 мин на ролик).
VIDEO_ENABLED: bool = os.getenv("VIDEO_ENABLED", "false").strip().lower() == "true"
# Видео не для каждого поста — раз в N успешных постов (см. video_state.py).
VIDEO_EVERY_N_POSTS: int = int(os.getenv("VIDEO_EVERY_N_POSTS", "3"))
# "same" — видео уходит вслед за альбомом фото в тот же информационный
# повод (Telegram: отдельным сообщением сразу после; VK: тем же постом,
# вложением рядом с фото). "separate" — отдельный самостоятельный пост
# только с видео (без повторного текста/фото).
VIDEO_POST_MODE: str = os.getenv("VIDEO_POST_MODE", "same").strip().lower()

# --- Озвучка (tts_generator.py) ---
# ОТДЕЛЬНО от VIDEO_ENABLED — можно включить видео без голоса (только Ken
# Burns + титры + музыка) или наоборот держать озвучку выключенной, пока
# не понравится ни один голос. Если VOICE_ENABLED=true, а VIDEO_ENABLED=
# false — озвучка просто не используется (нечего озвучивать).
VOICE_ENABLED: bool = os.getenv("VOICE_ENABLED", "false").strip().lower() == "true"
# "edge" (по умолчанию, нужен интернет) | "silero" (офлайн, нужен
# pip install torch — тяжёлая необязательная зависимость). Если основной
# движок недоступен — tts_generator.py сам пробует другой, см. его докстринг.
TTS_ENGINE: str = os.getenv("TTS_ENGINE", "edge").strip().lower()
# ru-RU-DmitryNeural (мужской) | ru-RU-SvetlanaNeural (женский).
TTS_VOICE: str = os.getenv("TTS_VOICE", "ru-RU-DmitryNeural")

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

# --- Устойчивость VK photo upload (vk_publisher.send_to_vk()) ---
# ОТДЕЛЬНО от MIN_PHOTOS_PER_POST выше (тот — гейт ДО публикации, на
# основе исходного car["photos"]). Этот порог — на этапе ЗАГРУЗКИ в VK:
# если отдельные фото не прикрепились после ретраев (VK-сторона иногда
# единично спотыкается на валидном файле — см. vk_publisher._upload_one_photo()
# докстринг, инцидент 2026-07-17, Chevrolet Trax 1.2 ACTIV), пост всё равно
# публикуется с тем, что получилось, если итоговых фото хватает — полный
# отказ только если успешных меньше порога. Тот же дефолт, что у
# MIN_PHOTOS_PER_POST (3) — можно развести значения независимо в .env.
MIN_PHOTOS_FOR_POST: int = int(os.getenv("MIN_PHOTOS_FOR_POST", "3"))

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

# --- Абсолютный ценовой гейт (post_validator.check_price_absolute()) ---
# Широкий сквозной "здравый смысл" диапазон для ИТОГОВОЙ цены под ключ —
# не заменяет проверку MAX_MARKUP_OVER_ORIGIN (та сверяет цену ОБЪЯВЛЕНИЯ с
# заводской, только для Encar), а ловит любой другой сбой ниже по цепочке
# (некорректный курс, баг расчёта таможни и т.п.) — независимо от источника
# и от того, доступна ли заводская цена для сверки. Добавлено 2026-07-17
# после инцидента: пост Chevrolet Trax ~5 млн ₽ ушёл в канал, хотя это
# заведомо нереальная цена для бюджетного авто под льготный утильсбор (см.
# FINAL_STATE.md/аудит). ВАЖНО: этот диапазон один на все модели — он не
# ловит "разумную, но неправильную для КОНКРЕТНОЙ модели" цену (5 млн для
# Trax был бы формально внутри 800 000-6 000 000) — за это отвечает более
# узкий per-модельный model_specs.ModelSpec.price_range_rub, см. его.
PRICE_MIN_RUB: int = int(os.getenv("PRICE_MIN_RUB", "800000"))
PRICE_MAX_RUB: int = int(os.getenv("PRICE_MAX_RUB", "6000000"))

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

# --- Яндекс/Дзен-витрина (yandex_post_builder.py/yandex_publisher.py) ---
# ОТДЕЛЬНЫЙ поток контента от лидогена (lead_post_builder.py/
# price_alert_builder.py, TELEGRAM_CHAT_ID) — посты про СВОИ лоты дилера
# (in_stock/under_order), не про чужие объявления. У Яндекс Карт нет API
# публикации — тексты уходят человеку на копирование в служебный чат, НЕ
# в публичный канал.
#
# По умолчанию = ADMIN_CHAT_ID (тот же личный чат, что уже используется для
# уведомлений об ошибках) — отдельно заводить ещё один чат не обязательно,
# но можно переопределить в .env, если понадобится именно свой.
YANDEX_DRAFTS_CHAT_ID: str = os.getenv("YANDEX_DRAFTS_CHAT_ID", ADMIN_CHAT_ID)

# Срок поставки под заказ (Корея -> Владивосток -> автовоз -> Казань),
# показывается диапазоном в under_order-шаблоне — единственное поле, где
# диапазон разрешён валидатором (пробег и цена — только точным числом).
# 45-60 дней — реальный срок с учётом доставки до Владивостока и автовоза
# (30-45 было слишком оптимистично), поправить здесь при изменении логистики.
DELIVERY_DAYS_MIN: int = int(os.getenv("DELIVERY_DAYS_MIN", "45"))
DELIVERY_DAYS_MAX: int = int(os.getenv("DELIVERY_DAYS_MAX", "60"))


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
