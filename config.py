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
VIDEO_ENABLED: bool = os.getenv("VIDEO_ENABLED", "true").strip().lower() == "true"
# Видео не для каждого поста — раз в N успешных постов (см. video_state.py).
VIDEO_EVERY_N_POSTS: int = int(os.getenv("VIDEO_EVERY_N_POSTS", "3"))
# "same" — видео уходит вслед за альбомом фото в тот же информационный
# повод (Telegram: отдельным сообщением сразу после; VK: тем же постом,
# вложением рядом с фото). "separate" — отдельный самостоятельный пост
# только с видео (без повторного текста/фото).
VIDEO_POST_MODE: str = os.getenv("VIDEO_POST_MODE", "same").strip().lower()

# --- Reels/Shorts из видеоклипов (shorts_generator.py) ---
# ОТДЕЛЬНЫЙ путь от video_generator.py: тот собирает слайдшоу из ФОТО
# объявления, а shorts_generator.py монтирует вертикальный ролик из готовых
# ВИДЕОклипов (1-2 штуки) с озвучкой, синхронными субтитрами и фоновой
# музыкой. Разрешение фиксировано под Reels/Shorts (9:16).
SHORTS_ENABLED: bool = os.getenv("SHORTS_ENABLED", "false").strip().lower() == "true"
SHORTS_WIDTH: int = int(os.getenv("SHORTS_WIDTH", "1080"))
SHORTS_HEIGHT: int = int(os.getenv("SHORTS_HEIGHT", "1920"))
SHORTS_FPS: int = int(os.getenv("SHORTS_FPS", "30"))
# Диапазон длительности итогового ролика (сек). Если суммарная длина
# исходных клипов больше — обрезаем, меньше — зацикливаем последовательность
# до нижней границы (см. shorts_generator._target_duration()).
SHORTS_MIN_SEC: int = int(os.getenv("SHORTS_MIN_SEC", "30"))
SHORTS_MAX_SEC: int = int(os.getenv("SHORTS_MAX_SEC", "45"))
# Сколько слов показывать в одной строке субтитров (тайминги слов приходят
# из edge-tts, см. tts_generator.synthesize_with_word_timings()).
SHORTS_SUBTITLE_WORDS_PER_LINE: int = int(os.getenv("SHORTS_SUBTITLE_WORDS_PER_LINE", "4"))
# Папка для готовых роликов (по умолчанию — output/shorts рядом с проектом).
SHORTS_OUTPUT_DIR: str = os.getenv("SHORTS_OUTPUT_DIR", "output/shorts")

# --- Монтаж shorts_generator.py: аудио-дакинг, переходы, speed ramping, beat sync ---
# Голос и музыка ОДНОВРЕМЕННО: голос громче, музыка — фон, а не поверх речи
# (см. shorts_generator._mix_audio() — реальный баг был в том, что после
# конца ОЦЕНЁННОГО окна голоса музыка возвращалась на 100% на весь остаток
# 30-45-секундного ролика, см. video_generator._add_audio()).
SHORTS_VOICE_VOLUME: float = float(os.getenv("SHORTS_VOICE_VOLUME", "0.7"))
SHORTS_MUSIC_VOLUME_WITH_VOICE: float = float(os.getenv("SHORTS_MUSIC_VOLUME_WITH_VOICE", "0.3"))
# Без голоса — музыка не фон, а единственный звук, поэтому громче.
SHORTS_MUSIC_VOLUME_NO_VOICE: float = float(os.getenv("SHORTS_MUSIC_VOLUME_NO_VOICE", "0.6"))

# Разбивка склеенного видео на сегменты для переходов/speed ramping.
SHORTS_SEGMENT_MIN_SEC: float = float(os.getenv("SHORTS_SEGMENT_MIN_SEC", "2.0"))
SHORTS_SEGMENT_MAX_SEC: float = float(os.getenv("SHORTS_SEGMENT_MAX_SEC", "4.0"))

