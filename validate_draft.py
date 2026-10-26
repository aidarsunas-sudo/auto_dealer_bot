"""Автоматические проверки черновика статьи (без сети, без правок статьи).

  python validate_draft.py --slug avto-iz-korei-pod-zakaz     # пишет articles/<slug>/checks.md и печатает его
  python validate_draft.py --slug utilsbor --article-min 4000  # другая нижняя граница объёма article.md (по умолчанию 6000)

Проверки: пометки; запрещённые слова; объём; числа только из facts.md / customs_rates.py / блока примеров / списка разрешённых;
блок «Как заказать» (ровно 3 ссылки с UTM) и домены ссылок; первое предложение post.md; разметка в post.md;
у каждого H2-раздела article.md есть источник в articles/<slug>/sources.md (раздел без источника или с «нет» — проблема).
Разметка (**, #, _) проверяется только в post.md: article.md — Markdown по формату, а post.md уходит в Telegram
без форматирования.
"""
import argparse
import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
ARTICLES = ROOT / "articles"
FACTS = ROOT / "facts.md"
CUSTOMS_RATES = ROOT / "customs_rates.py"   # числа-ставки, номера постановлений и произведения UTIL_BASE_RUB × UTIL_LGOTA_COEFF — из файла при каждом запуске
ALLOWED_DOMAINS = {"pickpluskazan.tb.ru", "wa.me", "t.me"}
FORBIDDEN = ("мы привезли", "дешевле рынка", "гарантируем")
MARKS = ("[ДОБАВИТЬ ДАННЫЕ", "[НЕТ ПРИМЕРА]")
ARTICLE_RANGE, POST_MAX, FIRST_SENTENCE_MAX = (6000, 9000), 3500, 140
URL_RE = re.compile(r"https?://[^\s)\]>]+")
# число не начинается внутри слова (Q3, GLB200): иначе «Q3 2023» склеивалось в «3 202»; разделитель тысяч — пробел или
# неразрывный пробел перед ровно тремя цифрами, за которыми нет ещё цифры («5 052 377» — одно число, «3 039 км» — одно)
NUM_RE = re.compile(r"(?<!\w)(?:\d{1,3}(?:[ \u00a0]\d{3}(?!\d))+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)")


def norm_num(s):
    return re.sub(r"[  ]", "", s).replace(",", ".")


def numbers(text, drop_list_numbers=False):
    text = URL_RE.sub(" ", text)
    if drop_list_numbers:
        text = re.sub(r"(?m)^\s*(?:[-*]\s+)?\d+[.)]\s+", "", text)  # номера пунктов «1. », «2) »
    return [norm_num(m) for m in NUM_RE.findall(text)]


def is_year(n):
    return re.fullmatch(r"(19|20)\d\d", n) is not None


