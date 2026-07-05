# -*- coding: utf-8 -*-
"""
agent_editor.py — "агент-редактор" в мультиагентном пайплайне (orchestrator.py).

Второй, отдельный вызов Claude API — проверяет черновик поста, который
написал agent_copywriter.py: сходятся ли цифры с исходными фактами,
соответствует ли тон заявленному типу поста, есть ли призыв к действию и
блок контактов.

ВАЖНО: редактор НИКОГДА не должен блокировать публикацию. Если ключа нет,
API недоступен, нет данных для сверки или ответ Claude не удалось разобрать —
считаем черновик "молча одобренным" (approved=True) с пояснением в feedback,
почему проверка на самом деле не проводилась. Это отдельно от настоящего
отклонения (approved=False) — вызывающий код логирует эти два случая по-разному.
"""

import logging

import config

logger = logging.getLogger("agent_editor")

EDITOR_SYSTEM_PROMPT = """Ты — редактор автомобильного канала в Telegram. Тебе дают
черновик поста и проверенные факты о машине. Проверь черновик по трём пунктам:

1. Все цифры в черновике (цена, год, пробег, объём двигателя, мощность)
   ТОЧНО совпадают с данными фактами — ничего не добавлено и не искажено.
2. Тон текста соответствует заявленному типу поста (например, "горячее
   предложение" должно ощущаться срочным, а не спокойным).
3. В конце есть призыв к действию и ТОЧНО такой блок контактов:
   📩 @korea_auto_kazan_bot
   📢 @auto_generals

Ответь СТРОГО в этом формате, ровно два поля, без ничего лишнего:
ВЕРДИКТ: ДА или НЕТ
ЗАМЕЧАНИЯ: <если ДА, напиши "нет замечаний"; если НЕТ, кратко перечисли, что не так>"""


def _build_review_prompt(post_text: str, car: dict, price_details: dict, post_type: str) -> str:
    facts = [
        f"Тип поста: {post_type}",
        f"Марка и модель: {car.get('brand', '')} {car.get('model', '')} {car.get('trim', '')}".strip(),
    ]
    if car.get("year"):
        facts.append(f"Год выпуска: {car['year']}")
    if car.get("mileage_km") is not None:
        facts.append(f"Пробег: {car['mileage_km']} км")
    if car.get("cc") and car.get("hp"):
        facts.append(f"Двигатель: {car['cc']} см³, {car['hp']} л.с.")
    facts.append(f"Верная итоговая цена: {price_details['total_rub']} ₽")

    return (
        "Проверь этот черновик поста:\n\n"
        f"---\n{post_text}\n---\n\n"
        "Факты, с которыми нужно сверить черновик:\n"
        + "\n".join(f"- {f}" for f in facts)
    )


def _parse_verdict(response_text: str) -> tuple:
    """Разбирает ответ Claude в формате 'ВЕРДИКТ: .. / ЗАМЕЧАНИЯ: ..'.
    Если формат не распознан — считаем это недоступностью редактора."""
    verdict_line = None
    feedback_line = ""

    for line in response_text.splitlines():
        line = line.strip()
        if line.upper().startswith("ВЕРДИКТ:"):
            verdict_line = line.split(":", 1)[1].strip().upper()
        elif line.upper().startswith("ЗАМЕЧАНИЯ:"):
            feedback_line = line.split(":", 1)[1].strip()

    if verdict_line is None:
        return True, "editor недоступен: не удалось разобрать ответ, публикуем без проверки"

    approved = verdict_line.startswith("ДА")
    return approved, ("" if approved else feedback_line)


def review_post(post_text: str, car: dict, price_details: dict, post_type: str) -> tuple:
    """
    Просит Claude проверить черновик поста на соответствие фактам и тону.

    Returns:
        (approved, feedback) — approved=True, feedback="" если всё хорошо.
        При любой проблеме с самой проверкой (нет ключа, сбой API, нет
        price_details для сверки) возвращает (True, "editor недоступен: ...") —
        "молчаливое одобрение", чтобы публикация никогда не блокировалась.
    """
    if not config.ANTHROPIC_API_KEY:
        return True, "editor недоступен: ANTHROPIC_API_KEY не задан"

    if price_details is None:
        return True, "editor недоступен: нет price_details для сверки цифр"

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        review_prompt = _build_review_prompt(post_text, car, price_details, post_type)

        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=300,
            system=EDITOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": review_prompt}],
        )

        return _parse_verdict(response.content[0].text)

    except ImportError:
        return True, "editor недоступен: библиотека anthropic не установлена"
    except Exception as error:
        logger.warning(f"agent_editor: ошибка Claude API, публикую без проверки ({error})")
        return True, f"editor недоступен: ошибка API ({error})"
