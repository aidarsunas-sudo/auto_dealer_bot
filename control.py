# -*- coding: utf-8 -*-
"""
control.py — включить/выключить автопубликацию по расписанию, одной
командой, без ручной правки .yml-файлов.

    python control.py status
    python control.py pause
    python control.py resume

Переключает и main_pipeline.yml, и run_bot.yml сразу (оба расписания —
main_pipeline.py и main.py). По умолчанию commit + push делаются
автоматически — иначе изменение останется только на этом компьютере и на
самом GitHub ничего не изменится (расписание там читается из
запушенной версии файла). Если хочешь сначала посмотреть, что получится,
без отправки на GitHub — добавь --no-push.

Логику пайплайнов (main_pipeline.py, main.py, orchestrator.py) не трогает
вообще — только строки schedule:/cron: в двух .yml-файлах.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

WORKFLOW_FILES = [
    Path(__file__).resolve().parent / ".github" / "workflows" / "main_pipeline.yml",
    Path(__file__).resolve().parent / ".github" / "workflows" / "run_bot.yml",
]

SCHEDULE_ACTIVE_RE = re.compile(r"^(\s*)schedule:\s*$", re.MULTILINE)
SCHEDULE_PAUSED_RE = re.compile(r"^(\s*)#\s*schedule:\s*$", re.MULTILINE)
CRON_ACTIVE_RE = re.compile(r"^(\s*)- cron: (.+)$", re.MULTILINE)
CRON_PAUSED_RE = re.compile(r"^(\s*)#\s*- cron: (.+)$", re.MULTILINE)


def read_state(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if SCHEDULE_ACTIVE_RE.search(text):
        return "active"
    if SCHEDULE_PAUSED_RE.search(text):
        return "paused"
    return "unknown"


def set_state(path: Path, target: str) -> bool:
    """Возвращает True, если файл реально поменялся."""
    text = path.read_text(encoding="utf-8")
    current = read_state(path)

    if current == target:
        return False
    if current == "unknown":
        raise SystemExit(f"{path.name}: не нашёл строку schedule:/cron: в ожидаемом виде — "
                          f"похоже, файл менялся вручную не так, как обычно. Правь руками.")

    if target == "paused":
        text = SCHEDULE_ACTIVE_RE.sub(lambda m: f"{m.group(1)}# schedule:", text)
        text = CRON_ACTIVE_RE.sub(lambda m: f"{m.group(1)}# - cron: {m.group(2)}", text)
    else:
        text = SCHEDULE_PAUSED_RE.sub(lambda m: f"{m.group(1)}schedule:", text)
        text = CRON_PAUSED_RE.sub(lambda m: f"{m.group(1)}- cron: {m.group(2)}", text)

    path.write_text(text, encoding="utf-8")
    return True


def git(*args) -> None:
    subprocess.run(["git", *args], check=True, cwd=Path(__file__).resolve().parent)


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["status", "pause", "resume"])
    parser.add_argument("--no-push", action="store_true",
                         help="только изменить и закоммитить локально, не отправлять на GitHub")
    args = parser.parse_args()

    if args.action == "status":
        for path in WORKFLOW_FILES:
            print(f"{path.name}: {read_state(path)}")
        return

    target = "paused" if args.action == "pause" else "active"
    changed_files = [p for p in WORKFLOW_FILES if set_state(p, target)]

    if not changed_files:
        print(f"Уже в состоянии {target!r} — менять нечего.")
        return

    for path in changed_files:
        print(f"{path.name}: -> {target}")

    rel_paths = [str(p.relative_to(Path(__file__).resolve().parent)) for p in changed_files]
    git("add", *rel_paths)
    action_word = "Приостановить" if target == "paused" else "Возобновить"
    git("commit", "-m", f"{action_word} автопубликацию по расписанию (control.py {args.action})")
    print("Закоммичено локально.")

    if args.no_push:
        print("--no-push: изменение НЕ отправлено на GitHub — расписание там ещё старое, "
              "пока не сделаешь git push вручную.")
    else:
        git("push")
        print("Отправлено на GitHub — расписание там уже поменялось.")


if __name__ == "__main__":
    run()
