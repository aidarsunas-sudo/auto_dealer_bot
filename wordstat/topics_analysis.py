"""Отбор тем из queries.csv и разбор конкурентов (без написания статей).

  python wordstat/topics_analysis.py topics     # queries.csv -> topics.csv
  python wordstat/topics_analysis.py analyze [файл_тем.csv]   # topics.csv (или указанный файл) -> analysis/<slug>.md

Выдача: Yandex Search API (YANDEX_SEARCH_API_KEY + YANDEX_SEARCH_FOLDER_ID в .env),
иначе wordstat/competitors.csv (колонки: тема, URL; «тема» = slug или главный запрос).
"""
import base64
import csv
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # model_specs.py лежит в корне проекта
REJECTED = HERE / "rejected_topics.csv"
QUERIES, TOPICS, COMPETITORS, ANALYSIS = (HERE / "queries.csv", HERE / "topics.csv",
                                          HERE / "competitors.csv", HERE / "analysis")
N_COMPANIONS, TOP_N = 5, 10
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

STOP = {"из", "в", "на", "под", "по", "для", "и", "с", "со", "от", "до", "за", "к", "у", "о", "об",
        "как", "что", "это", "ли", "или", "не", "во", "а", "то", "же", "2026", "2025"}
QUESTION_START = ("как", "сколько", "что", "почему", "зачем", "можно", "нужно", "какой", "какие",
                  "где", "когда", "чем", "стоит ли", "кто", "куда")
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                     "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def stems(text):
    return {w[:5] for w in re.findall(r"[a-zа-яё0-9]+", text.lower()) if w not in STOP}


def slugify(q):
    s = "".join(TRANSLIT.get(c, c) for c in q.lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60]


