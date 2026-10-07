# -*- coding: utf-8 -*-
"""
publish_lot.py — ОДНА точка входа для ручной публикации лота HeyDealer в канал
@auto_generals (посты «торги идут, участвуем за вас»; с фото-альбомом или с видео).

Конвейер (каждый шаг останавливает публикацию при проблеме):
    1. ссылка/хэш лота -> данные лота (API HeyDealer под сессией дилера) либо готовый
       lot_raw.json (--lot-json);
    2. данные лота + ваши поля (название, мощность, ставка…) -> lot;
    3. цена — ОДНОЙ общей формулой (pricing.py: надбавка 14%, банк, таможня, логистика);
    4. текст — тем же единым шаблоном (post_render.render_post(lot, "auction"));
    5. ГЕЙТ validate_post_before_publish() (фото/название/год/пробег/цена, config.MIN_YEAR…);
    6. проверка дублей по реестру published.jsonl (lot_id heydealer:<hash>, затем модель+год+пробег);
    7. ЧЕРНОВИК: текст, канал, размер медиа, цена и предупреждения — и ожидание вашего «да»;
    8. отправка и СРАЗУ запись в реестр (способ manual, шаблон auction, message_id).

БЕЗОПАСНОСТЬ. Без --publish команда только показывает черновик (dry-run) и ничего не шлёт.
--publish без --yes спрашивает «да» в терминале; в неинтерактивном запуске отказывается.
--yes добавляется ТОЛЬКО после вашего согласия в чате. Если --chat указывает не основной
канал (config.TELEGRAM_CHAT_ID) — это тестовая отправка: к тексту добавляется «[ТЕСТ] » и
запись в реестр НЕ делается. Вход в HeyDealer — только с явным --login (под вашими учётными
данными из credentials.json); без него при недействительной сессии команда остановится.

Примеры:
    python publish_lot.py https://dealer.heydealer.com/cars/nEB6WaPQ/ --bid 12000000 --cap 13000000 \\
        --title "Kia Ray Signature (The New Ray)" --hp 76 --video ролик.mp4          # черновик
    python publish_lot.py nEB6WaPQ --lot-json lot_raw.json --bid 12000000 --title "…" --hp 76 --pick 1 2 6 7
    ... --publish                       # спросит «да» в терминале
    ... --publish --yes --chat <тестовый чат>      # тестовая отправка с пометкой [ТЕСТ]
"""

import argparse
import json
import logging
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config
import registry
import telegram_media
from post_render import CAPTION_LIMIT, MSK, PostRenderError, RenderedPost, parse_deadline, render_post
from post_validator import validate_post_before_publish

logger = logging.getLogger("publish_lot")

HEYDEALER_API = "https://api.heydealer.com/v2/dealers/web/cars/{hash_id}/"
FUEL_CODES = {"gasoline": "petrol", "diesel": "diesel", "lpg": "lpg", "electric": "elec", "ev": "elec"}
TRANSMISSIONS = {"auto": "автомат", "manual": "механика"}
DEFAULT_PHOTO_COUNT = 10


class LotError(Exception):
    """Не удалось получить/разобрать данные лота."""


# ── данные лота ───────────────────────────────────────────────────────────

def extract_hash(text: str) -> str:
    """https://dealer.heydealer.com/cars/nEB6WaPQ/ (или просто nEB6WaPQ) -> nEB6WaPQ."""
    found = re.search(r"heydealer\.com/cars/([A-Za-z0-9]+)", text) or re.fullmatch(r"\s*([A-Za-z0-9]{6,12})\s*", text)
    if not found:
        raise LotError(f"не похоже на ссылку или хэш лота HeyDealer: {text!r}")
    return found.group(1)


def fetch_lot(hash_id: str, login: bool = False) -> dict:
    """Лот через API HeyDealer под сессией дилера. Сессия недействительна — вход ТОЛЬКО с login=True
    (живой логин под вашими учётными данными); иначе LotError с подсказкой."""
    import heydealer_scraper as hs
    session = hs._build_requests_session()
    if not hs._session_is_valid():
        if not login:
            raise LotError("сессия HeyDealer недействительна. Запустите с --login (войдёт под данными из "
                           "credentials.json) или передайте сохранённый лот через --lot-json")
        import heydealer_login as hl
        hl.login_and_save_session()
        session = hs._build_requests_session()
    response = session.get(HEYDEALER_API.format(hash_id=hash_id), timeout=30)
    if not response.ok:
        raise LotError(f"HeyDealer вернул {response.status_code} для лота {hash_id}")
    return response.json()


