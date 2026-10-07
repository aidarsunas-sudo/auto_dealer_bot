# -*- coding: utf-8 -*-
"""
registry.py — единый реестр опубликованного в Telegram-канале (published.jsonl).

ИСТОЧНИК ПРАВДЫ — САМ КАНАЛ. Автопайплайн, ручные посты и посты, сделанные
вне репозитория (отдельная сессия «торги HeyDealer»), в конечном счёте видны на
публичной странице https://t.me/s/auto_generals. Команда reconcile читает её
(пагинация ?before=<message_id>), разбирает посты и вносит в реестр те, которых
там ещё нет. Альбом из нескольких сообщений считается ОДНИМ постом: публичная
страница показывает его одним блоком с номером первого сообщения.

Формат published.jsonl — одна JSON-строка на пост, дописывается построчно
(коммитится из Actions так же, как published_log.json). Если у message_id есть
несколько строк, действует ПОСЛЕДНЯЯ (так запись уточняют, не переписывая файл).
Поля:
    message_id   номер сообщения в канале (альбом — первое сообщение)
    post_url     https://t.me/auto_generals/<message_id>
    posted_at    время публикации, ISO 8601, UTC (на странице канала время в UTC)
    lot_id       стабильный id лота ("heydealer:<hash>"), если известен
    lot_url      ссылка на лот на площадке, если известна
    platform     площадка: heydealer / charancha / kbchachacha / encar / onbid / null
    model        модель из заголовка поста
    year, mileage_km   из блока характеристик поста
    method       auto (автопайплайн) / manual (вне пайплайна) / unknown
    template     lead (бронирование) / auction (торги) / free (свободный текст)
    deadline_msk необязательное: окончание торгов по Москве, ISO без пояса
    source       reconcile / known / register_post — как запись попала в реестр
    recorded_at  когда запись внесена (UTC)

Способ (method) по умолчанию unknown. Исключения: лот сопоставлен с записью
published_log.json (см. match_lots(): это автопайплайн) и записи из KNOWN_POSTS.
Видео без подписи — дополнение к предыдущему посту, отдельной записью не ведётся.
Тестовые посты ([ТЕСТ], test_post.py) вносятся с is_test=true: лот в канале
реально показан, дубль по нему тоже нужно ловить.

Команды (reconcile по умолчанию dry-run, пишет только с --apply):
    python registry.py reconcile [--days 45] [--apply]
    python registry.py list [--last 20]
"""

import argparse
import html
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import requests

from post_validator import PublishBlockedError

logger = logging.getLogger("registry")

CHANNEL = "auto_generals"
CHANNEL_URL = f"https://t.me/s/{CHANNEL}"
_BASE = Path(__file__).resolve().parent
REGISTRY_PATH = _BASE / "published.jsonl"
PUBLISHED_LOG_PATH = _BASE / "published_log.json"

METHODS = ("auto", "manual", "unknown")
TEMPLATES = ("lead", "auction", "free")
MATCH_WINDOW_SEC = 900      # запись published_log позже поста: публикация + видео (до ~7 мин)
MATCH_CLOCK_SKEW_SEC = 1    # допуск на расхождение часов раннера и Telegram
_USER_AGENT = "Mozilla/5.0 (compatible; pickplus-registry/1.0)"

# Известные посты (по списку владельца). Ключ — message_id. Здесь только то, что
# нельзя вывести из текста поста: способ, шаблон, площадка, id лота, дедлайн торгов.
# Посты 5239-5266 и 5286 сделаны отдельной сессией HeyDealer-предложений вне
# пайплайна (published_log.json их не знает) — поэтому method=manual.
KNOWN_POSTS = {
    # 5230 — не из списка владельца: пост про Renault Samsung SM6 (лот HeyDealer Q8Y0LN6l),
    # сделан той же сессией (отчёт и текст — расшифровка сессии, этап 1б). Дедлайн из
    # неё же: 7 окт 16:48 по Сеулу = 10:48 МСК.
    5230: {"template": "auction", "method": "manual", "platform": "heydealer",
           "lot_id": "heydealer:Q8Y0LN6l", "deadline_msk": "2026-10-07T10:48"},
    5239: {"template": "auction", "method": "manual", "platform": "heydealer",
           "deadline_msk": "2026-10-08T07:37"},
    5249: {"template": "auction", "method": "manual", "platform": "heydealer",
           "deadline_msk": "2026-10-07T05:40"},
    5254: {"template": "free", "method": "manual", "model": "Changan Q05 Max+", "video": True},
    5255: {"template": "auction", "method": "manual", "platform": "heydealer",
           "deadline_msk": "2026-10-07T10:05"},
    5261: {"template": "auction", "method": "manual", "platform": "heydealer",
           "deadline_msk": "2026-10-08T06:54"},
    5266: {"template": "auction", "method": "manual", "platform": "heydealer",
           "deadline_msk": "2026-10-11T06:27"},
    5276: {"template": "lead", "method": "auto"},
    5286: {"template": "auction", "method": "manual", "platform": "heydealer",
           "lot_id": "heydealer:nEB6WaPQ", "video": True, "deadline_msk": "2026-10-09T07:17"},
}