def customs_numbers():
    """Числа из customs_rates.py (значения констант и комментарии/докстринг с номерами постановлений)."""
    if not CUSTOMS_RATES.exists():
        return set()
    text = re.sub(r"(?<=\d)_(?=\d)", "", CUSTOMS_RATES.read_text(encoding="utf-8"))  # 200_000 -> 200000
    nums = set(numbers(text))
    # произведения базовой ставки утильсбора на каждый льготный коэффициент (считаются из самого файла)
    spec = importlib.util.spec_from_file_location("customs_rates_fresh", CUSTOMS_RATES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in mod.UTIL_LGOTA_COEFF.values():
        prod = round(mod.UTIL_BASE_RUB * k, 6)
        nums.add(str(int(prod)) if prod == int(prod) else str(prod))
    return nums


def how_to_order_block(text):
    """Строки блока «Как заказать» от заголовка/строки до конца блока (пустая строка для простого блока, следующий # для заголовка)."""
    lines, out, mode = text.split("\n"), [], None
    for line in lines:
        if re.match(r"\s*#+\s.*как заказать", line, re.I) or re.match(r"\s*\*\*\s*как заказать", line, re.I):
            mode = "heading"; out.append(line); continue
        if re.match(r"\s*как заказать\s*:", line, re.I):
            mode = "plain"; out.append(line); continue
        if mode == "heading" and re.match(r"\s*#+\s", line):
            mode = None
        if mode == "plain" and not line.strip():
            mode = None
        if mode:
            out.append(line)
    return out


def first_sentence(post):
    body = re.sub(r"\A\s*Описание обложки:[^\n]*\n*", "", post, flags=re.I).lstrip()
    m = re.search(r"[.!?](\s|$)|\n", body)
    return (body[:m.start() + (1 if body[m.start()] in ".!?" else 0)] if m else body).strip()


SKIP_SECTIONS = {"как заказать"}   # блок вставляет скрипт (три ссылки с UTM), источник не нужен


def _norm_heading(h):
    return re.sub(r"\s+", " ", h.strip().strip("#").strip().rstrip(":")).lower()


def read_sources(path):
    """sources.md: строки «| Раздел | Источник |» (или «Раздел | Источник»). Возвращает {заголовок(норм.): источник}."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: ") or cells[0].lower() in ("раздел", "заголовок раздела"):
            continue
        out[_norm_heading(cells[0])] = cells[1]
    return out


def sections_have_sources(d, article_text):
    """Проблема: нет sources.md, раздел без строки в нём, с пустым источником или с источником «нет»."""
    headings = [l[3:] for l in article_text.splitlines() if l.startswith("## ")]
    sp = Path(d) / "sources.md"
    if not sp.exists():
        return False, "нет файла sources.md (список разделов с источником: факты / расчёт проекта / этап процесса)"
    src = read_sources(sp)
    bad = []
    for h in headings:
        key = _norm_heading(h)
        if key in SKIP_SECTIONS:
            continue
        s = (src.get(key) or "").strip()
        if not s:
            bad.append(f"«{h}»: без источника")
        elif s.lower().strip("«» ") == "нет":
            bad.append(f"«{h}»: источник «нет»")
    return (not bad), "; ".join(bad)


def validate(slug, articles_dir=None, article_min=None):
    """article_min — нижняя граница объёма article.md (по умолчанию ARTICLE_RANGE[0] = 6000)."""
    a_min = article_min if article_min is not None else ARTICLE_RANGE[0]
    d = Path(articles_dir or ARTICLES) / slug
    art = (d / "article.md").read_text(encoding="utf-8") if (d / "article.md").exists() else None
    post = (d / "post.md").read_text(encoding="utf-8") if (d / "post.md").exists() else None
    res = []
    if art is None or post is None:
        return [("Файлы article.md и post.md", False, "нет " + ("article.md" if art is None else "post.md"))]
    both = {"article.md": art, "post.md": post}

    # 1. пометки
    bad = [f"{n}: {m}" for n, t in both.items() for m in MARKS if m in t]
    cnt = {n: sum(t.count(m) for m in MARKS) for n, t in both.items()}
    res.append(("Нет [ДОБАВИТЬ ДАННЫЕ] и [НЕТ ПРИМЕРА]", not bad,
                "" if not bad else f"осталось пометок: article.md {cnt['article.md']}, post.md {cnt['post.md']}"))

    # 2. запрещённые слова
    found = [f"«{w}» в {n}" for n, t in both.items() for w in FORBIDDEN if w in t.lower()]
    res.append(("Нет слов «мы привезли», «дешевле рынка», «гарантируем»", not found, "; ".join(found)))

    # 3. объём
    la, lp = len(art), len(post)
    res.append((f"article.md {a_min}–{ARTICLE_RANGE[1]} знаков", a_min <= la <= ARTICLE_RANGE[1], f"{la} знаков"))
    res.append((f"post.md до {POST_MAX} знаков", lp <= POST_MAX, f"{lp} знаков"))

    # 4. числа
    facts_nums = set(numbers(FACTS.read_text(encoding="utf-8"))) if FACTS.exists() else set()
    facts_nums |= customs_numbers()   # разрешены и числа из customs_rates.py
    block_file = d / "example_block.md"
    if block_file.exists():
        ex_nums, ex_note = set(numbers(block_file.read_text(encoding="utf-8"))), ""
    else:
        blocks = re.findall(r"Пример расчёта на основе лота.*?(?=\n\n(?:[^\n_-]|$)|\Z)", art + "\n\n" + post, flags=re.S)
        ex_nums, ex_note = set(numbers("\n".join(blocks))), " (файла example_block.md нет: числа внутри блоков «Пример расчёта» не сверялись с источником)"
    unknown = {}
    for n, t in both.items():
        for x in numbers(t, drop_list_numbers=True):
            if not is_year(x) and x not in facts_nums and x not in ex_nums:
                unknown.setdefault(x, set()).add(n)
    detail = "; ".join(f"{x} ({', '.join(sorted(v))})" for x, v in sorted(unknown.items(), key=lambda kv: kv[0]))
    res.append(("Все числа есть в facts.md, customs_rates.py, блоке примеров или разрешены (годы, номера пунктов)", not unknown,
                (f"нет в источниках: {detail}" if unknown else "") + ex_note))

    # 5–6. блок «Как заказать» и домены
    want = f"utm_source=dzen&utm_campaign={slug}"
    for n, t in both.items():
        links = URL_RE.findall("\n".join(how_to_order_block(t)))
        ok = len(links) == 3 and all(want in u for u in links)
        res.append((f"{n}: в блоке «Как заказать» ровно 3 ссылки с {want}", ok,
                    "" if ok else f"ссылок: {len(links)}; без нужных UTM: {[u for u in links if want not in u]}"))
    others = sorted({urlparse(u).netloc.lower() for t in both.values() for u in URL_RE.findall(t)} - ALLOWED_DOMAINS)
    res.append((f"Других доменов в ссылках нет (разрешены {', '.join(sorted(ALLOWED_DOMAINS))})", not others, ", ".join(others)))

    # 7. первое предложение post.md
    fs = first_sentence(post)
    res.append((f"Первое предложение post.md не длиннее {FIRST_SENTENCE_MAX} знаков", len(fs) <= FIRST_SENTENCE_MAX, f"{len(fs)} знаков: «{fs[:60]}…»"))

    # 8. разметка в post.md
    no_urls = re.sub(r"@\w+", "", URL_RE.sub("", post))  # адреса и @имена каналов (auto_generals) — не разметка
    marks = [s for s, present in (("**", "**" in no_urls), ("#", "#" in no_urls), ("_ вне ссылок", "_" in no_urls)) if present]
    res.append(("post.md: нет символов разметки (**, #, _ вне ссылок)", not marks, ", ".join(marks)))

    # 9. у каждого раздела article.md есть источник в sources.md, и он не «нет»
    ok_s, detail_s = sections_have_sources(d, art)
    res.append(("Каждый раздел article.md имеет источник в sources.md (не «нет»)", ok_s, detail_s))
    return res


def content_hash(slug, articles_dir=None):
    """SHA-256 от article.md + post.md (переводы строк нормализованы); None, если файлов нет."""
    d = Path(articles_dir or ARTICLES) / slug
    if not (d / "article.md").exists() or not (d / "post.md").exists():
        return None
    h = hashlib.sha256()
    for name in ("article.md", "post.md"):
        h.update((d / name).read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8"))
    return h.hexdigest()


def render(slug, results):
    ok_all = all(r[1] for r in results)
    lines = [f"# Проверки черновика: {slug}", "", f"Итог: {'все проверки пройдены' if ok_all else 'есть проблемы'}", "",
             "| Проверка | Результат |", "|---|---|"]
    for name, ok, detail in results:
        lines.append(f"| {name} | {'ок' if ok else 'проблема'}{(': ' + detail) if detail else ''} |")
    digest = content_hash(slug)
    if digest:
        lines += ["", f"Контрольная сумма (article.md + post.md): {digest}"]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--article-min", type=int, default=None, help="нижняя граница объёма article.md (по умолчанию 6000)")
    args = ap.parse_args()
    d = ARTICLES / args.slug
    if not d.exists():
        sys.exit(f"нет папки {d}")
    text = render(args.slug, validate(args.slug, article_min=args.article_min))
    (d / "checks.md").write_text(text, encoding="utf-8")
    print(text)
    return 0 if "все проверки пройдены" in text else 1


if __name__ == "__main__":
    sys.exit(main())
