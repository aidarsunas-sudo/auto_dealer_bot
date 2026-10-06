# -*- coding: utf-8 -*-
"""
lot_reel_script.py — сценарий ролика «Разбор лота» (30–45 сек) через Claude API.

ГЕЙТ (CLAUDE.md, "Железные правила публикации"): генерация сценария идёт
ТОЛЬКО из ValidatedCar — результата post_validator.validate_post_before_publish().
generate_lot_script() проверяет isinstance и поднимает TypeError на голом
car-словаре (тот же приём, что у video_generator.generate_videos()).
generate_script_for_car() — удобная обёртка: сама прогоняет car через гейт
и при REJECT возвращает None (ничего не генерируется, REJECT уже в
rejected_posts.log).

Фича за флагом config.SCRIPT_ENABLED (по умолчанию false). dry_run=True —
сценарий только возвращается/логируется, хук НЕ пишется в лог хуков.
Ничего не публикуется ни в каком режиме — модуль только возвращает dict.

Системный промпт — prompts/lot_reel_script.txt; входной JSON подставляется
в user-сообщение при каждом вызове. Любой сбой (нет ключа, нет библиотеки,
API, невалидный JSON, нарушение формата чисел в озвучке) -> None, вызывающий
код не ломается.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config
from post_validator import ValidatedCar, validate_post_before_publish

logger = logging.getLogger("lot_reel_script")

CLAUDE_MODEL = "claude-opus-4-8"  # та же модель, что в tts_generator.py
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "lot_reel_script.txt"
HOOKS_LOG_PATH = Path(__file__).resolve().parent / "lot_reel_hooks.jsonl"

HOOK_TYPES = ("цифра", "вопрос", "контраст", "ошибка")
MAX_CAPTION_CHARS = 600
MAX_ATTEMPTS = 2  # один повтор, если озвучка нарушила формат чисел

REQUIRED_KEYS = ("hook", "voiceover_text", "on_screen_titles", "post_caption",
                 "hashtags", "hook_type")

# Озвучка — только слова: цифры, ₽ и сокращения TTS читает криво.
_VOICEOVER_FORBIDDEN_RE = re.compile(
    r"\d|₽|%|\b(?:км|млн|тыс|руб|л\.\s?с|г\.)(?![а-яё])", re.IGNORECASE)


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def read_previous_hooks(n: int | None = None, path: Path | None = None) -> list[str]:
    """Последние n хуков из лога (старые -> новые). Нет файла/битая строка -> пропуск."""
    path = path or HOOKS_LOG_PATH  # на момент вызова, не определения — иначе тест не подменит
    n = config.SCRIPT_PREVIOUS_HOOKS_N if n is None else n
    if n <= 0 or not path.exists():
        return []
    hooks: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    hook = json.loads(line).get("hook")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if hook:
                    hooks.append(hook)
    except OSError as error:
        logger.warning(f"lot_reel_script: не прочитан {path.name}: {error}")
        return []
    return hooks[-n:]


def append_hook(hook: str, car: dict, path: Path | None = None) -> None:
    path = path or HOOKS_LOG_PATH
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": hook,
        "car": car.get("url") or car.get("external_id") or "",
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        logger.error(f"lot_reel_script: не записан хук в {path.name}: {error}")


def build_input(car: ValidatedCar, total_rub: int, previous_hooks: list[str]) -> dict:
    """Входной JSON промпта. Только реальные данные; нет данных -> поле пустое/отсутствует."""
    options = car.get("options")
    if not isinstance(options, list):
        specs = car.get("specs")
        options = [specs] if isinstance(specs, str) and specs.strip() else []
    data = {
        "brand": car.get("brand", ""),
        "model": car.get("model", ""),
        "year": car.get("year"),
        "mileage_km": car.get("mileage_km"),
        "price_rub_total": total_rub,
        "options": options,
        "photos_count": len(car.get("photos") or []),
        "previous_hooks": previous_hooks,
    }
    if car.get("kazan_market_price"):
        data["kazan_market_price"] = car["kazan_market_price"]
    return data


def parse_script(text: str) -> dict:
    """Достаёт JSON из ответа модели (допускает ```-обёртку). ValueError при нарушении схемы."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("ответ — не JSON-объект")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"нет ключей: {missing}")
    if data["hook_type"] not in HOOK_TYPES:
        raise ValueError(f"hook_type {data['hook_type']!r} вне {HOOK_TYPES}")
    if not isinstance(data["on_screen_titles"], list) or not isinstance(data["hashtags"], list):
        raise ValueError("on_screen_titles/hashtags должны быть списками")
    if len(data["post_caption"]) > MAX_CAPTION_CHARS:
        raise ValueError(f"post_caption {len(data['post_caption'])} > {MAX_CAPTION_CHARS} знаков")
    bad = _VOICEOVER_FORBIDDEN_RE.search(data["voiceover_text"])
    if bad:
        raise ValueError(f"voiceover_text содержит цифру/₽/сокращение: {bad.group(0)!r}")
    return data


def generate_lot_script(car: ValidatedCar, total_rub: int, dry_run: bool = True) -> Optional[dict]:
    """
    Генерирует сценарий по ВАЛИДИРОВАННОМУ лоту.

    Args:
        car: ValidatedCar (не голый dict — TypeError).
        total_rub: итоговая цена под ключ, целое число (fields["total_rub_raw"]).
        dry_run: True — хук не пишется в лог хуков. Публикации нет в любом режиме.

    Returns:
        dict по схеме промпта или None при любом сбое.
    """
    if not isinstance(car, ValidatedCar):
        raise TypeError(
            "generate_lot_script() принимает только ValidatedCar из "
            "validate_post_before_publish(), не сырой car-словарь"
        )
    if not config.ANTHROPIC_API_KEY:
        logger.info("lot_reel_script: ANTHROPIC_API_KEY не задан — сценарий не сгенерирован")
        return None

    try:
        import anthropic  # лениво — тот же приём, что в tts_generator.py
    except ImportError:
        logger.warning("lot_reel_script: библиотека 'anthropic' не установлена (pip install anthropic)")
        return None

    try:
        system_prompt = load_system_prompt()
        payload = build_input(car, total_rub, read_previous_hooks())
        user_message = json.dumps(payload, ensure_ascii=False)
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

        script = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            try:
                script = parse_script(response.content[0].text)
                break
            except (ValueError, json.JSONDecodeError) as error:
                logger.warning(f"lot_reel_script: попытка {attempt}/{MAX_ATTEMPTS} "
                               f"отклонена: {error}")
        if script is None:
            return None
    except Exception as error:
        logger.warning(f"lot_reel_script: ошибка Claude API ({error}) — сценарий не сгенерирован")
        return None

    if script["hook"] in payload["previous_hooks"]:
        logger.warning("lot_reel_script: хук повторяет один из previous_hooks")

    if dry_run:
        logger.info(f"lot_reel_script [dry-run]: хук не записан в лог. {script['hook']!r}")
    else:
        append_hook(script["hook"], car)
    return script


def generate_script_for_car(car: dict, fields: dict | None, dry_run: bool = True) -> Optional[dict]:
    """
    ГЕЙТ + генерация: car -> validate_post_before_publish() -> сценарий.
    Нет fields/total_rub_raw -> None (цену не угадываем).
    """
    validated, reason = validate_post_before_publish(car, fields)
    if validated is None:
        logger.info(f"lot_reel_script: лот не прошёл гейт ({reason}) — сценарий не генерируется")
        return None
    total_rub = fields.get("total_rub_raw") if fields else None
    if not total_rub:
        logger.info("lot_reel_script: нет total_rub_raw — сценарий не генерируется")
        return None
    return generate_lot_script(validated, total_rub, dry_run=dry_run)