_PLATFORM_BY_DOMAIN = (("charancha.com", "charancha"), ("kbchachacha.com", "kbchachacha"),
                       ("encar.com", "encar"), ("onbid", "onbid"), ("heydealer.com", "heydealer"))


# ── файл реестра ──────────────────────────────────────────────────────────

def load_records(path: Optional[Path] = None) -> dict:
    """{message_id: запись} — по каждому message_id побеждает последняя строка.
    Битая строка пропускается с предупреждением (реестр не должен падать целиком)."""
    path = Path(path or REGISTRY_PATH)
    records: dict = {}
    if not path.exists():
        return records
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            records[int(record["message_id"])] = record
        except (ValueError, KeyError, TypeError):
            logger.warning(f"registry: строка {number} в {path.name} не разобрана — пропущена")
    return records


def append_record(record: dict, path: Optional[Path] = None) -> None:
    """Дописывает запись одной строкой. Проверяет перечисления способа и шаблона."""
    if record.get("method") not in METHODS:
        raise ValueError(f"method должен быть одним из {METHODS}, получено {record.get('method')!r}")
    if record.get("template") not in TEMPLATES:
        raise ValueError(f"template должен быть одним из {TEMPLATES}, получено {record.get('template')!r}")
    if not isinstance(record.get("message_id"), int):
        raise ValueError("message_id обязателен и должен быть целым числом")
    path = Path(path or REGISTRY_PATH)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")


# ── страница канала ───────────────────────────────────────────────────────

def fetch_channel_page(before: Optional[int] = None) -> str:
    params = {"before": before} if before else None
    response = requests.get(CHANNEL_URL, params=params, timeout=30, headers={"User-Agent": _USER_AGENT})
    response.raise_for_status()
    return response.text


def parse_channel_html(page: str) -> list:
    """Посты со страницы t.me/s/<канал>: id, время UTC, текст, число фото, есть ли видео.
    Блоки без номера сообщения (служебные) пропускаются."""
    posts = []
    for block in re.split(r'(?=<div class="tgme_widget_message_wrap)', page)[1:]:
        found = re.search(r'data-post="%s/(\d+)"' % re.escape(CHANNEL), block)
        if not found:
            continue
        stamp = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        text_div = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
        text = ""
        if text_div:
            text = re.sub(r"<br\s*/?>", "\n", text_div.group(1))
            text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        posts.append({
            "message_id": int(found.group(1)),
            "posted_at": datetime.fromisoformat(stamp.group(1)).astimezone(timezone.utc) if stamp else None,
            "text": text,
            "photos": len(re.findall(r"tgme_widget_message_photo_wrap", block)),
            "has_video": bool(re.search(r"tgme_widget_message_video", block)),
        })
    return posts


