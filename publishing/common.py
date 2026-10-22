"""Общее для publish.py / stats.py / report.py: пути, конфиг, publications.csv, маскировка токенов."""
import csv
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ARTICLES = ROOT / "articles"
PUBLICATIONS = HERE / "publications.csv"
STATS = HERE / "stats.csv"
PUB_FIELDS = ["slug", "главный запрос", "дата", "ссылки с UTM", "статус", "пригласительная ссылка", "опубликовать после"]
# статусы: черновик, одобрено (вручную), запланировано (автоодобрение, окно отмены), стоп (ручная отмена), опубликовано
HOLD_FILE = HERE / "HOLD"   # аварийная пауза: пока файл есть, «запланировано» не публикуется

load_dotenv(ROOT / ".env")


def load_config() -> dict:
    with open(HERE / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def mask(text, *secrets) -> str:
    """Убирает значения секретов из любого текста перед печатью."""
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def read_publications() -> list:
    if not PUBLICATIONS.exists():
        write_publications([])
        return []
    with open(PUBLICATIONS, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in PUB_FIELDS:
            r.setdefault(k, "")
    return rows


def write_publications(rows: list) -> None:
    tmp = PUBLICATIONS.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PUB_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, PUBLICATIONS)