# Speed ramping: доля сегментов, которые ускоряются (не все — иначе теряется
# ощущение ритма, эффект работает только на КОНТРАСТЕ обычной и ускоренной
# скорости), и диапазон множителя скорости.
SHORTS_SPEEDUP_SHARE: float = float(os.getenv("SHORTS_SPEEDUP_SHARE", "0.4"))
SHORTS_SPEEDUP_MIN: float = float(os.getenv("SHORTS_SPEEDUP_MIN", "1.3"))
SHORTS_SPEEDUP_MAX: float = float(os.getenv("SHORTS_SPEEDUP_MAX", "1.8"))

# Переходы между сегментами (xfade) — длительность.
SHORTS_TRANSITION_MIN_SEC: float = float(os.getenv("SHORTS_TRANSITION_MIN_SEC", "0.3"))
SHORTS_TRANSITION_MAX_SEC: float = float(os.getenv("SHORTS_TRANSITION_MAX_SEC", "0.5"))

# --- Beat sync (shorts_generator._detect_beats()) ---
# librosa НЕ используется (тяжёлая цепочка зависимостей — numba/audioread/
# soundfile, а весь проект и так на голом ffmpeg) — вместо полноценного
# beat-tracking лёгкий RMS-анализ через pydub: энергия по окнам
# SHORTS_BEAT_WINDOW_MS, локальные пики выше среднего в SHORTS_BEAT_RMS_FACTOR
# раз, не ближе SHORTS_BEAT_MIN_GAP_SEC друг к другу. Если pydub не
# установлен или анализ не удался — beat sync тихо отключается, монтаж
# режет по обычному плану длительностей (см. докстринг _detect_beats()).
SHORTS_BEAT_WINDOW_MS: int = int(os.getenv("SHORTS_BEAT_WINDOW_MS", "50"))
SHORTS_BEAT_RMS_FACTOR: float = float(os.getenv("SHORTS_BEAT_RMS_FACTOR", "1.3"))
SHORTS_BEAT_MIN_GAP_SEC: float = float(os.getenv("SHORTS_BEAT_MIN_GAP_SEC", "0.25"))

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
# Количество вариантов озвучки (разные SYSTEM_PROMPT'ы, но идентичные факты).
# Выбор детерминированный по hash(car_id) % MAX_VOICEOVER_VARIANTS.
MAX_VOICEOVER_VARIANTS: int = 5
# Ударения в марках для TTS (U+0301 = COMBINING ACUTE ACCENT).
# Применяется к тексту озвучки перед синтезом речи.
# Для Volkswagen используется вариант с дефисом (edge-tts лучше парсит ударение).
BRAND_PRONUNCIATION: dict = {
    "Kia": "Ки́а",                 # Киа (ударение на И)
    "Hyundai": "Хёнда́й",         # Хендай (ударение на А)
    "Volkswagen": "Фолькс-ВА-ген", # Фольксваген (дефис+капс ВА для явного ударения)
    "Audi": "А́уди",               # Ауди (ударение на А)
    "BMW": "БМВ",                 # БМВ (читается по буквам, ударения нет)
    "Mercedes": "Мерседе́с",       # Мерседес (ударение на Е)
    "Toyota": "То́йота",           # Тойота (ударение на О)
    "Honda": "Хонда́",             # Хонда (ударение на А)
}

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

# --- Сценарии роликов «Разбор лота» (lot_reel_script.py) ---
# Новая фича — за флагом, по умолчанию выключена. SCRIPT_DRY_RUN=true (тоже
# по умолчанию): сценарий только генерируется и пишется в лог, хук НЕ
# записывается в lot_reel_hooks.jsonl, ничего никуда не публикуется.
SCRIPT_ENABLED: bool = os.getenv("SCRIPT_ENABLED", "false").strip().lower() == "true"
SCRIPT_DRY_RUN: bool = os.getenv("SCRIPT_DRY_RUN", "true").strip().lower() == "true"
# Сколько последних хуков из лога передавать в previous_hooks.
SCRIPT_PREVIOUS_HOOKS_N: int = int(os.getenv("SCRIPT_PREVIOUS_HOOKS_N", "10"))
# Пакетный режим (lot_reel_script_batch.py): лотов в одном запросе к API.
SCRIPT_BATCH_MAX: int = int(os.getenv("SCRIPT_BATCH_MAX", "8"))

