# -*- coding: utf-8 -*-
"""
test_duplicate_guard.py — самотест защиты от дублей (python test_duplicate_guard.py,
без pytest, без сети: отправка в Telegram/VK подменена заглушками).

Что покрыто (требования этапа 3):
  - тот же лот повторно (по URL / lot_id / «площадка:external_id»);
  - тот же лот с другим шаблоном (торги -> бронирование);
  - ручной пост без lot_id (по сочетанию модель + год + пробег);
  - реальный published.jsonl: Kia K3 1.6 (6 повторов в канале), Kia Ray Signature, QM6;
  - publish_post(): дубль не отправляется (в т.ч. при dry_run), тестовый пост [ТЕСТ] и
    skip_duplicate_check обходят сверку, гейт идёт раньше сверки.
"""

import tempfile
from pathlib import Path

import agent_publisher
import config
import post_validator as pv
import registry as reg
from post_validator import PublishBlockedError


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def _record(message_id: int, **overrides) -> dict:
    base = {"message_id": message_id, "post_url": f"https://t.me/auto_generals/{message_id}",
            "posted_at": "2026-10-05T09:17:07+00:00", "lot_id": None, "lot_url": None, "platform": None,
            "model": None, "year": None, "mileage_km": None, "method": "auto", "template": "lead"}
    base.update(overrides)
    return base


def _car(**overrides) -> dict:
    year = (config.MIN_YEAR + config.MAX_YEAR) // 2
    car = {"brand": "Kia", "model": "K3", "trim": "1.6", "year": year, "mileage_km": 20000,
           "source": "kbchachacha", "external_id": "111",
           "url": "https://www.kbchachacha.com/public/car/detail.kbc?carSeq=111",
           "photos": [f"https://example.com/{i}.jpg" for i in range(config.MIN_PHOTOS_PER_POST + 2)]}
    car.update(overrides)
    return car


