# -*- coding: utf-8 -*-
"""
database.py — здесь вся работа с базой данных SQLite.

Что такое SQLite и зачем она нужна?
Это простая база данных, которая хранится в ОДНОМ файле на диске
(без установки отдельного сервера базы данных). Идеально для таких
небольших проектов. Файл базы будет лежать в data/cars.db.

Зачем вообще нужна база, если можно просто публиковать всё подряд?
Чтобы не публиковать одну и ту же машину дважды (защита от дублей) и
чтобы уметь сказать "о, у этой модели поменялась цена!" — а не просто
слепо публиковать всё заново каждый раз.
"""

import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager
from typing import Optional

import config

logger = logging.getLogger("database")

# SQL-команда, которая создаёт таблицу "cars", если её ещё нет.
# CREATE TABLE IF NOT EXISTS — "создай таблицу, но только если её ещё нет"
# (иначе при повторном запуске программа упала бы с ошибкой "таблица уже есть").
#
# UNIQUE(brand, model, trim) — говорит базе: "не может быть двух строк
# с одинаковым сочетанием бренд+модель+комплектация". Это и есть наша
# защита от дублей на уровне самой базы данных.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    trim TEXT NOT NULL,
    external_id TEXT NOT NULL,
    year TEXT,
    mileage_km INTEGER,
    price INTEGER,
    cc INTEGER,
    hp INTEGER,
    fuel_code TEXT,
    specs TEXT,
    url TEXT,
    last_updated TEXT NOT NULL,
    UNIQUE(brand, model, external_id)
);
"""

# Таблица истории публикаций — используется мультиагентным пайплайном
# (orchestrator.py + agent_analyst.py), чтобы знать, что и когда уже было
# опубликовано (например, чтобы не публиковать "горячее предложение" чаще
# одного раза в день). Без UNIQUE — это журнал, одна и та же машина может
# публиковаться несколько раз со временем (при новой цене и т.п.).
CREATE_POST_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS post_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id INTEGER NOT NULL,
    post_type TEXT NOT NULL,
    approved_by_editor INTEGER NOT NULL,
    rewrite_count INTEGER NOT NULL DEFAULT 0,
    posted_at TEXT NOT NULL,
    FOREIGN KEY (car_id) REFERENCES cars(id)
);
"""


@contextmanager
def get_connection():
    """
    Открывает соединение с базой данных и автоматически закрывает его
    после использования — даже если внутри произойдёт ошибка.

    Используется так:
        with get_connection() as conn:
            conn.execute(...)

    Это называется "контекстный менеджер" — конструкция Python, которая
    гарантирует, что "уборка" (закрытие файла базы) произойдёт всегда,
    а не только в happy-path сценарии.
    """
    conn = sqlite3.connect(config.DATABASE_PATH)
    # Это позволяет обращаться к результатам по имени колонки
    # (row["brand"]), а не только по номеру (row[0]) — так код читаемее.
    conn.row_factory = sqlite3.Row
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_POST_HISTORY_TABLE_SQL)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def save_car(conn: sqlite3.Connection, car: dict) -> str:
    """
    Сохраняет машину в базу данных. Если такая же (по бренду+модели+
    external_id) уже есть — либо ничего не делает (если данные не
    изменились), либо обновляет запись (если цена/характеристики
    поменялись).

    Что такое external_id?
    Для каталога новых авто у дилера это обычно совпадает с "trim"
    (названием комплектации) — там нет отдельного номера объявления.
    Для б/у объявлений (kbchachacha) это carSeq — уникальный номер
    конкретного объявления на сайте. Так мы не путаем "у этой модели
    поменялась цена" (новые авто) и "это вообще другое объявление"
    (б/у авто, где даже одинаковые модели — разные конкретные машины).

    Args:
        conn: открытое соединение с базой (из get_connection()).
        car: словарь с полями brand, model, trim, external_id (необязательно —
             если не указан, используется значение trim), year, mileage_km,
             price, cc, hp, fuel_code, specs, url.

    Returns:
        Строка-статус: "new" (новая запись), "updated" (обновлена),
        "unchanged" (без изменений — такая же запись уже была).
    """
    cursor = conn.cursor()

    external_id = car.get("external_id") or car["trim"]

    # Ищем — есть ли уже такая машина в базе?
    cursor.execute(
        "SELECT * FROM cars WHERE brand = ? AND model = ? AND external_id = ?",
        (car["brand"], car["model"], external_id),
    )
    existing_row = cursor.fetchone()

    # datetime.now() — текущее время. isoformat() превращает его в текст
    # вида "2026-07-02T18:30:00" — удобный для хранения в базе формат.
    now = datetime.now().isoformat(timespec="seconds")

    if existing_row is None:
        # Такой машины ещё не было — добавляем новую строку.
        cursor.execute(
            """INSERT INTO cars (brand, model, trim, external_id, year, mileage_km,
               price, cc, hp, fuel_code, specs, url, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (car["brand"], car["model"], car["trim"], external_id, car.get("year"),
             car.get("mileage_km"), car.get("price"), car.get("cc"), car.get("hp"),
             car.get("fuel_code"), car.get("specs"), car.get("url"), now),
        )
        conn.commit()
        logger.info(f"Новая машина в базе: {car['brand']} {car['model']} {car['trim']}")
        return "new"

    # Машина уже есть — проверяем, поменялась ли цена
    if existing_row["price"] != car.get("price"):
        cursor.execute(
            """UPDATE cars SET year = ?, mileage_km = ?, price = ?, cc = ?, hp = ?,
               fuel_code = ?, specs = ?, url = ?, last_updated = ? WHERE id = ?""",
            (car.get("year"), car.get("mileage_km"), car.get("price"), car.get("cc"),
             car.get("hp"), car.get("fuel_code"), car.get("specs"), car.get("url"),
             now, existing_row["id"]),
        )
        conn.commit()
        logger.info(
            f"Цена изменилась: {car['brand']} {car['model']} {car['trim']} "
            f"({existing_row['price']} -> {car.get('price')})"
        )
        return "updated"

    # Ничего не поменялось — не трогаем базу
    return "unchanged"


def get_last_price(conn: sqlite3.Connection, brand: str, model: str, external_id: str) -> Optional[int]:
    """Возвращает последнюю сохранённую цену машины (или None, если её ещё нет в базе)."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT price FROM cars WHERE brand = ? AND model = ? AND external_id = ?",
        (brand, model, external_id),
    )
    row = cursor.fetchone()
    return row["price"] if row else None