# ---------- 1. отбор тем ----------
def read_queries():
    best = {}  # один запрос из разных затравок — берём максимальную частоту
    with open(QUERIES, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            q, n = r["запрос"].strip().lower(), int(r["частота"])
            best[q] = max(best.get(q, 0), n)
    return best


def _topics_cfg():
    with open(HERE / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["topics"]


def _spec_brands():
    from model_specs import SPECS
    return {w for sp in SPECS for w in re.findall(r"[a-z0-9]+", sp.label.split()[0].lower()) if len(w) >= 2}


def _tokens(q):
    return re.findall(r"[a-zа-яё0-9]+", q.lower())


def _normalize(q, aliases):
    """Синонимы бренда -> каноническое слово (только для группировки). Возвращает (текст, список сработавших синонимов)."""
    used = []
    for canon, alts in aliases.items():
        for alt in sorted(alts, key=len, reverse=True):
            new = re.sub(rf"(?<!\w){re.escape(alt)}\w*", canon, q)
            if new != q:
                used.append(alt)
                q = new
    return q, used


def _reject_reason(q, cfg, roots, brands):
    """None — запрос проходит; иначе причина отсева."""
    low = q.lower()
    toks = _tokens(low)
    for w in cfg["stopwords"]:
        if any(t.startswith(w) for t in toks):
            return f"стоп-слово: {w}"

    def hit(root):
        if " " in root:
            return root in low
        if root.endswith("*"):
            return any(t.startswith(root[:-1]) for t in toks)
        return root in toks

    if any(hit(r) for r in roots):
        return None
    if any(hit(r) for r in cfg["geo_words"]):
        if any(hit(r) for r in cfg["auto_context"]) or (
                any(hit(r) for r in cfg["auto_context_with_brand"]) and any(hit(b) for b in brands)):
            return None
        return "гео-слово без авто-контекста"
    if any(hit(r) for r in cfg["customs_roots"]):
        if any(t.startswith(x) for t in toks for x in cfg["customs_requires"]):
            return None
        return "таможн без слов авто/машин/ввоз/ставк/пошлин/калькулятор"
    return "нет белого слова"


def _cluster(items, key=lambda q: q):
    """Жадная группировка по основам слов (как раньше): items — [(запрос, частота)] по убыванию частоты."""
    clusters = []
    for q, n in items:
        qs_ = stems(key(q))
        for c in clusters:
            h = c["head"]
            if h and (h <= qs_ or len(h & qs_) / len(h | qs_) >= 0.75):
                c["items"].append((q, n))
                break
        else:
            clusters.append({"head": qs_, "items": [(q, n)]})
    return [c["items"] for c in clusters]


def _write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def select_topics():
    cfg = _topics_cfg()
    roots = [r.lower() for r in cfg["whitelist"]] + (sorted(_spec_brands()) if cfg.get("use_model_specs_brands") else [])
    aliases = {k: [a.lower() for a in v] for k, v in cfg["aliases"].items()}
    brands = sorted(_spec_brands() | set(aliases) | {a for v in aliases.values() for a in v})
    qs = sorted(read_queries().items(), key=lambda x: -x[1])

    rejected, kept = [], []        # rejected: (причина, запрос, частота)
    for q, n in qs:
        reason = _reject_reason(q, cfg, roots, brands)
        (rejected.append((reason, q, n)) if reason else kept.append((q, n)))

    # темы с брендом (с учётом синонимов) — одна тема на бренд; остальное — по основам слов
    by_brand, rest, merged = {}, [], {}
    for q, n in kept:
        norm, used = _normalize(q, aliases)
        brand = next((b for b in aliases if b in _tokens(norm)), None)
        if brand:
            by_brand.setdefault(brand, []).append((q, n))
            for alt in used:
                merged.setdefault((alt, brand), []).append((q, n))
        else:
            rest.append((q, n))
    groups = list(by_brand.values()) + _cluster(rest)

    themes = []
    for items in groups:
        items = sorted(items, key=lambda x: -x[1])
        themes.append({"main": items[0], "items": items})
    themes.sort(key=lambda t: -t["main"][1])

    rej_rows = []
    def rej(reason, items):
        items = sorted(items, key=lambda x: -x[1])
        rej_rows.append({"причина": reason, "главный запрос": items[0][0], "частота главного": items[0][1],
                         "запросов в кластере": len(items), "примеры": " | ".join(q for q, _ in items[1:4])})

    final = []
    for t in themes:
        if t["main"][1] < cfg["min_main_freq"]:
            rej(f"частота главного ниже {cfg['min_main_freq']}", t["items"])
        elif len(final) >= cfg["max_topics"]:
            rej("вне топ-%d по частоте главного" % cfg["max_topics"], t["items"])
        else:
            final.append(t)
    for (alt, brand), items in merged.items():
        rej(f"дубль слит: синоним «{alt}» -> тема {brand}", items)
    by_reason = {}
    for reason, q, n in rejected:
        by_reason.setdefault(reason.split(":")[0] if reason.startswith("стоп") else reason, []).append((q, n, reason))
    for reason, items in by_reason.items():
        for cl in _cluster([(q, n) for q, n, _ in items]):
            r = next(r for q, n, r in items if q == sorted(cl, key=lambda x: -x[1])[0][0])
            rej(r, cl)
    rej_rows.sort(key=lambda r: -r["частота главного"])
    _write_csv(REJECTED, rej_rows, ["причина", "главный запрос", "частота главного", "запросов в кластере", "примеры"])

    rows = []
    for t in final:
        main, items = t["main"], t["items"]
        rest_q = [i for i in items if i[0] != main[0]][:N_COMPANIONS]
        tl = cfg["tails"]

        def is_tail(q, n):
            toks = _tokens(q)
            return tl["min_freq"] <= n <= tl["max_freq"] and q != main[0] and any(
                (w in q) if " " in w else any(t.startswith(w.rstrip("*")) for t in toks) if w.endswith("*") else (w in toks)
                for w in tl["words"])
        tails = [q for q, n in items if is_tail(q, n)][:tl["max_count"]]
        rows.append({"тема": slugify(main[0]), "главный запрос": main[0], "частота главного": main[1],
                     "сопутствующие": " | ".join(q for q, _ in rest_q),
                     "суммарная частота": sum(n for _, n in items), "запросов в теме": len(items),
                     "хвосты": " | ".join(tails)})
    _write_csv(TOPICS, rows, ["тема", "главный запрос", "частота главного", "сопутствующие", "суммарная частота",
                              "запросов в теме", "хвосты"])
    print(f"{len(rows)} тем -> {TOPICS}; отсеянных кластеров -> {REJECTED} ({len(rej_rows)})")


# ---------- 2. выдача ----------
SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
SEARCH_429_PAUSES = (30, 60, 120)   # секунды между повторами при ответе 429


def search_top(query, key, folder):
    """Топ-10 выдачи Яндекса: [(url, заголовок)]. ConnectionError/ConnectTimeout/ReadTimeout — до 3 повторов
    с паузой 5 с; 429 — до 3 повторов с паузами 30/60/120 с; другие ответы (400/401/403/500) — без повторов."""
    body = {"query": {"searchType": "SEARCH_TYPE_RU", "queryText": query}, "folderId": folder,
            "responseFormat": "FORMAT_XML"}
    net_retries = pause_429 = 0
    while True:
        try:
            r = requests.post(SEARCH_URL, json=body, timeout=(15, 90), headers={"Authorization": f"Api-Key {key}"})
        except (requests.ConnectionError, requests.ReadTimeout) as e:  # ConnectTimeout — подкласс ConnectionError
            if net_retries >= 3:
                raise RuntimeError(f"{type(e).__name__} после 3 повторов") from None
            net_retries += 1
            print(f"  {type(e).__name__}, повтор {net_retries}/3 через 5 с…")
            time.sleep(5)
            continue
        if r.status_code == 429 and pause_429 < len(SEARCH_429_PAUSES):
            print(f"  429, пауза {SEARCH_429_PAUSES[pause_429]} с…")
            time.sleep(SEARCH_429_PAUSES[pause_429])
            pause_429 += 1
            continue
        break
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300].replace(key, '***')}")
    root = ET.fromstring(base64.b64decode(r.json()["rawData"]))
    out = []
    for doc in root.iter("doc"):
        url = doc.find("url")
        if url is None or not (url.text or "").strip():
            continue
        title = doc.find("title")
        out.append((url.text.strip(), " ".join("".join(title.itertext()).split()) if title is not None else ""))
    return out[:TOP_N]