def run() -> None:
    results = []
    K3_URL = "https://www.kbchachacha.com/public/car/detail.kbc?carSeq=111"

    # ── 1. тот же лот повторно ───────────────────────────────────────────
    records = {1: _record(1, lot_url=K3_URL, lot_id=K3_URL, model="Kia K3 1.6", year=2023, mileage_km=20000)}
    results.append(_check("пустой реестр — дубля нет", reg.find_duplicate(_car(), {}) is None))
    dup = reg.find_duplicate(_car(), records)
    results.append(_check("тот же URL -> дубль по lot_id/url", dup is not None and dup["rule"] == "lot_id/url"))
    results.append(_check("URL без www и с хвостовым / -> тоже дубль",
                          reg.find_duplicate(_car(url="http://kbchachacha.com/public/car/detail.kbc?carSeq=111/"), records) is not None))
    results.append(_check("другой лот (другой carSeq), другие характеристики -> не дубль",
                          reg.find_duplicate(_car(url=K3_URL.replace("111", "222"), external_id="222", mileage_km=33000), records) is None))
    by_ext = {2: _record(2, lot_id="kbchachacha:111")}
    results.append(_check("«площадка:external_id» совпал с lot_id записи -> дубль",
                          reg.find_duplicate(_car(url="https://other.example/x"), by_ext) is not None))

    # ── 2. тот же лот с другим шаблоном ──────────────────────────────────
    auction = {3: _record(3, lot_id="heydealer:nEB6WaPQ", template="auction", method="manual", platform="heydealer")}
    lead_lot = _car(brand="Kia", model="Ray", trim="Signature", lot_id="heydealer:nEB6WaPQ", url=None, external_id=None, source=None)
    results.append(_check("лот вышел как «торги» -> как «бронирование» (lead) пропускается",
                          reg.find_duplicate(lead_lot, auction) is not None))
    results.append(_check("описание дубля содержит ссылку на пост, шаблон и способ",
                          all(s in reg.describe_duplicate(reg.find_duplicate(lead_lot, auction))
                              for s in ("https://t.me/auto_generals/3", "auction", "manual"))))

    # ── 3. ручной пост без lot_id: модель + год + пробег ────────────────
    manual = {4: _record(4, model="Kia Ray (The New Ray Best Selection)", year=2022, mileage_km=28360,
                         method="manual", template="auction", platform="heydealer")}
    def ray(**kw):
        lot = dict(brand="Kia", model="Ray", trim="Best Selection", year=2022, mileage_km=28390,
                   url="https://fem.encar.com/cars/detail/999", source="encar", external_id="999")
        lot.update(kw)
        return _car(**lot)
    d = reg.find_duplicate(ray(), manual)
    results.append(_check("ручной пост без lot_id: тот же автомобиль с другой ссылкой (пробег +30 км) -> дубль",
                          d is not None and d["rule"] == "модель+год+пробег"))
    results.append(_check("расхождение пробега больше допуска (+140 км) -> не дубль",
                          reg.find_duplicate(ray(mileage_km=28500), manual) is None))
    results.append(_check("другой год -> не дубль", reg.find_duplicate(ray(year=2023), manual) is None))
    results.append(_check("другая модель (Kia K3) с тем же годом и пробегом -> не дубль",
                          reg.find_duplicate(ray(model="K3", trim="1.6"), manual) is None))
    results.append(_check("у лота нет пробега -> по этому правилу не сравниваем",
                          reg.find_duplicate(ray(mileage_km=None), manual) is None))
    results.append(_check("у лота нет года -> не сравниваем", reg.find_duplicate(ray(year=None), manual) is None))
    results.append(_check("«Kia K3» не путается с «Kia K5» (первые два слова названия)",
                          not reg._same_model("Kia K3", "Kia K5") and reg._same_model("Kia K3", "Kia K3 1.6")
                          and reg._same_model("Mercedes-Benz GLB 200d", "Mercedes-Benz GLB 200 d")))
    test_rec = {5: _record(5, model="Kia K3", year=2023, mileage_km=14635, method="unknown", is_test=True)}
    results.append(_check("тестовый пост и способ unknown тоже считаются публикацией",
                          reg.find_duplicate(_car(year=2023, mileage_km=14635, url="https://example.com/t", external_id=None), test_rec) is not None))

    # ── 4. реальный published.jsonl (128 постов канала) ──────────────────
    real = reg.load_records(reg.REGISTRY_PATH)
    results.append(_check("реальный реестр прочитан", len(real) >= 100))
    k3 = _car(brand="Kia", model="K3", trim="1.6", year=2023, mileage_km=2682, url="https://example.com/other-platform", external_id=None, source=None)
    results.append(_check("реальные данные: Kia K3 1.6, 2023, 2682 км (вышел в канал 6 раз) -> дубль",
                          reg.find_duplicate(k3, real) is not None))
    ray_real = _car(brand="Kia", model="Ray", trim="Signature", year=2022, mileage_km=20266, url="https://example.com/ray", external_id=None, source=None)
    results.append(_check("реальные данные: Kia Ray Signature 2022, 20 266 км (5286, ручной) -> дубль",
                          reg.find_duplicate(ray_real, real) is not None))
    qm6 = _car(url="https://www.charancha.com/cars/01a11497-d7a6-79ab-8c3b-e1c990ac62e1", external_id=None, source=None)
    results.append(_check("реальные данные: QM6 по URL charancha (5276) -> дубль",
                          reg.find_duplicate(qm6, real) is not None))
    fresh = _car(brand="Hyundai", model="Palisade", trim="2.2", year=2022, mileage_km=12345, url="https://example.com/new", external_id=None, source=None)
    results.append(_check("реальные данные: новый лот, которого не было в канале -> не дубль",
                          reg.find_duplicate(fresh, real) is None))

    # ── 5. publish_post(): сверка перед отправкой ─────────────────────────
    sent = []
    originals = {name: getattr(agent_publisher, name) for name in
                 ("send_to_telegram", "send_photo_to_telegram", "send_media_group_to_telegram", "send_to_vk")}
    real_path, real_log = reg.REGISTRY_PATH, pv.REJECTED_LOG_PATH
    tmp = tempfile.TemporaryDirectory()
    try:
        agent_publisher.send_to_telegram = lambda *a, **k: sent.append("tg") or True
        agent_publisher.send_photo_to_telegram = lambda *a, **k: sent.append("tg") or True
        agent_publisher.send_media_group_to_telegram = lambda *a, **k: sent.append("tg") or True
        agent_publisher.send_to_vk = lambda *a, **k: sent.append("vk") or True
        reg.REGISTRY_PATH = Path(tmp.name) / "published.jsonl"
        pv.REJECTED_LOG_PATH = Path(tmp.name) / "rejected_posts.log"
        reg.append_record(_record(10, lot_url=K3_URL, lot_id=K3_URL, model="Kia K3 1.6",
                                  year=_car()["year"], mileage_km=20000), reg.REGISTRY_PATH)

        def publish(car, text="пост", **kw):
            sent.clear()
            return agent_publisher.publish_post(car, text, **kw)

        try:
            publish(_car())
            raised = None
        except reg.DuplicatePostError as error:
            raised = error
        results.append(_check("дубль: DuplicatePostError, в Telegram и VK ничего не ушло",
                              raised is not None and sent == [] and K3_URL in str(raised)))
        results.append(_check("DuplicatePostError — подкласс PublishBlockedError (старые except его ловят)",
                              isinstance(raised, PublishBlockedError)))
        try:
            publish(_car(), dry_run=True)
            dry_raised = False
        except reg.DuplicatePostError:
            dry_raised = True
        results.append(_check("сверка работает и в dry-run (dry-run показывает, что пост был бы пропущен)", dry_raised))

        results.append(_check("тестовый пост «[ТЕСТ] …» сверку обходит и уходит", publish(_car(), text="[ТЕСТ] пост") is True and "tg" in sent))
        results.append(_check("skip_duplicate_check=True обходит сверку", publish(_car(), skip_duplicate_check=True) is True and "tg" in sent))
        other = _car(url=K3_URL.replace("111", "222"), external_id="222", mileage_km=33000)
        results.append(_check("не дубль -> публикуется (Telegram и VK)", publish(other) is True and sent == ["tg", "vk"]))

        try:
            publish(_car(photos=[]))
            blocked = None
        except PublishBlockedError as error:
            blocked = error
        results.append(_check("гейт идёт РАНЬШЕ сверки: лот без фото -> PublishBlockedError, не DuplicatePostError",
                              blocked is not None and type(blocked) is PublishBlockedError))
    finally:
        for name, fn in originals.items():
            setattr(agent_publisher, name, fn)
        reg.REGISTRY_PATH, pv.REJECTED_LOG_PATH = real_path, real_log
        tmp.cleanup()

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
