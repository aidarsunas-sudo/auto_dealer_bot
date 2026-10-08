# -*- coding: utf-8 -*-
"""test_encar_snapshot.py — самотест encar_snapshot.py (python test_encar_snapshot.py). Без сети: HTTP подменяется."""

import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import config
import encar_scraper
import encar_snapshot as es

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
TARGET = {"brand": "Kia", "model": "K3", "year_from": 2022, "year_to": 2023, "mileage_max": 60000}
CODES = {"car_type": "Y", "manufacturer_ko": "기아", "model_group": "K3"}


def _check(name, condition):
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def item(i, mileage=20000, year=202205, **kw):
    base = {"Id": str(i), "Manufacturer": "기아", "Model": "K3", "Badge": "1.6 가솔린", "Year": year, "Mileage": mileage,
            "Price": 1500, "SellType": "일반", "FuelType": "가솔린", "Transmission": "오토",
            "Photos": [{"location": f"/carpicture/{i}_{n:03d}.jpg"} for n in range(20)]}
    base.update(kw)
    return base


def page_html(results, count=None, passed=True):
    queries = {'getCarNormal({"action":"x","cursor":"","from":0,"sortType":"MobileModifiedDate"})':
               {"data": {"Count": len(results) if count is None else count, "SearchResults": results}}}
    data = {"props": {"pageProps": {"isPassed": passed, "initialState": {"ryvussApi": {"queries": queries}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script></html>'


class Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, params=None, headers=None, timeout=None):
        action = json.loads(params["search"])["action"]
        self.calls.append(action)
        return self.handler(action)


def mileage_range(action):
    m = re.search(r"Mileage\.range\((\d*)\.\.(\d*)\)", action)
    return (int(m.group(1) or 0), int(m.group(2) or 10**9)) if m else (0, 10**9)


def fetcher(handler, **kw):
    sleeps = []
    return es.Fetcher(FakeSession(handler), sleep=sleeps.append, pause=kw.pop("pause", 5), **kw), sleeps


def run():
    results = []
    # ── строка поиска ─────────────────────────────────────────────────────
    results.append(_check("build_action: марка/модель + год ГГГГММ + пробег",
                          es.build_action(CODES, 2022, 2023, None, 60000) ==
                          "(And.Hidden.N._.CarType.Y._.Manufacturer.기아._.ModelGroup.K3._.Year.range(202201..202312)._.Mileage.range(..60000).)"))
    results.append(_check("build_action: нижняя граница пробега и без года",
                          es.build_action(CODES, None, None, 30001, 60000).endswith("Mileage.range(30001..60000).)")
                          and "Year" not in es.build_action(CODES, None, None, 0, 5)))

    # ── разбор страницы: нормальная и все виды блокировок ─────────────────
    ok = es.parse_list_html(200, page_html([item(1), item(2)], count=2))
    results.append(_check("parse: нормальная страница -> count и результаты", ok["count"] == 2 and len(ok["results"]) == 2))
    results.append(_check("parse: Count=0 с нормальной структурой — НЕ блокировка", es.parse_list_html(200, page_html([], count=0))["results"] == []))
    for status in (403, 429, 500):
        try:
            es.parse_list_html(status, "x"); stopped = False
        except es.SnapshotBlocked:
            stopped = True
        results.append(_check(f"parse: HTTP {status} -> SnapshotBlocked", stopped))
    for name, text in (("оболочка без данных", "<html><body>shell</body></html>"), ("страница блокировки", "<a>has_been_cr_blocked_AWS</a>"),
                       ("isPassed=false", page_html([item(1)], passed=False)), ("битая структура", '<script id="__NEXT_DATA__">{"props":{}}</script>')):
        try:
            es.parse_list_html(200, text); stopped = False
        except es.SnapshotBlocked:
            stopped = True
        results.append(_check(f"parse: {name} -> SnapshotBlocked (сбор останавливается)", stopped))

    # ── клиент: последовательно, с паузами, потолок ───────────────────────
    f, sleeps = fetcher(lambda a: Resp(200, page_html([item(1)])))
    f.get_list("a"); f.get_list("b"); f.get_list("c")
    results.append(_check("fetcher: пауза перед 2-м и 3-м запросом, перед первым нет", len(sleeps) == 2 and all(5 <= s <= 8 for s in sleeps)))
    f2, _ = fetcher(lambda a: Resp(200, page_html([item(1)])), max_requests=2)
    f2.get_list("a"); f2.get_list("b")
    try:
        f2.get_list("c"); capped = False
    except es.SnapshotBlocked:
        capped = True
    results.append(_check("fetcher: потолок запросов -> остановка", capped and f2.requests_made == 2))
    f3, _ = fetcher(lambda a: (_ for _ in ()).throw(ConnectionError("x")))
    try:
        f3.get_list("a"); net = False
    except es.SnapshotBlocked:
        net = True
    results.append(_check("fetcher: сетевая ошибка -> остановка, не падение", net))

    # ── деление диапазона пробега при >200 объявлений ─────────────────────
    fleet = [item(i, mileage=i * 200) for i in range(300)]  # 300 машин, пробег 0..59800

    def server(action):
        low, high = mileage_range(action)
        inside = [c for c in fleet if low <= c["Mileage"] <= high]
        return Resp(200, page_html(inside[:es.PAGE_LIMIT], count=len(inside)))
    f4, _ = fetcher(server)
    raw, truncated = es.collect_raw(f4, CODES, TARGET)
    results.append(_check("collect_raw: 300 объявлений (>200) собраны полностью делением пробега, без дублей и обрезки",
                          len(raw) == 300 and len({c["Id"] for c in raw}) == 300 and not truncated and f4.requests_made >= 3))
    one, _ = fetcher(lambda a: Resp(200, page_html([item(1), item(2)])))
    raw2, _ = es.collect_raw(one, CODES, TARGET)
    results.append(_check("collect_raw: малая выдача — один запрос", len(raw2) == 2 and one.requests_made == 1))

    # ── снимок и запуск ───────────────────────────────────────────────────
    tmp = Path(tempfile.mkdtemp())
    snap_path = tmp / "encar_snapshot.json"
    targets = [TARGET, {"brand": "Kia", "model": "K5", "year_from": 2022, "year_to": 2023, "mileage_max": 60000}]
    resolve = lambda t: {"car_type": "Y", "manufacturer_ko": "기아", "model_group": t["model"]}
    import encar_scraper as scraper_mod
    scraper_mod_filter = scraper_mod.filter_and_normalize
    snapshot = es.build_snapshot(targets, fetcher(lambda a: Resp(200, page_html([item(1), item(2, mileage=99999), item(3, SellType="리스")])))[0], resolve)
    k3 = snapshot["targets"][es.target_key(TARGET)]
    results.append(_check("build_snapshot: фильтры как в API-режиме (пробег > максимума и аренда отсеяны), 1 лот из 3",
                          k3["found_raw"] == 3 and len(k3["lots"]) == 1 and k3["lots"][0]["external_id"] == "1"))
    results.append(_check("build_snapshot: фото урезаны до 12, ссылки на ci.encar.com, источник encar",
                          len(k3["lots"][0]["photos"]) == es.MAX_PHOTOS_STORED and k3["lots"][0]["photos"][0].startswith("https://ci.encar.com/")
                          and k3["lots"][0]["source"] == "encar" and k3["lots"][0]["price"] == 15_000_000))
    results.append(_check("build_snapshot: обе модели, счётчик запросов", len(snapshot["targets"]) == 2 and snapshot["request_count"] == 2))

    import target_model_resolver
    saved_load = target_model_resolver.load_target_models
    target_model_resolver.load_target_models = lambda: list(targets)
    args = lambda **kw: SimpleNamespace(**{"output": str(snap_path), "force": False, "push": False, "notify": False, "dry_run": False, "models": None, **kw})
    try:
        good = fetcher(lambda a: Resp(200, page_html([item(1)])))[0]
        code = es.run(args(), fetcher=good, resolve_fn=resolve, now=NOW)
        results.append(_check("run: успешный сбор -> код 0, снимок и состояние записаны", code == 0 and snap_path.exists()
                              and es.load_state(snap_path).get("last_status") == "ok"))
        first_bytes = snap_path.read_bytes()
        results.append(_check("run: повторный запуск через 1 ч -> рано (код 3), сеть не трогаем",
                              es.run(args(), fetcher=fetcher(lambda a: 1 / 0)[0], resolve_fn=resolve, now=NOW + timedelta(hours=1)) == 3))
        blocked = fetcher(lambda a: Resp(429, "x"))[0]
        code = es.run(args(force=True), fetcher=blocked, resolve_fn=resolve, now=NOW + timedelta(hours=13))
        results.append(_check("run: ответ 429 -> остановка (код 2), прежний снимок НЕ перезаписан, статус blocked в состоянии",
                              code == 2 and snap_path.read_bytes() == first_bytes and "blocked" in es.load_state(snap_path)["last_status"]
                              and blocked.requests_made == 1))
        results.append(_check("run: после неудачи интервал 12 ч тоже действует (без --force рано)",
                              es.run(args(), fetcher=fetcher(lambda a: 1 / 0)[0], resolve_fn=resolve, now=NOW + timedelta(hours=14)) == 3))
        empty = fetcher(lambda a: Resp(200, "<html>shell</html>"))[0]
        results.append(_check("run: пустая страница -> остановка (код 2)", es.run(args(force=True), fetcher=empty, resolve_fn=resolve, now=NOW) == 2
                              and empty.requests_made == 1))
        notes = []
        es.reset_notice()
        es.run(args(force=True, notify=True), fetcher=fetcher(lambda a: Resp(403, ""))[0], resolve_fn=resolve, notify_fn=notes.append)
        results.append(_check("run --notify: при остановке ровно одно предупреждение админу", len(notes) == 1 and "403" in notes[0]))
        dry = tmp / "dry.json"
        code = es.run(args(output=str(dry), dry_run=True, force=True), fetcher=good, resolve_fn=resolve)
        results.append(_check("run --dry-run: файл не пишется", code == 0 and not dry.exists()))
    finally:
        target_model_resolver.load_target_models = saved_load

    # ── чтение пайплайном ────────────────────────────────────────────────
    fresh_notes, import_log = [], __import__("logging").getLogger("t")
    es.reset_notice()
    lots = es.lots_for_target(TARGET, import_log, snap_path, now=datetime.fromisoformat(json.loads(snap_path.read_text(encoding="utf-8"))["generated_at"]) + timedelta(hours=35), notify_fn=fresh_notes.append)
    results.append(_check("lots_for_target: снимок младше 36 ч -> лоты, без предупреждений", len(lots) == 1 and fresh_notes == []))
    stale_time = datetime.fromisoformat(json.loads(snap_path.read_text(encoding="utf-8"))["generated_at"]) + timedelta(hours=37)
    stale_notes = []
    es.reset_notice()
    a = es.lots_for_target(TARGET, import_log, snap_path, now=stale_time, notify_fn=stale_notes.append)
    b = es.lots_for_target(targets[1], import_log, snap_path, now=stale_time, notify_fn=stale_notes.append)
    results.append(_check("lots_for_target: снимок старше 36 ч -> [] и ОДНО предупреждение админу на весь прогон", a == [] and b == [] and len(stale_notes) == 1
                          and "устарел" in stale_notes[0]))
    miss_notes = []
    es.reset_notice()
    results.append(_check("lots_for_target: файла нет -> [] и предупреждение",
                          es.lots_for_target(TARGET, import_log, tmp / "нет.json", notify_fn=miss_notes.append) == [] and len(miss_notes) == 1))
    results.append(_check("lots_for_target: модели нет в снимке -> [] без предупреждения",
                          es.lots_for_target({"brand": "X", "model": "Y"}, import_log, snap_path, now=NOW, notify_fn=miss_notes.append) == [] and len(miss_notes) == 1))

    # ── адаптер Encar в режиме снимка не ходит в сеть ─────────────────────
    saved_cfg = (config.ENCAR_SOURCE, config.ENCAR_SNAPSHOT_PATH)
    saved_get = encar_scraper.requests.get
    config.ENCAR_SOURCE, config.ENCAR_SNAPSHOT_PATH = "snapshot", str(snap_path)
    encar_scraper.requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("сеть вызвана"))
    try:
        # свежесть: подменяем generated_at на «сейчас»
        data = json.loads(snap_path.read_text(encoding="utf-8")); data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snap_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        got = encar_scraper.search_target_model(TARGET, import_log)
        results.append(_check("encar_scraper.search_target_model при ENCAR_SOURCE=snapshot: лоты из файла, запросов к сети нет",
                              len(got) == 1 and got[0]["source"] == "encar" and got[0]["brand"] == "Kia"))
        results.append(_check("encar_scraper.browse_broad при ENCAR_SOURCE=snapshot: пропуск без сети", encar_scraper.browse_broad({}, import_log) == []))
    finally:
        config.ENCAR_SOURCE, config.ENCAR_SNAPSHOT_PATH = saved_cfg
        encar_scraper.requests.get = saved_get
    results.append(_check("по умолчанию ENCAR_SOURCE = api (прежнее поведение не изменено)", config.ENCAR_SOURCE == "api"))

    bad = results.count(False)
    print(f"\ntest_encar_snapshot: {len(results) - bad} OK / {bad} FAIL")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    run()
