# -*- coding: utf-8 -*-
"""
source_health.py — состояние источников объявлений в рамках ОДНОГО прогона.

1) Флаги включённости (is_enabled): Encar можно аварийно отключить переменной
   ENCAR_ENABLED=false. По умолчанию (и в main_pipeline.yml) он ВКЛЮЧЁН — флаг ничего не
   меняет, пока его не выставят вручную.

2) «Предохранитель» (record_failure/record_success/is_open): когда источник подряд
   SOURCE_FAILURE_THRESHOLD раз не смог загрузить данные (сейчас — список KBChachacha:
   таймауты на IP GitHub), он отключается на ОСТАТОК прогона — вместо 30 запросов по
   ~50 секунд (прогоны по 29-30 минут против обычных 3-5). Админу уходит ОДНО
   предупреждение. Любой успешный ответ обнуляет счётчик, поэтому единичные сбои
   предохранитель не срабатывают.

Состояние живёт в памяти процесса (прогон = процесс), reset() — для тестов.
"""

import logging
from typing import Callable, Optional

import config

logger = logging.getLogger("source_health")

_consecutive_failures: dict = {}
_open_reason: dict = {}
_skip_logged: set = set()


def reset() -> None:
    _consecutive_failures.clear()
    _open_reason.clear()
    _skip_logged.clear()


def is_enabled(source: str) -> bool:
    """Источник включён конфигурацией. Сейчас отключается только Encar (config.ENCAR_ENABLED)."""
    if source == "encar":
        return config.ENCAR_ENABLED
    return True


def is_open(source: str) -> bool:
    """Предохранитель сработал: источник отключён до конца прогона."""
    return source in _open_reason


def is_active(source: str) -> bool:
    return is_enabled(source) and not is_open(source)


def should_skip(source: str, log: Optional[logging.Logger] = None) -> bool:
    """True — источник сейчас обходить не нужно (отключён флагом или предохранителем).
    Причину пишет в лог один раз за прогон, чтобы не засорять его по каждой модели."""
    if is_active(source):
        return False
    if source not in _skip_logged:
        _skip_logged.add(source)
        reason = ("отключён флагом ENCAR_ENABLED=false" if not is_enabled(source)
                  else f"отключён предохранителем до конца прогона ({_open_reason[source]})")
        (log or logger).warning(f"{source}: {reason} — запросы к источнику пропускаются")
    return True


def record_success(source: str) -> None:
    _consecutive_failures[source] = 0


def record_failure(source: str, reason: str, notify_fn: Optional[Callable[[str], object]] = None) -> bool:
    """
    Фиксирует сбой источника. На SOURCE_FAILURE_THRESHOLD-м подряд открывает предохранитель и
    отправляет админу ОДНО предупреждение. Возвращает True, если предохранитель сработал именно
    сейчас.
    """
    if source in _open_reason:
        return False
    count = _consecutive_failures.get(source, 0) + 1
    _consecutive_failures[source] = count
    if count < config.SOURCE_FAILURE_THRESHOLD:
        return False
    _open_reason[source] = reason
    message = (f"⚠️ Источник {source} отключён до конца этого прогона: {count} сбоя(ев) подряд "
               f"(последний: {reason}). Остальные источники работают. Похоже на блокировку IP "
               f"GitHub — см. logs/main_pipeline.log.")
    logger.error(message)
    try:
        if notify_fn is None:
            from notifier import notify_error as notify_fn
        notify_fn(message)
    except Exception as error:  # предупреждение — не повод ронять прогон
        logger.error(f"source_health: не удалось отправить предупреждение админу ({error})")
    return True
