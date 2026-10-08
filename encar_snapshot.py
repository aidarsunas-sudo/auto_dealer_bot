# -*- coding: utf-8 -*-
"""
encar_snapshot.py — локальный сборщик лотов Encar для компьютера владельца.

Зачем. api.encar.com закрыт для адресов GitHub (и для РФ-адресов), а страницы списка car.encar.com с
домашнего IP владельца открываются и отдают данные прямо в HTML (серверный рендер, __NEXT_DATA__ —
те же объявления SearchResults, что и в API). Сборщик читает эти страницы для моделей из
target_models.json и сохраняет результат в data/encar_snapshot.json; main_pipeline на GitHub при
ENCAR_SOURCE=snapshot берёт лоты Encar оттуда (encar_scraper.search_target_model -> lots_for_target).

Правила (по ТЗ владельца):
  * не чаще раза в ENCAR_SNAPSHOT_MIN_INTERVAL_HOURS (12 ч; считается и после неудачи) — без --force;
  * строго последовательно, одна страница за раз, пауза ENCAR_SNAPSHOT_PAUSE_SEC (+случайная добавка);
  * без обхода защиты: обычный GET с браузерным User-Agent, без прокси, без смены заголовков под защиту;
  * ответ 403/429, другой не-200, «пустая» страница (нет данных списка, isPassed=false) — НЕМЕДЛЕННАЯ
    остановка с предупреждением; прежний снимок при этом НЕ перезаписывается неполным;
  * потолок ENCAR_SNAPSHOT_MAX_REQUESTS запросов за запуск.
В Telegram/VK ничего не публикуется. Админу уходит сообщение только с --notify.

Страница отдаёт максимум 200 свежих объявлений на запрос (page=2 приходит без данных), поэтому, если
по модели найдено больше 200, диапазон пробега делится пополам (рекурсивно, не мельче 3000 км).

Запуск:   python encar_snapshot.py [--force] [--push] [--notify] [--models "Kia K3" ...] [--dry-run]
Коды возврата: 0 — снимок записан, 2 — остановка из-за блокировки/пустой страницы/потолка запросов,
3 — рано (не прошло 12 часов), 4 — прочая ошибка.
"""

import argparse
import json
import logging
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import config

logger = logging.getLogger("encar_snapshot")

LIST_URL = "https://car.encar.com/list/car"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
PAGE_LIMIT = 200            # столько объявлений отдаёт серверный рендер на один запрос
MIN_SPLIT_KM = 3000         # диапазон пробега уже не делим
MAX_PHOTOS_STORED = 12      # фото на лот в снимке (посту хватает 10)
SNAPSHOT_VERSION = 1
STATE_SUFFIX = "_state.json"
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class SnapshotBlocked(Exception):
    """Площадка ответила блокировкой/пустой страницей/лимитом — сбор останавливается."""


# ── разбор страницы ───────────────────────────────────────────────────────

def build_action(codes: dict, year_from: Optional[int] = None, year_to: Optional[int] = None,
                 mileage_min: Optional[int] = None, mileage_max: Optional[int] = None) -> str:
    """Строка поиска Encar (параметр action) для марки/модели + необязательные диапазоны года (ГГГГММ) и пробега."""
    parts = ["Hidden.N", f"CarType.{codes['car_type']}", f"Manufacturer.{codes['manufacturer_ko']}",
             f"ModelGroup.{codes['model_group']}"]
    if year_from or year_to:
        low = str(year_from * 100 + 1) if year_from else ""
        high = str(year_to * 100 + 12) if year_to else ""
        parts.append(f"Year.range({low}..{high})")
    if mileage_min is not None or mileage_max is not None:
        parts.append(f"Mileage.range({mileage_min if mileage_min is not None else ''}.."
                     f"{mileage_max if mileage_max is not None else ''})")
    return "(And." + "._.".join(parts) + ".)"