def classify_post(text: str) -> dict:
    """Шаблон, модель, год и пробег из текста поста. Что не нашлось — None.
    Префикс «[ТЕСТ] » (test_post.py) снимается, признак возвращается в is_test."""
    is_test = text.lstrip().startswith("[ТЕСТ]")
    if is_test:
        text = re.sub(r"^\s*\[ТЕСТ\]\s*", "", text)
    head = text[:300]
    if "торги идут" in head or "участвуем за вас" in head:
        template = "auction"
    elif "уже можно забронировать" in head:
        template = "lead"
    else:
        template = "free"

    first_line = text.splitlines()[0] if text.strip() else ""
    title = re.sub(r"^[^\w(]+", "", first_line).strip()
    model = title.split(" — ")[0].strip() if " — " in title else (title or None)

    year = None
    for pattern in (r"Год:\s*(\d{4})", r"Выпуск:\s*(?:[А-Яа-яё]+\s+)?(\d{4})"):
        found = re.search(pattern, text)
        if found:
            year = int(found.group(1))
            break
    mileage = re.search(r"Пробег:?\s*([\d  ]+?)\s*км", text)
    return {"template": template, "model": model or None, "year": year,
            "mileage_km": int(re.sub(r"\D", "", mileage.group(1))) if mileage else None,
            "is_test": is_test}


# ── сопоставление с published_log.json (автопайплайн) ─────────────────────

def load_published_log(path: Optional[Path] = None) -> list:
    """[(время UTC, url)] из published_log.json (там время — наивное, UTC раннера Actions)."""
    path = Path(path or PUBLISHED_LOG_PATH)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for url, value in data.items():
        stamp = value.get("posted_at")
        if stamp:
            entries.append((datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc), url))
    return entries


def match_lots(posts: list, entries: list) -> dict:
    """
    {message_id: URL лота} — какой лот из published_log стоит за каким постом.

    Автопайплайн публикует пост (альбом), при необходимости видео к нему, и только
    ПОСЛЕ этого пишет запись в published_log — поэтому запись всегда позже своего
    поста (в тестах на реальных данных: от 0 до ~110 с). Правила:
      - запись -> ближайший ПРЕДШЕСТВУЮЩИЙ пост-альбом с текстом (lead/auction);
        пост не может быть позже записи (допуск на скос часов — MATCH_CLOCK_SKEW_SEC);
      - один пост — не больше одной записи и наоборот (записи идут по порядку времени);
      - дальше MATCH_WINDOW_SEC от записи пост не ищется.
    Больший допуск вперёд ломает пары лотов, опубликованных в одну минуту (проверено
    по строкам «Опубликовано:» в logs/main_pipeline.log: 67 из 67 верно при допуске 1 с,
    при 5 с — 18 из 67 неверно). Не нашлось — лота нет в результате, ничего не угадываем.
    """
    candidates = [p for p in posts if p["text"] and p["posted_at"]
                  and classify_post(p["text"])["template"] in ("lead", "auction")]
    used: set = set()
    matched: dict = {}
    for stamp, url in sorted(entries):
        best = None
        for post in candidates:
            if post["message_id"] in used:
                continue
            delta = (stamp - post["posted_at"]).total_seconds()
            if -MATCH_CLOCK_SKEW_SEC <= delta <= MATCH_WINDOW_SEC and (best is None or delta < best[0]):
                best = (delta, post)
        if best:
            used.add(best[1]["message_id"])
            matched[best[1]["message_id"]] = url
    return matched


def platform_from_url(url: Optional[str]) -> Optional[str]:
    for domain, name in _PLATFORM_BY_DOMAIN:
        if url and domain in url:
            return name
    return None


# ── сверка с каналом ──────────────────────────────────────────────────────

