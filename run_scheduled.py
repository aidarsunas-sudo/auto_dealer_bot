# -*- coding: utf-8 -*-
"""
run_scheduled.py — запускает main.py автоматически каждые N часов, пока
это окно командной строки открыто.

Это ПРОСТОЙ вариант расписания — работает, только пока запущен и открыт
(если закроешь окно cmd или выключишь компьютер — расписание остановится).
Для настоящей автоматизации "чтобы работало само, даже когда компьютер
выключен" — смотри GitHub Actions (см. .github/workflows/run_bot.yml
и файл SETUP_GITHUB_ACTIONS.md).

Но если тебе пока не хочется возиться с GitHub — это рабочий и простой
вариант: запусти один раз, оставь окно открытым (можно свернуть).

Использование:
    python run_scheduled.py                  # каждые 6 часов (по умолчанию)
    python run_scheduled.py --hours 4          # каждые 4 часа
    python run_scheduled.py --hours 4 --dry-run  # тестовый режим
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta


def run_once(dry_run: bool) -> None:
    """Запускает main.py как отдельный процесс (как будто ты сам ввёл
    команду в cmd) и ждёт, пока он закончит."""
    command = [sys.executable, "main.py"]  # sys.executable — путь к твоему python.exe
    if dry_run:
        command.append("--dry-run")

    print(f"\n{'=' * 60}")
    print(f"Запуск main.py в {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}\n")

    # subprocess.run() запускает main.py как будто отдельную программу —
    # это проще и надёжнее, чем импортировать и вызывать функцию напрямую
    # (если main.py упадёт с ошибкой, это не сломает сам планировщик).
    result = subprocess.run(command)

    if result.returncode != 0:
        print(f"⚠️ main.py завершился с ошибкой (код {result.returncode})")
    else:
        print("✅ Прогон завершён успешно")


def main() -> None:
    parser = argparse.ArgumentParser(description="Планировщик — запускает main.py по расписанию")
    parser.add_argument("--hours", type=float, default=6, help="Интервал между запусками, часы (по умолчанию 6)")
    parser.add_argument("--dry-run", action="store_true", help="Передаётся в main.py как есть")
    args = parser.parse_args()

    interval_seconds = args.hours * 3600

    print(f"Планировщик запущен. Интервал: каждые {args.hours} ч.")
    print("Чтобы остановить — закрой это окно или нажми Ctrl+C.\n")

    while True:
        try:
            run_once(args.dry_run)
        except KeyboardInterrupt:
            print("\nОстановлено пользователем (Ctrl+C).")
            break
        except Exception as error:
            # Даже если что-то совсем неожиданное случилось при запуске
            # main.py — планировщик не должен сам "умирать", а должен
            # попробовать снова в следующий раз по расписанию.
            print(f"⚠️ Ошибка при запуске: {error}")

        next_run = datetime.now() + timedelta(seconds=interval_seconds)
        print(f"\nСледующий запуск: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        print("(можно свернуть это окно — работа продолжится в фоне)\n")

        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\nОстановлено пользователем (Ctrl+C).")
            break


if __name__ == "__main__":
    main()
