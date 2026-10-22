"""Автоодобрение черновиков с окном отмены (вместо ручного «одобрено»).

  python publishing/auto_approve.py                 # dry-run по умолчанию: показывает ворота и что отправил бы в чат, ничего не меняет
  python publishing/auto_approve.py --slug utilsbor # только один черновик
  python publishing/auto_approve.py --run           # реально: уведомление в notify_chat_id и статус «запланировано»

Для каждой записи со статусом «черновик» проверяет по порядку:
  а) validate_draft.py по slug без проблем (нижняя граница объёма article.md: auto_approve.article_min в config.yaml);
  б) дата актуальности ставок в customs_rates.py (строка «Ставки актуальны на:», берётся самая поздняя дата) не старше
     auto_approve.rates_max_age_days;
  в) нет файла publishing/HOLD;
  г) не больше auto_approve.max_per_7_days автоматических статей за 7 дней;
  д) статья не изменилась после последней проверки (контрольная сумма в checks.md совпадает с текущей).
Всё прошло -> уведомление в notify_chat_id, и только после успешной отправки статус «запланировано» и
«опубликовать после» = сейчас + window_hours. Не прошло -> статус не меняется, уведомление с причиной.
Остановить запланированную статью: статус «стоп» в publications.csv или файл publishing/HOLD.
Публикует запланированное publish.py. Токен не печатается.
"""
import argparse
import hashlib
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from common import ARTICLES, HOLD_FILE, load_config, mask, read_publications, write_publications  # noqa: E402
import validate_draft  # noqa: E402
from publish import split_cover, strip_markdown  # noqa: E402

CUSTOMS_RATES = ROOT / "customs_rates.py"
DEFAULT_ARTICLE_MIN = 6000
FMT = "%Y-%m-%d %H:%M"


def rates_verified_on():
    """(дата, причина): RATES_VERIFIED_ON из customs_rates.py. Дату вписывает только человек после сверки ставок с ПП №1291.
    Дата None и причина, если константы нет, она пуста или не разбирается как YYYY-MM-DD."""
    m = re.search(r"(?m)^RATES_VERIFIED_ON\s*=\s*[\"']([^\"']*)[\"']", CUSTOMS_RATES.read_text(encoding="utf-8"))
    if not m:
        return None, "константа RATES_VERIFIED_ON не найдена в customs_rates.py"
    raw = m.group(1).strip()
    if not raw:
        return None, "RATES_VERIFIED_ON пуста: впишите дату сверки ставок с ПП №1291 (YYYY-MM-DD) в customs_rates.py"
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), ""
    except ValueError:
        return None, f"RATES_VERIFIED_ON={raw!r} не разбирается как дата YYYY-MM-DD"


def recorded_hash(slug):
    p = ARTICLES / slug / "checks.md"
    m = re.search(r"Контрольная сумма \(article\.md \+ post\.md\): ([0-9a-f]{64})", p.read_text(encoding="utf-8")) if p.exists() else None
    return m.group(1) if m else None


def parse_after(value, tz):
    try:
        return datetime.strptime((value or "").strip(), FMT).replace(tzinfo=tz)
    except ValueError:
        return None


def auto_count(rows, now, window_h, tz):
    """Сколько статей запланировано автоматически за последние 7 суток (по «опубликовать после» минус окно)."""
    n = 0
    for r in rows:
        t = parse_after(r.get("опубликовать после"), tz)
        if t and t - timedelta(hours=window_h) >= now - timedelta(days=7):
            n += 1
    return n


def global_gates(cfg, rows, now, tz, extra_scheduled=0):
    """(б), (в), (г): общие для всех черновиков. Возвращает список (имя, ок, подробности)."""
    ac = cfg["auto_approve"]
    out = []
    d, why = rates_verified_on()
    name_b = f"б) ставки сверены (RATES_VERIFIED_ON) не позднее {ac['rates_max_age_days']} дн. назад"
    if d is None:
        out.append((name_b, False, why))
    else:
        age = (now.date() - d).days
        ok_b = 0 <= age <= ac["rates_max_age_days"]
        out.append((name_b, ok_b, f"сверено {d:%d.%m.%Y}, возраст {age} дн." + ("" if ok_b else (" — дата в будущем" if age < 0 else " — старше допустимого, сверьте ставки заново"))))
    out.append(("в) нет файла publishing/HOLD", not HOLD_FILE.exists(), "файл есть" if HOLD_FILE.exists() else ""))
    n = auto_count(rows, now, ac["window_hours"], tz) + extra_scheduled
    out.append((f"г) не больше {ac['max_per_7_days']} авто-статьи за 7 дней", n < ac["max_per_7_days"], f"уже запланировано за 7 дней: {n}"))
    return out


