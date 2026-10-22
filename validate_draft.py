"""Автоматические проверки черновика статьи (без сети, без правок статьи).

  python validate_draft.py --slug avto-iz-korei-pod-zakaz     # пишет articles/<slug>/checks.md и печатает его

Проверки: пометки; запрещённые слова; объём; числа только из facts.md / customs_rates.py / блока примеров / списка разрешённых;
блок «Как заказать» (ровно 3 ссылки с UTM) и домены ссылок; первое предложение post.md; разметка в post.md.
Разметка (**, #, _) проверяется только в post.md: article.md — Markdown по формату, а post.md уходит в Telegram
без форматирования.
"""
import argparse
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
NUM_RE = re.compile(r"\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


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


def validate(slug, articles_dir=None):
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
    res.append((f"article.md {ARTICLE_RANGE[0]}–{ARTICLE_RANGE[1]} знаков", ARTICLE_RANGE[0] <= la <= ARTICLE_RANGE[1], f"{la} знаков"))
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
    return res


def render(slug, results):
    ok_all = all(r[1] for r in results)
    lines = [f"# Проверки черновика: {slug}", "", f"Итог: {'все проверки пройдены' if ok_all else 'есть проблемы'}", "",
             "| Проверка | Результат |", "|---|---|"]
    for name, ok, detail in results:
        lines.append(f"| {name} | {'ок' if ok else 'проблема'}{(': ' + detail) if detail else ''} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    args = ap.parse_args()
    d = ARTICLES / args.slug
    if not d.exists():
        sys.exit(f"нет папки {d}")
    text = render(args.slug, validate(args.slug))
    (d / "checks.md").write_text(text, encoding="utf-8")
    print(text)
    return 0 if "все проверки пройдены" in text else 1


if __name__ == "__main__":
    sys.exit(main())
