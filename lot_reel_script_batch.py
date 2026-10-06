# -*- coding: utf-8 -*-
"""
lot_reel_script_batch.py — ПАКЕТНЫЙ режим сценариев «Разбор лота»: один вызов
Claude API на несколько лотов (prompts/lot_reel_script_batch.txt). Одиночный
режим (lot_reel_script.py) не меняется — отсюда только импортируются его
хелперы (формат ответа, лог хуков, входной JSON лота).

ГЕЙТ (CLAUDE.md): каждый лот ОТДЕЛЬНО проходит validate_post_before_publish();
не прошедшие (или без total_rub_raw) в пакет не попадают — ValidatedCar
существует только для прошедших. Флаг config.SCRIPT_ENABLED=false (по
умолчанию) -> generate_batch_scripts() возвращает [] и не трогает API.
Ничего не публикуется ни в каком режиме.

Проверки пакета поверх проверок одиночного (parse_script): массив той же длины
и порядка, lot_id совпадают, хуки не повторяются внутри пакета и не совпадают
с previous_hooks, первые два слова озвучки у лотов разные. Нарушение ->
один повтор запроса, затем этот кусок пропускается (всё-или-ничего внутри
куска: частично битый пакет не берём).
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import config
import lot_reel_script as single
from post_validator import ValidatedCar, validate_post_before_publish

logger = logging.getLogger("lot_reel_script_batch")

BATCH_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "lot_reel_script_batch.txt"


def load_batch_prompt() -> str:
    return BATCH_PROMPT_PATH.read_text(encoding="utf-8")


def _lot_id(car: dict) -> str:
    return str(car.get("url") or car.get("external_id") or "")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _opening(voiceover: str) -> str:
    return " ".join(re.findall(r"\w+", voiceover.lower())[:2])


def parse_batch(text: str, lot_ids: list[str], previous_hooks: list[str]) -> list[dict]:
    """Разбирает ответ-массив. ValueError при любом нарушении схемы/правил пакета."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("ответ — не JSON-массив")
    if len(data) != len(lot_ids):
        raise ValueError(f"объектов {len(data)}, лотов {len(lot_ids)}")

    seen_hooks = {_norm(h) for h in previous_hooks}
    seen_openings: set[str] = set()
    result = []
    for expected_id, item in zip(lot_ids, data):
        if not isinstance(item, dict):
            raise ValueError("элемент массива — не объект")
        if item.get("lot_id") != expected_id:
            raise ValueError(f"lot_id {item.get('lot_id')!r} вместо {expected_id!r} (порядок/состав)")
        script = single.parse_script(json.dumps(item, ensure_ascii=False))  # схема/числа/caption
        hook = _norm(script["hook"])
        if hook in seen_hooks:
            raise ValueError(f"хук повторяется: {script['hook']!r}")
        seen_hooks.add(hook)
        opening = _opening(script["voiceover_text"])
        if opening in seen_openings:
            raise ValueError(f"одинаковое начало озвучки: {opening!r}")
        seen_openings.add(opening)
        result.append(script)
    return result


def generate_batch_scripts(lots: list[tuple[dict, Optional[dict]]], dry_run: bool = True) -> list[dict]:
    """
    Args:
        lots: список (car, fields) — fields из prepare_post_fields() (нужен
              total_rub_raw), как в validate_post_before_publish().
        dry_run: True — хуки не пишутся в лог хуков.

    Returns:
        список сценариев (каждый с lot_id) только для лотов, прошедших гейт и
        успешно сгенерированных; [] при выключенном флаге/любом сбое.
    """
    if not config.SCRIPT_ENABLED:
        logger.info("lot_reel_script_batch: SCRIPT_ENABLED=false — пакет не генерируется")
        return []

    # ГЕЙТ: каждый лот отдельно.
    accepted: list[tuple[ValidatedCar, int]] = []
    seen_ids: set[str] = set()
    for car, fields in lots:
        validated, reason = validate_post_before_publish(car, fields)
        if validated is None:
            logger.info(f"lot_reel_script_batch: {_lot_id(car) or '?'} не прошёл гейт ({reason}) — не в пакет")
            continue
        total_rub = fields.get("total_rub_raw") if fields else None
        lot_id = _lot_id(validated)
        if not total_rub or not lot_id or lot_id in seen_ids:
            logger.info(f"lot_reel_script_batch: {lot_id or '?'} без цены/id или дубль — не в пакет")
            continue
        seen_ids.add(lot_id)
        accepted.append((validated, total_rub))
    if not accepted:
        return []

    if not config.ANTHROPIC_API_KEY:
        logger.info("lot_reel_script_batch: ANTHROPIC_API_KEY не задан — пакет не сгенерирован")
        return []
    try:
        import anthropic  # лениво — тот же приём, что в tts_generator.py
    except ImportError:
        logger.warning("lot_reel_script_batch: библиотека 'anthropic' не установлена")
        return []

    try:
        system_prompt = load_batch_prompt()
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    except Exception as error:
        logger.warning(f"lot_reel_script_batch: не инициализирован клиент ({error})")
        return []

    previous_hooks = single.read_previous_hooks()
    scripts: list[dict] = []
    size = max(1, config.SCRIPT_BATCH_MAX)
    for start in range(0, len(accepted), size):
        chunk = accepted[start:start + size]
        lot_ids = [_lot_id(car) for car, _ in chunk]
        items = []
        for car, total_rub in chunk:
            item = single.build_input(car, total_rub, [])
            del item["previous_hooks"]  # в пакете он общий, на верхнем уровне
            items.append({"lot_id": _lot_id(car), **item})
        payload = {"lots": items}
        if previous_hooks:
            payload["previous_hooks"] = previous_hooks
        user_message = json.dumps(payload, ensure_ascii=False)

        chunk_scripts = None
        for attempt in range(1, single.MAX_ATTEMPTS + 1):
            try:
                response = client.messages.create(
                    model=single.CLAUDE_MODEL,
                    max_tokens=1000 * len(chunk),
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                )
                chunk_scripts = parse_batch(response.content[0].text, lot_ids, previous_hooks)
                break
            except (ValueError, json.JSONDecodeError) as error:
                logger.warning(f"lot_reel_script_batch: попытка {attempt}/{single.MAX_ATTEMPTS} "
                               f"отклонена: {error}")
            except Exception as error:
                logger.warning(f"lot_reel_script_batch: ошибка Claude API ({error})")
                break
        if chunk_scripts is None:
            continue  # этот кусок пропускаем, остальные — как получится

        scripts.extend(chunk_scripts)
        previous_hooks = previous_hooks + [s["hook"] for s in chunk_scripts]  # уникальность между кусками
        if not dry_run:
            for (car, _), script in zip(chunk, chunk_scripts):
                single.append_hook(script["hook"], car)
    return scripts