# --- build_video.py (ролик по лоту: publish_lot.py) ---
# Папка готовых роликов (в .gitignore): output/<lot_id>.mp4
VIDEO_OUTPUT_DIR: str = os.getenv("VIDEO_OUTPUT_DIR", "output")
# Лёгкий наезд, % (не больше 5): фото растёт от (100-N)% до 100% рамки — края не обрезаются.
BUILD_VIDEO_ZOOM_PCT: float = float(os.getenv("BUILD_VIDEO_ZOOM_PCT", "3"))
# Длина ролика БЕЗ голосовой записи, сек (в итоге зажата в 20-30); с записью длина = длине записи.
BUILD_VIDEO_TARGET_SEC: float = float(os.getenv("BUILD_VIDEO_TARGET_SEC", "25"))
# Громкость музыки, LUFS: когда она единственный звук / когда подложена под голос (голос -16 LUFS).
BUILD_VIDEO_MUSIC_LUFS: int = int(os.getenv("BUILD_VIDEO_MUSIC_LUFS", "-26"))
BUILD_VIDEO_MUSIC_UNDER_VOICE_LUFS: int = int(os.getenv("BUILD_VIDEO_MUSIC_UNDER_VOICE_LUFS", "-38"))

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
# По умолчанию с 2026-10-08: 320 000 + 100 000 + 150 000 = 570 000 ₽ (значения из .env и секретов GitHub
# Actions перекрывают их — менять там же).
LOGISTICS_RUB: float = float(os.getenv("LOGISTICS_RUB", "320000"))
BROKER_FEE_RUB: float = float(os.getenv("BROKER_FEE_RUB", "100000"))
MARGIN_RUB: float = float(os.getenv("MARGIN_RUB", "150000"))

# Комиссия банка за перевод оплаты за саму машину продавцу в Корее — берётся
# ТОЛЬКО от цены авто (не от таможни/доставки/брокера/маржи), но от цены УЖЕ
# с корейской надбавкой (см. EXCHANGE_SPREAD_PCT и post_builder.calculate_price_details()).
# 0.5% с 2026-10-07 (раньше 2.5%). ВАЖНО: значение, заданное в .env и в секрете
# GitHub Actions BANK_COMMISSION_PCT, перекрывает этот default — обновлять там же.
BANK_COMMISSION_PCT: float = float(os.getenv("BANK_COMMISSION_PCT", "0.005"))

# Корейская надбавка к цене машины — доля (не проценты!), которая прибавляется к
# цене авто, пересчитанной по курсу ЦБ. С 2026-10-07 это не только «спред по воне»,
# а всё, что платится на корейской стороне сверх цены: процент аукциона, экспортные
# документы и банковский спред на KRW (типично 14%: при ЦБ 1₽≈16,2₩ эффективный
# курс 1₽≈14,2₩). Банковский ПЕРЕВОД (BANK_COMMISSION_PCT) и логистика — отдельные
# статьи. Таможенные платежи (ЕТС, сбор, утильсбор) считаются по ОФИЦИАЛЬНОМУ курсу
# ЦБ РФ (закон) и надбавку не включают. Формула — pricing.calculate_price().
# 0.0 = надбавка не учитывается. Старое имя переменной EXCHANGE_SPREAD_PCT
# поддерживается (если KOREA_COSTS_PCT не задана).
KOREA_COSTS_PCT: float = float(os.getenv("KOREA_COSTS_PCT", os.getenv("EXCHANGE_SPREAD_PCT", "0.14")))
EXCHANGE_SPREAD_PCT: float = KOREA_COSTS_PCT  # устаревшее имя: оставлено, чтобы старый код не сломался

