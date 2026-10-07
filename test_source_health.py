# -*- coding: utf-8 -*-
"""
test_source_health.py — самотест source_health.py: отключение Encar флагом и «предохранитель»
KBChachacha (python test_source_health.py, без pytest и без сети).

Реальные данные: логика предохранителя повторяет сбой 4-7 октября (прогоны по 29-30 минут из-за
30 подряд таймаутов к kbchachacha.com при обычных 3-5); проверка check_source_health — на тех же
источниках (kbchachacha/encar/charancha/onbid), что в реальном пайплайне.
"""

import logging

import requests

import broad_collector
import candidate_diversity
import config
import source_health as sh
import target_collector
import used_car_scraper


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


class _Log:
    """Минимальный логгер для check_source_health()."""
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)

    info = error = warning


def run() -> None:
    results = []
    saved = {name: getattr(config, name) for name in ("ENCAR_ENABLED", "SOURCE_FAILURE_THRESHOLD")}
    notes: list = []
    notify = lambda message: notes.append(message)
    real_adapters = dict(target_collector.NOLOGIN_TARGET_ADAPTERS)
    real_broad = dict(broad_collector.BROAD_ADAPTERS)
    real_load_targets, real_load_segment = target_collector.load_target_models, broad_collector.load_rotating_segment
    real_fetch = used_car_scraper.fetch_used_car_list
    real_notify = None
    try:
        config.ENCAR_ENABLED, config.SOURCE_FAILURE_THRESHOLD = True, 3

        # ── 2. Encar: флаг ───────────────────────────────────────────────
        sh.reset()
        results.append(_check("ENCAR_ENABLED=true: Encar активен", sh.is_enabled("encar") and sh.is_active("encar")))
        config.ENCAR_ENABLED = False
        results.append(_check("ENCAR_ENABLED=false: Encar отключён, остальные источники включены",
                              not sh.is_enabled("encar") and all(sh.is_enabled(s) for s in ("kbchachacha", "charancha", "onbid"))))

        calls = []
        def fake(name):
            return lambda *a, **k: calls.append(name) or []
        target_collector.NOLOGIN_TARGET_ADAPTERS.clear()
        target_collector.NOLOGIN_TARGET_ADAPTERS.update({s: fake(s) for s in ("kbchachacha", "encar", "charancha", "onbid")})
        target_collector.load_target_models = lambda: [{"brand": "Kia", "model": "K5"}, {"brand": "Kia", "model": "K3"}]
        sh.reset()
        log = _Log()
        target_collector.collect_target_model_cars(log)
        results.append(_check("target_collector: при ENCAR_ENABLED=false Encar не вызывается ни для одной модели (остальные три — для обеих)",
                              "encar" not in calls and calls.count("kbchachacha") == 2 and calls.count("onbid") == 2))
        results.append(_check("причина пропуска Encar записана в лог ОДИН раз за прогон, не по каждой модели",
                              sum("encar" in w and "ENCAR_ENABLED" in w for w in log.warnings) == 1))

        broad_calls = []
        broad_collector.BROAD_ADAPTERS.clear()
        broad_collector.BROAD_ADAPTERS.update({s: (lambda name: lambda *a, **k: broad_calls.append(name) or [])(s)
                                               for s in ("kbchachacha", "encar", "charancha", "onbid")})
        broad_collector.load_rotating_segment = lambda log: {"max_hp": 160}
        sh.reset()
        broad_collector.collect_broad_cars(_Log())
        results.append(_check("broad_collector: при ENCAR_ENABLED=false Encar пропущен, три других обойдены",
                              broad_calls == ["kbchachacha", "charancha", "onbid"]))
        config.ENCAR_ENABLED = True
        calls.clear(); sh.reset()
        target_collector.collect_target_model_cars(_Log())
        results.append(_check("ENCAR_ENABLED=true: Encar снова обходится (локальный режим не сломан)", calls.count("encar") == 2))

        # ── check_source_health не тревожит про отключённые/срезанные ───
        cars = [{"source": "kbchachacha"}, {"source": "charancha"}, {"source": "onbid"}]
        config.ENCAR_ENABLED = False; sh.reset()
        alerts = []
        silent = candidate_diversity.check_source_health(cars, _Log(), notify_fn=alerts.append)
        results.append(_check("Encar отключён флагом и без лотов — «тихой смертью» не считается, тревоги нет", silent == [] and alerts == []))
        config.ENCAR_ENABLED = True
        silent = candidate_diversity.check_source_health(cars, _Log(), notify_fn=alerts.append)
        results.append(_check("Encar включён и без лотов — это тревога (прежнее поведение)", silent == ["encar"] and len(alerts) == 1))

        # ── 3. предохранитель ────────────────────────────────────────────
        config.ENCAR_ENABLED = True; sh.reset(); notes.clear()
        results.append(_check("1-й и 2-й сбой: предохранитель не срабатывает, предупреждений нет",
                              not sh.record_failure("kbchachacha", "таймаут", notify) and not sh.record_failure("kbchachacha", "таймаут", notify)
                              and not sh.is_open("kbchachacha") and notes == []))
        results.append(_check("3-й подряд: предохранитель срабатывает, админу уходит ОДНО предупреждение",
                              sh.record_failure("kbchachacha", "страница 1: ConnectTimeout", notify) is True and sh.is_open("kbchachacha") and len(notes) == 1
                              and "kbchachacha" in notes[0] and "до конца этого прогона" in notes[0]))
        sh.record_failure("kbchachacha", "ещё", notify); sh.record_failure("kbchachacha", "ещё", notify)
        results.append(_check("после срабатывания новых предупреждений нет (одно на прогон)", len(notes) == 1))
        results.append(_check("другие источники не затронуты", sh.is_active("charancha") and sh.is_active("onbid") and not sh.is_active("kbchachacha")))

        sh.reset(); notes.clear()
        for _ in range(2):
            sh.record_failure("kbchachacha", "таймаут", notify)
        sh.record_success("kbchachacha")
        sh.record_failure("kbchachacha", "таймаут", notify); sh.record_failure("kbchachacha", "таймаут", notify)
        results.append(_check("успешный ответ обнуляет серию: 2 сбоя + успех + 2 сбоя = предохранитель НЕ сработал", not sh.is_open("kbchachacha") and notes == []))
        config.SOURCE_FAILURE_THRESHOLD = 5; sh.reset()
        for _ in range(4):
            sh.record_failure("kbchachacha", "x", notify)
        results.append(_check("порог берётся из config.SOURCE_FAILURE_THRESHOLD (5: после 4 сбоев ещё закрыт)", not sh.is_open("kbchachacha")))
        config.SOURCE_FAILURE_THRESHOLD = 3

        # ── предохранитель на реальной загрузке списка KBChachacha ───────
        import notifier
        real_notify, notifier.notify_error = notifier.notify_error, notify
        sh.reset(); notes.clear()
        fetch_calls = []
        def failing(search_params, page, *a, **k):
            fetch_calls.append(page)
            raise requests.ConnectTimeout("connect timeout")
        used_car_scraper.fetch_used_car_list = failing
        for _ in range(3):
            used_car_scraper.parse_all_pages({"x": 1})
        results.append(_check("parse_all_pages: три таймаута подряд (как 4.10) -> предохранитель открыт, админ уведомлён один раз",
                              sh.is_open("kbchachacha") and len(fetch_calls) == 3 and len(notes) == 1))
        before = len(fetch_calls)
        out = used_car_scraper.parse_all_pages({"x": 1})
        results.append(_check("четвёртая модель: запросов к сайту (с повторами по ~50 с) больше нет, возвращается []",
                              out == [] and len(fetch_calls) == before))

        sh.reset(); notes.clear(); fetch_calls.clear()
        used_car_scraper.fetch_used_car_list = lambda sp, page, *a, **k: "<html></html>"
        used_car_scraper.parse_used_car_list, real_parse = (lambda html: []), used_car_scraper.parse_used_car_list
        try:
            sh.record_failure("kbchachacha", "x", notify); sh.record_failure("kbchachacha", "x", notify)
            used_car_scraper.parse_all_pages({"x": 1})  # сайт ответил (пусто) — серия прервана
            sh.record_failure("kbchachacha", "x", notify)
        finally:
            used_car_scraper.parse_used_car_list = real_parse
        results.append(_check("ответ сайта (даже пустая выдача) прерывает серию сбоев", not sh.is_open("kbchachacha")))
        sh.reset()
        results.append(_check("reset() очищает состояние", not sh.is_open("kbchachacha")))
    finally:
        for name, value in saved.items():
            setattr(config, name, value)
        target_collector.NOLOGIN_TARGET_ADAPTERS.clear(); target_collector.NOLOGIN_TARGET_ADAPTERS.update(real_adapters)
        broad_collector.BROAD_ADAPTERS.clear(); broad_collector.BROAD_ADAPTERS.update(real_broad)
        target_collector.load_target_models, broad_collector.load_rotating_segment = real_load_targets, real_load_segment
        used_car_scraper.fetch_used_car_list = real_fetch
        if real_notify:
            import notifier
            notifier.notify_error = real_notify
        sh.reset()

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
