# -*- coding: utf-8 -*-
"""
test_lot_reel_script_batch.py — самотест lot_reel_script_batch.py
(python test_lot_reel_script_batch.py, без pytest и без сети: Claude API — заглушка).
"""

import json
import sys
import tempfile
import types
from pathlib import Path

import config
import lot_reel_script as single
import lot_reel_script_batch as batch

PHOTOS = [f"https://example.com/{i}.jpg" for i in range(config.MIN_PHOTOS_PER_POST)]
YEAR = (config.MIN_YEAR + config.MAX_YEAR) // 2


def _lot(lot_id: str, brand="Kia", model="K5", trim="2.0 Gasoline", photos=None, year=YEAR,
         total=2_600_000, **extra) -> tuple[dict, dict]:
    car = {"brand": brand, "model": model, "trim": trim, "year": year, "mileage_km": 28000,
           "photos": PHOTOS if photos is None else photos, "url": lot_id, **extra}
    return car, {"total_rub_raw": total}


def _script(lot_id: str, hook: str, opening: str, hook_type="цифра") -> dict:
    return {"lot_id": lot_id, "hook": hook, "hook_type": hook_type,
            "voiceover_text": f"{opening} Цена под ключ — два миллиона шестьсот тысяч рублей.",
            "on_screen_titles": ["2022 год", "28 000 км", "2,6 млн ₽"],
            "post_caption": "Разбор лота.", "hashtags": ["#киа"]}


def _fake_anthropic(replies: list, calls: list):
    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            text = replies[min(len(calls) - 1, len(replies) - 1)]
            if callable(text):
                text = text(kwargs)
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False)
            return types.SimpleNamespace(content=[types.SimpleNamespace(text=text)])

    class _Client:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    return types.SimpleNamespace(Anthropic=_Client)


def _check(label: str, cond: bool) -> bool:
    print(f"[{'OK' if cond else 'FAIL'}] {label}")
    return cond


