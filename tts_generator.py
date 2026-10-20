# -*- coding: utf-8 -*-
"""
tts_generator.py — озвучка для роликов video_generator.py: короткий текст
подводки продавца (генерируется через Claude API из фактов об объявлении)
+ синтез речи (edge-tts основной движок, Silero — офлайн-фолбэк).

Архитектура с переключением (config.TTS_ENGINE):
    "edge"   (по умолчанию) — edge-tts, требует интернет, качество хорошее,
             бесплатно (использует голоса Microsoft Edge "как сервис", без
             самого браузера).
    "silero" — офлайн нейросетевой TTS (torch), работает без интернета,
             но требует pip install torch (тяжёлая зависимость, не
             установлена по умолчанию — см. requirements.txt).

Если основной движок не сработал (нет интернета для edge, ошибка API) —
пробуем ЛЮБОЙ другой доступный, а не валим генерацию ролика целиком. Если
ни один не сработал (или TTS_ENABLED=false) — video_generator.py собирает
ролик без озвучки, только с warning в лог (см. agent_publisher.py).

Текст для озвучки — ОТДЕЛЬНЫЙ от текста поста (post_text_builder.py):
короче, разговорнее, числа словами (TTS читает вслух, не глазами), без
эмодзи/markdown, иностранные модели транслитом кириллицей — то есть то же
требование к фактической точности (числа/характеристики только из fields,
ничего не выдумывается), что и в archive/post_generator.py, но другой
стиль вывода.
"""

import asyncio
import hashlib
import os
import time
import logging
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger("tts_generator")

CLAUDE_MODEL = "claude-opus-4-8"

# ru-RU-DmitryNeural (мужской) | ru-RU-SvetlanaNeural (женский) — выбор
# зафиксирован в config.TTS_VOICE после прослушивания обоих вариантов.
EDGE_RATE = "+10%"  # чуть выше обычной скорости, см. ТЗ
# Сервис Edge-TTS нестабилен: примерно каждый второй запрос отвечает «No audio was received» (воспроизведено
# 2026-10-08, текст при этом корректный) — поэтому синтез повторяется, с паузой и запасным голосом.
EDGE_ATTEMPTS = 4
EDGE_RETRY_PAUSE_SEC = 2.0