def parse_list_html(status: int, text: str) -> dict:
    """{"count": N, "results": [...]} из HTML страницы списка. Блокировка/пустая оболочка -> SnapshotBlocked."""
    if status in (403, 429):
        raise SnapshotBlocked(f"HTTP {status}")
    if status != 200:
        raise SnapshotBlocked(f"неожиданный HTTP {status}")
    if "has_been_cr_blocked" in text:
        raise SnapshotBlocked("страница блокировки (has_been_cr_blocked)")
    match = _NEXT_DATA.search(text)
    if not match:
        raise SnapshotBlocked("пустая страница: нет данных списка (оболочка без __NEXT_DATA__)")
    try:
        page_props = json.loads(match.group(1))["props"]["pageProps"]
        if page_props.get("isPassed") is False:
            raise SnapshotBlocked("площадка не пропустила запрос (isPassed=false)")
        queries = page_props["initialState"]["ryvussApi"]["queries"]
        key = next(k for k in queries if k.startswith("getCarNormal"))
        data = queries[key]["data"]
        return {"count": int(data.get("Count") or 0), "results": list(data.get("SearchResults") or [])}
    except SnapshotBlocked:
        raise
    except (KeyError, StopIteration, TypeError, ValueError):
        raise SnapshotBlocked("пустая страница: структура данных списка не найдена")


class Fetcher:
    """Один последовательный клиент: пауза между запросами, счётчик, потолок запросов."""

    def __init__(self, session=None, sleep: Callable[[float], None] = time.sleep, pause: Optional[float] = None,
                 max_requests: Optional[int] = None):
        if session is None:
            import requests
            session = requests.Session()
        self.session, self.sleep = session, sleep
        self.pause = config.ENCAR_SNAPSHOT_PAUSE_SEC if pause is None else pause
        self.max_requests = config.ENCAR_SNAPSHOT_MAX_REQUESTS if max_requests is None else max_requests
        self.requests_made = 0

    def get_list(self, action: str) -> dict:
        if self.requests_made >= self.max_requests:
            raise SnapshotBlocked(f"достигнут потолок {self.max_requests} запросов за запуск")
        if self.requests_made:
            self.sleep(self.pause + random.uniform(0, 3))
        self.requests_made += 1
        search = json.dumps({"type": "car", "action": action, "title": "snapshot", "sort": "MobileModifiedDate"},
                            ensure_ascii=False, separators=(",", ":"))
        try:
            response = self.session.get(LIST_URL, params={"page": 1, "search": search}, headers=HEADERS, timeout=40)
        except Exception as error:
            raise SnapshotBlocked(f"сетевая ошибка: {type(error).__name__}")
        return parse_list_html(response.status_code, response.text)


# ── сбор ──────────────────────────────────────────────────────────────────

def collect_raw(fetcher: Fetcher, codes: dict, target: dict, log=logger) -> tuple:
    """Все сырые объявления по цели. Если за запрос найдено больше 200 — делим пробег пополам. (items, truncated)"""
    year_from, year_to = target.get("year_from"), target.get("year_to")
    top = int(target.get("mileage_max") or 300_000)
    items, seen, truncated = [], set(), False

    def walk(low: int, high: int) -> None:
        nonlocal truncated
        page = fetcher.get_list(build_action(codes, year_from, year_to, low if low else None, high))
        if page["count"] > PAGE_LIMIT and high - low > MIN_SPLIT_KM:
            middle = (low + high) // 2
            walk(low, middle)
            walk(middle + 1, high)
            return
        if page["count"] > len(page["results"]):
            truncated = True
        for item in page["results"]:
            if item.get("Id") not in seen:
                seen.add(item.get("Id"))
                items.append(item)

    walk(0, top)
    if truncated:
        log.warning(f"encar_snapshot: {target['brand']} {target['model']}: часть объявлений не вошла "
                    f"(слишком много на узком диапазоне пробега)")
    return items, truncated


def target_key(target: dict) -> str:
    return f"{target['brand']}|{target['model']}|{target.get('engine') or ''}"