def _plural_owners(n: int) -> str:
    return f"{n} смен{'а' if n == 1 else ('ы' if n in (2, 3, 4) else '')} владельца"


def history_from_carhistory(ch: Optional[dict]) -> Optional[str]:
    """
    Фактическая сводка истории из carhistory HeyDealer — ТОЛЬКО то, что есть в данных.
    Нет carhistory -> None (блока «История» в посте не будет). Слово «чистая» не используется;
    «нет выплат» пишется лишь когда счётчики явно равны нулю. Замеры ЛКП и замечания осмотра в
    carhistory нет — их добавляют вручную (--history-extra).
    """
    if not ch:
        return None
    parts = []
    mine, other = ch.get("my_car_accident_count"), ch.get("other_car_accident_count")
    if mine is not None and other is not None:
        if mine == 0 and other == 0:
            parts.append("страховых выплат по ДТП нет")
        else:
            if mine:
                parts.append(f"ДТП с ущербом своему авто: {mine} (выплаты {int(ch.get('my_car_accident_cost') or 0):,} ₩)".replace(",", " "))
            if other:
                parts.append(f"ДТП с ущербом чужим авто: {other} (выплаты {int(ch.get('other_car_accident_cost') or 0):,} ₩)".replace(",", " "))
    flags = [("stolen_count", "угон"), ("total_loss_count", "тотал"), ("flooded_count", "затопление")]
    present = [name for key, name in flags if ch.get(key)]
    if present:
        parts.append("зафиксированы: " + ", ".join(present))
    elif all(ch.get(key) == 0 for key, _ in flags):
        parts.append("угона, тотала и затопления не было")
    owners = ch.get("owner_changed_count")
    if owners:
        dates = sorted(o.get("owner_changed_date", "")[:10] for o in ch.get("owner_changed_list", []) if o.get("owner_changed_date"))
        shown = ", ".join(f"{d[8:10]}.{d[5:7]}.{d[:4]}" for d in dates) if 0 < len(dates) <= 3 else ""
        parts.append(_plural_owners(owners) + (f" ({shown})" if shown else ""))
    used = [name for key, name in (("has_rent_use_record", "прокат"), ("has_business_use_record", "коммерческое использование"),
                                   ("has_public_use_record", "госслужба")) if ch.get(key)]
    if used:
        parts.append("использовался: " + ", ".join(used))
    elif all(ch.get(k) is False for k in ("has_rent_use_record", "has_business_use_record", "has_public_use_record")):
        parts.append("в прокате, коммерческом и государственном использовании не был")
    return "; ".join(parts) if parts else None


def lot_from_heydealer(raw: dict, *, title: str, hp: int, bid: int, cap: Optional[int] = None, cc: Optional[int] = None,
                       fuel: Optional[str] = None, transmission: Optional[str] = None, history: Optional[str] = None,
                       history_extra: Optional[str] = None, year_note: Optional[str] = None) -> dict:
    """Словарь лота для render_post/гейта/реестра. Берётся из лота: год и месяц выпуска (manufactured_date),
    модельный год (-> примечание), пробег, объём (carhistory.displacement), топливо, КПП, история, дедлайн, фото.
    Задаётся оператором: название (на площадке оно корейское), мощность, ставка (аукцион слепой)."""
    det, au = raw.get("detail") or {}, raw.get("auction") or {}
    vi, ch = det.get("vehicle_information") or {}, det.get("carhistory") or {}
    stamp = vi.get("manufactured_date") or det.get("initial_registration_date")
    if not stamp:
        raise LotError("в лоте нет даты выпуска (manufactured_date) — год и возраст для пошлины определить нельзя")
    made = datetime.fromisoformat(str(stamp).replace(" ", "T")[:19])
    if year_note is None and det.get("year") and int(det["year"]) != made.year:
        year_note = f" (модельный год {det['year']})"
    cc = cc or ch.get("displacement")
    if not cc:
        raise LotError("в лоте нет объёма двигателя (carhistory.displacement) — укажите --cc")
    fuel_code = fuel or FUEL_CODES.get(str(det.get("fuel", "")).lower())
    if not fuel_code:
        raise LotError(f"неизвестный тип топлива {det.get('fuel')!r} — укажите --fuel (petrol/diesel/lpg/elec)")
    summary = history if history is not None else history_from_carhistory(ch)
    if history_extra:
        summary = f"{summary}; {history_extra}" if summary else history_extra
    return {
        "title": title, "year": made.year, "month": made.month, "year_note": year_note or "",
        "mileage_km": det.get("mileage"), "cc": int(cc), "hp": int(hp), "fuel_code": fuel_code,
        "transmission": transmission or TRANSMISSIONS.get(str(det.get("transmission", "")).lower(), "не указано"),
        "price": int(bid), "cap_price": int(cap) if cap else None, "history": summary,
        "auction_end_at": au.get("end_at"), "lot_id": f"heydealer:{raw.get('hash_id')}", "source": "heydealer",
        "photos": list(det.get("image_urls") or []),
    }