def build_record(post: dict, lot_urls: dict, now: Optional[datetime] = None) -> dict:
    """Запись реестра по посту канала: разбор текста + сопоставление с published_log
    (lot_urls = match_lots(...)) + KNOWN_POSTS."""
    info = classify_post(post["text"])
    known = KNOWN_POSTS.get(post["message_id"], {})
    lot_url = lot_urls.get(post["message_id"])
    record = {
        "message_id": post["message_id"],
        "post_url": f"https://t.me/{CHANNEL}/{post['message_id']}",
        "posted_at": post["posted_at"].isoformat() if post["posted_at"] else None,
        "lot_id": known.get("lot_id") or lot_url,
        "lot_url": lot_url,
        "platform": known.get("platform") or platform_from_url(lot_url),
        "model": known.get("model") or info["model"],
        "year": info["year"],
        "mileage_km": info["mileage_km"],
        "method": known.get("method") or ("auto" if lot_url else "unknown"),
        "template": known.get("template") or info["template"],
        "source": "known" if known else "reconcile",
    }
    if known.get("deadline_msk"):
        record["deadline_msk"] = known["deadline_msk"]
    if info["is_test"]:
        record["is_test"] = True
    record["recorded_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return record


# ── защита от дублей ──────────────────────────────────────────────────────

MILEAGE_TOLERANCE_KM = 50  # один и тот же лот на разных площадках показывает пробег с небольшим расхождением


class DuplicatePostError(PublishBlockedError):
    """Лот уже опубликован в канале (по реестру). Подкласс PublishBlockedError: все
    вызывающие места, ловящие блокировку гейта, переживут её так же — но main_pipeline
    ловит её отдельно: пропуск дубля — штатная ситуация, а не сбой."""


def _norm_key(value) -> str:
    text = re.sub(r"^https?://(www\.)?", "", str(value).strip().lower())
    return text.rstrip("/")


def lot_keys(lot: dict) -> set:
    """Все идентификаторы лота, по которым его можно узнать в реестре: lot_id, URL,
    пара «площадка:external_id». Пустые значения игнорируются."""
    keys = {_norm_key(v) for v in (lot.get("lot_id"), lot.get("url"), lot.get("lot_url")) if v}
    if lot.get("source") and lot.get("external_id"):
        keys.add(f"{lot['source']}:{lot['external_id']}".lower())
    return keys


def _tokens(title: Optional[str]) -> list:
    return re.findall(r"[0-9a-zа-яё]+", (title or "").lower())


def _same_model(title_a: Optional[str], title_b: Optional[str]) -> bool:
    """Модели считаются одной, если у более короткого названия два первых слова
    (марка + первое слово модели) есть в более длинном: «Kia Ray» ⊂ «Kia Ray Signature (The New Ray)»,
    но «Kia K3» ≠ «Kia K5». Пустое название ничему не равно."""
    a, b = _tokens(title_a), _tokens(title_b)
    if len(a) < 2 or len(b) < 2:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return all(token in long_ for token in short[:2])


def lot_title(lot: dict) -> str:
    """brand + model + trim без повторов — как заголовок поста (post_text_builder._build_title)."""
    parts = []
    for value in (lot.get("brand"), lot.get("model"), lot.get("trim")):
        value = (value or "").strip()
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


def find_duplicate(lot: dict, records: Optional[dict] = None) -> Optional[dict]:
    """
    Ищет лот в реестре. Возвращает {"record": запись, "rule": ..., "detail": ...} или None.

    1. По идентификатору — lot_id / URL / «площадка:external_id» (точное совпадение
       после нормализации). Это основной признак: у автопостов он есть всегда.
    2. Если идентификатор не сошёлся — по сочетанию модель + год + пробег (±MILEAGE_TOLERANCE_KM
       км): у ручных постов lot_id и URL может не быть, а тот же автомобиль на другой
       площадке имеет другую ссылку. Нужны год и пробег ОБОИХ (нет пробега — по этому
       правилу не сравниваем, не гадаем).
    Шаблон поста значения не имеет: лот, вышедший как «торги», нельзя повторить как
    «бронирование». Тестовые посты и записи с method=unknown тоже считаются публикацией.
    """
    records = load_records() if records is None else records
    keys = lot_keys(lot)
    if keys:
        for record in records.values():
            record_keys = {_norm_key(v) for v in (record.get("lot_id"), record.get("lot_url")) if v}
            shared = keys & record_keys
            if shared:
                return {"record": record, "rule": "lot_id/url", "detail": sorted(shared)[0]}

    year, mileage, title = lot.get("year"), lot.get("mileage_km"), lot_title(lot)
    if year is None or mileage is None or not title:
        return None
    try:
        year, mileage = int(year), int(mileage)
    except (TypeError, ValueError):
        return None
    for record in records.values():
        if record.get("year") != year or record.get("mileage_km") is None:
            continue
        if abs(record["mileage_km"] - mileage) <= MILEAGE_TOLERANCE_KM and _same_model(title, record.get("model")):
            return {"record": record, "rule": "модель+год+пробег",
                    "detail": f"{record.get('model')} {year} / {record['mileage_km']} км"}
    return None


def describe_duplicate(duplicate: dict) -> str:
    record = duplicate["record"]
    return (f"уже опубликован: {record.get('post_url')} ({str(record.get('posted_at'))[:16]} UTC, "
            f"{record.get('template')}, способ {record.get('method')}); совпадение: {duplicate['rule']} — {duplicate['detail']}")


class Rows(list):
    """Результат reconcile(): список (статус, запись) + число пропущенных видео без подписи."""
    skipped_media = 0


def _same(existing: dict, new: dict) -> bool:
    ignore = {"recorded_at"}
    return {k: v for k, v in existing.items() if k not in ignore} == {k: v for k, v in new.items() if k not in ignore}


def reconcile(days: int = 45, apply: bool = False, max_pages: int = 120, delay: float = 0.7,
              fetch: Callable[[Optional[int]], str] = fetch_channel_page,
              registry_path: Optional[Path] = None, log_entries: Optional[list] = None,
              now: Optional[datetime] = None) -> list:
    """
    Читает канал с конца до постов старше `days` суток, сопоставляет с реестром.
    Возвращает [(статус, запись)], статус: new / update / same. Пишет в файл только
    при apply=True (иначе dry-run). Повторный запуск ничего не дублирует.
    """
    registry_path = Path(registry_path or REGISTRY_PATH)
    existing = load_records(registry_path)
    if log_entries is None:
        log_entries = load_published_log()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)

    collected: dict = {}
    skipped_media = 0
    before = None
    for page_number in range(max_pages):
        posts = parse_channel_html(fetch(before))
        if not posts:
            break
        for post in posts:
            if post["posted_at"] and post["posted_at"] < cutoff:
                continue
            if post["message_id"] in collected:
                continue
            if not post["text"]:
                skipped_media += 1  # видео/медиа без подписи — дополнение к предыдущему посту
                continue
            collected[post["message_id"]] = post
        oldest = min(posts, key=lambda p: p["message_id"])
        if oldest["message_id"] == before or (oldest["posted_at"] and oldest["posted_at"] < cutoff):
            break
        before = oldest["message_id"]
        if delay and page_number + 1 < max_pages:
            time.sleep(delay)

    lot_urls = match_lots(list(collected.values()), log_entries)
    result = Rows()
    result.skipped_media = skipped_media
    for message_id in sorted(collected):
        record = build_record(collected[message_id], lot_urls)
        old = existing.get(message_id)
        if old is None:
            status = "new"
        elif _same(old, record):
            status = "same"
        elif old.get("method") == "unknown" or old.get("source") == "reconcile":
            status = "update"  # уточняем запись, оставленную сверкой
        else:
            status = "same"    # ручные/known записи сверка не перетирает
        result.append((status, record))
        if apply and status in ("new", "update"):
            append_record(record, registry_path)
    return result


