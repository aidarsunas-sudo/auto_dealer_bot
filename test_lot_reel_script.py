# -*- coding: utf-8 -*-
"""
test_lot_reel_script.py — самотест lot_reel_script.py (python test_lot_reel_script.py,
без pytest и без сети: Claude API подменён заглушкой).
"""

import json
import sys
import tempfile
import types
from pathlib import Path

import config
import lot_reel_script as lrs
from post_validator import ValidatedCar, validate_post_before_publish

GOOD = {
    "hook": "Kia K5 2022 из Кореи: 2,6 млн ₽ под ключ",
    "voiceover_text": "Киа К пять две тысячи двадцать второго года. Цена под ключ — "
                       "два миллиона шестьсот тысяч рублей.",
    "on_screen_titles": ["2022 год", "32 000 км", "2,6 млн ₽"],
    "post_caption": "Разбор лота.",
    "hashtags": ["#киа"],
    "hook_type": "цифра",
}


def _validated_car() -> ValidatedCar:
    year = (config.MIN_YEAR + config.MAX_YEAR) // 2
    car = {"brand": "Kia", "model": "Ray", "trim": "1.0 Gasoline", "year": year,
           "mileage_km": 30000, "specs": "кожа, камера",
           "photos": [f"https://example.com/{i}.jpg" for i in range(config.MIN_PHOTOS_PER_POST)],
           "url": "https://example.com/car/1"}
    validated, reason = validate_post_before_publish(car)
    assert validated is not None, reason
    return validated


def _fake_anthropic(replies: list[str], calls: list):
    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            text = replies[min(len(calls) - 1, len(replies) - 1)]
            return types.SimpleNamespace(content=[types.SimpleNamespace(text=text)])

    class _Client:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    return types.SimpleNamespace(Anthropic=_Client)


def _check(label: str, cond: bool) -> bool:
    print(f"[{'OK' if cond else 'FAIL'}] {label}")
    return cond


def run() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp1251-консоль Windows не знает ₽
    results = []
    car = _validated_car()

    # Флаг по умолчанию выключен
    results.append(_check("SCRIPT_ENABLED по умолчанию false (в тестовой среде)",
                          config.SCRIPT_ENABLED is False))
    results.append(_check("SCRIPT_DRY_RUN по умолчанию true", config.SCRIPT_DRY_RUN is True))

    # Гейт: голый dict -> TypeError
    try:
        lrs.generate_lot_script({"brand": "Kia"}, 2_600_000)
        results.append(_check("голый dict отвергнут", False))
    except TypeError:
        results.append(_check("голый dict отвергнут (TypeError)", True))

    # Системный промпт: правило про числа на месте
    prompt = lrs.load_system_prompt()
    results.append(_check("промпт содержит правило чисел для voiceover_text",
                          "ТОЛЬКО словами" in prompt and "ФОРМАТ ЧИСЕЛ" in prompt))

    # parse_script: валидный, ```-обёртка, цифры в озвучке, длинный caption, плохой hook_type
    results.append(_check("parse_script: валидный JSON", lrs.parse_script(json.dumps(GOOD)) == GOOD))
    results.append(_check("parse_script: ```json-обёртка",
                          lrs.parse_script("```json\n" + json.dumps(GOOD) + "\n```") == GOOD))
    for label, patch in [
        ("цифра в озвучке", {"voiceover_text": "Цена 2 миллиона"}),
        ("знак ₽ в озвучке", {"voiceover_text": "Два миллиона ₽"}),
        ("сокращение «км» в озвучке", {"voiceover_text": "Пробег тридцать тысяч км"}),
        ("caption > 600", {"post_caption": "я" * 601}),
        ("hook_type вне списка", {"hook_type": "крик"}),
    ]:
        try:
            lrs.parse_script(json.dumps({**GOOD, **patch}))
            results.append(_check(f"parse_script отвергает: {label}", False))
        except ValueError:
            results.append(_check(f"parse_script отвергает: {label}", True))
    results.append(_check("слово «километров» в озвучке допустимо",
                          bool(lrs.parse_script(json.dumps(
                              {**GOOD, "voiceover_text": "Тридцать тысяч километров."})))))

    # build_input: kazan_market_price только если передан
    inp = lrs.build_input(car, 2_600_000, ["h1"])
    results.append(_check("build_input: без kazan_market_price поля нет", "kazan_market_price" not in inp))
    results.append(_check("build_input: previous_hooks и цена переданы",
                          inp["previous_hooks"] == ["h1"] and inp["price_rub_total"] == 2_600_000))

    # Лог хуков: последние N
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hooks.jsonl"
        for i in range(15):
            lrs.append_hook(f"hook {i}", {"url": f"u{i}"}, path=path)
        last = lrs.read_previous_hooks(10, path=path)
        results.append(_check("read_previous_hooks: последние 10",
                              last == [f"hook {i}" for i in range(5, 15)]))

    # Полный вызов с заглушкой API
    real_key, real_log = config.ANTHROPIC_API_KEY, lrs.HOOKS_LOG_PATH
    with tempfile.TemporaryDirectory() as tmp:
        lrs.HOOKS_LOG_PATH = Path(tmp) / "hooks.jsonl"
        config.ANTHROPIC_API_KEY = "test"
        try:
            calls: list = []
            sys.modules["anthropic"] = _fake_anthropic([json.dumps(GOOD)], calls)
            script = lrs.generate_lot_script(car, 2_600_000, dry_run=True)
            results.append(_check("dry_run: сценарий получен, хук НЕ записан",
                                  script == GOOD and not lrs.HOOKS_LOG_PATH.exists()))
            results.append(_check("входной JSON подставлен в user-сообщение",
                                  json.loads(calls[0]["messages"][0]["content"])["brand"] == "Kia"
                                  and calls[0]["system"] == prompt))

            script = lrs.generate_lot_script(car, 2_600_000, dry_run=False)
            results.append(_check("не dry_run: хук записан в лог",
                                  lrs.read_previous_hooks(10) == [GOOD["hook"]]))

            calls = []
            bad = {**GOOD, "voiceover_text": "Цена 2 600 000 ₽"}
            sys.modules["anthropic"] = _fake_anthropic([json.dumps(bad), json.dumps(GOOD)], calls)
            results.append(_check("повтор после нарушения формата -> валидный сценарий",
                                  lrs.generate_lot_script(car, 2_600_000) == GOOD and len(calls) == 2))

            calls = []
            sys.modules["anthropic"] = _fake_anthropic([json.dumps(bad)], calls)
            results.append(_check("два нарушения подряд -> None",
                                  lrs.generate_lot_script(car, 2_600_000) is None and len(calls) == 2))

            config.ANTHROPIC_API_KEY = ""
            results.append(_check("нет ключа -> None",
                                  lrs.generate_lot_script(car, 2_600_000) is None))
        finally:
            config.ANTHROPIC_API_KEY = real_key
            lrs.HOOKS_LOG_PATH = real_log
            sys.modules.pop("anthropic", None)

    # Обёртка с гейтом: REJECT -> None без вызова API
    rejected = lrs.generate_script_for_car({"brand": "Kia", "photos": []}, {"total_rub_raw": 1})
    results.append(_check("generate_script_for_car: лот не прошёл гейт -> None", rejected is None))

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