# ── медиа ─────────────────────────────────────────────────────────────────

@dataclass
class MediaPlan:
    kind: str                      # "album" | "video"
    files: list
    description: str
    errors: list = field(default_factory=list)


def plan_media(*, video: Optional[str], photos: Optional[list], pick: Optional[list], image_urls: list, tmp_dir: Path) -> MediaPlan:
    """Что отправляем: видео (--video), свои фото (--photos) либо фото лота (--pick, по умолчанию первые 10)."""
    if video:
        path = Path(video)
        if not path.is_file():
            return MediaPlan("video", [], f"видео не найдено: {path}", [f"файл видео не найден: {path}"])
        meta = telegram_media.probe_video(path)
        size = path.stat().st_size
        errors = [f"видео {size / 1024 / 1024:.1f} МБ > 50 МБ (лимит Bot API)"] if size > telegram_media.VIDEO_MAX_BYTES else []
        dims = f"{meta.get('width')}×{meta.get('height')}, {meta.get('duration')} с" if meta else "параметры не определены (нет ffprobe)"
        return MediaPlan("video", [path], f"видео {path.name}: {size / 1024 / 1024:.1f} МБ, {dims}", errors)

    files, notes = [], []
    if photos:
        files = [Path(p) for p in photos]
        missing = [str(p) for p in files if not p.is_file()]
        if missing:
            return MediaPlan("album", [], "фото не найдены", [f"файл не найден: {m}" for m in missing])
        notes.append("свои фото")
    else:
        from vk_publisher import _download_photo
        indexes = pick or list(range(1, min(DEFAULT_PHOTO_COUNT, len(image_urls)) + 1))
        for number in indexes:
            if not 1 <= number <= len(image_urls):
                return MediaPlan("album", [], "неверный номер фото", [f"--pick {number}: в лоте {len(image_urls)} фото"])
            data = _download_photo(image_urls[number - 1])
            if not data:
                return MediaPlan("album", [], "фото не скачалось", [f"не удалось скачать фото №{number} лота"])
            target = tmp_dir / f"lot_{number:02d}.jpg"
            target.write_bytes(data)
            files.append(target)
        notes.append(f"фото лота №{', '.join(map(str, indexes))} из {len(image_urls)}")
    errors = []
    if not telegram_media.ALBUM_MIN <= len(files) <= telegram_media.ALBUM_MAX:
        errors.append(f"в альбоме должно быть {telegram_media.ALBUM_MIN}-{telegram_media.ALBUM_MAX} фото, сейчас {len(files)}")
    sizes = []
    for path in files:
        try:
            sizes.append(len(telegram_media.jpeg_bytes(path)))
        except Exception as error:
            errors.append(f"{path.name}: не читается как изображение ({type(error).__name__})")
    total = sum(sizes) / 1024 / 1024
    return MediaPlan("album", files, f"альбом из {len(files)} фото ({'; '.join(notes)}), после сжатия {total:.1f} МБ", errors)


# ── черновик ──────────────────────────────────────────────────────────────

@dataclass
class Draft:
    lot: dict
    post: RenderedPost
    channel: str
    test_mode: bool
    caption: str
    gate_reason: str = ""
    duplicate: Optional[dict] = None
    warnings: list = field(default_factory=list)

    @property
    def blockers(self) -> list:
        found = []
        if self.gate_reason:
            found.append(f"гейт validate_post_before_publish не пройден: {self.gate_reason}")
        if self.duplicate:
            found.append("дубль — " + registry.describe_duplicate(self.duplicate))
        if len(self.caption) > CAPTION_LIMIT:
            found.append(f"подпись {len(self.caption)} симв. > {CAPTION_LIMIT} — сократите историю (--history/--history-extra)")
        return found