def draft_gates(cfg, slug):
    """(а) и (д): для конкретного черновика."""
    ac = cfg["auto_approve"]
    amin = (ac.get("article_min") or {}).get(slug, DEFAULT_ARTICLE_MIN)
    results = validate_draft.validate(slug, article_min=amin)
    bad = [f"{name}: {detail}" if detail else name for name, ok, detail in results if not ok]
    a = ("а) validate_draft.py без проблем" + (f" (article-min {amin})"), not bad, "; ".join(bad))
    cur, rec = validate_draft.content_hash(slug), recorded_hash(slug)
    if cur is None:
        d = ("д) статья не изменилась после последней проверки", False, "нет article.md или post.md")
    elif rec is None:
        d = ("д) статья не изменилась после последней проверки", False,
             "в checks.md нет контрольной суммы: запустите validate_draft.py после проверки текста")
    else:
        d = ("д) статья не изменилась после последней проверки", cur == rec,
             "" if cur == rec else "текст изменён после последней проверки (checks.md): перепроверьте и запустите validate_draft.py")
    return [a, d], results


def title_and_preview(slug):
    text = (ARTICLES / slug / "post.md").read_text(encoding="utf-8")
    body, _ = split_cover(text)
    plain = strip_markdown(body).strip()
    return (plain.split("\n", 1)[0] if plain else slug), plain[:600]


def format_gates(gates):
    return "\n".join(f"{'ок ' if ok else 'НЕТ'} {name}" + (f" — {detail}" if detail else "") for name, ok, detail in gates)


def build_message(slug, passed, gates, when, window_h):
    title, preview = title_and_preview(slug)
    if passed:
        return (f"Запланирована публикация: «{title}» ({slug})\n"
                f"Опубликовать после: {when:{FMT}} МСК (окно отмены {window_h} ч)\n\n"
                f"Проверки:\n{format_gates(gates)}\n\nПервые 600 знаков:\n{preview}\n\n"
                f"Остановить: статус «стоп» в publishing/publications.csv или файл publishing/HOLD.")
    return (f"Автоодобрение не прошло: «{title}» ({slug})\n\nПроверки:\n{format_gates(gates)}\n\n"
            f"Статус не изменён (черновик).")


def send_notification(token, chat_id, text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text[:4000]}, timeout=30)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        return False, mask(f"{type(e).__name__}: {e}", token)
    return bool(data.get("ok")), data.get("description", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="реально: отправить уведомление и менять статус (по умолчанию dry-run)")
    ap.add_argument("--slug", help="проверить только этот черновик")
    args = ap.parse_args()
    cfg = load_config()
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = datetime.now(tz)
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), str(cfg.get("notify_chat_id") or "")
    window_h = cfg["auto_approve"]["window_hours"]
    print("Режим:", "РЕАЛЬНЫЙ ЗАПУСК" if args.run else "dry-run (ничего не отправляется и не меняется)")
    if args.run and not (token and chat_id):
        print("Нет TELEGRAM_BOT_TOKEN или notify_chat_id — уведомить нельзя, ничего не менял.")
        return 1

    rows = read_publications()
    drafts = [r for r in rows if r["статус"].strip().lower() == "черновик" and (not args.slug or r["slug"] == args.slug)]
    print(f"Черновиков: {len(drafts)}")
    scheduled_now = 0
    for row in drafts:
        slug = row["slug"]
        gates = global_gates(cfg, rows, now, tz, extra_scheduled=scheduled_now)
        dg, _ = draft_gates(cfg, slug)
        gates = dg[:1] + gates + dg[1:]          # порядок а, б, в, г, д
        passed = all(ok for _, ok, _ in gates)
        when = now + timedelta(hours=window_h)
        msg = build_message(slug, passed, gates, when, window_h)
        print(f"\n=== {slug}: {'ВСЕ ВОРОТА ПРОЙДЕНЫ' if passed else 'не пройдено'}\n{format_gates(gates)}")
        print(f"\n--- {'уведомление, которое было бы отправлено' if not args.run else 'отправляю уведомление'} в notify_chat_id:\n{msg}")
        if not args.run:
            print(f"\n[dry-run] при реальном запуске: {'статус «запланировано», опубликовать после ' + format(when, FMT) if passed else 'статус остался бы «черновик»'}")
            continue
        ok, err = send_notification(token, chat_id, msg)
        if not ok:
            print(f"Уведомление не отправлено ({err}) — статус не меняю.")
            continue
        if passed:
            row["статус"], row["опубликовать после"] = "запланировано", when.strftime(FMT)
            write_publications(rows)
            scheduled_now += 1
            print(f"Статус {slug}: запланировано, опубликовать после {row['опубликовать после']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