def get_car_id(conn: sqlite3.Connection, brand: str, model: str, external_id: str) -> Optional[int]:
    """
    Возвращает id строки в cars по натуральному ключу (brand, model, external_id),
    или None, если такой машины ещё нет.

    Нужно мультиагентному пайплайну (orchestrator.py), чтобы связать
    post_history.car_id с конкретной машиной после save_car(). save_car()
    намеренно не меняет свою сигнатуру (она возвращает только строку-статус),
    чтобы не трогать её существующие вызовы в main.py — вместо этого id
    достаётся отдельным лёгким запросом.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM cars WHERE brand = ? AND model = ? AND external_id = ?",
        (brand, model, external_id),
    )
    row = cursor.fetchone()
    return row["id"] if row else None


def get_average_price(
    conn: sqlite3.Connection, brand: str, model: str, exclude_external_id: Optional[str] = None
) -> Optional[float]:
    """
    Средняя цена (price, KRW) среди всех сохранённых машин этого brand+model
    в таблице cars — используется agent_analyst.py, чтобы понять, заметно ли
    текущая цена ниже "обычной" для этой модели.

    exclude_external_id: не учитывать в среднем саму текущую машину (чтобы её
    собственная цена не "подтягивала" среднее к себе).

    Возвращает None, если подходящих записей меньше 2 — по 0-1 записям
    считать "средним по модели" нечестно.
    """
    cursor = conn.cursor()
    if exclude_external_id:
        cursor.execute(
            """SELECT AVG(price) AS avg_price, COUNT(*) AS cnt FROM cars
               WHERE brand = ? AND model = ? AND external_id != ? AND price IS NOT NULL""",
            (brand, model, exclude_external_id),
        )
    else:
        cursor.execute(
            """SELECT AVG(price) AS avg_price, COUNT(*) AS cnt FROM cars
               WHERE brand = ? AND model = ? AND price IS NOT NULL""",
            (brand, model),
        )
    row = cursor.fetchone()
    if row is None or row["cnt"] < 2:
        return None
    return row["avg_price"]


def add_post_history(
    conn: sqlite3.Connection,
    car_id: int,
    post_type: str,
    approved_by_editor: bool,
    rewrite_count: int = 0,
) -> None:
    """Записывает факт публикации поста — для проверки 'горячее предложение
    уже было сегодня' (agent_analyst.py) и для будущей аналитики."""
    cursor = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    cursor.execute(
        """INSERT INTO post_history (car_id, post_type, approved_by_editor, rewrite_count, posted_at)
           VALUES (?, ?, ?, ?, ?)""",
        (car_id, post_type, int(approved_by_editor), rewrite_count, now),
    )
    conn.commit()


def was_post_type_used_today(conn: sqlite3.Connection, post_type: str) -> bool:
    """
    Проверяет, был ли пост данного типа (например, "горячее предложение")
    уже опубликован сегодня — глобально, по всему каналу, независимо от
    конкретной машины.
    """
    cursor = conn.cursor()
    today = datetime.now().date().isoformat()
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM post_history WHERE post_type = ? AND date(posted_at) = ?",
        (post_type, today),
    )
    row = cursor.fetchone()
    return row["cnt"] > 0