def build_draft(lot: dict, rates: dict, *, chat: Optional[str] = None, registry_path: Optional[Path] = None) -> Draft:
    """Текст (render_post) -> гейт -> дубли. Поднимает PostRenderError, если пост собрать нельзя."""
    post = render_post(lot, "auction", rates)
    car = dict(lot, brand=lot["title"], model="", trim="")  # как делает render_post: название целиком в brand
    validated, reason = validate_post_before_publish(car, post.fields)
    duplicate = registry.find_duplicate(car, registry.load_records(registry_path))
    channel = chat or config.TELEGRAM_CHAT_ID
    test_mode = channel != config.TELEGRAM_CHAT_ID
    caption = post.text.strip()
    if test_mode:
        caption = "[ТЕСТ] " + caption
    return Draft(lot=lot, post=post, channel=channel, test_mode=test_mode, caption=caption,
                 gate_reason="" if validated is not None else reason, duplicate=duplicate, warnings=list(post.warnings))


def format_draft(draft: Draft, media: MediaPlan, publish: bool) -> str:
    out = ["=" * 64, "ЧЕРНОВИК ПОСТА" + (" (ТЕСТОВАЯ отправка: «[ТЕСТ]», в реестр не пишется)" if draft.test_mode else ""), "=" * 64,
           draft.caption, "-" * 64,
           f"Канал назначения: {draft.channel}" + ("  ← ТЕСТОВЫЙ чат" if draft.test_mode else "  ← основной канал"),
           f"Медиа: {media.description}",
           f"Подпись: {len(draft.caption)} симв. из {CAPTION_LIMIT}",
           f"Лот в реестре: heydealer:{draft.lot['lot_id'].split(':')[-1]}"]
    pc = draft.post.fields
    out.append(f"Цена под ключ: {pc['total_rub']} ₽ при ставке {draft.lot['price']:,} ₩".replace(",", " "))
    for warning in draft.warnings:
        out.append(f"⚠ {warning}")
    problems = draft.blockers + media.errors
    out.append("-" * 64)
    out += [f"✖ БЛОКИРУЕТ: {p}" for p in problems] or ["✔ гейт пройден, дублей нет, медиа в порядке"]
    out.append("РЕЖИМ: ПУБЛИКАЦИЯ (после подтверждения)" if publish else "РЕЖИМ: DRY-RUN — ничего не отправлено (добавьте --publish)")
    return "\n".join(out)


# ── отправка и запись в реестр ────────────────────────────────────────────

def publish(draft: Draft, media: MediaPlan, registry_path: Optional[Path] = None) -> Optional[dict]:
    """Отправляет пост и СРАЗУ записывает его в реестр (кроме тестовой отправки). Возвращает запись
    реестра (либо {"message_id": ...} для теста) или None, если отправка не удалась."""
    problems = draft.blockers + media.errors
    if problems:
        raise RuntimeError("публикация заблокирована: " + "; ".join(problems))
    if media.kind == "video":
        message_id = telegram_media.send_video_file(media.files[0], draft.caption, draft.channel)
    else:
        ids = telegram_media.send_album(media.files, draft.caption, draft.channel)
        message_id = ids[0] if ids else None
    if not message_id:
        return None
    if draft.test_mode:
        return {"message_id": message_id, "test": True}
    lot = draft.lot
    deadline = parse_deadline(lot.get("auction_end_at"))
    record = {
        "message_id": message_id, "post_url": f"https://t.me/{registry.CHANNEL}/{message_id}",
        "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lot_id": lot["lot_id"], "lot_url": None, "platform": "heydealer", "model": lot["title"],
        "year": lot["year"], "mileage_km": lot["mileage_km"], "method": "manual", "template": "auction",
        "source": "publish_lot",
    }
    if deadline is not None:
        record["deadline_msk"] = deadline.astimezone(MSK).strftime("%Y-%m-%dT%H:%M")
    record["recorded_at"] = record["posted_at"]
    try:
        registry.append_record(record, registry_path)
    except Exception as error:  # пост уже в канале — главное не потерять данные для ручной регистрации
        print(f"‼ ПОСТ ОПУБЛИКОВАН (message_id {message_id}), но запись в реестр не удалась ({error}). "
              f"Внесите вручную: python registry.py register-post https://t.me/{registry.CHANNEL}/{message_id} "
              f"--lot-id {lot['lot_id']} --platform heydealer --template auction --apply")
    return record


# ── CLI ───────────────────────────────────────────────────────────────────

def _load_config_file(path: Optional[str]) -> dict:
    if not path:
        return {}
    text = Path(path).read_text(encoding="utf-8")
    if path.lower().endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text) or {}
    return json.loads(text)


