# -*- coding: utf-8 -*-
"""Тесты разбора чисел в validate_draft.py (скриптовый стиль, как остальные test_*.py проекта).

  python test_validate_draft.py
"""
import validate_draft as v


def nums(text):
    return v.numbers(text)


def test_thousands_group_is_one_number():
    assert nums("Итог 5 052 377 ₽") == ["5052377"], nums("Итог 5 052 377 ₽")
    assert nums("Итог 5 052 377 ₽") == ["5052377"]
    assert nums("Цена 59 000 000 ₩") == ["59000000"]


def test_model_name_digit_does_not_glue_with_year():
    # «Q3 2023» раньше склеивалось в «3 202» + «3»
    assert nums("Audi Q3, 2023 год, пробег 3 039 км") == ["2023", "3039"], nums("Audi Q3, 2023 год, пробег 3 039 км")
    assert nums("Audi Q3 2023 года") == ["2023"], nums("Audi Q3 2023 года")
    assert nums("Mercedes-Benz GLB 200d, 2023 год") == ["200", "2023"], nums("Mercedes-Benz GLB 200d, 2023 год")


def test_3202_standalone_is_one_number():
    assert nums("3 202") == ["3202"], nums("3 202")
    assert nums("сумма 3 202 ₽") == ["3202"]


def test_no_gluing_with_following_digit():
    assert nums("2 3 4") == ["2", "3", "4"], nums("2 3 4")
    assert nums("3 2023") == ["3", "2023"], nums("3 2023")


def test_decimals_and_dates_unchanged():
    assert nums("1 000 ₩ = 60,80 ₽") == ["1000", "60.80"], nums("1 000 ₩ = 60,80 ₽")
    assert nums("01.11.2025") == ["01.11", "2025"], nums("01.11.2025")
    assert nums("ПП №1291 (ред. №1713)") == ["1291", "1713"], nums("ПП №1291 (ред. №1713)")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("OK  ", name)
            except AssertionError as e:
                failed += 1
                print("FAIL", name, e)
    print("=== Все проверки пройдены ===" if not failed else f"=== Провалено: {failed} ===")
    raise SystemExit(1 if failed else 0)
