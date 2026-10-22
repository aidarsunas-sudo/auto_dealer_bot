"""Публикация одобренных статей в Telegram.

  python publishing/publish.py             # dry_run: что опубликовали бы, ничего не отправляется
  python publishing/publish.py --publish   # реальная публикация (токен TELEGRAM_BOT_TOKEN)
  --ignore-schedule     пропустить только проверку дня недели и времени (лимит «одна статья в день» остаётся)
  --allow-main-channel  разрешить --publish в основной канал (TELEGRAM_CHAT_ID или @auto_generals)
  --no-invite-link      публиковать с общей ссылкой, если именную пригласительную ссылку создать не удалось

Берёт из publications.csv записи со статусом «одобрено» (ставится вручную), публикует
articles/<slug>/post.md. Не больше одной статьи за запуск и в день, только по расписанию из config.yaml.
Перед отправкой: вырезает строку «Описание обложки: …» (сохраняет в articles/<slug>/cover.txt), создаёт
именную пригласительную ссылку на канал (name = slug) и подставляет её в блок «Как заказать» вместо общей
ссылки на Telegram-канал (ссылки на ботов и готовые invite не трогаются), убирает разметку Markdown (**, _, #).
post.md не меняется.
"""
import argparse
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from common import ARTICLES, load_config, mask, read_publications, write_publications

API = "https://api.telegram.org/bot{token}/{method}"
APPROVED = "одобрено"
PUBLISHED = "опубликовано"
TG_LIMIT = 4096
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TG_LINK = re.compile(r"https?://t\.me/(?!\+|joinchat/)([A-Za-z0-9_]{4,})\S*?(?=[\s)\]>]|$)")
COVER_RE = re.compile(r"\A\s*Описание обложки:[^\n]*\n*", re.I)


def tg(token, method, payload):
    """Вызов Bot API; токен в тексте ошибок маскируется."""
    try:
        r = requests.post(API.format(token=token, method=method), json=payload, timeout=30)
        return r.json()
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "description": mask(f"{type(e).__name__}: {e}", token)}