# 5 вариантов системных промптов с разными зацепками, но идентичные факты.
# Выбор детерминированный по hash(car_id) % len(SYSTEM_PROMPTS).
SYSTEM_PROMPTS = [
    # Вариант 0: стандартный — дружелюбная подводка
    """Ты — голос продавца автомобильного канала Pick+ Auto \
(доставка авто из Кореи в Казань). Пишешь короткий текст для ОЗВУЧКИ \
видео-ролика об одном конкретном автомобиле — его будет читать TTS-\
синтезатор, а не показывать текстом.

Правила:
- Используй ТОЛЬКО факты, которые тебе передали в сообщении — ничего не \
придумывай и не добавляй от себя (характеристики, комплектацию, цену).
- Стиль — живая, дружелюбная подводка продавца, как будто говоришь с \
человеком, а не зачитываешь рекламный текст.
- Обязательно включи: модель и год, 2-3 главных плюса (сформулируй их из \
переданных характеристик — экономичный двигатель, небольшой пробег, год \
выпуска и т.п. — НЕ выдумывая новых фактов сверх переданных), пробег, \
цену под ключ.
- Заверши фразой в духе "Подробности в Пикплюс Авто" (можно перефразировать \
естественнее для устной речи, смысл не менять).
- ЧИСЛА пиши СЛОВАМИ, а не цифрами — это будет читать TTS-синтезатор: \
"один миллион двести тысяч рублей", "тридцать девять тысяч пятьсот \
километров", НЕ "1 200 000 ₽" и НЕ "39 500 км".
- Иностранные названия моделей — транслитом кириллицей, как их произносят \
по-русски ("Kia Sportage" -> "Киа Спортейдж", "Hyundai Sonata" -> "Хёндай \
Соната"), чтобы TTS прочитал правильно, а не побуквенно на английском.
- БЕЗ эмодзи, markdown, кавычек — чистый текст под озвучку.
- Пиши только сам текст, без вступлений вроде "Вот текст:" и пояснений после.""",

    # Вариант 1: энергичный тон с восклицанием
    """Ты — воодушевлённый голос продавца канала Pick+ Auto. Пишешь \
короткий текст для ОЗВУЧКИ видео-ролика об автомобиле.

Правила:
- ТОЛЬКО факты из переданных данных, ничего не придумывай.
- Тон — энергичный, немного восторженный, как будто ты сам влюблён в эту машину.
- Обязательно: модель, год, 2-3 ключевых плюса из характеристик, пробег, цена.
- Заверши словами в духе "Жди её в Пикплюс Авто!" или аналогично.
- ЧИСЛА словами, без цифр.
- Иностранные названия — русским транслитом.
- Чистый текст, без markdown/эмодзи.
- Только сам текст, без вступлений.""",

    # Вариант 2: вопросительный тон
    """Ты — консультант канала Pick+ Auto. Обращаешься к слушателю \
как к другу, задаёшь вопросы. Пишешь короткий текст озвучки.

Правила:
- Используй факты из сообщения — фантазировать нельзя.
- Стиль — естественный диалог: "А что, если я тебе расскажу про..."
- Включи: модель, год, 2-3 преимущества (из переданных характеристик), \
пробег, цену.
- Заверши приглашением: "Узнай подробнее на Пикплюс Авто" или подобно.
- ЧИСЛА словами.
- Иностранные названия — по-русски.
- Чистый текст, без разметки.
- Только текст озвучки.""",

    # Вариант 3: спокойный, информационный
    """Ты — профессиональный менеджер канала Pick+ Auto. Описываешь \
автомобиль в спокойном, уверенном тоне.

Правила:
- Факты только из переданных данных.
- Тон — деловой, но тёплый, без излишнего пафоса.
- Обязательно назови: модель, год, основные характеристики-плюсы (2-3 из \
переданных), пробег, цена под ключ.
- Закончи: "Подробнее — на сайте Пикплюс Авто" или аналогично.
- ЧИСЛА словами.
- Иностранные названия — русский транслит.
- Без эмодзи и markdown.
- Чистый текст.""",

    # Вариант 4: "пакет выгод" — объявительский стиль
    """Ты — голос объявления на канале Pick+ Auto. Подаёшь автомобиль \
как готовый пакет выгод для покупателя.

Правила:
- Только переданные факты, никакой импровизации.
- Подчеркивай преимущества: "Вот что ты получаешь: модель, год, и главное — ..."
- Назови 2-3 главных плюса, пробег, цену.
- Заверши: "Жди доставки через Пикплюс Авто!" или похоже.
- ЧИСЛА словами.
- Иностранные названия — русским транслитом.
- Текст под озвучку, без markdown и эмодзи.
- Только сам текст озвучки.""",
]


def _get_system_prompt_for_car(car_id: str) -> str:
    """
    Выбирает детерминированный вариант SYSTEM_PROMPT по car_id.
    Один и тот же car_id всегда вернёт один и тот же промпт.
    Разные car_id → разные промпты (если их хеши дают разные остатки).
    """
    variant_idx = int(hashlib.md5(car_id.encode()).hexdigest(), 16) % len(SYSTEM_PROMPTS)
    return SYSTEM_PROMPTS[variant_idx]


