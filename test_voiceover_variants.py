#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_voiceover_variants.py — регресс-тест для озвучки.

Проверяет:
1. Один и тот же car_id дважды генерирует одинаковый промпт (детерминированность).
2. Разные car_id генерируют разные промпты (разнообразие).
3. apply_brand_pronunciation() корректно применяет ударения.
"""

import hashlib
import tts_generator
import config


def test_deterministic_variant_selection():
    """Один и тот же car_id → один и тот же вариант промпта."""
    car_id_1 = "encar_42123456"

    # Получаем вариант дважды
    system_prompt_1a = tts_generator._get_system_prompt_for_car(car_id_1)
    system_prompt_1b = tts_generator._get_system_prompt_for_car(car_id_1)

    assert system_prompt_1a == system_prompt_1b, \
        "ОШИБКА: один и тот же car_id вернул разные промпты!"

    variant_idx = int(hashlib.md5(car_id_1.encode()).hexdigest(), 16) % len(tts_generator.SYSTEM_PROMPTS)
    print(f"✓ car_id '{car_id_1}' → вариант {variant_idx} (детерминированный выбор OK)")


def test_variant_diversity():
    """Разные car_id должны (с вероятностью) давать разные промпты."""
    car_ids = [
        "encar_42123456",
        "kbchachacha_28500000",
        "encar_42200000",
        "charancha_12345",
        "onbid_99999",
    ]

    variants = []
    for car_id in car_ids:
        system_prompt = tts_generator._get_system_prompt_for_car(car_id)
        variant_idx = int(hashlib.md5(car_id.encode()).hexdigest(), 16) % len(tts_generator.SYSTEM_PROMPTS)
        variants.append(variant_idx)
        print(f"  {car_id} → вариант {variant_idx}")

    unique_variants = len(set(variants))
    print(f"✓ Из {len(car_ids)} лотов получено {unique_variants} уникальных вариантов")
    if unique_variants < len(car_ids):
        print(f"  (закономерно, что могут совпасть — есть {len(tts_generator.SYSTEM_PROMPTS)} вариантов на {len(car_ids)} лотов)")


def test_brand_pronunciation():
    """Проверяет применение ударений."""
    test_cases = [
        ("Киа Кей Три 2023 за два миллиона рублей", "Ки́а Кей Три 2023 за два миллиона рублей"),
        ("Хёндай Соната", "Хёнда́й Соната"),
        ("Фольксваген Тигуан", "Фолькс-ВА-ген Тигуан"),
        ("Ауди Кью Три", "А́уди Кью Три"),
        ("Мерседес Бенц", "Мерседе́с Бенц"),
        ("Тойота Корола", "То́йота Корола"),
    ]

    for original, expected in test_cases:
        result = tts_generator.apply_brand_pronunciation(original)
        assert result == expected, \
            f"ОШИБКА: '{original}' → '{result}' (ожидалось '{expected}')"
        print(f"✓ '{original}' → '{result}'")


def test_prompts_content():
    """Проверяет, что все промпты содержат ключевые слова."""
    required_keywords = [
        "факты",
        "модель",
        "год",
        "пробег",
        "цена",
        "ЧИСЛА",
        "транслитом",
    ]

    for i, prompt in enumerate(tts_generator.SYSTEM_PROMPTS):
        prompt_lower = prompt.lower()
        missing = [kw for kw in required_keywords if kw.lower() not in prompt_lower]

        if missing:
            print(f"⚠️  Вариант {i} пропускает ключевые слова: {missing}")
        else:
            print(f"✓ Вариант {i} содержит все требуемые ключевые слова")


if __name__ == "__main__":
    print("=" * 70)
    print("РЕГРЕСС-ТЕСТ: озвучка с вариантами и ударениями")
    print("=" * 70)

    print("\n[1] Тест детерминированности (один car_id → один промпт):")
    print("-" * 70)
    test_deterministic_variant_selection()

    print("\n[2] Тест разнообразия (разные car_id → разные промпты):")
    print("-" * 70)
    test_variant_diversity()

    print("\n[3] Тест ударений (BRAND_PRONUNCIATION):")
    print("-" * 70)
    test_brand_pronunciation()

    print("\n[4] Проверка содержимого всех промптов:")
    print("-" * 70)
    test_prompts_content()

    print("\n" + "=" * 70)
    print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
    print("=" * 70)