# Фиксированный курс ПОКУПКИ воны, ₩ за 1 ₽ (например 15.2). Если задан, цена машины считается как
# цена в ₩ / KRW_PER_RUB (вместо «курс ЦБ × (1 + KOREA_COSTS_PCT)»), KOREA_COSTS_PCT при этом не
# применяется; таможня по-прежнему по ЦБ. По умолчанию (переменная не задана) — 15,2 ₩/₽ с 2026-10-08.
# Явно пустая переменная KRW_PER_RUB= или 0 = режим «ЦБ + надбавка».
# Режим нужен, чтобы воспроизводить цены, посчитанные по реальному курсу покупки владельца
# (контрольный лот Kia Ray Signature: 15,2 ₩/₽ и комиссия 2,5% дают 1 533 901 ₽ при ставке 12 млн ₩).
_KRW_PER_RUB_RAW = os.getenv("KRW_PER_RUB", "15.2").strip()
KRW_PER_RUB: float | None = float(_KRW_PER_RUB_RAW) if _KRW_PER_RUB_RAW and float(_KRW_PER_RUB_RAW) > 0 else None
# Когда курс покупки обновляли в последний раз (YYYY-MM-DD). При смене KRW_PER_RUB обновляйте и дату:
# старше KRW_RATE_MAX_AGE_DAYS дней -> pricing.calculate_price() добавляет предупреждение в результат.
KRW_PER_RUB_UPDATED: str = os.getenv("KRW_PER_RUB_UPDATED", "2026-10-08").strip()
KRW_RATE_MAX_AGE_DAYS: int = int(os.getenv("KRW_RATE_MAX_AGE_DAYS", "7"))

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
# 2 дня: при текущих ~6 реально publishable моделях (Encar мёртв,
# kbchachacha targeted в отрицательном кэше) блокировка на 5 дней
# математически гарантировала пустые прогоны — 5 из 6 моделей заблокированы,
# единственная оставшаяся не покрывала POST_LIMIT. Когда Encar вернётся
# и kbchachacha targeted заработает (25+ моделей), можно поднять обратно.
RECENT_MODEL_DAYS: int = int(os.getenv("RECENT_MODEL_DAYS", "2"))

# Та же марка — не больше MAX_SAME_BRAND_IN_A_ROW постов ПОДРЯД (даже
# разными моделями — напр. Hyundai Avante, потом Hyundai Casper, потом
# Hyundai Kona это 3 Hyundai подряд) — см.
# candidate_diversity.limit_consecutive_brand(). Считается по фактической
# истории публикаций (published_log.json), не только в рамках одного
# прогона — иначе прогон, начинающийся сразу после марки-серии из
# предыдущего прогона, мог бы продолжить ту же серию.
MAX_SAME_BRAND_IN_A_ROW: int = int(os.getenv("MAX_SAME_BRAND_IN_A_ROW", "2"))

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

# --- Групповой (batch) ценовой гейт (post_validator.check_price_vs_batch_median()) ---
# В отличие от check_price_vs_new_krw() (справочник model_specs.ModelSpec.
# new_price_max_krw заполнен только для 1 из 99 моделей — для остальных 98
# только WARNING в лог, публикация не блокируется) — этот гейт не требует
# справочника вообще: эталон "нормальной" цены строится из самой партии
# лотов этой же модели в ТЕКУЩЕМ прогоне парсинга (см. target_collector.py/
# broad_collector.py). Добавлено 2026-07-21 после разбора инцидента Kia K5
# (encar #42247766, 37.9М ₩) — доказательство, что абсолютный справочный
# гейт закрывает только те модели, для которых кто-то вручную вбил цену.
BATCH_MEDIAN_FACTOR: float = float(os.getenv("BATCH_MEDIAN_FACTOR", "1.3"))
# Меньше этого числа priced-лотов одной модели в партии — медиане не
# доверяем (1-2 точки — не выборка, а совпадение) — ни один лот из такой
# группы не блокируется этим гейтом.
MIN_BATCH_N: int = int(os.getenv("MIN_BATCH_N", "5"))

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

# --- Очередь Яндекс-черновиков (yandex_queue.py, main_pipeline.py) ---
# Лидоген публикует лоты чаще, чем нужно слать Яндекс-черновики — main_pipeline.py
# после каждой успешной публикации кладёт лот в очередь (yandex_queue.json),
# а отдельным шагом в конце прогона забирает из неё максимум ОДИН лот раз в
# YANDEX_MIN_INTERVAL_DAYS дней (не чаще).
YANDEX_MIN_INTERVAL_DAYS: int = int(os.getenv("YANDEX_MIN_INTERVAL_DAYS", "2"))
# Лот в очереди старше этого — считается протухшим (цена/наличие могли
# устареть) и выкидывается БЕЗ отправки, не гадаем.
YANDEX_QUEUE_MAX_AGE_DAYS: int = int(os.getenv("YANDEX_QUEUE_MAX_AGE_DAYS", "7"))
# Модель, уже опубликованная в Яндекс за последние N дней, — не приоритетна
# при выборе следующего лота из очереди (см. yandex_queue.pick_best_lot()) —
# тот же принцип "предпочесть разнообразие, но не блокировать совсем", что и
# у candidate_diversity.filter_recent_models() для лидогена.
YANDEX_RECENT_MODEL_DAYS: int = int(os.getenv("YANDEX_RECENT_MODEL_DAYS", "14"))

