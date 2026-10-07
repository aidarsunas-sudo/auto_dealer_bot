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
Записи ведутся ОТДЕЛЬНО по каждой площадке публикации (channel). Старые записи без channel/status — это
Telegram со статусом published. Поля:
    channel      площадка публикации: telegram / instagram / youtube
    status       published (вышло) / pending_manual (площадка не подключена или лимит исчерпан — выложить
                 вручную; для остальных площадок публикация идёт)
    message_id   номер сообщения в канале Telegram (альбом — первое сообщение); у Instagram/YouTube — null,
                 ссылка на пост — post_url (дописывается register-platform)
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
    python registry.py register-post https://t.me/auto_generals/<номер> [--lot-id ...] [--apply]
    python registry.py register-platform --channel instagram --lot-id heydealer:nEB6WaPQ --post-url <ссылка> [--apply]
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
# Площадки ПУБЛИКАЦИИ (где вышел пост) — не путать с полем platform (откуда лот: heydealer/charancha…).
CHANNELS = ("telegram", "instagram", "youtube")
# published — пост вышел; pending_manual — площадка не подключена/лимит исчерпан, пост нужно выложить
# вручную (инструмент не считает это ошибкой и публикует на остальные площадки).
STATUSES = ("published", "pending_manual")
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

# Kia Ray Signature (пост Telegram 5286) вышел ещё в Instagram и YouTube; ссылки на эти посты владелец даст
# позже (register-platform --post-url …). Записи по каждой площадке — отдельные.
KNOWN_EXTRA_RECORDS = [
    {"channel": channel, "status": "published", "message_id": None, "post_url": None, "posted_at": None,
     "lot_id": "heydealer:nEB6WaPQ", "lot_url": None, "platform": "heydealer",
     "model": "Kia Ray Signature (The New Ray)", "year": 2022, "mileage_km": 20266,
     "method": "manual", "template": "auction", "deadline_msk": "2026-10-09T07:17", "source": "known"}
    for channel in ("instagram", "youtube")
]

_PLATFORM_BY_DOMAIN = (("charancha.com", "charancha"), ("kbchachacha.com", "kbchachacha"),
                       ("encar.com", "encar"), ("onbid", "onbid"), ("heydealer.com", "heydealer"))


# ── файл реестра ──────────────────────────────────────────────────────────

def channel_of(record: dict) -> str:
    """Площадка публикации; у записей, сделанных до появления поля, это Telegram."""
    return record.get("channel") or "telegram"


def status_of(record: dict) -> str:
    return record.get("status") or "published"


def record_key(record: dict):
    """Ключ записи: Telegram — message_id (int); остальные площадки — «канал:lot_id» (одна запись
    на лот и площадку; ссылку на пост можно дописать позже)."""
    if channel_of(record) == "telegram":
        return int(record["message_id"])
    return f"{channel_of(record)}:{record['lot_id']}"


