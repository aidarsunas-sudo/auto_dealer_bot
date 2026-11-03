"""Сбор запросов из Яндекс Вордстата в queries.csv.

  python wordstat/wordstat_collect.py                     # через API
  python wordstat/wordstat_collect.py --import-csv f.csv  # ручная выгрузка -> queries.csv

Ключ и каталог: YANDEX_SEARCH_API_KEY и YANDEX_SEARCH_FOLDER_ID в .env (Wordstat в Yandex Search API).
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
HOUR_LOG = HERE / "requests_log.json"
FIELDS = ["запрос", "частота", "затравка", "дата сбора"]


class RateLimit(Exception):
    """Исчерпан часовой лимит — дальше слать нельзя, останавливаем весь запуск."""


class SeedFailed(Exception):
    """Затравка не получилась после всех повторов; текст — код ответа или тип ошибки."""


def load_config():
    with open(HERE / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def out_path(cfg):
    p = Path(cfg.get("output", "queries.csv"))
    return p if p.is_absolute() else HERE / p


def _hour_log():
    """Метки времени наших запросов за последний час (файл переживает перезапуски)."""
    try:
        stamps = json.loads(HOUR_LOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamps = []
    now = time.time()
    return [t for t in stamps if now - t < 3600]


def _log_request(stamps):
    stamps.append(time.time())
    HOUR_LOG.write_text(json.dumps(stamps), encoding="utf-8")


def post(api_key, body, cfg, stamps):
    """POST topRequests. Каждая попытка отправки (и неудачная тоже) идёт в часовой счётчик.
    ConnectionError/ConnectTimeout/ReadTimeout — до 3 повторов с паузой 5 с; 429 — повторы с растущей паузой;
    500 и прочие ошибки — без повторов. Неудача -> SeedFailed. Ключ нигде не печатается."""
    pause429, timeouts = cfg["retry_pause_sec"], 0
    for attempt in range(cfg["max_retries"] + 4):
        if len(stamps) >= cfg["hourly_limit"] - cfg["hourly_reserve"]:
            raise RateLimit(f"за последний час отправлено {len(stamps)} запросов — стоп, "
                            f"лимит {cfg['hourly_limit']}/час")
        _log_request(stamps)  # считаем до отправки: попытка засчитана, даже если упала
        try:
            r = requests.post(
                URL, json=body, timeout=(15, 90),
                headers={"Authorization": f"Api-key {api_key}", "Content-Type": "application/json"},
            )
        except (requests.ConnectionError, requests.ReadTimeout) as e:  # ConnectTimeout — подкласс ConnectionError
            timeouts += 1
            if timeouts > 3:
                raise SeedFailed(f"{type(e).__name__} после 3 повторов")
            print(f"  {type(e).__name__}, повтор {timeouts}/3 через 5 с…")
            time.sleep(5)
            continue
        except requests.RequestException as e:
            raise SeedFailed(type(e).__name__)
        if r.status_code == 429:
            if attempt >= cfg["max_retries"]:
                raise SeedFailed("HTTP 429, лимит не снялся после повторов")
            print(f"  429, пауза {pause429} с…")
            time.sleep(pause429)
            pause429 *= 2
            continue
        if not r.ok:
            raise SeedFailed(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()
    raise SeedFailed("повторы исчерпаны")


# ---------- CSV ----------
def read_existing(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def merge_and_save(path, new_rows):
    """Без дублей по паре (запрос, затравка); свежая запись заменяет старую."""
    rows = {(r["запрос"], r["затравка"]): r for r in read_existing(path)}
    for r in new_rows:
        rows[(r["запрос"], r["затравка"])] = r
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows.values())
    return len(rows)


# ---------- API ----------
def collect(cfg):
    load_dotenv(HERE.parent / ".env")
    api_key, folder = os.getenv("YANDEX_SEARCH_API_KEY"), os.getenv("YANDEX_SEARCH_FOLDER_ID")
    if not api_key or not folder:
        sys.exit("YANDEX_SEARCH_API_KEY и YANDEX_SEARCH_FOLDER_ID должны быть заданы в .env")

    num = max(1, min(int(cfg["num_phrases"]), 2000))
    stamps = _hour_log()
    print(f"Запросов за последний час: {len(stamps)} (лимит {cfg['hourly_limit']})")

    today = date.today().isoformat()
    sent_before, results, failed = len(stamps), {}, {}
    for seed in cfg["seeds"]:
        body = {"phrase": seed, "numPhrases": num, "regions": [str(r) for r in cfg.get("regions") or []],
                "devices": cfg["devices"], "folderId": folder}
        try:
            data = post(api_key, body, cfg, stamps)
        except RateLimit as e:
            print(f"Остановка: {e}")
            break
        except SeedFailed as e:
            failed[seed] = str(e)
            print(f"{seed}: НЕ УДАЛОСЬ — {e}")
            continue
        popular, assoc = data.get("results") or [], data.get("associations") or []
        # сначала «что ещё искали», потом популярные: при совпадении фразы остаётся запись из results
        rows = [{"запрос": it["phrase"], "частота": int(it["count"]), "затравка": seed, "дата сбора": today}
                for it in assoc + popular]
        total = merge_and_save(out_path(cfg), rows)  # дописываем сразу после каждой затравки
        results[seed] = (len(popular), len(assoc))
        print(f"{seed}: {len(popular)} популярных, {len(assoc)} похожих (в файле {total} строк)")
        time.sleep(0.15)  # лимит 10 запросов/с

    print(f"\nУспешно: {len(results)}, неудачно: {len(failed)}; "
          f"попыток отправки за этот запуск: {len(stamps) - sent_before}; "
          f"в часовом счётчике: {len(stamps)}/{cfg['hourly_limit']}")
    print(f"Итого строк в {out_path(cfg)}: {len(read_existing(out_path(cfg)))}")


# ---------- ручной CSV ----------
def _num(s):
    return int("".join(ch for ch in str(s) if ch.isdigit()) or 0)


def import_manual(cfg, src, seed):
    """Ручная выгрузка Вордстата: столбцы «Слова/Фраза» и «Показы/Число показов»
    (разделитель ; или ,), в начале могут быть служебные строки."""
    raw = Path(src).read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    delim = ";" if text.count(";") >= text.count(",") else ","
    rows = [r for r in csv.reader(text.splitlines(), delimiter=delim) if len(r) >= 2]
    today = date.today().isoformat()
    new_rows = []
    for phrase, count, *_ in rows:
        phrase = phrase.strip().strip('"')
        if not phrase or not any(ch.isdigit() for ch in str(count)):
            continue  # заголовки и служебные строки
        new_rows.append({"запрос": phrase, "частота": _num(count),
                         "затравка": seed or Path(src).stem, "дата сбора": today})
    total = merge_and_save(out_path(cfg), new_rows)
    print(f"Импортировано {len(new_rows)} строк, всего в {out_path(cfg)}: {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--import-csv", help="готовый CSV, выгруженный из Вордстата вручную")
    ap.add_argument("--seed", help="затравка для импортируемого CSV (по умолчанию — имя файла)")
    a = ap.parse_args()
    c = load_config()
    if a.import_csv:
        import_manual(c, a.import_csv, a.seed)
    else:
        collect(c)