def manual_urls():
    if not COMPETITORS.exists():
        return {}
    out = {}
    with open(COMPETITORS, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["тема"].strip().lower(), []).append(r["URL"].strip())
    return out


# ---------- 3. разбор страницы ----------
def parse_page(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": UA, "Accept-Language": "ru"})
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "lxml")
    for t in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
        t.decompose()
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else url)
    root = soup.find("article") or soup.find("main") or soup.body or soup
    heads = [(h.name, h.get_text(" ", strip=True)) for h in root.find_all(["h2", "h3"])
             if h.get_text(strip=True)]
    text = root.get_text(" ", strip=True)
    questions = [t for _, t in heads if t.endswith("?") or t.lower().startswith(QUESTION_START)]
    for s in re.findall(r"[^.!?\n]{15,160}\?", text):
        s = s.strip()
        if s not in questions and len(questions) < 15:
            questions.append(s)
    return {"url": url, "title": title, "heads": heads, "words": len(text.split()),
            "questions": questions, "text": text.lower()}


def _parse_with_retry(url):
    """Скачивание страницы; при таймауте (ConnectTimeout/ReadTimeout) — до 3 повторов с паузой 5 с."""
    for attempt in range(4):
        try:
            return parse_page(url)
        except requests.Timeout as e:
            if attempt == 3:
                raise
            print(f"  {type(e).__name__} на {url}, повтор {attempt + 1}/3 через 5 с…")
            time.sleep(5)


def gaps(topic, pages):
    main_st = stems(topic["главный запрос"])
    expected = [q for q in topic["сопутствующие"].split(" | ") if q]
    missing_all, only_text = [], []
    for q in expected:
        need = stems(q) - main_st
        if not need:
            continue
        in_head = any(need <= stems(h) for p in pages for _, h in p["heads"])
        in_text = any(all(s in p["text"] for s in need) for p in pages)
        if not in_head and not in_text:
            missing_all.append(q)
        elif not in_head:
            only_text.append(q)
    return missing_all, only_text


def write_md(topic, pages, failed):
    ANALYSIS.mkdir(exist_ok=True)
    L = [f"# {topic['главный запрос']}", "",
         f"Суммарная частота темы: {topic['суммарная частота']}. "
         f"Сопутствующие запросы: {topic['сопутствующие'] or '—'}", "",
         f"Разобрано страниц: {len(pages)}", ""]
    for i, p in enumerate(pages, 1):
        L += [f"## {i}. {p['title']}", f"{p['url']} · ~{p['words']} слов"]
        if p.get("serp_title"):
            L.append(f"В выдаче: {p['serp_title']}")
        L.append("")
        L += [f"- {'  ' if n == 'h3' else ''}{h}" for n, h in p["heads"]] or ["- (подзаголовков нет)"]
        if p["questions"]:
            L += ["", "Разобранные вопросы:"] + [f"- {q}" for q in p["questions"]]
        L.append("")
    miss, only_text = gaps(topic, pages)
    L += ["## Пробелы (чего нет у всех конкурентов)", ""]
    L += ([f"- нигде не упомянуто: {q}" for q in miss] +
          [f"- нет ни в одном подзаголовке (только в тексте): {q}" for q in only_text]) or \
         ["- по сопутствующим запросам пробелов не найдено"]
    if failed:
        L += ["", "## Не удалось скачать"] + [f"- {u} ({e})" for u, e in failed]
    (ANALYSIS / f"{topic['тема']}.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def analyze(topics_file=None):
    load_dotenv(HERE.parent / ".env")
    key, folder = os.getenv("YANDEX_SEARCH_API_KEY"), os.getenv("YANDEX_SEARCH_FOLDER_ID")
    manual = manual_urls()
    with open(Path(topics_file) if topics_file else TOPICS, encoding="utf-8-sig", newline="") as f:
        topics = list(csv.DictReader(f))
    for t in topics:
        urls = []  # [(url, заголовок в выдаче)]
        if key and folder:
            try:
                urls = search_top(t["главный запрос"], key, folder)
            except Exception as e:
                print(f"{t['тема']}: поиск не удался ({e}), пробую competitors.csv")
        if not urls:
            urls = [(u, "") for u in (manual.get(t["тема"].lower()) or manual.get(t["главный запрос"].lower()) or [])]
        if not urls:
            print(f"{t['тема']}: нет ни выдачи, ни строк в competitors.csv — пропуск")
            continue
        pages, failed = [], []
        for u, serp_title in urls:
            try:
                pages.append({**_parse_with_retry(u), "serp_title": serp_title})
            except Exception as e:
                failed.append((u, f"{type(e).__name__}: {str(e)[:120]}"))
            time.sleep(1)
        if pages:
            write_md(t, pages, failed)
            print(f"{t['тема']}: {len(pages)} стр., {len(failed)} ошибок -> analysis/{t['тема']}.md")
        else:
            print(f"{t['тема']}: ни одна страница не скачалась")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"topics": select_topics, "analyze": lambda: analyze(*sys.argv[2:3])}.get(cmd, lambda: sys.exit(__doc__))()
