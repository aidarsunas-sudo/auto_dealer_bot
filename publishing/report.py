"""Сводка report.md: статьи по визитам, какие темы дали больше, что повторять.

  python publishing/report.py
"""
import csv
import re
import sys
from collections import defaultdict
from datetime import date

from common import HERE, STATS, read_publications

REPORT = HERE / "report.md"
# теги темы по ключевым корням запроса
TAGS = {"цена/стоимость": ("цен", "сколько", "стоим"),
        "растаможка/утильсбор": ("растамож", "таможн", "утильсбор", "утилсбор"),
        "как заказать/купить": ("заказ", "купит", "пригна", "привез"),
        "Китай": ("кита", "changan"),
        "Корея": ("коре", "hyundai", "kia")}


def tags_of(query):
    q = query.lower()
    return [t for t, roots in TAGS.items() if any(r in q for r in roots)] or ["другое"]


def num(x):
    m = re.sub(r"\D", "", str(x or ""))
    return int(m) if m else None


def main():
    pubs = {r["slug"]: r for r in read_publications()}
    stats = []
    if STATS.exists():
        with open(STATS, encoding="utf-8-sig", newline="") as f:
            stats = list(csv.DictReader(f))
    visits, views, reads = defaultdict(int), defaultdict(int), defaultdict(int)
    for s in stats:
        visits[s["slug"]] += num(s["визиты"]) or 0
        views[s["slug"]] += num(s["просмотры Дзена"]) or 0
        reads[s["slug"]] += num(s["дочитывания"]) or 0

    published = [s for s, r in pubs.items() if r["статус"] == "опубликовано"]
    slugs = sorted(set(published) | set(visits), key=lambda s: -visits[s])
    L = [f"# Отчёт по статьям · {date.today():%d.%m.%Y}", ""]
    if not slugs:
        L.append("Данных пока нет: нет опубликованных статей и статистики (publish.py, затем stats.py).")
        REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
        print("Данных нет,", REPORT.name, "записан с пометкой")
        return 0

    L += ["## Статьи по визитам", "",
          "| # | Статья | Запрос | Визиты | Просмотры Дзена | Дочитывания | Дочитываемость |",
          "|---|---|---|---|---|---|---|"]
    for i, s in enumerate(slugs, 1):
        q = pubs.get(s, {}).get("главный запрос", "—")
        rate = f"{reads[s] / views[s]:.0%}" if views[s] else "—"
        L.append(f"| {i} | {s} | {q} | {visits[s]} | {views[s] or '—'} | {reads[s] or '—'} | {rate} |")

    by_tag = defaultdict(list)
    for s in slugs:
        for t in tags_of(pubs.get(s, {}).get("главный запрос", s)):
            by_tag[t].append(visits[s])
    ranked = sorted(by_tag.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))
    L += ["", "## Темы (в среднем визитов на статью)", "", "| Тема | Статей | Визитов всего | В среднем |", "|---|---|---|---|"]
    for t, v in ranked:
        L.append(f"| {t} | {len(v)} | {sum(v)} | {sum(v) / len(v):.1f} |")

    avg = sum(visits[s] for s in slugs) / len(slugs)
    L += ["", "## Что повторять", ""]
    top = [s for s in slugs if visits[s] > avg][:3]
    L += [f"- {s} ({pubs.get(s, {}).get('главный запрос', '—')}): {visits[s]} визитов при среднем {avg:.1f}" for s in top] or \
         ["- Пока нет статей выше среднего."]
    if len(ranked) > 1:
        L.append(f"- Тема-лидер по среднему: {ranked[0][0]} ({sum(ranked[0][1]) / len(ranked[0][1]):.1f} визитов на статью).")
    weak = [s for s in published if visits[s] == 0]
    if weak:
        L += ["", "Без визитов: " + ", ".join(weak) + " (проверьте UTM-метки в ссылках и данные Метрики)."]
    L += ["", f"Недель в статистике: {len({s['неделя'] for s in stats})}. Мало данных, выводы предварительные."]
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{REPORT.name}: {len(slugs)} статей")
    return 0


if __name__ == "__main__":
    sys.exit(main())