def _print_table(rows: list) -> None:
    for status, record in rows:
        print(f"{status:<6} {record['message_id']}  {str(record['posted_at'])[:16]}  "
              f"{record['template']:<7} {record['method']:<7} {record['platform'] or '-':<11} "
              f"{(record['model'] or '-')[:34]:<34} {record['year'] or '-'} / {record['mileage_km'] or '-'} км  "
              f"{record['lot_id'] or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Реестр публикаций Pick+ Auto (published.jsonl)")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("reconcile", help="сверить реестр с публичной страницей канала (по умолчанию dry-run)")
    rec.add_argument("--days", type=int, default=45, help="глубина сверки, суток (по умолчанию 45)")
    rec.add_argument("--apply", action="store_true", help="записать изменения в published.jsonl")
    rec.add_argument("--max-pages", type=int, default=120)
    lst = sub.add_parser("list", help="показать последние записи реестра")
    lst.add_argument("--last", type=int, default=20)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "reconcile":
        rows = reconcile(days=args.days, apply=args.apply, max_pages=args.max_pages)
        _print_table(rows)
        counts = {s: sum(1 for st, _ in rows if st == s) for s in ("new", "update", "same")}
        print(f"\nпостов за {args.days} сут.: {len(rows)} | новых {counts['new']}, "
              f"уточнено {counts['update']}, без изменений {counts['same']} | "
              f"видео без подписи (дополнения к постам, не вносятся): {rows.skipped_media}")
        print("ЗАПИСАНО в published.jsonl" if args.apply else "DRY-RUN — ничего не записано (добавьте --apply)")
        return 0

    records = sorted(load_records().values(), key=lambda r: r["message_id"])[-args.last:]
    _print_table([("-", r) for r in records])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