def main(argv: Optional[list] = None, rates: Optional[dict] = None) -> int:
    parser = argparse.ArgumentParser(description="Ручная публикация лота HeyDealer в канал (по умолчанию dry-run)",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("Примеры:")[1])
    parser.add_argument("lot", help="ссылка на лот HeyDealer или хэш (nEB6WaPQ)")
    parser.add_argument("--bid", type=int, help="ориентир ставки, ₩ (аукцион слепой — цены у лота нет)")
    parser.add_argument("--cap", type=int, help="верхняя ставка, ₩ (для «при ставке до … млн ₩ — до … ₽»)")
    parser.add_argument("--title", help="название для поста (на площадке оно корейское)")
    parser.add_argument("--hp", type=int, help="мощность, л.с. (площадка не отдаёт)")
    parser.add_argument("--cc", type=int); parser.add_argument("--fuel"); parser.add_argument("--transmission")
    parser.add_argument("--history", help="полностью заменить автосводку истории")
    parser.add_argument("--history-extra", dest="history_extra", help="дописать к автосводке (замер ЛКП, осмотр)")
    parser.add_argument("--year-note", dest="year_note")
    parser.add_argument("--config", help="JSON/YAML с теми же полями (флаги командной строки важнее)")
    parser.add_argument("--lot-json", dest="lot_json", help="готовый lot_raw.json вместо запроса к API")
    parser.add_argument("--login", action="store_true", help="разрешить вход в HeyDealer, если сессия недействительна")
    parser.add_argument("--video"); parser.add_argument("--photos", nargs="+"); parser.add_argument("--pick", nargs="+", type=int)
    parser.add_argument("--chat", help="чат назначения; не основной канал = тестовая отправка с пометкой [ТЕСТ]")
    parser.add_argument("--registry", help=argparse.SUPPRESS)
    parser.add_argument("--publish", action="store_true", help="опубликовать (без флага — только черновик)")
    parser.add_argument("--yes", action="store_true", help="подтверждение дано оператором в чате (без него — вопрос в терминале)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    defaults = _load_config_file(args.config)
    def pick_value(name, cast=None):
        value = getattr(args, name, None)
        value = value if value is not None else defaults.get(name)
        return cast(value) if (value is not None and cast) else value
    title, hp, bid = pick_value("title"), pick_value("hp", int), pick_value("bid", int)
    missing = [flag for flag, v in (("--title", title), ("--hp", hp), ("--bid", bid)) if not v]
    if missing:
        print(f"Ошибка: не заданы {', '.join(missing)} (название на площадке корейское, мощность и ставку площадка не отдаёт)")
        return 2

    tmp_dir = Path(tempfile.mkdtemp(prefix="publish_lot_"))
    try:
        hash_id = extract_hash(args.lot)
        raw = json.loads(Path(args.lot_json).read_text(encoding="utf-8")) if args.lot_json else fetch_lot(hash_id, login=args.login)
        raw.setdefault("hash_id", hash_id)
        lot = lot_from_heydealer(raw, title=title, hp=hp, bid=bid, cap=pick_value("cap", int), cc=pick_value("cc", int),
                                 fuel=pick_value("fuel"), transmission=pick_value("transmission"), history=pick_value("history"),
                                 history_extra=pick_value("history_extra"), year_note=pick_value("year_note"))
        if rates is None:
            from rates import fetch_cbr_rates
            rates = fetch_cbr_rates()
        registry_path = Path(args.registry) if args.registry else None
        draft = build_draft(lot, rates, chat=args.chat, registry_path=registry_path)
        media = plan_media(video=args.video, photos=args.photos, pick=args.pick, image_urls=lot["photos"], tmp_dir=tmp_dir)
    except (LotError, PostRenderError) as error:
        print(f"Ошибка: {error}")
        return 2

    print(format_draft(draft, media, args.publish))
    problems = draft.blockers + media.errors
    if problems:
        return 3
    if not args.publish:
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("Нужно подтверждение оператора: запустите в терминале или добавьте --yes после согласия в чате.")
            return 4
        answer = input(f"Опубликовать в {draft.channel}? Введите «да»: ").strip().lower()
        if answer != "да":
            print("Отменено — ничего не отправлено.")
            return 4
    result = publish(draft, media, registry_path)
    if result is None:
        print("Отправка не удалась — в реестр ничего не записано (подробности в логе).")
        return 5
    if result.get("test"):
        print(f"Тестовое сообщение отправлено (message_id {result['message_id']}); в реестр не записано.")
    else:
        print(f"ОПУБЛИКОВАНО: {result['post_url']} — запись в реестр внесена (message_id {result['message_id']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