def build_snapshot(targets: list, fetcher: Fetcher, resolve_fn: Callable, log=logger,
                   normalize_fn: Optional[Callable] = None) -> dict:
    """Последовательно обходит цели. Любая SnapshotBlocked прерывает сбор целиком (исключение уходит наверх)."""
    if normalize_fn is None:
        from encar_scraper import filter_and_normalize as normalize_fn
    snapshot = {"version": SNAPSHOT_VERSION, "generated_at": _now().isoformat(timespec="seconds"),
                "source": "car.encar.com/list/car (HTML)", "targets": {}}
    for target in targets:
        codes = resolve_fn(target)
        if codes is None:
            log.warning(f"encar_snapshot: коды Encar для {target['brand']} {target['model']} не найдены — пропуск")
            continue
        raw, truncated = collect_raw(fetcher, codes, target, log)
        lots = normalize_fn(raw, target)
        for lot in lots:
            lot["photos"] = list(lot.get("photos") or [])[:MAX_PHOTOS_STORED]
        snapshot["targets"][target_key(target)] = {"brand": target["brand"], "model": target["model"],
                                                   "found_raw": len(raw), "truncated": truncated, "lots": lots}
        log.info(f"encar_snapshot: {target['brand']} {target['model']}: {len(raw)} объявлений -> {len(lots)} лотов "
                 f"(запросов всего {fetcher.requests_made})")
    snapshot["request_count"] = fetcher.requests_made
    return snapshot


# ── файлы и расписание ────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_path(path: Path) -> Path:
    return path.with_name(path.stem + STATE_SUFFIX)


def load_state(path: Path) -> dict:
    try:
        return json.loads(_state_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, **fields) -> None:
    state = load_state(path)
    state.update(fields)
    _write_atomic(_state_path(path), state)


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def too_early(path: Path, now: Optional[datetime] = None, min_hours: Optional[float] = None) -> Optional[float]:
    """Сколько часов осталось до разрешённого запуска (None — можно запускать)."""
    last = load_state(path).get("last_attempt_at")
    if not last:
        return None
    hours = config.ENCAR_SNAPSHOT_MIN_INTERVAL_HOURS if min_hours is None else min_hours
    ready = datetime.fromisoformat(last) + timedelta(hours=hours)
    left = (ready - (now or _now())).total_seconds() / 3600
    return left if left > 0 else None