# --- Дайджест Яндекс-витрины (yandex_digest_builder.py) ---
# ОТДЕЛЬНЫЙ режим от одиночного черновика выше (yandex_queue.py) — раз в
# сутки собирает несколько лучших лотов, опубликованных за последние N часов,
# в один пост-подборку. По умолчанию выключен локально — включается явно в
# workflow (см. .github/workflows/yandex_digest.yml), тот же приём, что у
# VIDEO_ENABLED в main_pipeline.yml.
YANDEX_DIGEST_ENABLED: bool = os.getenv("YANDEX_DIGEST_ENABLED", "false").strip().lower() == "true"

# Окно отбора лотов: сначала последние YANDEX_DIGEST_WINDOW_HOURS часов;
# если лотов меньше YANDEX_DIGEST_MIN_FOR_WINDOW — расширяем до
# YANDEX_DIGEST_FALLBACK_WINDOW_HOURS. Если и там лотов меньше
# YANDEX_DIGEST_MIN_LOTS — дайджест в этот день не строится (см.
# yandex_digest_builder.select_digest_lots()), не гадаем на 1 лоте.
YANDEX_DIGEST_WINDOW_HOURS: int = int(os.getenv("YANDEX_DIGEST_WINDOW_HOURS", "24"))
YANDEX_DIGEST_FALLBACK_WINDOW_HOURS: int = int(os.getenv("YANDEX_DIGEST_FALLBACK_WINDOW_HOURS", "48"))
YANDEX_DIGEST_MIN_FOR_WINDOW: int = int(os.getenv("YANDEX_DIGEST_MIN_FOR_WINDOW", "3"))
YANDEX_DIGEST_MIN_LOTS: int = int(os.getenv("YANDEX_DIGEST_MIN_LOTS", "2"))
# По ТЗ — "3-5 лотов", но по 1 фото на лот и жёсткий лимит Яндекс Бизнес
# в 4 фото на публикацию (см. YANDEX_DIGEST_MAX_PHOTOS ниже) — 5 лотов
# означали бы либо превышение лимита фото, либо лот без фото в подборке.
# Верхнюю границу синхронизировал с реальным лимитом площадки: 4, не 5.
YANDEX_DIGEST_MAX_LOTS: int = int(os.getenv("YANDEX_DIGEST_MAX_LOTS", "4"))

# Лот старше этого в пуле (yandex_digest_pool.json) считается неактуальным
# для дайджеста и вычищается при следующей записи — пул иначе рос бы вечно
# (в отличие от yandex_queue.json, отсюда лоты НЕ удаляются при выборке в
# дайджест, только по возрасту — один лот может попасть в несколько разных
# дайджестов подряд идущих дней, если каждый раз остаётся в окне).
YANDEX_DIGEST_POOL_MAX_AGE_DAYS: int = int(os.getenv("YANDEX_DIGEST_POOL_MAX_AGE_DAYS", "4"))

# Лимиты формата Яндекс Бизнес (https://yandex.ru/support/business-priority/ru/manage/publications,
# проверено 2026-07-23): текст публикации — до 3000 символов с пробелами,
# фото — не больше 4 на публикацию (320x160..5000x5000 px). Уточнить, если
# Яндекс изменит лимиты — единственное место, где они захардкожены.
YANDEX_DIGEST_MAX_CHARS: int = int(os.getenv("YANDEX_DIGEST_MAX_CHARS", "3000"))
YANDEX_DIGEST_MAX_PHOTOS: int = int(os.getenv("YANDEX_DIGEST_MAX_PHOTOS", "4"))


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