def apply_brand_pronunciation(text: str) -> str:
    """
    Применяет ударения из config.BRAND_PRONUNCIATION к тексту озвучки.

    config.BRAND_PRONUNCIATION содержит пары: английское название → русское с ударением.
    Функция ищет русский транслит (например, "Киа", "Хёндай") и заменяет на версию
    с ударением (Ки́а, Хёнда́й).

    Например:
      "Киа Кей Три 2023" + {"Kia": "Ки́а"} →
      "Ки́а Кей Три 2023"

    Работает для текстов, где Claude уже транслитерировал марки в кириллицу.
    """
    result = text
    # BRAND_PRONUNCIATION хранит пары "English" → "Русский с ударением"
    # Нам нужно найти в тексте русский транслит и заменить на версию с ударением.
    # Но просто по словарю сложно понять, какой русский вариант соответствует какому английскому,
    # поэтому строим обратное отображение: русский_без_ударения → русский_с_ударением.

    # Словарь типичных транслитераций:
    # Для Volkswagen используем "Фолькс-ВА-ген" (дефис + капс помогает edge-tts)
    transliterations = {
        "Киа": "Ки́а",
        "Хёндай": "Хёнда́й",
        "Фольксваген": "Фолькс-ВА-ген",
        "Ауди": "А́уди",
        "БМВ": "БМВ",  # читается по буквам, ударения нет
        "Мерседес": "Мерседе́с",
        "Тойота": "То́йота",
        "Хонда": "Хонда́",
    }

    for russian_no_stress, russian_with_stress in transliterations.items():
        result = result.replace(russian_no_stress, russian_with_stress)

    return result


def _build_user_prompt(fields: dict, min_words: int, max_words: int, duration_sec: int) -> str:
    # ВАЖНО: марка+модель ОТДЕЛЬНО от trim/engine_display — fields["title"]
    # включает объём двигателя в само название (например "Kia K3 1.6",
    # см. post_text_builder._build_title()), и если отдать его Claude как
    # единую "модель", число "1.6" транслитерируется вместе с названием
    # ("Кей Три тысяча шестьсот" вместо отдельно "Киа Кей Три" + отдельно
    # "мотор 1.6 л" ниже) — баг найден и исправлен 2026-07-17.
    model_name = f"{fields.get('brand', '')} {fields.get('model', '')}".strip()
    facts = [
        f"Марка и модель (транслитерируй ТОЛЬКО это, без объёма двигателя): {model_name}",
        f"Год выпуска: {fields.get('year', '')}",
        f"Двигатель: {fields.get('engine_display', '')}",
        f"Пробег: {fields.get('mileage_km', '')}",
        f"КПП: {fields.get('transmission', '')}",
        f"{fields.get('price_label', 'Стоимость под ключ')}: {fields.get('total_rub', '')} рублей",
    ]
    return (
        f"Напиши текст подводки под озвучку ролика длительностью около {duration_sec} секунд "
        f"({min_words}-{max_words} слов):\n\n" + "\n".join(f"- {f}" for f in facts)
    )


