# -*- coding: utf-8 -*-
"""Ротация моделей автомобилей в SEO-постах: каталог, извлечение, учёт израсходованных.

Каталог — publishing/models_catalog.json (список {"brand", "model", "aliases"}).
Обновление списка — вручную; когда все модели израсходованы, публикация останавливается
с явным сообщением (см. exhaustion_message), второй круг молча не начинается.

  python publishing/model_rotation.py --list            # все модели каталога
  python publishing/model_rotation.py --used            # израсходованные (из журнала)
  python publishing/model_rotation.py --remaining       # оставшиеся
  python publishing/model_rotation.py --find "текст"    # какие модели найдутся в тексте

«Модель израсходована» = была фокусом поста: пример расчёта (строка «Автомобиль: …»)
или заголовок. Простые перечисления марок/моделей в теле обзорной статьи не считаются.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

CATALOG_PATH = HERE / "models_catalog.json"
JOURNAL_PATH = HERE / "published_posts.jsonl"
ROTATION_LOG = ROOT / "logs" / "duplicates.log"
EXHAUSTION_MSG = ("все модели из каталога опубликованы, требуется решение: "
                  "обновите publishing/models_catalog.json или снимите записи из журнала для нового цикла")

_CAR_LINE_RE = re.compile(r"^\s*-\s*Автомобиль:\s*([^,\n]+)", re.I | re.M)

_cache: list | None = None


def load_catalog() -> list:
    global _cache
    if _cache is None:
        _cache = json.loads(CATALOG_PATH.read_text(encoding="utf-8")).get("models", [])
    return _cache


def canonical(model: dict) -> str:
    return f"{model['brand']} {model['model']}".strip().lower()


def _alias_re(alias: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9а-яё])" + re.escape(alias.lower()) + r"(?![a-z0-9а-яё])")


def find_models(text: str) -> list:
    """Все модели каталога, найденные в тексте (по alias, канонический вид 'brand model')."""
    hay = (text or "").lower()
    found = []
    for m in load_catalog():
        if any(_alias_re(a).search(hay) for a in m.get("aliases", [canonical(m)])):
            found.append(canonical(m))
    return found


def focus_models(text: str) -> list:
    """Фокусные модели поста: из строки «Автомобиль: …» блока примера и из первой строки-заголовка.

    Перечисления моделей в теле обзора сюда не входят — иначе обзорные статьи
    («корейские марки машин») ложно блокировались бы ротацией."""
    text = text or ""
    heads = [m.group(1) for m in _CAR_LINE_RE.finditer(text)]
    first = next((l for l in text.splitlines() if l.strip()), "")
    heads.append(first)
    found = []
    for h in heads:
        found.extend(find_models(h))
    return sorted(set(found))


def read_journal() -> list:
    if not JOURNAL_PATH.exists():
        return []
    out = []
    for line in JOURNAL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def canonicalize(brand: str, model: str) -> str | None:
    """Каноническое имя каталога для пары brand+model (лота) или None, если модель не из каталога."""
    return next(iter(find_models(f"{brand or ''} {model or ''}")), None)


def used_models() -> set:
    """Все модели, бывшие фокусом хотя бы одного опубликованного поста."""
    used = set()
    for e in read_journal():
        used |= set(e.get("focus_models") or [])
    return used


def remaining_models() -> list:
    return [canonical(m) for m in load_catalog() if canonical(m) not in used_models()]


def exhausted() -> bool:
    cat = {canonical(m) for m in load_catalog()}
    return bool(cat) and cat <= used_models()


def exhaustion_message() -> str:
    return EXHAUSTION_MSG


def log_event(message: str) -> None:
    from datetime import datetime
    ROTATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ROTATION_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} | models | {message}\n")


def used_line() -> str:
    """Список израсходованных моделей для промпта/логов: 'audi q3, bmw x6, …'."""
    return ", ".join(sorted(used_models()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="все модели каталога")
    g.add_argument("--used", action="store_true", help="израсходованные (фокус постов)")
    g.add_argument("--remaining", action="store_true", help="оставшиеся неопубликованными")
    g.add_argument("--find", metavar="TEXT", help="найти модели в тексте (фокусные и все)")
    args = ap.parse_args()

    if args.list:
        for m in load_catalog():
            print(f"{m['brand']} {m['model']}")
        print(f"всего: {len(load_catalog())}")
    elif args.used:
        print("израсходовано:", len(used_models()))
        for m in sorted(used_models()):
            print(" ", m)
    elif args.remaining:
        rem = remaining_models()
        print("осталось:", len(rem), "из", len(load_catalog()))
        for m in rem:
            print(" ", m)
        if not rem:
            print(EXHAUSTION_MSG)
    elif args.find is not None:
        print("фокусные:", focus_models(args.find))
        print("все:", find_models(args.find))
    return 0


if __name__ == "__main__":
    sys.exit(main())
