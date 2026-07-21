# -*- coding: utf-8 -*-
"""
test_post_format_schema.py — самотест post_format_schema.py (в стиле
model_specs.py — python test_post_format_schema.py, без pytest/unittest,
без новых зависимостей).

Три случая:
    1. Реальный пример в ТЕКУЩЕМ формате (lead_post_builder.py) — должен пройти.
    2. Реальный пример в СТАРОМ формате (post_builder.build_telegram_post,
       уже архивный путь) — должен быть отклонён, с понятной причиной.
    3. Заглушка на случай будущего Claude-сгенерированного поста — пока
       Claude не подключён, тестируем на гипотетическом тексте, который
       ДОЛЖЕН пройти (то есть валидатор одинаково относится к любому
       источнику текста, а не только к шаблону).
"""

from post_format_schema import validate_post_format, validate_media_group

# --- 1. Текущий формат (сгенерирован реальным lead_post_builder.py) ---
CURRENT_FORMAT_EXAMPLE = """🔥 Kia K5 노블레스 — уже можно забронировать!

📋 Характеристики:
📅 Год: 2023
🔧 Двигатель: 2.0 л, 160 л.с.
⚙️ КПП: автомат
🛣 Пробег: 35 244 км

💰 Стоимость под ключ в Казани: 2 622 023 ₽

👉 Пишите мне — посчитаю точный расчёт под ваш бюджет: @korea_auto_kazan_bot"""

# --- 2. Старый формат (post_builder.build_telegram_post(), archive/-путь
# больше не используется, но именно ТАК выглядел пост, который случайно
# ушёл в канал 06.07 из-за main.py — см. историю проекта) ---
OLD_FORMAT_EXAMPLE = """🚘 Audi Q3 — 35 TDI Sportback

📅 Год выпуска: 2021
🛣 Пробег: 45 000 км
🔧 1968 см³, 150 л.с.

💰 Цена под ключ Казань: 3 260 767 ₽
✅ Включает пошлину, ЕТС, утильсбор, доставку и комиссию

🔗 https://www.kbchachacha.com/public/car/detail.kbc?carSeq=12345678"""

# --- 3. Заглушка: гипотетический Claude-сгенерированный пост, СОБЛЮДАЮЩИЙ
# схему (доказывает, что валидатор одинаково строг к любому источнику
# текста, а не завязан на то, что текст пришёл именно от шаблона).
# Когда Claude-генерация будет реально подключена — заменить эту константу
# на настоящий ответ модели и убедиться, что промпт заставляет её
# придерживаться той же структуры.
FUTURE_CLAUDE_STUB_EXAMPLE = """🔥 Hyundai Avante 1.6 Gasoline — отличный вариант для города!

📋 Характеристики:
📅 Год: 2023
🔧 Двигатель: 1.6 л, 123 л.с.
⚙️ КПП: автомат
🛣 Пробег: 28 000 км

💰 Стоимость под ключ в Казани: 1 950 000 ₽

👉 Пишите — обсудим детали: @korea_auto_kazan_bot"""


def _check(label: str, post_text: str, expect_ok: bool) -> bool:
    result = validate_post_format(post_text)
    passed = bool(result) == expect_ok
    status = "OK" if passed else "FAIL"
    print(f"[{status}] {label}: ok={result.ok} (ожидалось {expect_ok})")
    if result.errors:
        for e in result.errors:
            print(f"         - {e}")
    return passed


def run() -> None:
    results = [
        _check("Текущий формат (lead_post_builder.py)", CURRENT_FORMAT_EXAMPLE, expect_ok=True),
        _check("Старый формат (post_builder.build_telegram_post, архив)", OLD_FORMAT_EXAMPLE, expect_ok=False),
        _check("Заглушка будущего Claude-поста", FUTURE_CLAUDE_STUB_EXAMPLE, expect_ok=True),
    ]

    # Доп. проверки validate_media_group — количество фото и подпись
    media_ok = validate_media_group(["url1", "url2", "url3", "url4"], has_caption_on_first=True)
    results.append(bool(media_ok))
    print(f"[{'OK' if media_ok else 'FAIL'}] Альбом из 4 фото с подписью: ok={media_ok.ok}")

    media_too_many = validate_media_group(["u"] * 11, has_caption_on_first=True)
    results.append(not media_too_many)
    print(f"[{'OK' if not media_too_many else 'FAIL'}] Альбом из 11 фото (должен быть отклонён): "
          f"ok={media_too_many.ok}, errors={media_too_many.errors}")

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")


if __name__ == "__main__":
    run()
