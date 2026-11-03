# -*- coding: utf-8 -*-
"""
test_publish_lot.py — самотест publish_lot.py (python test_publish_lot.py, без pytest, без сети;
отправка в Telegram подменена заглушками — ни одно сообщение не уходит).

Реальные данные: test_data/heydealer/lot_nEB6WaPQ.json — обезличенный реальный лот HeyDealer
(Kia Ray Signature, пост 5286); реестр published.jsonl репозитория (в нём этот лот уже есть).
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

import config
import post_validator as pv
import publish_lot as pl
import registry as reg
import telegram_media

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "test_data" / "heydealer" / "lot_nEB6WaPQ.json"
REAL_REGISTRY = ROOT / "published.jsonl"
# Тесты не должны зависеть от .env/секретов и дефолтов курса: фиксируем параметры формулы («ЦБ + 14%», банк 0,5%, 570 000 ₽)
config.KRW_PER_RUB = None
config.KOREA_COSTS_PCT, config.BANK_COMMISSION_PCT = 0.14, 0.005
config.LOGISTICS_RUB, config.BROKER_FEE_RUB, config.MARGIN_RUB, config.IMPORTER_TYPE = 320000.0, 100000.0, 150000.0, "phys"

RATES = {"krw_rub": 0.0625, "eur_rub": 95.33, "usd_rub": 85.0, "cny_rub": 12.0, "jpy_rub": 0.55, "_source": "cbr"}
PARAMS = {"KOREA_COSTS_PCT": 0.14, "BANK_COMMISSION_PCT": 0.005, "LOGISTICS_RUB": 320000.0, "BROKER_FEE_RUB": 100000.0,
          "MARGIN_RUB": 150000.0, "IMPORTER_TYPE": "phys", "TELEGRAM_CHAT_ID": "@main_channel_test"}
TITLE = "Kia Ray Signature (The New Ray)"


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def _raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _lot(**kw) -> dict:
    args = dict(title=TITLE, hp=76, bid=12_000_000, cap=13_000_000)
    args.update(kw)
    return pl.lot_from_heydealer(_raw(), **args)


def _make_photos(folder: Path, n: int) -> list:
    paths = []
    for i in range(n):
        path = folder / f"p{i}.jpg"
        Image.new("RGB", (640, 480), (30 * i % 255, 90, 160)).save(path, "JPEG")
        paths.append(str(path))
    return paths


def _raises(exc, fn) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


def run() -> None:
    results = []
    saved = {name: getattr(config, name) for name in PARAMS}
    for name, value in PARAMS.items():
        setattr(config, name, value)
    real_log = pv.REJECTED_LOG_PATH
    tmp = tempfile.TemporaryDirectory()
    work = Path(tmp.name)
    pv.REJECTED_LOG_PATH = work / "rejected_posts.log"
    sent = {"album": [], "video": []}
    orig_album, orig_video = telegram_media.send_album, telegram_media.send_video_file
    try:
        # ── ссылка/хэш ───────────────────────────────────────────────────
        results.append(_check("extract_hash: ссылка, ссылка без слэша, голый хэш",
                              pl.extract_hash("https://dealer.heydealer.com/cars/nEB6WaPQ/") == "nEB6WaPQ"
                              and pl.extract_hash("https://dealer.heydealer.com/cars/nEB6WaPQ") == "nEB6WaPQ"
                              and pl.extract_hash("nEB6WaPQ") == "nEB6WaPQ"))
        results.append(_check("extract_hash: чужая ссылка -> LotError", _raises(pl.LotError, lambda: pl.extract_hash("https://example.com/x"))))

        # ── история из carhistory ────────────────────────────────────────
        ch = _raw()["detail"]["carhistory"]
        history = pl.history_from_carhistory(ch)
        results.append(_check("история реального лота: выплат нет, угона/тотала/затопления не было, 2 смены владельца с датами, прокат не использовался",
                              history == "страховых выплат по ДТП нет; угона, тотала и затопления не было; "
                                         "2 смены владельца (22.08.2025, 22.09.2025); в прокате, коммерческом и государственном использовании не был"))
        results.append(_check("слово «чистая» не используется", "чистая" not in history.lower()))
        crash = pl.history_from_carhistory({"my_car_accident_count": 2, "my_car_accident_cost": 1_500_000, "other_car_accident_count": 0,
                                            "stolen_count": 1, "total_loss_count": 0, "flooded_count": 0, "owner_changed_count": 1,
                                            "owner_changed_list": [{"owner_changed_date": "2024-01-05"}], "has_rent_use_record": True})
        results.append(_check("ДТП/угон/прокат: факты с суммой, без «нет выплат»",
                              "ДТП с ущербом своему авто: 2 (выплаты 1 500 000 ₩)" in crash and "угон" in crash and "1 смена владельца (05.01.2024)" in crash
                              and "использовался: прокат" in crash and "выплат по ДТП нет" not in crash))
        results.append(_check("нет данных истории -> None (блока в посте не будет)", pl.history_from_carhistory(None) is None
                              and pl.history_from_carhistory({}) is None))

        # ── данные лота ──────────────────────────────────────────────────
        lot = _lot()
        results.append(_check("лот: год 2022 и месяц 12 из manufactured_date, примечание «модельный год 2023», пробег 20266",
                              (lot["year"], lot["month"], lot["year_note"], lot["mileage_km"]) == (2022, 12, " (модельный год 2023)", 20266)))
        results.append(_check("лот: объём 998 из carhistory, бензин, автомат, дедлайн из auction.end_at, lot_id heydealer:nEB6WaPQ",
                              (lot["cc"], lot["fuel_code"], lot["transmission"], lot["auction_end_at"], lot["lot_id"])
                              == (998, "petrol", "автомат", "2026-10-09 13:17:09", "heydealer:nEB6WaPQ")))
        results.append(_check("ставки и мощность — от оператора", (lot["price"], lot["cap_price"], lot["hp"]) == (12_000_000, 13_000_000, 76)))
        results.append(_check("--history заменяет автосводку, --history-extra дописывает",
                              _lot(history="своя история")["history"] == "своя история"
                              and _lot(history_extra="замер ЛКП в норме")["history"].endswith("; замер ЛКП в норме")))
        bad_fuel = _raw(); bad_fuel["detail"]["fuel"] = "hydrogen"
        results.append(_check("неизвестное топливо -> LotError с подсказкой --fuel",
                              _raises(pl.LotError, lambda: pl.lot_from_heydealer(bad_fuel, title=TITLE, hp=76, bid=1))))
        no_date = _raw(); no_date["detail"]["vehicle_information"] = {}
        results.append(_check("нет даты выпуска -> LotError", _raises(pl.LotError, lambda: pl.lot_from_heydealer(no_date, title=TITLE, hp=76, bid=1))))
        no_cc = _raw(); no_cc["detail"]["carhistory"].pop("displacement")
        results.append(_check("нет объёма -> LotError, с --cc работает", _raises(pl.LotError, lambda: pl.lot_from_heydealer(no_cc, title=TITLE, hp=76, bid=1))
                              and pl.lot_from_heydealer(no_cc, title=TITLE, hp=76, bid=1, cc=998)["cc"] == 998))

        # ── медиа ────────────────────────────────────────────────────────
        photos = _make_photos(work, 4)
        album = pl.plan_media(video=None, photos=photos, pick=None, image_urls=[], tmp_dir=work)
        results.append(_check("альбом из 4 своих фото: без ошибок, размер указан", album.kind == "album" and album.errors == [] and "4 фото" in album.description))
        results.append(_check("одно фото -> ошибка (альбом 2-10)", pl.plan_media(video=None, photos=photos[:1], pick=None, image_urls=[], tmp_dir=work).errors != []))
        results.append(_check("нет файла фото -> ошибка", pl.plan_media(video=None, photos=["/нет/файла.jpg"], pick=None, image_urls=[], tmp_dir=work).errors != []))
        results.append(_check("неверный --pick -> ошибка", pl.plan_media(video=None, photos=None, pick=[99], image_urls=["u"] * 3, tmp_dir=work).errors != []))
        big = work / "big.mp4"; big.write_bytes(b"0" * 200)
        telegram_media.VIDEO_MAX_BYTES, real_limit = 100, telegram_media.VIDEO_MAX_BYTES
        try:
            over = pl.plan_media(video=str(big), photos=None, pick=None, image_urls=[], tmp_dir=work)
        finally:
            telegram_media.VIDEO_MAX_BYTES = real_limit
        results.append(_check("видео больше 50 МБ (лимит подменён) -> ошибка", over.kind == "video" and any("МБ" in e for e in over.errors)))
        video_ok = pl.plan_media(video=str(big), photos=None, pick=None, image_urls=[], tmp_dir=work)
        results.append(_check("видео в пределах лимита: без ошибок, описание с размером", video_ok.errors == [] and "big.mp4" in video_ok.description))

        # ── черновик: текст, гейт, дубли ─────────────────────────────────
        empty_registry = work / "empty.jsonl"
        draft = pl.build_draft(lot, RATES, registry_path=empty_registry)
        results.append(_check("черновик: цена 1 583 959 ₽ при 12 млн ₩ и до 1 655 565 ₽ при 13 млн ₩ (общая формула), без блокировок",
                              "1 583 959 ₽ при ставке 12 млн ₩" in draft.caption and "до 1 655 565 ₽" in draft.caption and draft.blockers == []))
        results.append(_check("черновик: основной канал, не тестовый, подпись без [ТЕСТ]", draft.channel == "@main_channel_test" and not draft.test_mode
                              and not draft.caption.startswith("[ТЕСТ]")))
        real = pl.build_draft(lot, RATES, registry_path=REAL_REGISTRY)
        results.append(_check("реальный реестр: этот же лот (пост 5286) определён как дубль по lot_id и заблокирован",
                              real.duplicate is not None and "5286" in registry_msg(real) and any("дубль" in b for b in real.blockers)))
        old_lot = _lot(); old_lot["year"] = 2018
        gate = pl.build_draft(old_lot, RATES, registry_path=empty_registry)
        results.append(_check("гейт: год 2018 вне коридора -> блокировка «гейт validate_post_before_publish»",
                              any("гейт" in b for b in gate.blockers)))
        longer = pl.build_draft(_lot(history="х" * 1100), RATES, registry_path=empty_registry)
        results.append(_check("подпись > 1024 -> блокировка с подсказкой сократить историю", any("1024" in b for b in longer.blockers)))
        test_draft = pl.build_draft(lot, RATES, chat="@my_test_chat", registry_path=empty_registry)
        results.append(_check("--chat другой -> тестовый режим: подпись с «[ТЕСТ] »", test_draft.test_mode and test_draft.caption.startswith("[ТЕСТ] 🔥")))
        text = pl.format_draft(draft, album, publish=False)
        results.append(_check("экран черновика: текст, канал, размер медиа, режим DRY-RUN",
                              all(s in text for s in ("ЧЕРНОВИК ПОСТА", "Канал назначения: @main_channel_test", "альбом из 4 фото", "DRY-RUN"))))

        # ── отправка и запись в реестр (заглушки) ───────────────────────
        telegram_media.send_album = lambda files, caption, chat_id, dry_run=False: sent["album"].append((len(files), caption, chat_id)) or [7001, 7002, 7003]
        telegram_media.send_video_file = lambda path, caption, chat_id, dry_run=False: sent["video"].append((str(path), caption, chat_id)) or 7010
        reg_path = work / "published_a.jsonl"
        record = pl.publish(draft, album, reg_path)
        saved_rec = reg.load_records(reg_path).get(7001)
        results.append(_check("альбом отправлен один раз в основной канал; в реестр записан message_id ПЕРВОГО сообщения 7001",
                              len(sent["album"]) == 1 and sent["album"][0][2] == "@main_channel_test" and saved_rec is not None and record["message_id"] == 7001))
        results.append(_check("запись реестра: manual / auction / heydealer / lot_id / дедлайн 9 окт 07:17 МСК / модель / год / пробег",
                              (saved_rec["method"], saved_rec["template"], saved_rec["platform"], saved_rec["lot_id"], saved_rec["deadline_msk"],
                               saved_rec["model"], saved_rec["year"], saved_rec["mileage_km"], saved_rec["source"])
                              == ("manual", "auction", "heydealer", "heydealer:nEB6WaPQ", "2026-10-09T07:17", TITLE, 2022, 20266, "publish_lot")
                              and saved_rec["post_url"] == "https://t.me/auto_generals/7001"))
        again = pl.build_draft(lot, RATES, registry_path=reg_path)
        results.append(_check("после публикации тот же лот сразу блокируется как дубль", again.duplicate is not None and again.blockers != []))
        results.append(_check("заблокированный черновик не отправляется (RuntimeError, заглушка не вызвана)",
                              _raises(RuntimeError, lambda: pl.publish(again, album, reg_path)) and len(sent["album"]) == 1))

        video_plan = pl.plan_media(video=str(big), photos=None, pick=None, image_urls=[], tmp_dir=work)
        video_draft = pl.build_draft(_lot(), RATES, registry_path=work / "published_b.jsonl")
        video_record = pl.publish(video_draft, video_plan, work / "published_b.jsonl")
        results.append(_check("видео-пост: отправлено видео, в реестр записан message_id 7010 (тот же код, что у фото)",
                              len(sent["video"]) == 1 and video_record["message_id"] == 7010
                              and reg.load_records(work / "published_b.jsonl")[7010]["template"] == "auction"))

        test_path = work / "published_c.jsonl"
        test_result = pl.publish(test_draft, album, test_path)
        results.append(_check("тестовая отправка: caption с [ТЕСТ], чат тестовый, в реестр НИЧЕГО не записано",
                              test_result.get("test") is True and sent["album"][-1][2] == "@my_test_chat"
                              and sent["album"][-1][1].startswith("[ТЕСТ] ") and not test_path.exists()))
        telegram_media.send_album = lambda *a, **k: None
        fail_path = work / "published_d.jsonl"
        results.append(_check("Telegram не принял -> None, реестр не тронут",
                              pl.publish(pl.build_draft(lot, RATES, registry_path=fail_path), album, fail_path) is None and not fail_path.exists()))
        telegram_media.send_album = lambda *a, **k: [7100]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rec_fail = pl.publish(pl.build_draft(lot, RATES, registry_path=empty_registry), album, work)  # путь-каталог: запись упадёт
        results.append(_check("пост ушёл, а запись в реестр не удалась -> громкое сообщение с готовой командой register-post",
                              rec_fail["message_id"] == 7100 and "ПОСТ ОПУБЛИКОВАН" in out.getvalue() and "register-post" in out.getvalue()
                              and "https://t.me/auto_generals/7100" in out.getvalue()))

        # ── CLI ──────────────────────────────────────────────────────────
        telegram_media.send_album = lambda files, caption, chat_id, dry_run=False: sent["album"].append((len(files), caption, chat_id)) or [7201, 7202]
        sent["album"].clear()
        base = [FIXTURE.stem and "nEB6WaPQ", "--lot-json", str(FIXTURE), "--bid", "12000000", "--cap", "13000000",
                "--title", TITLE, "--hp", "76", "--photos", *photos]
        cli_reg = work / "published_cli.jsonl"

        def cli(extra, stdin_text=None, tty=False):
            out, real_stdin = io.StringIO(), sys.stdin
            fake = io.StringIO(stdin_text or "")
            fake.isatty = lambda: tty
            sys.stdin = fake
            try:
                with contextlib.redirect_stdout(out):
                    code = pl.main(base + ["--registry", str(cli_reg)] + extra, rates=RATES)
            finally:
                sys.stdin = real_stdin
            return code, out.getvalue()

        code, text = cli([])
        results.append(_check("CLI без --publish: черновик показан, exit 0, ничего не отправлено", code == 0 and "DRY-RUN" in text and sent["album"] == []))
        code, text = cli(["--publish"])
        results.append(_check("CLI --publish без --yes в неинтерактивном запуске: отказ (exit 4), ничего не отправлено",
                              code == 4 and "подтверждение" in text and sent["album"] == []))
        code, text = cli(["--publish"], stdin_text="нет\n", tty=True)
        results.append(_check("CLI --publish, оператор ответил «нет»: exit 4, ничего не отправлено", code == 4 and sent["album"] == []))
        code, text = cli(["--publish", "--yes", "--chat", "@my_test_chat"])
        results.append(_check("CLI тестовая отправка в чужой чат: exit 0, [ТЕСТ], реестр не создан",
                              code == 0 and sent["album"] and sent["album"][-1][1].startswith("[ТЕСТ] ") and not cli_reg.exists()))
        sent["album"].clear()
        code, text = cli(["--publish"], stdin_text="Да\n", tty=True)
        results.append(_check("CLI --publish, оператор ввёл «да» в терминале: опубликовано, запись в реестре",
                              code == 0 and "ОПУБЛИКОВАНО: https://t.me/auto_generals/7201" in text and 7201 in reg.load_records(cli_reg)))
        code, text = cli(["--publish", "--yes"])
        results.append(_check("повторный запуск того же лота: блокировка как дубль (exit 3), повторной отправки нет",
                              code == 3 and "дубль" in text and len(sent["album"]) == 1))
        code, _ = pl.main(["nEB6WaPQ", "--lot-json", str(FIXTURE)], rates=RATES), None
        results.append(_check("не заданы --title/--hp/--bid -> exit 2", code == 2))
    finally:
        telegram_media.send_album, telegram_media.send_video_file = orig_album, orig_video
        pv.REJECTED_LOG_PATH = real_log
        for name, value in saved.items():
            setattr(config, name, value)
        tmp.cleanup()

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


def registry_msg(draft) -> str:
    return reg.describe_duplicate(draft.duplicate) if draft.duplicate else ""


if __name__ == "__main__":
    run()
