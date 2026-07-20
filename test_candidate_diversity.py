# -*- coding: utf-8 -*-
"""
test_candidate_diversity.py — самотест механизмов разнообразия, добавленных
2026-07-20 (без pytest, в стиле остальных self-test'ов — python
test_candidate_diversity.py).

Разбор симптома "посты всегда с Encar, K5 повторяется" показал (см.
FINAL_STATE.md): kbchachacha/charancha молчали неделями из-за реальных
багов (не тема этого теста, см. test post_validator/vk_publisher), а
"тихая смерть" источника и повтор одной марки подряд оставались
незамеченными структурно — этот файл проверяет именно структурные фиксы:
limit_consecutive_brand() и check_source_health().
"""

import sys

from candidate_diversity import limit_consecutive_brand, check_source_health


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _car(brand: str, model: str, i: int) -> dict:
    return {"brand": brand, "model": model, "url": f"https://example.com/{brand}{model}{i}", "source": "encar"}


def _no_three_in_a_row(brands: list) -> bool:
    return all(not (brands[i] == brands[i + 1] == brands[i + 2]) for i in range(len(brands) - 2))


def run() -> None:
    results = []

    # 1. Без хвоста истории — марка не встаёт 3+ раз подряд, порядок иначе не трогается
    candidates = [_car("Hyundai", "Avante", 1), _car("Hyundai", "Casper", 2), _car("Hyundai", "Kona", 3),
                  _car("Kia", "K3", 1), _car("Renault", "XM3", 1)]
    result = limit_consecutive_brand(candidates, [], max_in_a_row=2)
    brands = [c["brand"] for c in result]
    ok = _no_three_in_a_row(brands) and len(result) == len(candidates)
    results.append(ok)
    _safe_print(f"[{'OK' if ok else 'FAIL'}] Без хвоста истории — нет 3 подряд одной марки: {brands}")

    # 2. Хвост истории уже на пределе (2 Hyundai подряд из предыдущего прогона) —
    # первый кандидат этого прогона НЕ должен продолжить Hyundai-серию
    candidates2 = [_car("Hyundai", "Avante", 1), _car("Kia", "K3", 1)]
    result2 = limit_consecutive_brand(candidates2, ["Hyundai", "Hyundai"], max_in_a_row=2)
    passed2 = result2[0]["brand"] != "Hyundai"
    results.append(passed2)
    _safe_print(f"[{'OK' if passed2 else 'FAIL'}] Хвост истории учитывается (не продолжает "
                f"Hyundai-серию из прошлого прогона): {[c['brand'] for c in result2]}")

    # 3. Единственная марка в пуле — деградация до "повтор лучше, чем ничего"
    candidates3 = [_car("Kia", "K3", 1), _car("Kia", "K5", 1)]
    result3 = limit_consecutive_brand(candidates3, ["Kia", "Kia"], max_in_a_row=2)
    passed3 = len(result3) == 2  # ничего не потеряно, просто повтор допущен
    results.append(passed3)
    _safe_print(f"[{'OK' if passed3 else 'FAIL'}] Нет альтернативы марке — ничего не теряем "
                f"(повтор допущен): {[c['brand'] for c in result3]}")

    # 4. Пустой список
    results.append(limit_consecutive_brand([], [], max_in_a_row=2) == [])

    # 5. check_source_health: молчащие источники обнаружены и уведомление отправлено
    notified = []
    cars = [{"source": "encar"}, {"source": "encar"}, {"source": "charancha"}]
    import logging
    silent = check_source_health(cars, logging.getLogger("test"), notify_fn=lambda msg: notified.append(msg))
    passed5 = set(silent) == {"kbchachacha", "onbid"} and len(notified) == 1
    results.append(passed5)
    _safe_print(f"[{'OK' if passed5 else 'FAIL'}] check_source_health находит молчащие "
                f"источники и уведомляет: silent={silent}")

    # 6. check_source_health: все источники представлены — уведомления НЕТ
    notified2 = []
    cars2 = [{"source": s} for s in ("kbchachacha", "encar", "charancha", "onbid")]
    silent2 = check_source_health(cars2, logging.getLogger("test"), notify_fn=lambda msg: notified2.append(msg))
    passed6 = silent2 == [] and not notified2
    results.append(passed6)
    _safe_print(f"[{'OK' if passed6 else 'FAIL'}] check_source_health молчит, если все "
                f"источники представлены: silent={silent2}")

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
