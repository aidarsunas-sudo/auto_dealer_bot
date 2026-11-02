# -*- coding: utf-8 -*-
"""Журнал опубликованных постов и защита от дублей.

  python publishing/post_journal.py --init          # заполнить журнал из publications.csv («опубликовано»)
  python publishing/post_journal.py --list [N]      # последние N записей (по умолчанию 10)
  python publishing/post_journal.py --check <slug>  # сверить пост со историей (ничего не меняет)

Файл publishing/published_posts.jsonl — по строке JSON на пост:
  {"ts": "ISO", "date": "YYYY-MM-DD", "slug": ..., "query": ...,
   "sha256": "…полного текста…", "body_sha256": "…нормализованного тела…", "preview": "первые 200 знаков"}

Защита от дублей: find_duplicate() сверяет новый текст с телами post.md прошлых
публикаций на диске (articles/<slug>/post.md). Дубль — точное совпадение хэша
нормализованного тела либо difflib-сходство >= DUPLICATE_THRESHOLD (0.8).
Порог откалиброван на существующих постах: разные темы дают 0.01–0.35, дубль >= 0.9.
"""
import argparse
import difflib
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from common import ARTICLES, read_publications  # noqa: E402
import model_rotation  # noqa: E402  каталог моделей, извлечение и учёт израсходованных

JOURNAL = HERE / "published_posts.jsonl"
DUP_LOG = ROOT / "logs" / "duplicates.log"
PREVIEW_LEN = 200
DEFAULT_LOOKBACK = 30
DUPLICATE_THRESHOLD = 0.8

COVER_RE = re.compile(r"\A\s*Описание обложки:[^\n]*\n*", re.I)
EXAMPLE_START = re.compile(r"^Пример расчёта на основе лота\s*$", re.I)
EXAMPLE_END = re.compile(r"^Это пример на\s", re.I)
ORDER_START = re.compile(r"^Как заказать\s*:?\s*$", re.I)


def normalize(text: str) -> str:
    """Тело поста без обложки, блока примера, блока «Как заказать», ссылек и разметки — для сравнения."""
    t = COVER_RE.sub("", text or "")
    out, in_example = [], False
    for raw in t.splitlines():
        line = raw.strip()
        if ORDER_START.match(line):
            break
        if EXAMPLE_START.match(line):
            in_example = True
            continue
        if in_example:
            if EXAMPLE_END.match(line):
                in_example = False
            continue
        if not line:
            continue
        line = re.sub(r"https?://\S+", " ", line)
        line = line.replace("**", " ").replace("\\", " ")
        line = re.sub(r"^[#>\-*•\d]+[\.\)]?\s*", "", line)
        line = re.sub(r"[_`«»\"']", " ", line)
        line = re.sub(r"\s+", " ", line)
        if line:
            out.append(line.lower())
    return " ".join(out)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_journal() -> list:
    if not JOURNAL.exists():
        return []
    rows = []
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def recent(n: int = DEFAULT_LOOKBACK) -> list:
    return read_journal()[-n:]


def find_published(slug: str) -> dict | None:
    """Последняя запись журнала для slug или None — пост ещё не публиковался."""
    for entry in reversed(read_journal()):
        if entry.get("slug") == slug:
            return entry
    return None