def run() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    results = []
    real = (config.ANTHROPIC_API_KEY, config.SCRIPT_ENABLED, config.SCRIPT_BATCH_MAX,
            single.HOOKS_LOG_PATH)

    prompt = batch.load_batch_prompt()
    results.append(_check("промпт: массив с lot_id и правило чисел",
                          '"lot_id"' in prompt and "ТОЛЬКО словами" not in prompt
                          and "без цифр, знака ₽ и сокращений" in prompt))

    # Тестовый лот пользователя: Encar 42247766, Kia K5 2022, 28 000 км, 2,6 млн ₽, Казань ~2,9 млн ₽
    user_lot = _lot("encar:42247766", kazan_market_price=2_900_000,
                    options=["кожа", "камера 360"])
    a, b, c = user_lot, _lot("lot-b", brand="Kia", model="Ray", trim="1.0 Gasoline"), _lot("lot-c")
    rejected = _lot("lot-bad", photos=[])  # без фото — гейт режет
    scripts_ok = [
        _script("encar:42247766", "Kia K5 2022: 2,6 млн ₽ под ключ", "Смотрите, что за машина."),
        _script("lot-b", "Почему Kia Ray берут чаще, чем кажется?", "Начнём с пробега.", "вопрос"),
        _script("lot-c", "Покупатели путают цену и итог", "Итак, по порядку.", "ошибка"),
    ]

    try:
        with tempfile.TemporaryDirectory() as tmp:
            single.HOOKS_LOG_PATH = Path(tmp) / "hooks.jsonl"
            config.ANTHROPIC_API_KEY = "test"
            config.SCRIPT_BATCH_MAX = 8

            # Флаг выключен -> [] и API не трогаем
            config.SCRIPT_ENABLED = False
            calls: list = []
            sys.modules["anthropic"] = _fake_anthropic([scripts_ok], calls)
            results.append(_check("SCRIPT_ENABLED=false -> [] без вызова API",
                                  batch.generate_batch_scripts([a, b, c]) == [] and not calls))

            # Гейт: лот без фото в пакет не попадает
            config.SCRIPT_ENABLED = True
            calls = []
            sys.modules["anthropic"] = _fake_anthropic([scripts_ok], calls)
            out = batch.generate_batch_scripts([a, rejected, b, c], dry_run=True)
            sent = json.loads(calls[0]["messages"][0]["content"])
            ids = [lot["lot_id"] for lot in sent["lots"]]
            results.append(_check("гейт: отклонённый лот не ушёл в API, порядок сохранён",
                                  ids == ["encar:42247766", "lot-b", "lot-c"] and len(out) == 3))
            results.append(_check("kazan_market_price передан только лоту, у которого он есть",
                                  sent["lots"][0].get("kazan_market_price") == 2_900_000
                                  and all("kazan_market_price" not in l for l in sent["lots"][1:])))
            results.append(_check("dry_run: хуки не записаны",
                                  not single.HOOKS_LOG_PATH.exists()))
            results.append(_check("системный промпт — пакетный", calls[0]["system"] == prompt))

            # Все лоты отклонены -> [] без API
            calls = []
            sys.modules["anthropic"] = _fake_anthropic([scripts_ok], calls)
            results.append(_check("все лоты отклонены -> [] без вызова API",
                                  batch.generate_batch_scripts([rejected]) == [] and not calls))

            # Не dry_run: хуки пишутся, следующий пакет получает previous_hooks
            batch.generate_batch_scripts([a, b, c], dry_run=False)
            results.append(_check("не dry_run: 3 хука в логе",
                                  len(single.read_previous_hooks(10)) == 3))
            calls = []
            fresh = [_script("lot-c", "Другой хук про Kia K5", "Вот как это выглядит.")]
            sys.modules["anthropic"] = _fake_anthropic([fresh], calls)
            batch.generate_batch_scripts([c])
            sent = json.loads(calls[0]["messages"][0]["content"])
            results.append(_check("previous_hooks из лога переданы в пакет",
                                  len(sent.get("previous_hooks", [])) == 3))

            # Нарушения пакета -> повтор -> успех / [] при двойном нарушении
            def _case(label, bad, expect_len):
                calls = []
                sys.modules["anthropic"] = _fake_anthropic([bad, scripts_ok], calls)
                config_hooks = single.HOOKS_LOG_PATH
                single.HOOKS_LOG_PATH = Path(tmp) / "empty.jsonl"
                try:
                    out = batch.generate_batch_scripts([a, b, c])
                finally:
                    single.HOOKS_LOG_PATH = config_hooks
                return _check(label, len(out) == expect_len and len(calls) == 2)

            dup_hook = [dict(s) for s in scripts_ok]; dup_hook[1]["hook"] = dup_hook[0]["hook"]
            results.append(_case("повтор хука внутри пакета -> повтор запроса -> успех", dup_hook, 3))
            same_open = [dict(s) for s in scripts_ok]
            same_open[1]["voiceover_text"] = same_open[0]["voiceover_text"]
            results.append(_case("одинаковое начало озвучки -> повтор запроса -> успех", same_open, 3))
            digits = [dict(s) for s in scripts_ok]; digits[2]["voiceover_text"] = "Цена 2 600 000 ₽"
            results.append(_case("цифры в озвучке -> повтор запроса -> успех", digits, 3))
            wrong_order = [scripts_ok[1], scripts_ok[0], scripts_ok[2]]
            results.append(_case("нарушен порядок lot_id -> повтор запроса -> успех", wrong_order, 3))
            results.append(_case("не тот размер массива -> повтор запроса -> успех", scripts_ok[:2], 3))

            calls = []
            sys.modules["anthropic"] = _fake_anthropic([dup_hook], calls)
            results.append(_check("два нарушения подряд -> []",
                                  batch.generate_batch_scripts([a, b, c]) == [] and len(calls) == 2))

            # Куски по SCRIPT_BATCH_MAX, хуки между кусками не повторяются
            config.SCRIPT_BATCH_MAX = 2
            single.HOOKS_LOG_PATH = Path(tmp) / "chunks.jsonl"
            calls = []
            by_id = {s["lot_id"]: s for s in scripts_ok}
            responder = lambda kw: [by_id[l["lot_id"]] for l in
                                    json.loads(kw["messages"][0]["content"])["lots"]]
            sys.modules["anthropic"] = _fake_anthropic([responder], calls)
            out = batch.generate_batch_scripts([a, b, c])
            second = json.loads(calls[1]["messages"][0]["content"])
            results.append(_check("SCRIPT_BATCH_MAX=2: два запроса (2+1), хуки 1-го куска -> previous_hooks 2-го",
                                  len(calls) == 2 and len(out) == 3
                                  and len(second.get("previous_hooks", [])) == 2))

            # Без ключа -> []
            config.ANTHROPIC_API_KEY = ""
            results.append(_check("нет ключа -> []", batch.generate_batch_scripts([a]) == []))
    finally:
        (config.ANTHROPIC_API_KEY, config.SCRIPT_ENABLED, config.SCRIPT_BATCH_MAX,
         single.HOOKS_LOG_PATH) = real
        sys.modules.pop("anthropic", None)

    results.append(_check("SCRIPT_ENABLED по умолчанию false", config.SCRIPT_ENABLED is False))

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