def generate_voiceover_text(fields: dict, min_words: int = 40, max_words: int = 60,
                             duration_sec: int = 25) -> Optional[str]:
    """
    Генерирует текст подводки через Claude API. При любой проблеме (нет
    ключа, библиотека не установлена, сбой сети/API) возвращает None —
    вызывающий код (video_generator.py) просто пропускает озвучку, не
    ломает сборку ролика. Тот же принцип отказоустойчивости, что у
    archive/post_generator.py.

    Выбор SYSTEM_PROMPT детерминированный по car_id: один и тот же лот
    всегда получает одну озвучку, но разные лоты получают разные варианты.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.info("tts_generator: ANTHROPIC_API_KEY не задан — текст для озвучки не сгенерирован")
        return None

    try:
        import anthropic  # лениво — см. archive/post_generator.py, тот же приём

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        user_prompt = _build_user_prompt(fields, min_words, max_words, duration_sec)
        car_id = fields.get("car_id", "")
        system_prompt = _get_system_prompt_for_car(car_id)
        variant_idx = int(hashlib.md5(car_id.encode()).hexdigest(), 16) % len(SYSTEM_PROMPTS)

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        logger.info(f"tts_generator: текст подводки сгенерирован (вариант {variant_idx}, "
                    f"{len(text.split())} слов, {response.usage.input_tokens} вх. + "
                    f"{response.usage.output_tokens} исх. токенов)")
        return text

    except ImportError:
        logger.warning("tts_generator: библиотека 'anthropic' не установлена (pip install anthropic) — "
                        "текст для озвучки не сгенерирован")
        return None
    except Exception as error:
        logger.warning(f"tts_generator: ошибка Claude API ({error}) — текст для озвучки не сгенерирован")
        return None


def _edge_voices() -> list:
    """Голос по порядку попыток: основной (config.TTS_VOICE), с 3-й попытки — запасной женский."""
    fallback = os.getenv("TTS_VOICE_FALLBACK", "ru-RU-SvetlanaNeural")
    return [config.TTS_VOICE, config.TTS_VOICE] + [fallback] * max(EDGE_ATTEMPTS - 2, 0)


def _synthesize_edge(text: str, output_path: Path, sleep=time.sleep) -> bool:
    try:
        import edge_tts
    except ImportError:
        logger.warning("tts_generator: библиотека 'edge-tts' не установлена (pip install edge-tts)")
        return False

    # Применяем ударения перед синтезом
    text_with_stress = apply_brand_pronunciation(text)

    for attempt, voice in enumerate(_edge_voices()[:EDGE_ATTEMPTS], start=1):
        async def _run():
            communicate = edge_tts.Communicate(text_with_stress, voice, rate=EDGE_RATE)
            await communicate.save(str(output_path))

        try:
            asyncio.run(_run())
            if output_path.exists() and output_path.stat().st_size > 0:
                if attempt > 1:
                    logger.info(f"tts_generator: edge-tts сработал с попытки {attempt} (голос {voice})")
                return True
            logger.warning(f"tts_generator: edge-tts вернул пустой файл (попытка {attempt}/{EDGE_ATTEMPTS})")
        except Exception as error:
            logger.warning(f"tts_generator: edge-tts не смог синтезировать речь "
                           f"(попытка {attempt}/{EDGE_ATTEMPTS}, голос {voice}): {error}")
        if attempt < EDGE_ATTEMPTS:
            sleep(EDGE_RETRY_PAUSE_SEC * attempt)
    return False


def synthesize_with_word_timings(text: str, output_path: Path) -> Optional[list]:
    """
    Синтез речи через edge-tts С ЗАХВАТОМ ТАЙМИНГОВ СЛОВ — для синхронных
    субтитров в shorts_generator.py.

    Обычный synthesize_voiceover()/_synthesize_edge() зовёт communicate.save(),
    который ВЫБРАСЫВАЕТ события WordBoundary. Здесь вместо save() используется
    communicate.stream(): аудио-чанки пишем в файл сами, а из чанков типа
    "WordBoundary" собираем (start_sec, end_sec, word). edge-tts отдаёт offset
    и duration в 100-нс единицах (10_000_000 = 1 сек).

    Возвращает список словарей [{"start": float, "end": float, "word": str}, ...]
    при успехе, либо None при любом сбое (нет edge-tts, нет интернета, пустой
    результат) — вызывающий код тогда собирает ролик без субтитров/голоса, не
    падает (тот же принцип отказоустойчивости, что и во всём модуле).

    ВАЖНО: тайминги относятся к тексту ПОСЛЕ apply_brand_pronunciation() — то
    есть к реально произнесённым словам (напр. "Фолькс-ВА-ген"), поэтому и в
    субтитрах показываем этот же вариант, а не исходный, чтобы слова совпадали
    со звуком.
    """
    if not text:
        return None
    try:
        import edge_tts
    except ImportError:
        logger.warning("tts_generator: библиотека 'edge-tts' не установлена — тайминги слов недоступны")
        return None

    text_with_stress = apply_brand_pronunciation(text)
    timings: list = []

    async def _run(voice: str) -> bool:
        timings.clear()
        communicate = edge_tts.Communicate(text_with_stress, voice, rate=EDGE_RATE)
        wrote_audio = False
        with open(output_path, "wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                    wrote_audio = True
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 10_000_000
                    end = (chunk["offset"] + chunk["duration"]) / 10_000_000
                    timings.append({"start": start, "end": end, "word": chunk["text"]})
        return wrote_audio

    wrote_audio = False
    for attempt, voice in enumerate(_edge_voices()[:EDGE_ATTEMPTS], start=1):
        try:
            wrote_audio = asyncio.run(_run(voice))
            if wrote_audio and output_path.exists() and output_path.stat().st_size > 0:
                break
            logger.warning(f"tts_generator: edge-tts stream вернул пустой файл (попытка {attempt}/{EDGE_ATTEMPTS})")
        except Exception as error:
            logger.warning(f"tts_generator: edge-tts stream не смог синтезировать речь "
                           f"(попытка {attempt}/{EDGE_ATTEMPTS}, голос {voice}): {error}")
            wrote_audio = False
        if attempt < EDGE_ATTEMPTS:
            time.sleep(EDGE_RETRY_PAUSE_SEC * attempt)
    else:
        return None

    if not wrote_audio or not output_path.exists() or output_path.stat().st_size == 0:
        logger.warning("tts_generator: edge-tts stream вернул пустой файл")
        return None
    if not timings:
        # Аудио есть, но границы слов не пришли — субтитры не построить, но
        # голос использовать можно; вернём пустой список (не None), чтобы
        # вызывающий код отличил "нет таймингов" от "нет голоса".
        logger.info("tts_generator: аудио озвучки готово, но WordBoundary не пришли — субтитры пропущены")
        return []

    logger.info(f"tts_generator: озвучка + тайминги готовы (edge stream, {len(timings)} слов)")
    return timings


def _synthesize_silero(text: str, output_path: Path) -> bool:
    """
    Офлайн-фолбэк — требует pip install torch (тяжёлая зависимость, НЕ в
    requirements.txt по умолчанию, см. докстринг файла). Модель качается
    через torch.hub при первом использовании (нужен интернет ОДИН раз,
    дальше работает офлайн — в отличие от edge-tts, которому интернет
    нужен на каждый запрос).
    """
    try:
        import torch
    except ImportError:
        logger.warning("tts_generator: библиотека 'torch' не установлена (pip install torch) — "
                        "silero-фолбэк недоступен")
        return False

    # Применяем ударения перед синтезом
    text_with_stress = apply_brand_pronunciation(text)

    try:
        model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models", model="silero_tts",
            language="ru", speaker="v4_ru", trust_repo=True,
        )
        speaker = "eugene" if "dmitry" in config.TTS_VOICE.lower() else "kseniya"
        audio = model.apply_tts(text=text_with_stress, speaker=speaker, sample_rate=48000)
        import soundfile as sf
        sf.write(str(output_path), audio.numpy(), 48000)
    except Exception as error:
        logger.warning(f"tts_generator: silero не смог синтезировать речь ({error})")
        return False

    if not output_path.exists() or output_path.stat().st_size == 0:
        logger.warning("tts_generator: silero вернул пустой файл")
        return False
    return True


_ENGINES = {"edge": _synthesize_edge, "silero": _synthesize_silero}


def synthesize_voiceover(text: str, output_path: Path) -> bool:
    """
    Синтезирует речь по тексту в output_path (mp3/wav). Порядок движков:
    сначала config.TTS_ENGINE, затем ЛЮБОЙ другой доступный — сбой одного
    движка (например, нет интернета для edge-tts) не должен оставлять
    ролик без озвучки, если есть рабочий офлайн-фолбэк. Возвращает False,
    только если ВСЕ движки не смогли — вызывающий код тогда просто
    собирает ролик без звука голоса (с warning, не падает).
    """
    if not text:
        return False

    primary = config.TTS_ENGINE if config.TTS_ENGINE in _ENGINES else "edge"
    order = [primary] + [name for name in _ENGINES if name != primary]

    for engine_name in order:
        logger.info(f"tts_generator: пробую озвучку через {engine_name}")
        if _ENGINES[engine_name](text, output_path):
            logger.info(f"tts_generator: озвучка готова ({engine_name})")
            return True

    logger.warning("tts_generator: ни один TTS-движок не сработал — ролик будет без голоса")
    return False