def in_schedule(cfg, rows, now, ignore_schedule=False):
    sch = cfg["schedule"]
    if not ignore_schedule:  # --ignore-schedule пропускает только день недели и время
        if DAYS[now.weekday()] not in [d.lower() for d in sch["days"]]:
            return False, f"сегодня ({DAYS[now.weekday()]}) не день публикации {sch['days']}"
        hh, mm = map(int, str(sch["time"]).split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return False, f"ещё рано: публикация с {sch['time']} ({sch['timezone']})"
    if any(r["статус"] == PUBLISHED and r["дата"] == now.date().isoformat() for r in rows):
        return False, "сегодня уже была публикация"
    return True, ""


def substitute_invite(text, invite_url):
    """Заменяет ссылки на Telegram-каналы в блоке «Как заказать». Блок начинается с заголовка (# или **)
    или со строки «Как заказать:» в начале строки (простой блок кончается пустой строкой).
    Возвращает (текст, число замен)."""
    out, mode, count = [], None, 0

    def repl(m):
        nonlocal count
        if m.group(1).lower().endswith("bot"):
            return m.group(0)
        count += 1
        return invite_url

    for line in text.split("\n"):
        if re.match(r"\s*#+\s", line):
            mode = "heading" if re.search(r"как заказать", line, re.I) else None
        elif re.match(r"\s*\*\*\s*как заказать", line, re.I):
            mode = "heading"
        elif re.match(r"\s*как заказать\s*:", line, re.I):
            mode = "plain"
        elif mode == "plain" and not line.strip():
            mode = None
        if mode:
            line = TG_LINK.sub(repl, line)
        out.append(line)
    return "\n".join(out), count


def split_cover(text):
    """Вырезает первую строку «Описание обложки: …». Возвращает (текст без неё, строка описания или None)."""
    m = COVER_RE.match(text)
    return (text[m.end():], m.group(0).strip()) if m else (text, None)


def strip_markdown(text):
    """Убирает ** и _ (выделение) и # в начале строки; ссылки (utm_source и т.п.) не затрагиваются."""
    parts = re.split(r"(https?://\S+)", text)
    for i in range(0, len(parts), 2):
        t = re.sub(r"\*\*(.+?)\*\*", r"\1", parts[i], flags=re.S).replace("**", "")
        t = re.sub(r"(?m)^[ \t]*#+[ \t]+", "", t)
        parts[i] = re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", r"\1", t)
    return "".join(parts)


def main_channels():
    return {c.strip().lower() for c in (os.getenv("TELEGRAM_CHAT_ID"), "@auto_generals") if c and c.strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true", help="реально отправить (по умолчанию dry_run)")
    ap.add_argument("--ignore-schedule", action="store_true", help="пропустить проверку дня недели и времени")
    ap.add_argument("--allow-main-channel", action="store_true", help="разрешить --publish в основной канал")
    ap.add_argument("--no-invite-link", action="store_true",
                    help="публиковать с общей ссылкой, если именную пригласительную ссылку создать не удалось")
    args = ap.parse_args()
    cfg = load_config()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel = cfg.get("telegram_channel") or os.getenv("TELEGRAM_CHANNEL")   # куда публикуем
    promo = cfg.get("promo_channel") or "@auto_generals"                      # куда ведёт именная ссылка
    print("Режим:", "ПУБЛИКАЦИЯ" if args.publish else "dry_run (ничего не отправляется)")
    is_main = bool(channel) and str(channel).strip().lower() in main_channels()
    if args.publish and is_main and not args.allow_main_channel:
        print(f"Отказ: целевой канал {channel} совпадает с основным (TELEGRAM_CHAT_ID или @auto_generals), "
              f"куда выходят посты лидогена. Чтобы опубликовать статью именно туда, добавьте флаг --allow-main-channel; "
              f"иначе укажите другой канал в telegram_channel (publishing/config.yaml).")
        return 1

    rows = read_publications()
    now = datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))
    ok, why = in_schedule(cfg, rows, now, args.ignore_schedule)
    approved = [r for r in rows if r["статус"].strip().lower() == APPROVED]
    print(f"Одобрено к публикации: {len(approved)}; расписание: {'можно' if ok else 'нельзя — ' + why}")
    if not approved:
        return 0
    if not ok and args.publish:
        print("Вне расписания — ничего не публикую.")
        return 0

    row = approved[0]  # одна статья за запуск
    slug = row["slug"]
    path = ARTICLES / slug / "post.md"
    if not path.exists():
        print(f"{slug}: нет файла {path} — пропуск")
        return 1
    text = path.read_text(encoding="utf-8")
    if not channel:
        print("Канал не задан: telegram_channel в config.yaml или TELEGRAM_CHANNEL в .env")
        return 1

    invite = row["пригласительная ссылка"].strip()
    body, cover = split_cover(text)
    if not args.publish:
        shown, n = substitute_invite(body, invite or "<ИМЕННАЯ_ССЫЛКА>")
        final = strip_markdown(shown)
        print(f"\nСтатья: {slug} ({row['главный запрос']})\ntelegram_channel (куда публикуем): {channel}"
              + ("  [основной канал: для --publish нужен --allow-main-channel]" if is_main else ""))
        print(f"promo_channel (куда ведёт именная ссылка): {promo}")
        print("Описание обложки: " + (f"вырезано из поста, будет сохранено в articles/{slug}/cover.txt" if cover else "не найдено"))
        print(f"Именная ссылка: {'уже есть в publications.csv' if invite else f'будет создана в {promo} (name=' + slug[:32] + ')'}; "
              f"замен общей ссылки на канал в блоке «Как заказать»: {n}")
        print(f"Итоговая длина: {len(final)} знаков (лимит {TG_LIMIT})")
        print("\n----- итоговый текст -----\n" + final)
        return 0

    if not token:
        print("TELEGRAM_BOT_TOKEN не задан в .env — публикация невозможна")
        return 1

    if not invite and not args.no_invite_link:
        res = tg(token, "createChatInviteLink", {"chat_id": promo, "name": slug[:32]})
        if res.get("ok"):
            invite = res["result"]["invite_link"]
            row["пригласительная ссылка"] = invite
            write_publications(rows)  # сохраняем сразу: при сбое отправки ссылка переиспользуется
        else:
            print(f"Именная ссылка для {promo} не создана: {res.get('description')}. Пост не опубликован. "
                  f"Дайте боту право приглашать пользователей в {promo} или запустите с --no-invite-link "
                  f"(тогда пост уйдёт с общей ссылкой).")
            return 1
    if cover:
        cover_path = ARTICLES / slug / "cover.txt"
        if not cover_path.exists():
            cover_path.write_text(cover + "\n", encoding="utf-8")
            print(f"Описание обложки сохранено: {cover_path}")
    if invite:
        body, n = substitute_invite(body, invite)
        print(f"Именная ссылка подставлена в {n} мест(а) блока «Как заказать»" if n
              else "В блоке «Как заказать» нет общей ссылки на канал — текст не менял")
    final = strip_markdown(body)
    if len(final) > TG_LIMIT:
        print(f"{slug}: итоговый текст {len(final)} символов > лимита Telegram {TG_LIMIT} — не публикую")
        return 1

    res = tg(token, "sendMessage", {"chat_id": channel, "text": final})
    if not res.get("ok"):
        print(f"Отправка не удалась: {res.get('description')}")
        return 1
    row["статус"], row["дата"] = PUBLISHED, now.date().isoformat()
    write_publications(rows)
    print(f"Опубликовано: {slug}, message_id {res['result']['message_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