def append_entry(slug: str, query: str, post_text: str, date: str | None = None) -> dict:
    """Пишет запись в журнал. Вызывать только после успешной (не dry-run) публикации."""
    now = datetime.now()
    entry = {
        "ts": now.isoformat(timespec="seconds"),
        "date": date or now.date().isoformat(),
        "slug": slug,
        "query": query,
        "sha256": _sha(post_text),
        "body_sha256": _sha(normalize(post_text)),
        "models": model_rotation.find_models(post_text),
        "focus_models": model_rotation.focus_models(post_text),
        "preview": re.sub(r"\s+", " ", COVER_RE.sub("", post_text or "")).strip()[:PREVIEW_LEN],
    }
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def find_duplicate(post_text: str, exclude_slug: str | None = None,
                   lookback: int = DEFAULT_LOOKBACK, threshold: float | None = None):
    """(запись журнала с максимальным сходством или None, сходство 0..1).

    Сверка идёт с полными текстами post.md прошлых публикаций (articles/<slug>/post.md),
    а не с превью в журнале."""
    threshold = DUPLICATE_THRESHOLD if threshold is None else threshold
    body, body_hash = normalize(post_text), _sha(normalize(post_text))
    best, best_ratio = None, 0.0
    for entry in recent(lookback):
        slug = entry.get("slug")
        if not slug or slug == exclude_slug:
            continue
        path = ARTICLES / slug / "post.md"
        if not path.exists():
            continue
        old_body = normalize(path.read_text(encoding="utf-8"))
        if not old_body:
            continue
        if _sha(old_body) == body_hash:
            return entry, 1.0
        r = difflib.SequenceMatcher(None, body, old_body).ratio()
        if r > best_ratio:
            best, best_ratio = entry, r
    return (best, best_ratio) if best_ratio >= threshold else (None, best_ratio)


def log_duplicate(new_slug: str, dup: dict | None, ratio: float, where: str) -> None:
    DUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    target = f"«{dup.get('query')}» ({dup.get('slug')}, {dup.get('date')})" if dup else "—"
    with open(DUP_LOG, "a", encoding="utf-8") as f:
        f.write(f"{stamp} | {where} | {new_slug}: сходство {ratio:.2f} с {target} — публикация пропущена\n")


def history_block(n: int = DEFAULT_LOOKBACK) -> str:
    """Готовый блок «уже опубликовано» для промпта generate_draft.py: посты + израсходованные модели."""
    entries = recent(n)
    if not entries:
        return ""
    lines = []
    for e in entries:
        preview = (e.get("preview") or "")[:150]
        lines.append(f"- {e.get('date')} | {e.get('query')} (slug {e.get('slug')}) | начало поста: {preview}")
    used = model_rotation.used_line()
    lines.append("")
    lines.append(f"ИЗРАСХОДОВАННЫЕ МОДЕЛИ (уже были в канале, о них не писать): {used or 'пока нет'}")
    return "\n".join(lines)


def init_journal() -> int:
    """Добавляет в журнал записи по publications.csv со статусом «опубликовано», которых ещё нет."""
    have = {e.get("slug") for e in read_journal()}
    added = 0
    for row in read_publications():
        slug = (row.get("slug") or "").strip()
        if (row.get("статус") or "").strip().lower() != "опубликовано" or not slug or slug in have:
            continue
        path = ARTICLES / slug / "post.md"
        if not path.exists():
            continue
        append_entry(slug, row.get("главный запрос") or "", path.read_text(encoding="utf-8"),
                     date=(row.get("дата") or "").strip() or None)
        added += 1
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", action="store_true", help="заполнить журнал из publications.csv («опубликовано»)")
    group.add_argument("--list", type=int, nargs="?", const=10, metavar="N", help="показать последние N записей")
    group.add_argument("--check", metavar="SLUG", help="сверить post.md slug с историей (ничего не меняет)")
    args = ap.parse_args()

    if args.init:
        added = init_journal()
        print(f"журнал: добавлено записей {added}; всего {len(read_journal())} ({JOURNAL})")
        return 0
    if args.list is not None:
        for e in read_journal()[-args.list:]:
            print(f"{e.get('date')} | {e.get('slug')} | {e.get('query')} | {e.get('preview', '')[:80]}…")
        return 0
    path = ARTICLES / args.check / "post.md"
    if not path.exists():
        print(f"нет файла {path}")
        return 1
    dup, r = find_duplicate(path.read_text(encoding="utf-8"), exclude_slug=args.check)
    if dup:
        print(f"ДУБЛЬ: сходство {r:.2f} с «{dup.get('query')}» ({dup.get('slug')}, {dup.get('date')})")
        return 3
    print(f"дубля нет (максимальное сходство с историей {r:.2f} < {DUPLICATE_THRESHOLD})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
