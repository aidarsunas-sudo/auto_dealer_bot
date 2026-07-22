#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_voiceover_samples.py — генерирует примеры озвучек для тестирования.

Создаёт mp3 файлы для нескольких разных лотов, чтобы слышно было:
1. Разнообразие (разные варианты озвучки для разных car_id).
2. Ударения (правильное произношение марок).

Использует edge-tts (требует интернет).
"""

import json
from pathlib import Path
import tts_generator
import config

# Примеры лотов для тестирования
SAMPLE_CARS = [
    {
        "car_id": "kbchachacha_28500000",
        "brand": "Kia",
        "model": "K5",
        "year": 2023,
        "engine_display": "2.0 л, 150 л.с., бензин",
        "transmission": "автомат",
        "mileage_km": 25000,
        "total_rub": 2_750_000,
    },
    {
        "car_id": "encar_42123456",
        "brand": "Hyundai",
        "model": "Sonata",
        "year": 2023,
        "engine_display": "1.6 л, 130 л.с., бензин",
        "transmission": "автомат",
        "mileage_km": 18500,
        "total_rub": 2_500_000,
    },
    {
        "car_id": "charancha_55000",
        "brand": "Volkswagen",
        "model": "Tiguan",
        "year": 2022,
        "engine_display": "1.4 л, 125 л.с., бензин",
        "transmission": "автомат",
        "mileage_km": 32000,
        "total_rub": 2_900_000,
    },
]


def prepare_fields(car: dict) -> dict:
    """Подготавливает поля для generate_voiceover_text."""
    return {
        "car_id": car["car_id"],
        "brand": car["brand"],
        "model": car["model"],
        "year": car["year"],
        "engine_display": car["engine_display"],
        "transmission": car["transmission"],
        "mileage_km": car["mileage_km"],
        "total_rub": car["total_rub"],
        "price_label": "Стоимость под ключ",
        "title": f"{car['brand']} {car['model']}",
    }


def generate_samples():
    """Генерирует озвучки для примеров лотов."""
    output_dir = Path("voiceover_samples")
    output_dir.mkdir(exist_ok=True)

    print(f"📁 Генерирую примеры в папку: {output_dir.absolute()}")
    print("=" * 70)

    for i, car in enumerate(SAMPLE_CARS, 1):
        car_id = car["car_id"]
        brand_model = f"{car['brand']} {car['model']}"

        # Определяем вариант озвучки
        import hashlib
        variant_idx = int(hashlib.md5(car_id.encode()).hexdigest(), 16) % len(tts_generator.SYSTEM_PROMPTS)

        print(f"\n[{i}/{len(SAMPLE_CARS)}] {brand_model} (ID: {car_id})")
        print(f"    Вариант озвучки: {variant_idx}")

        fields = prepare_fields(car)

        # Генерируем текст
        print("    Генерирую текст через Claude API...")
        text = tts_generator.generate_voiceover_text(fields)

        if not text:
            print(f"    ❌ Не удалось сгенерировать текст")
            continue

        print(f"    ✓ Текст ({len(text.split())} слов):")
        print(f"      {text[:100]}...")

        # Синтезируем речь
        output_file = output_dir / f"{i}_{car_id.replace('/', '_')}_{variant_idx}.mp3"
        print(f"    Генерирую озвучку: {output_file.name}...")

        if tts_generator.synthesize_voiceover(text, output_file):
            file_size_kb = output_file.stat().st_size / 1024
            print(f"    ✅ Готово ({file_size_kb:.1f} КБ)")
        else:
            print(f"    ❌ Ошибка при синтезе речи")

    print("\n" + "=" * 70)
    print(f"✅ Примеры озвучек сохранены в: {output_dir.absolute()}")
    print("\nДляпрослушивания:")
    for mp3_file in sorted(output_dir.glob("*.mp3")):
        print(f"  • {mp3_file.name}")


def test_determinism():
    """Проверяет, что один car_id генерирует идентичную озвучку."""
    print("\n" + "=" * 70)
    print("ТЕСТ ДЕТЕРМИНИРОВАННОСТИ")
    print("=" * 70)

    car = SAMPLE_CARS[0]
    fields = prepare_fields(car)

    print(f"\nОдин и тот же лот дважды ({car['brand']} {car['model']}):")

    # Первый раз
    print("  Запрос 1...")
    text_1 = tts_generator.generate_voiceover_text(fields)

    # Второй раз
    print("  Запрос 2...")
    text_2 = tts_generator.generate_voiceover_text(fields)

    if text_1 == text_2:
        print(f"  ✅ ИДЕНТИЧНЫЕ ТЕКСТЫ (детерминированность OK)")
    else:
        print(f"  ⚠️  РАЗНЫЕ ТЕКСТЫ (это может быть OK, если Claude генерирует вариативно)")
        print(f"    Текст 1: {text_1[:80]}...")
        print(f"    Текст 2: {text_2[:80]}...")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("ГЕНЕРАТОР ПРИМЕРОВ ОЗВУЧЕК")
    print("=" * 70)

    if not config.ANTHROPIC_API_KEY:
        print("\n❌ ОШИБКА: ANTHROPIC_API_KEY не установлен в .env")
        print("Установите ключ и повторите попытку.")
        exit(1)

    print("\n⚠️  ВАЖНО: для генерации нужен интернет (edge-tts)")
    print("Это займет ~1-2 минуты на лот.\n")

    test_determinism()
    generate_samples()