def load_records(path: Optional[Path] = None) -> dict:
    """{ключ: запись} (см. record_key) — по каждому ключу побеждает последняя строка.
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
            records[record_key(record)] = record
        except (ValueError, KeyError, TypeError):
            logger.warning(f"registry: строка {number} в {path.name} не разобрана — пропущена")
    return records


def append_record(record: dict, path: Optional[Path] = None) -> None:
    """Дописывает запись одной строкой. Проверяет перечисления способа, шаблона, площадки и статуса."""
    if record.get("method") not in METHODS:
        raise ValueError(f"method должен быть одним из {METHODS}, получено {record.get('method')!r}")
    if record.get("template") not in TEMPLATES:
        raise ValueError(f"template должен быть одним из {TEMPLATES}, получено {record.get('template')!r}")
    channel = channel_of(record)
    if channel not in CHANNELS:
        raise ValueError(f"channel должен быть одним из {CHANNELS}, получено {channel!r}")
    if status_of(record) not in STATUSES:
        raise ValueError(f"status должен быть одним из {STATUSES}, получено {record.get('status')!r}")
    if channel == "telegram":
        if not isinstance(record.get("message_id"), int):
            raise ValueError("message_id обязателен и должен быть целым числом (для Telegram)")
    elif not record.get("lot_id"):
        raise ValueError(f"для площадки {channel} нужен lot_id (запись ведётся по лоту)")
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
        "channel": "telegram",
        "status": "published",
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
    """Заголовок лота — тем же сборщиком, что и пост (brand + model + trim без повторов, корейские
    комплектации переведены по brand_names_ko.TRIM_RU), чтобы сверка шла «латиница с латиницей»."""
    from post_text_builder import _build_title
    return _build_title(lot)


def find_duplicate(lot: dict, records: Optional[dict] = None, channel: str = "telegram") -> Optional[dict]:
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
    Проверка идёт ПО ПЛОЩАДКЕ (channel): лот, уже вышедший в Telegram, не считается дублем для
    Instagram/YouTube. Учитываются только записи со статусом published (pending_manual не блокирует).
    """
    records = load_records() if records is None else records
    records = {k: r for k, r in records.items() if channel_of(r) == channel and status_of(r) == "published"}
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

    def normal(record):  # старые записи без channel/status равны новым со значениями по умолчанию
        return {**{"channel": "telegram", "status": "published"}, **{k: v for k, v in record.items() if k not in ignore}}
    return normal(existing) == normal(new)


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


# ── ручная регистрация поста, вышедшего вне системы ───────────────────────

_OVERRIDE_KEYS = ("lot_id", "lot_url", "platform", "model", "year", "mileage_km", "template", "method", "deadline_msk")


def parse_post_link(link: str) -> int:
    """https://t.me/auto_generals/5286 (или .../s/auto_generals/5286, или просто номер) -> 5286."""
    found = re.search(r"t\.me/(?:s/)?%s/(\d+)" % re.escape(CHANNEL), str(link)) or re.fullmatch(r"\s*(\d+)\s*", str(link))
    if not found:
        raise ValueError(f"не похоже на ссылку на пост канала @{CHANNEL}: {link!r} (ожидается https://t.me/{CHANNEL}/<номер>)")
    return int(found.group(1))


