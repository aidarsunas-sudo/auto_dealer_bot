# -*- coding: utf-8 -*-
"""
test_registry.py — самотест registry.py (python test_registry.py, без pytest и без
сети). Парсер проверяется на РЕАЛЬНОМ HTML публичной страницы канала
(test_data/tme_auto_generals/wraps_8_posts.html — сырые блоки 8 известных постов),
сопоставление с автопайплайном — на реальном published_log.json репозитория.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import registry as reg

FIXTURE = Path(__file__).resolve().parent / "test_data" / "tme_auto_generals" / "wraps_8_posts.html"
IDS = [5239, 5249, 5254, 5255, 5261, 5266, 5276, 5286]


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def _raises(exception, fn) -> bool:
    try:
        fn()
    except exception:
        return True
    return False


def _fake_fetch(page_html: str):
    """Первая страница — фикстура, дальше пусто (конец канала)."""
    return lambda before=None: page_html if before is None else ""


def run() -> None:
    results = []
    page = FIXTURE.read_text(encoding="utf-8")
    posts = {p["message_id"]: p for p in reg.parse_channel_html(page)}

    # ── разбор страницы ───────────────────────────────────────────────────
    results.append(_check("парсер находит все 8 постов", sorted(posts) == IDS))
    results.append(_check("время UTC: QM6 = 2026-10-07 05:50:33",
                          posts[5276]["posted_at"] == datetime(2026, 10, 7, 5, 50, 33, tzinfo=timezone.utc)))
    results.append(_check("время UTC: Casper = 2026-10-06 14:12:07 (= 17:12 МСК)",
                          posts[5266]["posted_at"] == datetime(2026, 10, 6, 14, 12, 7, tzinfo=timezone.utc)))
    results.append(_check("альбомы считаются одним постом: 5239 — 10 фото, 5249 — 5 фото",
                          posts[5239]["photos"] == 10 and posts[5249]["photos"] == 5))
    results.append(_check("видео определяется: 5254 и 5286 — видео, 5239 — нет",
                          posts[5254]["has_video"] and posts[5286]["has_video"] and not posts[5239]["has_video"]))

    # ── разбор текста ─────────────────────────────────────────────────────
    info = {mid: reg.classify_post(posts[mid]["text"]) for mid in IDS}
    results.append(_check("шаблоны: 5239/5249/5255/5261/5266/5286 — auction, 5276 — lead, 5254 — free",
                          all(info[m]["template"] == "auction" for m in (5239, 5249, 5255, 5261, 5266, 5286))
                          and info[5276]["template"] == "lead" and info[5254]["template"] == "free"))
    results.append(_check("модель из заголовка: 5249 = Mercedes-Benz GLB 200 d, 5276 = Renault QM6",
                          info[5249]["model"] == "Mercedes-Benz GLB 200 d" and info[5276]["model"] == "Renault QM6"))
    results.append(_check("Kia Ray Signature (5286): модель, год 2022 (из «декабрь 2022»), пробег 20266",
                          info[5286]["model"] == "Kia Ray Signature (The New Ray)"
                          and info[5286]["year"] == 2022 and info[5286]["mileage_km"] == 20266))
    results.append(_check("QM6: год 2022 из блока «Год:»", info[5276]["year"] == 2022))
    results.append(_check("текст без данных -> None, не падает",
                          reg.classify_post("просто текст") == {"template": "free", "model": "просто текст",
                                                                 "year": None, "mileage_km": None, "is_test": False}))
    results.append(_check("пустой текст не падает", reg.classify_post("")["model"] is None))
    test_info = reg.classify_post("[ТЕСТ] 🔥 Kia K3 — уже можно забронировать!\n\n📋 Характеристики:\n📅 Год: 2023\n🛣 Пробег: 14 635 км")
    results.append(_check("тестовый пост: префикс [ТЕСТ] снят, is_test=True, модель «Kia K3», lead",
                          test_info == {"template": "lead", "model": "Kia K3", "year": 2023,
                                        "mileage_km": 14635, "is_test": True}))

    # ── сопоставление с автопайплайном (реальный published_log.json) ─────
    entries = reg.load_published_log()
    lot_urls = reg.match_lots(list(posts.values()), entries)
    qm6_url = lot_urls.get(5276)
    results.append(_check("QM6 (5276) сопоставлен с charancha-лотом из published_log (запись через ~37 с)",
                          bool(qm6_url) and "charancha.com" in qm6_url))
    results.append(_check("ручные посты (Casper 5266, Kia Ray 5286, все торги) НЕ сопоставляются с автопайплайном",
                          all(m not in lot_urls for m in (5239, 5249, 5254, 5255, 5261, 5266, 5286))))
    # Регресс реального бага: два лота в одну минуту. Запись M1 (21:32:06) принадлежит
    # посту A (21:31:06), хотя пост B вышел на 4 с позже; запись M2 — посту B.
    t0 = datetime(2026, 9, 23, 21, 31, 6, tzinfo=timezone.utc)
    lead = "🔥 Kia K3 — уже можно забронировать!\n📅 Год: 2023"
    pair = [{"message_id": 100, "posted_at": t0, "text": lead},
            {"message_id": 101, "posted_at": t0, "text": "", "photos": 0},  # видео без подписи — не кандидат
            {"message_id": 110, "posted_at": t0.replace(minute=32, second=10), "text": lead}]
    marks = [(t0.replace(minute=32, second=6), "https://example.com/A"),
             (t0.replace(minute=32, second=50), "https://example.com/B")]
    results.append(_check("два лота в одну минуту: записи не путаются местами",
                          reg.match_lots(pair, marks) == {100: "https://example.com/A", 110: "https://example.com/B"}))
    results.append(_check("пост позже записи (> допуска часов) к ней не относится",
                          reg.match_lots([pair[2]], [marks[0]]) == {}))
    results.append(_check("запись без поста в окне остаётся без пары",
                          reg.match_lots([pair[0]], [(t0.replace(hour=23), "https://example.com/X")]) == {}))
    results.append(_check("площадка по URL", reg.platform_from_url(qm6_url) == "charancha"
                          and reg.platform_from_url("https://fem.encar.com/cars/detail/1") == "encar"
                          and reg.platform_from_url(None) is None))

    # ── файл реестра ──────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "published.jsonl"
        base = {"message_id": 1, "template": "lead", "method": "unknown", "model": "Тест Ё"}
        reg.append_record(base, path)
        reg.append_record(dict(base, method="auto"), path)
        loaded = reg.load_records(path)
        raw = path.read_text(encoding="utf-8")
        results.append(_check("по message_id побеждает последняя строка", loaded[1]["method"] == "auto" and len(loaded) == 1))
        results.append(_check("кириллица не экранируется, строки заканчиваются \\n",
                              "Тест Ё" in raw and raw.endswith("\n") and raw.count("\n") == 2))
        results.append(_check("неверный method отклоняется", _raises(ValueError, lambda: reg.append_record(dict(base, method="robot"), path))))
        results.append(_check("неверный template отклоняется", _raises(ValueError, lambda: reg.append_record(dict(base, template="x"), path))))
        results.append(_check("без message_id отклоняется", _raises(ValueError, lambda: reg.append_record({"template": "lead", "method": "auto"}, path))))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("{битая строка\n")
        results.append(_check("битая строка пропускается, остальное читается", reg.load_records(path)[1]["method"] == "auto"))
        results.append(_check("нет файла -> пустой реестр", reg.load_records(Path(tmp) / "нет.jsonl") == {}))

    # ── reconcile: dry-run, apply, идемпотентность ───────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "published.jsonl"
        fetch = _fake_fetch(page)
        rows = reg.reconcile(days=3650, apply=False, fetch=fetch, registry_path=path, log_entries=entries, delay=0)
        results.append(_check("dry-run: найдено 8 новых, файл не создан",
                              len(rows) == 8 and all(s == "new" for s, _ in rows) and not path.exists()))

        reg.reconcile(days=3650, apply=True, fetch=fetch, registry_path=path, log_entries=entries, delay=0)
        records = reg.load_records(path)
        results.append(_check("apply: 8 записей, 8 строк в файле",
                              sorted(records) == IDS and len(path.read_text(encoding="utf-8").splitlines()) == 8))

        reg.reconcile(days=3650, apply=True, fetch=fetch, registry_path=path, log_entries=entries, delay=0)
        results.append(_check("повторный apply ничего не дублирует", len(path.read_text(encoding="utf-8").splitlines()) == 8))

        results.append(_check("5254 Changan: free / manual, модель из KNOWN",
                              records[5254]["template"] == "free" and records[5254]["method"] == "manual"
                              and records[5254]["model"] == "Changan Q05 Max+"))
        results.append(_check("5286 Kia Ray Signature: manual, lot_id heydealer:nEB6WaPQ, дедлайн 9 окт 07:17 МСК",
                              records[5286]["method"] == "manual" and records[5286]["lot_id"] == "heydealer:nEB6WaPQ"
                              and records[5286]["deadline_msk"] == "2026-10-09T07:17"))
        results.append(_check("5276 QM6: auto, lead, charancha, lot_url из published_log",
                              records[5276]["method"] == "auto" and records[5276]["template"] == "lead"
                              and records[5276]["platform"] == "charancha" and bool(records[5276]["lot_url"])))
        results.append(_check("5239 Kia Ray Best Selection: auction, heydealer, дедлайн 8 окт 07:37",
                              records[5239]["platform"] == "heydealer" and records[5239]["deadline_msk"] == "2026-10-08T07:37"))
        results.append(_check("ссылка на пост https://t.me/auto_generals/<id>",
                              all(r["post_url"] == f"https://t.me/auto_generals/{mid}" for mid, r in records.items())))

        # ручную запись сверка не перетирает
        manual = dict(records[5266], model="Правленая вручную", source="register_post")
        reg.append_record(manual, path)
        rows = reg.reconcile(days=3650, apply=True, fetch=fetch, registry_path=path, log_entries=entries, delay=0)
        results.append(_check("ручная правка записи не перетирается сверкой",
                              reg.load_records(path)[5266]["model"] == "Правленая вручную"
                              and dict((r["message_id"], s) for s, r in rows)[5266] == "same"))

    # ── видео без подписи и известный пост вне фикстуры ─────────────────
    video_only = ('<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="auto_generals/5999">'
                  '<a class="tgme_widget_message_video_player"></a>'
                  '<time datetime="2026-10-07T12:30:00+00:00"></time></div></div>')
    with tempfile.TemporaryDirectory() as tmp:
        rows = reg.reconcile(days=3650, apply=False, fetch=_fake_fetch(page + video_only),
                             registry_path=Path(tmp) / "p.jsonl", log_entries=entries, delay=0)
        results.append(_check("видео без подписи не вносится, но учитывается в skipped_media",
                              [r["message_id"] for _, r in rows] == IDS and rows.skipped_media == 1))
    sm6 = reg.build_record({"message_id": 5230, "posted_at": datetime(2026, 10, 5, 9, 1, tzinfo=timezone.utc),
                            "text": "🔥 Renault Samsung SM6 1.5 dCi SE — торги идут, участвуем за вас!\n📅 Год: 2018\n🛣 Пробег: 140 000 км"}, {})
    results.append(_check("5230 SM6: manual, auction, heydealer:Q8Y0LN6l, дедлайн 7 окт 10:48 МСК, 2018 / 140000 км",
                          sm6["method"] == "manual" and sm6["template"] == "auction" and sm6["lot_id"] == "heydealer:Q8Y0LN6l"
                          and sm6["deadline_msk"] == "2026-10-07T10:48" and sm6["year"] == 2018 and sm6["mileage_km"] == 140000))
    unknown = reg.build_record({"message_id": 1, "posted_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                                "text": "Просто текст без шаблона"}, {})
    results.append(_check("пост без признаков: method=unknown, template=free, lot_id=None",
                          unknown["method"] == "unknown" and unknown["template"] == "free" and unknown["lot_id"] is None))

    # ── register_post: пост, вышедший вне системы ────────────────────────
    anywhere = lambda before=None: page  # страница с 8 реальными постами при любом before
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "published.jsonl"
        status, rec = reg.register_post("https://t.me/auto_generals/5254", fetch=anywhere, registry_path=path)
        results.append(_check("register_post (dry-run): ручной пост 5254 найден на странице, статус new, файл не создан",
                              status == "new" and rec["message_id"] == 5254 and rec["method"] == "manual" and rec["template"] == "free"
                              and not path.exists()))
        reg.register_post("https://t.me/auto_generals/5254", fetch=anywhere, registry_path=path, apply=True)
        results.append(_check("register_post --apply записывает одну строку", len(path.read_text(encoding="utf-8").splitlines()) == 1))
        status, _ = reg.register_post("https://t.me/auto_generals/5254", fetch=anywhere, registry_path=path, apply=True)
        results.append(_check("повтор без изменений: статус same, дублей нет", status == "same" and len(path.read_text(encoding="utf-8").splitlines()) == 1))

        status, rec = reg.register_post("t.me/auto_generals/5239", {"lot_id": "heydealer:XYZ12345", "platform": "heydealer"},
                                        fetch=anywhere, registry_path=path, apply=True)
        results.append(_check("данные лота из команды попадают в запись (lot_id, площадка), остальное — из текста поста",
                              status == "new" and rec["lot_id"] == "heydealer:XYZ12345" and rec["year"] == 2022 and rec["source"] == "register_post"))
        status, rec = reg.register_post("5239", {"deadline_msk": "2026-10-08T08:00"}, fetch=anywhere, registry_path=path, apply=True)
        results.append(_check("повторная регистрация меняет только переданные поля (lot_id не теряется)",
                              status == "update" and rec["lot_id"] == "heydealer:XYZ12345" and rec["deadline_msk"] == "2026-10-08T08:00"
                              and reg.load_records(path)[5239]["deadline_msk"] == "2026-10-08T08:00"))
        status, rec = reg.register_post("5266", {"method": "unknown", "template": "lead"}, fetch=anywhere, registry_path=path)
        results.append(_check("способ и шаблон можно задать явно", rec["method"] == "unknown" and rec["template"] == "lead"))
        results.append(_check("поста нет на странице -> LookupError с подсказкой про первое сообщение альбома",
                              _raises(LookupError, lambda: reg.register_post("5999", fetch=anywhere, registry_path=path))))
        results.append(_check("чужая ссылка -> ValueError", _raises(ValueError, lambda: reg.register_post("https://t.me/other/5", fetch=anywhere, registry_path=path))))
        results.append(_check("неверный template -> ValueError", _raises(ValueError, lambda: reg.register_post("5276", {"template": "x"}, fetch=anywhere, registry_path=path))))
        results.append(_check("неизвестное поле -> ValueError", _raises(ValueError, lambda: reg.register_post("5276", {"colour": "red"}, fetch=anywhere, registry_path=path))))

    # ── глубина сверки ────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        rows = reg.reconcile(days=1, apply=False, fetch=_fake_fetch(page), registry_path=Path(tmp) / "p.jsonl",
                             log_entries=entries, delay=0, now=datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc))
        results.append(_check("посты старше глубины сверки отбрасываются (при now=7 окт 18:00 UTC и days=1 остаются 5276 и 5286)",
                              [r["message_id"] for _, r in rows] == [5276, 5286]))

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
