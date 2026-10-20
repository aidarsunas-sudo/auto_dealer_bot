# -*- coding: utf-8 -*-
"""test_tts_retry.py — повторные попытки edge-tts (python test_tts_retry.py). Без сети: edge_tts подменяется."""

import sys
import tempfile
import types
from pathlib import Path

import tts_generator as tg


def _check(name, condition):
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def install_fake(fail_first: int, calls: list):
    """Подставляет модуль edge_tts: первые fail_first обращений отвечают «No audio was received»."""
    module = types.ModuleType("edge_tts")

    class Communicate:
        def __init__(self, text, voice, rate=None):
            calls.append(voice)
            self.fail = len(calls) <= fail_first

        async def save(self, path):
            if self.fail:
                raise RuntimeError("No audio was received. Please verify that your parameters are correct.")
            Path(path).write_bytes(b"ID3" + b"\0" * 100)

        async def stream(self):
            if self.fail:
                raise RuntimeError("No audio was received. Please verify that your parameters are correct.")
            yield {"type": "audio", "data": b"ID3" + b"\0" * 100}
            yield {"type": "WordBoundary", "offset": 0, "duration": 5_000_000, "text": "слово"}
    module.Communicate = Communicate
    sys.modules["edge_tts"] = module


def run():
    results = []
    out = Path(tempfile.mkdtemp()) / "v.mp3"
    sleeps = []
    saved = sys.modules.get("edge_tts")
    try:
        calls = []; install_fake(0, calls)
        results.append(_check("успех с первой попытки: 1 вызов, пауз нет", tg._synthesize_edge("текст", out, sleep=sleeps.append) and len(calls) == 1 and not sleeps))
        for fails in (1, 2, 3):
            calls = []; sleeps.clear(); out.unlink(missing_ok=True); install_fake(fails, calls)
            ok = tg._synthesize_edge("текст", out, sleep=sleeps.append)
            results.append(_check(f"{fails} сбоя подряд -> озвучка всё равно получена с попытки {fails + 1}", ok and len(calls) == fails + 1 and out.stat().st_size > 0))
        calls = []; sleeps.clear(); install_fake(2, calls)
        ok = tg._synthesize_edge("т", out, sleep=lambda s: None)
        results.append(_check("с 3-й попытки голос запасной (после двух неудач основного)", ok and len(calls) == 3 and calls[0] == calls[1] != calls[2]))
        calls = []; sleeps.clear(); install_fake(99, calls)
        results.append(_check("все 4 попытки неудачны -> False (ролик без голоса, не падение)", tg._synthesize_edge("текст", out, sleep=sleeps.append) is False
                              and len(calls) == tg.EDGE_ATTEMPTS and len(sleeps) == tg.EDGE_ATTEMPTS - 1))
        results.append(_check("паузы растут (2, 4, 6 с)", sleeps == [2.0, 4.0, 6.0]))
        # вариант с таймингами слов
        saved_sleep = tg.time.sleep; tg.time.sleep = lambda s: None
        calls = []; install_fake(1, calls)
        timings = tg.synthesize_with_word_timings("текст", out)
        results.append(_check("synthesize_with_word_timings: 1 сбой -> повтор -> тайминги получены", timings and timings[0]["word"] == "слово" and len(calls) == 2))
        calls = []; install_fake(99, calls)
        results.append(_check("synthesize_with_word_timings: все попытки неудачны -> None", tg.synthesize_with_word_timings("текст", out) is None and len(calls) == tg.EDGE_ATTEMPTS))
        tg.time.sleep = saved_sleep
    finally:
        if saved is not None:
            sys.modules["edge_tts"] = saved
        else:
            sys.modules.pop("edge_tts", None)
    bad = results.count(False)
    print(f"\ntest_tts_retry: {len(results) - bad} OK / {bad} FAIL")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    run()