def register_post(link: str, overrides: Optional[dict] = None, apply: bool = False,
                  fetch: Callable[[Optional[int]], str] = fetch_channel_page,
                  registry_path: Optional[Path] = None, now: Optional[datetime] = None) -> tuple:
    """
    Вносит в реестр пост, опубликованный ВНЕ системы (вручную в Telegram, отдельной сессией).
    Пост читается с публичной страницы канала (текст, время, шаблон, модель, год, пробег),
    данные лота, которых в тексте нет (lot_id, площадка, дедлайн торгов…), берутся из overrides.
    По умолчанию способ = manual. Уже есть запись по этому message_id — меняются только поля
    из overrides (остальное не теряется). Dry-run по умолчанию; запись — только apply=True.

    Возвращает (статус, запись): new / update / same. Для альбома указывайте ссылку на ПЕРВОЕ
    сообщение (так он показан на странице канала). Поднимает ValueError (плохая ссылка/значения)
    и LookupError (поста нет на странице).
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
    unknown = set(overrides) - set(_OVERRIDE_KEYS)
    if unknown:
        raise ValueError(f"неизвестные поля: {sorted(unknown)}; допустимо: {_OVERRIDE_KEYS}")
    message_id = parse_post_link(link)
    registry_path = Path(registry_path or REGISTRY_PATH)
    existing = load_records(registry_path).get(message_id)

    if existing is not None:
        record = dict(existing)
    else:
        post = next((p for p in parse_channel_html(fetch(message_id + 1)) if p["message_id"] == message_id), None)
        if post is None:
            raise LookupError(f"пост {message_id} не найден на публичной странице канала "
                              f"(для альбома укажите номер ПЕРВОГО сообщения альбома)")
        if not post["text"] and not overrides.get("model"):
            raise ValueError("у поста нет подписи — укажите модель (--model), иначе запись будет пустой")
        record = build_record(post, {}, now)
        if message_id not in KNOWN_POSTS:
            record["method"] = "manual"  # команда нужна именно для постов вне системы
        record["source"] = "register_post"
    record.update(overrides)
    for key, allowed in (("method", METHODS), ("template", TEMPLATES)):
        if record.get(key) not in allowed:
            raise ValueError(f"{key} должен быть одним из {allowed}, получено {record.get(key)!r}")
    for key in ("year", "mileage_km"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    if overrides and existing is not None:
        record["source"] = "register_post"
    status = "new" if existing is None else ("same" if _same(existing, record) else "update")
    if apply and status != "same":
        record["recorded_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        append_record(record, registry_path)
    return status, record


def ensure_known_extras(registry_path: Optional[Path] = None, apply: bool = False, now: Optional[datetime] = None) -> list:
    """Добавляет в реестр записи Instagram/YouTube из KNOWN_EXTRA_RECORDS, если их ещё нет (идемпотентно).
    Возвращает [(статус, запись)]; пишет только при apply=True."""
    registry_path = Path(registry_path or REGISTRY_PATH)
    existing = load_records(registry_path)
    rows = []
    for template in KNOWN_EXTRA_RECORDS:
        record = dict(template)
        old = existing.get(record_key(record))
        status = "new" if old is None else ("same" if _same(old, record) else "same")  # уже внесённое не перетираем
        if status == "new":
            record["recorded_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
            if apply:
                append_record(record, registry_path)
        rows.append((status, record if old is None else old))
    return rows


def register_platform(channel: str, lot_id: str, overrides: Optional[dict] = None, apply: bool = False,
                      registry_path: Optional[Path] = None, now: Optional[datetime] = None) -> tuple:
    """
    Запись о публикации лота на площадке (Instagram/YouTube/…): одна запись на лот и площадку.
    Уже есть запись — меняются только переданные поля (так позже дописывают ссылку: post_url).
    overrides: post_url, status, posted_at, model, year, mileage_km, template, method, platform,
    deadline_msk. Нет записи — берётся основа из любой записи этого лота в реестре (модель/год/пробег).
    Возвращает (new|update|same, запись). Dry-run по умолчанию.
    """
    if channel not in CHANNELS or channel == "telegram":
        raise ValueError(f"для Telegram используйте register-post; площадка должна быть одной из {CHANNELS[1:]}")
    registry_path = Path(registry_path or REGISTRY_PATH)
    records = load_records(registry_path)
    key = f"{channel}:{lot_id}"
    old = records.get(key)
    if old is not None:
        record = dict(old)
    else:
        sibling = next((r for r in records.values() if r.get("lot_id") == lot_id), None)
        if sibling is None and not (overrides or {}).get("model"):
            raise LookupError(f"лота {lot_id} нет в реестре — укажите --model/--year/--mileage")
        record = {"channel": channel, "status": "published", "message_id": None, "post_url": None, "posted_at": None,
                  "lot_id": lot_id, "lot_url": None, "platform": (sibling or {}).get("platform"),
                  "model": (sibling or {}).get("model"), "year": (sibling or {}).get("year"),
                  "mileage_km": (sibling or {}).get("mileage_km"), "method": "manual",
                  "template": (sibling or {}).get("template") or "auction", "source": "register_platform"}
    record.update({k: v for k, v in (overrides or {}).items() if v is not None})
    record["channel"] = channel
    status = "new" if old is None else ("same" if _same(old, record) else "update")
    if apply and status != "same":
        record["recorded_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        append_record(record, registry_path)
    return status, record


def _print_table(rows: list) -> None:
    for status, record in rows:
        ident = record.get("message_id") or record.get("channel")
        print(f"{status:<6} {str(ident):<10}  {str(record.get('posted_at'))[:16]}  "
              f"{record['template']:<7} {record['method']:<7} {(record['platform'] or '-'):<11} {status_of(record):<14} "
              f"{(record['model'] or '-')[:34]:<34} {record['year'] or '-'} / {record['mileage_km'] or '-'} км  "
              f"{record['lot_id'] or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Реестр публикаций Pick+ Auto (published.jsonl)")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("reconcile", help="сверить реестр с публичной страницей канала (по умолчанию dry-run)")
    rec.add_argument("--days", type=int, default=45, help="глубина сверки, суток (по умолчанию 45)")
    rec.add_argument("--apply", action="store_true", help="записать изменения в published.jsonl")
    rec.add_argument("--max-pages", type=int, default=120)
    reg = sub.add_parser("register-post", help="внести в реестр пост, опубликованный вне системы (dry-run по умолчанию)")
    reg.add_argument("link", help=f"ссылка на пост: https://t.me/{CHANNEL}/<номер> (для альбома — первое сообщение)")
    reg.add_argument("--lot-id", dest="lot_id"); reg.add_argument("--lot-url", dest="lot_url")
    reg.add_argument("--platform"); reg.add_argument("--model")
    reg.add_argument("--year", type=int); reg.add_argument("--mileage", dest="mileage_km", type=int)
    reg.add_argument("--template", choices=TEMPLATES); reg.add_argument("--method", choices=METHODS)
    reg.add_argument("--deadline-msk", dest="deadline_msk", help="окончание торгов по Москве, ISO без пояса: 2026-10-09T07:17")
    reg.add_argument("--apply", action="store_true", help="записать в published.jsonl")
    rp = sub.add_parser("register-platform", help="записать публикацию лота на Instagram/YouTube (dry-run по умолчанию)")
    rp.add_argument("--channel", required=True, choices=CHANNELS[1:]); rp.add_argument("--lot-id", dest="lot_id", required=True)
    rp.add_argument("--post-url", dest="post_url"); rp.add_argument("--status", choices=STATUSES)
    rp.add_argument("--model"); rp.add_argument("--year", type=int); rp.add_argument("--mileage", dest="mileage_km", type=int)
    rp.add_argument("--apply", action="store_true", help="записать в published.jsonl")
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
        extras = ensure_known_extras(apply=args.apply)
        _print_table(extras)
        print("ЗАПИСАНО в published.jsonl" if args.apply else "DRY-RUN — ничего не записано (добавьте --apply)")
        return 0

    if args.command == "register-platform":
        try:
            status, record = register_platform(args.channel, args.lot_id, {k: getattr(args, k) for k in
                                               ("post_url", "status", "model", "year", "mileage_km")}, apply=args.apply)
        except (ValueError, LookupError) as error:
            print(f"Ошибка: {error}")
            return 1
        _print_table([(status, record)])
        print("ЗАПИСАНО в published.jsonl" if (args.apply and status != "same") else
              ("без изменений — запись уже актуальна" if status == "same" else "DRY-RUN — ничего не записано (добавьте --apply)"))
        return 0

    if args.command == "register-post":
        try:
            status, record = register_post(args.link, {k: getattr(args, k) for k in _OVERRIDE_KEYS}, apply=args.apply)
        except (ValueError, LookupError) as error:
            print(f"Ошибка: {error}")
            return 1
        _print_table([(status, record)])
        print("ЗАПИСАНО в published.jsonl" if (args.apply and status != "same") else
              ("без изменений — запись уже актуальна" if status == "same" else "DRY-RUN — ничего не записано (добавьте --apply)"))
        return 0

    # Telegram по message_id, затем записи Instagram/YouTube (у них message_id нет)
    records = sorted(load_records().values(),
                     key=lambda r: (r.get("message_id") is None, r.get("message_id") or 0, channel_of(r)))[-args.last:]
    _print_table([("-", r) for r in records])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