def load_snapshot(path: Optional[Path] = None) -> Optional[dict]:
    try:
        return json.loads(Path(path or config.ENCAR_SNAPSHOT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshot_age_hours(snapshot: dict, now: Optional[datetime] = None) -> Optional[float]:
    try:
        generated = datetime.fromisoformat(snapshot["generated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return ((now or _now()) - generated).total_seconds() / 3600


# ── чтение пайплайном (GitHub) ────────────────────────────────────────────

_notified = False


def reset_notice() -> None:
    global _notified
    _notified = False


def _warn_admin(message: str, log, notify_fn: Optional[Callable] = None) -> None:
    """Одно предупреждение админу за прогон (повторы только в лог)."""
    global _notified
    log.warning(message)
    if _notified:
        return
    _notified = True
    try:
        if notify_fn is None:
            from notifier import notify_error as notify_fn
        notify_fn(message)
    except Exception as error:  # уведомление не должно ломать прогон
        log.error(f"encar_snapshot: не удалось отправить предупреждение админу: {error}")


def lots_for_target(target: dict, log=logger, path: Optional[Path] = None, now: Optional[datetime] = None,
                    notify_fn: Optional[Callable] = None) -> list:
    """Лоты цели из снимка. Нет файла/снимок старше ENCAR_SNAPSHOT_MAX_AGE_HOURS -> [] и ОДНО предупреждение админу."""
    snapshot = load_snapshot(path)
    if snapshot is None:
        _warn_admin("⚠️ Encar: снимок data/encar_snapshot.json не найден или не читается — Encar в этом прогоне "
                    "пропущен. Запустите encar_snapshot.py на компьютере владельца.", log, notify_fn)
        return []
    age = snapshot_age_hours(snapshot, now)
    if age is None or age > config.ENCAR_SNAPSHOT_MAX_AGE_HOURS:
        shown = "неизвестен" if age is None else f"{age:.0f} ч"
        _warn_admin(f"⚠️ Encar: снимок устарел (возраст {shown}, предел {config.ENCAR_SNAPSHOT_MAX_AGE_HOURS:.0f} ч) — "
                    f"Encar в этом прогоне пропущен. Проверьте задачу сборщика на компьютере владельца.", log, notify_fn)
        return []
    entry = snapshot.get("targets", {}).get(target_key(target))
    if entry is None:
        log.info(f"encar: {target['brand']} {target['model']} в снимке нет — 0 лотов")
        return []
    lots = [dict(lot) for lot in entry.get("lots", [])]
    log.info(f"encar: {target['brand']} {target['model']} -> {len(lots)} лотов из снимка (возраст {age:.1f} ч)")
    return lots


# ── CLI ───────────────────────────────────────────────────────────────────

def _git(args: list, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def push_snapshot(path: Path, repo: Path, log=logger) -> bool:
    """Коммит ТОЛЬКО файла снимка и push в текущую ветку, если это main. Любая ошибка -> False + лог."""
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    if branch != "main":
        log.warning(f"encar_snapshot: --push только из ветки main (сейчас {branch!r}) — пропуск")
        return False
    rel = str(path.resolve().relative_to(repo.resolve())).replace("\\", "/")
    _git(["add", "--", rel], repo)
    if _git(["diff", "--cached", "--quiet", "--", rel], repo).returncode == 0:
        log.info("encar_snapshot: снимок не изменился — коммит не нужен")
        return True
    steps = [["commit", "-m", "Снимок Encar [skip ci]", "--", rel], ["pull", "--rebase", "--autostash"], ["push"]]
    for step in steps:
        result = _git(step, repo)
        if result.returncode != 0:
            log.error(f"encar_snapshot: git {step[0]} не удался: {(result.stderr or result.stdout).strip()[:300]}")
            return False
    return True


def run(args, fetcher: Optional[Fetcher] = None, resolve_fn: Optional[Callable] = None, notify_fn: Optional[Callable] = None,
        now: Optional[datetime] = None) -> int:
    path = Path(args.output or config.ENCAR_SNAPSHOT_PATH)
    left = None if args.force else too_early(path, now)
    if left is not None:
        logger.info(f"encar_snapshot: рано — следующий запуск через {left:.1f} ч (--force отменяет проверку)")
        return 3

    from target_model_resolver import load_target_models
    targets = load_target_models()
    if args.models:
        wanted = {m.lower() for m in args.models}
        targets = [t for t in targets if f"{t['brand']} {t['model']}".lower() in wanted]
    if resolve_fn is None:
        import encar_scraper
        from target_model_resolver import resolve
        resolve_fn = lambda t: resolve("encar", t["brand"], t["model"], encar_scraper._lookup_codes, logger)
    fetcher = fetcher or Fetcher()

    save_state(path, last_attempt_at=(now or _now()).isoformat(timespec="seconds"))
    try:
        snapshot = build_snapshot(targets, fetcher, resolve_fn, logger)
    except SnapshotBlocked as error:
        message = (f"⚠️ Encar снимок: сбор остановлен — {error}. Прежний снимок сохранён без изменений; "
                   f"запросов сделано {fetcher.requests_made}.")
        logger.warning(message)
        save_state(path, last_status=f"blocked: {error}")
        if args.notify:
            _warn_admin(message, logger, notify_fn)
        return 2
    if args.dry_run:
        total = sum(len(t["lots"]) for t in snapshot["targets"].values())
        logger.info(f"encar_snapshot: DRY-RUN — собрано {total} лотов по {len(snapshot['targets'])} моделям, файл не записан")
        return 0
    _write_atomic(path, snapshot)
    save_state(path, last_success_at=snapshot["generated_at"], last_status="ok")
    logger.info(f"encar_snapshot: записан {path} ({sum(len(t['lots']) for t in snapshot['targets'].values())} лотов, "
                f"запросов {snapshot['request_count']})")
    if args.push and not push_snapshot(path, Path(__file__).resolve().parent, logger):
        return 4
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Локальный сборщик снимка Encar (car.encar.com, только чтение)")
    parser.add_argument("--force", action="store_true", help="игнорировать интервал между запусками")
    parser.add_argument("--push", action="store_true", help="после записи закоммитить и отправить ТОЛЬКО файл снимка (ветка main)")
    parser.add_argument("--notify", action="store_true", help="при остановке отправить предупреждение админу")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="собрать, но файл не писать")
    parser.add_argument("--models", nargs="+", help="только эти модели, например: --models \"Kia K3\" \"Kia K5\"")
    parser.add_argument("--output", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        return run(args)
    except Exception as error:
        logger.error(f"encar_snapshot: непредвиденная ошибка: {type(error).__name__}: {error}")
        return 4


if __name__ == "__main__":
    sys.exit(main())
