# -*- coding: utf-8 -*-
"""
test_age_calculator.py — самотест age_calculator.py (без pytest, в стиле
остальных self-test'ов проекта — python test_age_calculator.py).

Регрессия на конкретную находку 2026-07 (см. FINAL_STATE.md): наивная
разница календарных лет (current_year - manufacture_year) даёт неверную
возрастную категорию у границы 3/5/7 лет, если не учитывать месяц —
    - машина, выпущенная в декабре N-3 года, наивно попадает в "35"
      (3-5 лет), хотя реально прошло меньше 3 лет — должна быть в 'u3'.
    - машина, выпущенная в январе N-5 года, наивно попадает в "35",
      хотя реально прошло больше 5 лет — должна быть в '57'.

Тесты считают ожидаемое ОТНОСИТЕЛЬНО сегодняшней даты (не зашитые
абсолютные годы) — чтобы не ломаться в будущем визуально, как и
самотест ЕТС в customs_calculator.py.
"""

import datetime

from age_calculator import age_bucket_from_year


def _check(label: str, year: int, month, expected: str) -> bool:
    actual = age_bucket_from_year(year, month)
    passed = actual == expected
    print(f"[{'OK' if passed else 'FAIL'}] {label}: year={year} month={month} -> "
          f"{actual!r} (ожидалось {expected!r})")
    return passed


def run() -> None:
    today = datetime.date.today()
    results = []

    # 1. Декабрь N-3 года (наивно "3 года", реально меньше) — должно быть u3.
    dec_minus_3 = today.year - 3
    results.append(_check("Декабрь, наивно ровно 3 года (на деле меньше)",
                           dec_minus_3, 12, "u3" if today.month < 12 else "35"))

    # 2. Январь N-3 года — уже точно >=3 года по месяцу (если сегодня не январь).
    jan_minus_3 = today.year - 3
    if today.month > 1:
        results.append(_check("Январь N-3 года — уже прошло >=3 лет", jan_minus_3, 1, "35"))

    # 3. Месяц неизвестен (None) — ведёт себя как январь (максимально старая,
    # чтобы не занижать утильсбор — исправлено при аудите 2026-09-18).
    jan_minus_3 = today.year - 3
    none_month = age_bucket_from_year(jan_minus_3, None)
    january_equiv = age_bucket_from_year(jan_minus_3, 1)
    ok3 = none_month == january_equiv
    results.append(ok3)
    print(f"[{'OK' if ok3 else 'FAIL'}] month=None ведёт себя как month=1: "
          f"{none_month!r} == {january_equiv!r}")

    # 4. Январь N-5 года — наивно "5 лет" (уложилось бы в 35), реально уже
    # больше 5 лет (если сегодня не январь) — должно быть 57.
    jan_minus_5 = today.year - 5
    if today.month > 1:
        results.append(_check("Январь N-5 года — уже прошло >5 лет", jan_minus_5, 1, "57"))

    # 5. Ровно на границах (используем сегодняшний месяц, чтобы получить
    # ровно 36/60/84 месяца прошедших).
    results.append(_check("Ровно 36 месяцев (граница u3/35, включительно 35)",
                           today.year - 3, today.month, "35"))
    results.append(_check("Ровно 60 месяцев (граница 35/57, включительно 35)",
                           today.year - 5, today.month, "35"))
    results.append(_check("Ровно 84 месяца (граница 57/o7, включительно 57)",
                           today.year - 7, today.month, "57"))
    results.append(_check("37 месяцев назад — точно 35 (не на границе)",
                           today.year - 3, today.month - 1 if today.month > 1 else 12, "35"))

    # 6. Новая машина (текущий месяц) — точно u3.
    results.append(_check("Только что выпущена — u3", today.year, today.month, "u3"))

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
