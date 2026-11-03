# -*- coding: utf-8 -*-
"""Синхронизация publishing/models_catalog.json со свежими данными парсера.

Каталог ротации моделей пересобирается из актуальных лотов — только модели, по которым
реально есть лоты в наличии. Алиасы существующих моделей сохраняются.

  python publishing/sync_models_catalog.py                     # живой слепок зала HeyDealer
  python publishing/sync_models_catalog.py --from-db           # из data/cars.db (офлайн)
  python publishing/sync_models_catalog.py --from-file F.json  # из сохранённой выгрузки (офлайн)
  python publishing/sync_models_catalog.py --dry-run           # показать, что изменилось бы

Живой слепок идёт через сохранённую сессию heydealer_dealer_session.json (без логина).
Если сессия истекла — скрипт останавливается с инструкцией, сам не логинится.
Нормализация: корейские названия марок/моделей (brand_names_ko + локальный словарь),
очистка «올 뉴»/«더 뉴»/кодов поколений «(U11)». Не распознанные названия — в отчёт, не в каталог.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

CATALOG_PATH = HERE / "models_catalog.json"

# Локальные корейские названия моделей (в brand_names_ko.MODEL_KO их всего 5).
# Ключ — каноническая модель lower как в каталоге/target_models; значения — варианты из зала.
MODEL_KO_EXTRA = {
    "tucson": ("투싼",),
    "sonata": ("쏘나타",),
    "carnival": ("카니발",),
    "sorento": ("쏘렌토",),
    "palisade": ("팰리세이드",),
    "santa fe": ("싼타페",),
    "kona": ("코나",),
    "niro": ("니로",),
    "avante": ("아반떼",),
    "grandeur": ("그랜저",),
    "azera": ("그랜저",),
    "k7": ("K7",),
    "k8": ("K8",),
    "k9": ("K9",),
    "mohave": ("모하비",),
    "stinger": ("스팅어",),
    "telluride": ("텔루라이드",),
    "g70": ("G70",),
    "g80": ("G80",),
    "g90": ("G90",),
    "gv70": ("GV70",),
    "gv80": ("GV80",),
    "gle": ("GLE클스", "GLE 클래스", "GLE"),
    "gle coupe": ("GLE쿠페",),
    "glc": ("GLC클스", "GLC"),
    "glb": ("GLB",),
    "rc f": ("RC F",),
    "sf90": ("SF90",),
    "renegade": ("레니게이드",),
    "xm": ("XM",),
    "x1": ("X1",),
    "x3": ("X3",),
    "x4": ("X4",),
    "x5": ("X5",),
    "x6": ("X6", "뉴 X6"),
    "x7": ("X7",),
    "accent": ("액센트",),
    "ray": ("레이",),
    "morning": ("모닝",),
    "spark": ("스파크",),
    "malibu": ("말리부",),
    "trailblazer": ("트레일블레이저",),
    "trax": ("트랙스",),
    "captiva": ("캡티바",),
    "equinox": ("이쿼녹스",),
    "casper": ("캐스퍼",),
    "venue": ("베뉴",),
    "lm": ("LM",),
    "q50": ("Q50",),
    "seltos": ("셀토스",),
    "g37": ("G37",),
    "discovery": ("디스커버리",),
    "xc90": ("XC90",),
    "v40": ("V40",),
    "crown": ("크라운 크로스오버", "크라운"),
    "forte": ("포르테",),
    "fluence": ("필랑트",),
    "wrangler": ("랭글러",),
    "amg gt": ("AMG GT",),
    "m2": ("M2",),
    "x5m": ("X5M",),
    "x6m": ("X6M",),
    "q m6": ("QM6",),
    "sm6": ("SM6",),
    "xm3": ("XM3",),
    "tivoli": ("티볼리",),
    "korando": ("코란도",),
    "rexton": ("렉스턴",),
    "tivoli air": ("티볼리 에어",),
    "soul": ("쏘울",),
    "niro ev": ("니로EV",),
    "ioniq 6": ("아이오닉 6",),
    "ioniq 5 n": ("아이오닉 5 N",),
    "porter": ("포터",),
    "bongo": ("봉고",),
    "lexon": ("렉스턴",),
}

# Марки корейских моделей (если в названии лота марки нет — берём отсюда)
MODEL_BRAND = {
    "tucson": "Hyundai", "avante": "Hyundai", "sonata": "Hyundai", "grandeur": "Hyundai",
    "azera": "Hyundai", "palisade": "Hyundai", "santa fe": "Hyundai", "kona": "Hyundai",
    "niro": "Kia", "niro ev": "Kia", "carnival": "Kia", "sorento": "Kia", "k3": "Kia",
    "k5": "Kia", "k7": "Kia", "k8": "Kia", "k9": "Kia", "k9": "Kia", "mohave": "Kia",
    "stinger": "Kia", "telluride": "Kia", "sportage": "Kia", "soul": "Kia", "seltos": "Kia",
    "ray": "Kia", "morning": "Kia", "ev6": "Kia", "ev9": "Kia", "tasman": "Kia",
    "g70": "Genesis", "g80": "Genesis", "g90": "Genesis", "gv60": "Genesis",
    "gv70": "Genesis", "gv80": "Genesis",
    "qm6": "Renault", "sm6": "Renault", "xm3": "Renault",
    "tivoli": "KG Mobility", "korando": "KG Mobility", "rexton": "KG Mobility",
    "spark": "Chevrolet", "malibu": "Chevrolet", "trailblazer": "Chevrolet",
    "trax": "Chevrolet", "captiva": "Chevrolet", "equinox": "Chevrolet",
    "casper": "Hyundai", "venue": "Hyundai",
    "rc f": "Lexus", "lm": "Lexus", "q50": "Infiniti", "fluence": "Renault",
    "sf90": "Ferrari", "renegade": "Jeep", "wrangler": "Jeep",
    "amg gt": "Mercedes-Benz", "m2": "BMW", "x5m": "BMW", "x6m": "BMW",
}

GENERATION_RE = re.compile(r"\s*\([a-z0-9]{2,5}\)", re.I)
NEW_PREFIX_RE = re.compile(r"^(올\s*뉴|더\s*뉴|뉴|올뉴|더뉴)\s*")
BRAND_WORD_RE = re.compile(
    r"(bmw|벤츠|benz|mercedes|audi|아우디|vw|volkswagen|폭스바겐|lexus|렉서스|jeep|지프|"
    r"ferrari|페라리|porche|포르쉐|toyota|토요타|honda|혼다|nissan|닛산|infiniti|인피니티|"
    r"land\s*rover|랜드로버|volvo|볼보|mini|미니|ford|포드|lincoln|링컨|cadillac|캐딜락|tesla|테슬라)", re.I)

EDGE_L = r"(?<![a-z0-9])"
EDGE_R = r"(?![a-z0-9])"


def _case_model(model: str) -> str:
    """Регистр модели: короткие коды и код+цифра — ВЕРХНИМ (XM, GLE, G80, Q50), иначе Title."""
    tokens = model.split()
    if all(len(t) <= 3 for t in tokens) or re.match(r"^[a-z]{1,3}\d", model):
        return model.upper()
    return model.title()


def _norm_key(text: str) -> str:
    t = (text or "").strip().lower()
    t = NEW_PREFIX_RE.sub("", t)
    t = GENERATION_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def _load_catalog() -> dict:
    if not CATALOG_PATH.exists():
        return {}
    out = {}
    for m in json.loads(CATALOG_PATH.read_text(encoding="utf-8")).get("models", []):
        out[f"{m['brand']} {m['model']}".strip().lower()] = m
    return out


def _iter_lot_texts(raw: list) -> list:
    """Нормализация сырого списка лотов к (text, year) — оба формата: вложенный (API) и плоский (выгрузка)."""
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        detail = item.get("detail") or {}
        full_name = detail.get("full_name") or item.get("full_name") or ""
        model_part = detail.get("model_part_name") or item.get("model_part_name") or ""
        year = detail.get("year") or item.get("year")
        out.append((f"{model_part} {full_name}", year))
    return out


def normalize_lot(text: str, known_aliases: dict) -> tuple | None:
    """(brand, model) канонические или None. known_aliases: alias lower -> (brand, model)."""
    hay = _norm_key(text)
    if not hay:
        return None
    # 1) по известным алиасам (длинные первыми, чтобы «gle coupe» раньше «gle»)
    for alias in sorted(known_aliases, key=len, reverse=True):
        if re.search(EDGE_L + re.escape(alias) + EDGE_R, hay):
            return known_aliases[alias]
    # 2) марка: латинские ключи BRAND_KO + корейские варианты (BRAND_KO/ALT значения)
    from brand_names_ko import BRAND_KO, BRAND_KO_ALT, from_korean
    brand, brand_spans = None, []
    for ko_name, latin in ((v, k) for k, v in BRAND_KO.items()):
        for m in re.finditer(re.escape(ko_name.lower()), hay):
            brand_spans.append(m.span())
            brand = brand or from_korean(ko_name) or latin
    for latin, variants in BRAND_KO_ALT.items():
        for v in variants:
            for m in re.finditer(re.escape(v.lower()), hay):
                brand_spans.append(m.span())
                brand = brand or from_korean(v) or latin
    latin_map = {"bmw": "BMW", "audi": "Audi", "vw": "Volkswagen", "volkswagen": "Volkswagen",
                 "benz": "Mercedes-Benz", "mercedes": "Mercedes-Benz", "lexus": "Lexus",
                 "jeep": "Jeep", "ferrari": "Ferrari", "porche": "Porsche", "porsche": "Porsche",
                 "toyota": "Toyota", "honda": "Honda", "nissan": "Nissan", "infiniti": "Infiniti",
                 "land rover": "Land Rover", "volvo": "Volvo", "mini": "Mini",
                 "ford": "Ford", "lincoln": "Lincoln", "cadillac": "Cadillac", "tesla": "Tesla",
                 "renault": "Renault", "peugeot": "Peugeot"}
    for m in BRAND_WORD_RE.finditer(hay):
        raw = m.group(1).lower().replace(" ", "")
        if raw in latin_map:
            brand_spans.append(m.span())
            brand = brand or latin_map[raw]
    rest = hay
    for a, b in sorted(brand_spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    rest = NEW_PREFIX_RE.sub("", rest).strip()
    rest = GENERATION_RE.sub(" ", rest)
    rest = re.sub(r"[·•,]|디젤|가솔린|하이브리드|전기|리미티드|모던|프레스티지|시그니처|노블레스|티타늄", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" -")
    # 3) модель по локальному словарю корейских названий (по hay — марка может быть только корейской)
    for model, variants in MODEL_KO_EXTRA.items():
        if any(re.search(EDGE_L + re.escape(v.lower()) + EDGE_R, hay) for v in (model,) + variants):
            brand_final = brand or MODEL_BRAND.get(model, "")
            return (brand_final, _case_model(model))
    # 4) латинский кандидат из остатка
    rest = re.sub(r"^\d+(\.\d+)?\s*", "", rest)  # объём двигателя в начале: «1.5 e-tech» -> «e-tech»
    m = re.match(r"([a-z0-9][a-z0-9 .-]{0,18})", rest)
    if not m:
        return None
    cand = re.sub(r"\s+(sDrive|xDrive|4MATIC|AWD|2WD|4WD|quattro|TD|i|TD[Ii]|TFSI|LPG|HTRAC).*$", "",
                  m.group(1).strip(" .-"), flags=re.I).strip(" .-")
    if re.fullmatch(r"\d{1,2}", cand) and brand:
        return (brand, f"{cand} Series")
    if re.search(r"\d", cand) and 2 <= len(cand) <= 15:
        return (brand or "", _case_model(cand))
    return None


def fetch_heydealer_lots() -> list:
    """Живой слепок аукционного зала через сохранённую сессию (без логина)."""
    import heydealer_scraper
    if not heydealer_scraper._session_is_valid():
        sys.exit("сессия HeyDealer отсутствует/истекла — скрипт не логинится сам. "
                 "Обновите сессию: python model_search.py --brand <B> --model <M> --confirm-login heydealer "
                 "(или любой запуск model_search.py с этим флагом), затем повторите синхронизацию.")
    sess = heydealer_scraper._build_requests_session()
    lots = []
    for page in range(1, heydealer_scraper.MAX_PAGES + 1):
        params = {"page": page, "type": "auction", "is_subscribed": "false",
                  "is_retried": "false", "is_previously_bid": "false", "order": "default"}
        resp = sess.get(heydealer_scraper.CARS_API_URL, params=params, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        lots.extend(data)
    return lots


def fetch_db_lots() -> list:
    import sqlite3
    con = sqlite3.connect(ROOT / "data" / "cars.db")
    rows = con.execute("SELECT brand, model, year FROM cars").fetchall()
    con.close()
    return [{"full_name": f"{b} {m}", "model_part_name": "", "year": y} for b, m, y in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from-db", action="store_true", help="источник: data/cars.db (офлайн)")
    src.add_argument("--from-file", metavar="JSON", help="источник: сохранённая выгрузка лотов (офлайн)")
    ap.add_argument("--dry-run", action="store_true", help="только показать изменения, файл не перезаписывать")
    args = ap.parse_args()

    if args.from_db:
        raw, source = fetch_db_lots(), "data/cars.db"
    elif args.from_file:
        raw, source = json.loads(Path(args.from_file).read_text(encoding="utf-8")), str(args.from_file)
    else:
        raw, source = fetch_heydealer_lots(), "heydealer:auction_live"

    old_catalog = _load_catalog()
    # алиасы старого каталога + корейские названия — для распознавания лотов
    known: dict = {}
    for key, m in old_catalog.items():
        b, md = m["brand"], m["model"]
        for a in m.get("aliases", []):
            known[a.lower()] = (b, md)
        known[key] = (b, md)
        known[md.lower()] = (b, md)

    counts: Counter = Counter()
    unrecognized: Counter = Counter()
    for text, year in _iter_lot_texts(raw):
        pair = normalize_lot(text, known)
        if pair:
            brand, model = pair
            if model:
                counts[(brand or "", model)] += 1
        else:
            unrecognized[_norm_key(text)[:60]] += 1

    models = []
    kept_aliases = 0
    for (brand, model), n in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        key = f"{brand} {model}".strip().lower()
        aliases = {key, model.lower()}
        if key in old_catalog:
            aliases |= {a.lower() for a in old_catalog[key].get("aliases", [])}
            kept_aliases += 1
        for variant in MODEL_KO_EXTRA.get(model.lower(), ()):
            aliases.add(variant.lower())
        models.append({"brand": brand or "", "model": model, "lots": n,
                       "aliases": sorted(a for a in aliases if a)})

    removed = sorted(set(old_catalog) - {f"{m['brand']} {m['model']}".strip().lower() for m in models})
    print(f"источник: {source} | лотов прочитано: {len(raw)}")
    print(f"моделей в каталоге: {len(models)} (было {len(old_catalog)}; алиасы сохранены у {kept_aliases}; "
          f"убрано без лотов: {len(removed)}; не распознано названий: {len(unrecognized)})")
    for m in models[:40]:
        print(f"  {m['brand']} {m['model']} — лотов: {m['lots']}")
    if removed:
        print("убрано из каталога (нет лотов):", ", ".join(removed))
    if unrecognized:
        print("не распознано (топ-10):", ", ".join(f"{k}×{v}" for k, v in unrecognized.most_common(10)))

    if not models:
        sys.exit("каталог пуст — модели не распознаны, файл не перезаписан")
    if args.dry_run:
        print("\n[dry-run] файл не перезаписан")
        return 0
    payload = {"updated": date.today().isoformat(), "source": source, "models": models}
    CATALOG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nзаписано: {CATALOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
