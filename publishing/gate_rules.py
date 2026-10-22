"""Правила ворот (е) auto_approve.py и выбора темы в run_chain.py (generate). Только чтение: .env не загружает, ничего не пишет."""
import re
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CUSTOMS_RATES = ROOT / "customs_rates.py"

# Главный запрос / slug темы про таможню: такая тема требует CUSTOMS_VERIFIED_ON (slug — транслитерация запроса)
QUERY_CUSTOMS = re.compile(r"растаможк|таможн|пошлин|(?<!\w)етс(?!\w)|единая ставка", re.I)
SLUG_CUSTOMS = re.compile(r"rastamozh|tamozhn|poshlin|(?:^|-)ets(?:-|$)|edinaya-stavka")
# Тема про утильсбор требует RATES_VERIFIED_ON
QUERY_UTIL = re.compile(r"утильсбор|утилизационн", re.I)
SLUG_UTIL = re.compile(r"utilsbor|utilizats")

# Слова, рядом с которыми числа (ставки, суммы) запрещены без CUSTOMS_VERIFIED_ON; просто «растаможка» не входит
TERMS = re.compile(r"пошлин\w*|(?<!\w)етс(?!\w)|таможенн\w*\s+сбор\w*|единая\s+ставк\w*", re.I)
# Число с единицей (%, евро, ₽, тыс., млн, $) или сумма с разрядами («20 000»); голые числа и годы не считаются
NUMBER = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|процент\w*|евро|€|₽|руб\w*|тыс\w*|млн|\$|долл\w*|usd|eur)"
                    r"|\d{1,3}(?:[\s ]\d{3})+", re.I)
WINDOW = 60
EXAMPLE_MARK = "пример расчёта на основе лота"


def const_status(name, today, max_age_days):
    """(ok, причина): константа name из customs_rates.py — дата YYYY-MM-DD, не в будущем и не старше max_age_days."""
    m = re.search(rf"(?m)^{name}\s*=\s*[\"']([^\"']*)[\"']", CUSTOMS_RATES.read_text(encoding="utf-8"))
    if not m:
        return False, f"константа {name} не найдена в customs_rates.py"
    raw = m.group(1).strip()
    if not raw:
        return False, f"{name} пуста: владелец не сверил данные с первоисточником"
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return False, f"{name}={raw!r} не разбирается как дата YYYY-MM-DD"
    age = (today - d).days
    if age < 0:
        return False, f"{name} {d:%d.%m.%Y} в будущем"
    if age > max_age_days:
        return False, f"{name} {d:%d.%m.%Y} старше {max_age_days} дн."
    return True, f"{name} {d:%d.%m.%Y}, возраст {age} дн."


def is_customs_topic(query, slug):
    return bool(QUERY_CUSTOMS.search(query or "") or SLUG_CUSTOMS.search((slug or "").lower()))


def is_util_topic(query, slug):
    return bool(QUERY_UTIL.search(query or "") or SLUG_UTIL.search((slug or "").lower()))


def _plain(line):
    return re.sub(r"\s+", " ", re.sub(r"[*_#>]+", "", line)).strip().lower()


def strip_example(text, example_text=""):
    """Вырезает блок примера: строки из example_block.md и блоки, начинающиеся строкой «Пример расчёта на основе лота»
    (маркер, затем список и курсивная сноска)."""
    skip = {_plain(l) for l in example_text.splitlines()} - {""}
    lines = [l for l in text.splitlines() if _plain(l) not in skip]
    out, i = [], 0
    while i < len(lines):
        if _plain(lines[i]) == EXAMPLE_MARK:
            i += 1
            while i < len(lines):
                nxt = next((l.lstrip() for l in lines[i:] if l.strip()), "")
                if lines[i].strip() or nxt[:1] in ("-", "—", "_", "*"):
                    i += 1
                else:
                    break
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def numbers_near_terms(text):
    """[(слово, число)]: число с единицей или сумма в пределах WINDOW знаков от пошлин*/ЕТС/«таможенный сбор»/«единая ставка»."""
    found = []
    for m in TERMS.finditer(text):
        seg = text[max(0, m.start() - WINDOW): m.end() + WINDOW]
        n = NUMBER.search(seg)
        if n:
            found.append((m.group(0), n.group(0).strip()))
    return found
