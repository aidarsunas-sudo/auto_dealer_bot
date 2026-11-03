# -*- coding: utf-8 -*-
"""Тест блокировки run_chain.py: одиночная блокировка с ожиданием.

  python test_run_chain_lock.py
"""
import tempfile
import threading
import time
from pathlib import Path

import run_chain as rc


def _use_temp_lock():
    tmp = Path(tempfile.mkdtemp())
    rc.LOGS = tmp
    rc.LOCK = tmp / "chain.lock"


def test_different_step_sets_run_one_after_another():
    """Два запуска с разными наборами шагов стартуют одновременно: второй дожидается первого, оба завершаются."""
    _use_temp_lock()
    events = []

    def run(name, work_sec):
        if not rc.acquire_lock_wait(wait_sec=10, poll_sec=0.05):
            events.append((name, "timeout"))
            return
        events.append((name, "start"))
        time.sleep(work_sec)
        events.append((name, "end"))
        rc.release_lock()

    t1 = threading.Thread(target=run, args=("weekly:collect,topics,stats,report", 0.5))
    t2 = threading.Thread(target=run, args=("daily:approve,publish", 0.2))
    t1.start(); time.sleep(0.05); t2.start()
    t1.join(); t2.join()
    kinds = [e[1] for e in events]
    assert "timeout" not in kinds, events
    assert kinds == ["start", "end", "start", "end"], events  # не перекрываются
    assert not rc.LOCK.exists()


def test_same_set_blocks_when_holder_alive():
    """Пока блокировка занята, второй запуск (тот же набор шагов) не входит и по истечении ожидания получает отказ."""
    _use_temp_lock()
    assert rc.acquire_lock()
    t0 = time.time()
    got = rc.acquire_lock_wait(wait_sec=0.5, poll_sec=0.05)
    assert got is False and time.time() - t0 >= 0.5
    rc.release_lock()
    assert rc.acquire_lock_wait(wait_sec=0.5, poll_sec=0.05) is True
    rc.release_lock()


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("OK  ", name)
            except AssertionError as e:
                failed += 1
                print("FAIL", name, e)
    print("=== Все проверки пройдены ===" if not failed else f"=== Провалено: {failed} ===")
    raise SystemExit(1 if failed else 0)
