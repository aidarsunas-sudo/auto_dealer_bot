"""Недельная статистика визитов по статьям из Яндекс.Метрики -> stats.csv.

  python publishing/stats.py                       # прошлая полная неделя (пн–вс)
  python publishing/stats.py --week-start 2026-10-05

stats.csv: slug, неделя, визиты, просмотры Дзена, дочитывания (последние две — вручную; при
пересчёте недели введённые вручную значения сохраняются).
"""
import argparse
import csv
import os
import sys
from datetime import date, timedelta

import requests

from common import STATS, load_config, mask

URL = "https://api-metrika.yandex.net/stat/v1/data"
FIELDS = ["slug", "неделя", "визиты", "просмотры Дзена", "дочитывания"]


def read_stats():
    if not STATS.exists():
        return []
    with open(STATS, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week-start", help="понедельник недели, YYYY-MM-DD (по умолчанию — прошлая неделя)")
    args = ap.parse_args()
    token = os.getenv("METRIKA_TOKEN")
    if not token:
        print("METRIKA_TOKEN не задан в .env — ничего не делаю")
        return 1
    cfg = load_config()["metrika"]

    if args.week_start:
        d1 = date.fromisoformat(args.week_start)
    else:
        today = date.today()
        d1 = today - timedelta(days=today.weekday() + 7)
    d2 = d1 + timedelta(days=6)

    params = {"ids": cfg["counter_id"], "metrics": "ym:s:visits", "dimensions": "ym:s:UTMCampaign",
              "filters": f"ym:s:UTMSource=='{cfg['utm_source']}'", "date1": d1.isoformat(), "date2": d2.isoformat(),
              "limit": 10000}
    try:
        r = requests.get(URL, params=params, headers={"Authorization": f"OAuth {token}"}, timeout=60)
    except requests.RequestException as e:
        print("Ошибка сети:", mask(f"{type(e).__name__}: {e}", token))
        return 1
    if not r.ok:
        print(f"Метрика: HTTP {r.status_code}: {mask(r.text[:300], token)}")
        return 1

    week = f"{d1.isoformat()}..{d2.isoformat()}"
    new = {(row["dimensions"][0]["name"], week): int(row["metrics"][0]) for row in r.json().get("data", [])}
    old = {(x["slug"], x["неделя"]): x for x in read_stats()}
    for (slug, wk), visits in new.items():
        prev = old.get((slug, wk), {})
        old[(slug, wk)] = {"slug": slug, "неделя": wk, "визиты": visits,
                           "просмотры Дзена": prev.get("просмотры Дзена", ""), "дочитывания": prev.get("дочитывания", "")}
    with open(STATS, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(old.values())
    print(f"Неделя {week}: статей с визитами {len(new)}, всего визитов {sum(new.values())} -> {STATS.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
