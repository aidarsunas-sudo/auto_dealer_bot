# Снимок Encar с компьютера владельца

**Зачем.** `api.encar.com` закрыт для GitHub и для РФ-адресов. Страницы списка `car.encar.com` с вашего IP открываются и отдают данные в HTML. Сборщик `encar_snapshot.py` читает эти страницы для моделей из `target_models.json` и пишет `data/encar_snapshot.json`; `main_pipeline` на GitHub (`ENCAR_SOURCE=snapshot`) берёт лоты Encar из этого файла.

**Правила сборщика.** Не чаще раза в 12 часов (считается и после неудачи); строго последовательно, пауза 7–10 с между запросами, потолок 150 запросов за запуск; обычный GET с браузерным User-Agent, без прокси и без обхода защиты. На ответ 403/429, любой не-200 или «пустую» страницу (нет данных списка) сбор **останавливается**, пишет предупреждение, а прежний снимок остаётся нетронутым. В канал сборщик ничего не публикует; админу пишет только с `--notify` и только при остановке.

**Свежесть.** Снимок старше 36 часов в пайплайне не используется: Encar в прогоне пропускается, админу уходит ОДНО предупреждение за прогон.

## Порядок включения (важно)

1. **Сначала снимок, потом слияние.** Пока в репозитории нет `data/encar_snapshot.json`, прогоны с `ENCAR_SOURCE=snapshot` будут каждый раз предупреждать админа. Поэтому: пункты 1–4 → только потом слияние PR.
2. Отдельная копия репозитория на `main` (чтобы не мешать рабочей копии):
   ```bash
   git clone https://github.com/aidarsunas-sudo/auto_dealer_bot.git C:\Users\Xiaomi\auto_dealer_bot_snapshot
   cd C:\Users\Xiaomi\auto_dealer_bot_snapshot
   pip install -r requirements.txt
   ```
   Для `--notify` нужен `.env` в этой копии с `BOT_TOKEN` и `ADMIN_CHAT_ID` (скопируйте из рабочей; значения нигде не показываются). Каталог `logs` создайте: `mkdir logs`.
3. Проверочный запуск **без записи** (≈3–6 запросов на модель, около 5–10 минут на все модели):
   ```bash
   python encar_snapshot.py --dry-run --force --models "Kia K3"
   ```
   В логе должно быть `Kia K3: 284 объявлений -> 281 лотов`, файл не создаётся.
4. Первый настоящий запуск: `python encar_snapshot.py --force`, затем проверьте `data/encar_snapshot.json` (в нём `generated_at`, `targets` по моделям, `lots`).
5. Передача в репозиторий. Либо вручную (`git add data/encar_snapshot.json`, коммит, `git push`), либо флагом `--push`: он коммитит **только** этот файл и делает `pull --rebase` + `push`, но лишь из ветки `main`. Это прямой push в `main` от вашего имени — включайте `--push`, только если согласны.

## Планировщик Windows (не включён)

Файл `deploy/windows/encar_snapshot_task.xml` — задача на запуск каждые 12 часов, **в нём `Enabled=false`**. Ничего не регистрировалось.
Импорт и включение (PowerShell, когда будете готовы; пути в XML — под `auto_dealer_bot_snapshot` и ваш Python 3.14):
```powershell
schtasks /Create /TN "PickPlus Encar Snapshot" /XML "C:\Users\Xiaomi\auto_dealer_bot_snapshot\deploy\windows\encar_snapshot_task.xml"
schtasks /Change /TN "PickPlus Encar Snapshot" /ENABLE
```
Проверка: `schtasks /Run /TN "PickPlus Encar Snapshot"`, затем `logs\encar_snapshot.log`. Выключить: `schtasks /Change /TN "PickPlus Encar Snapshot" /DISABLE`; удалить: `schtasks /Delete /TN "PickPlus Encar Snapshot" /F`.
Компьютер должен быть включён и с интернетом; пропущенный запуск выполнится при первой возможности (`StartWhenAvailable`).

## Коды возврата и что делать

| Код | Значение | Действие |
|---|---|---|
| 0 | снимок записан | — |
| 2 | остановка: 403/429, пустая страница, потолок запросов | не повторять сразу; подождать, проверить сайт в браузере; если закрыт — это блокировка |
| 3 | рано (не прошло 12 часов) | `--force`, только если уверены |
| 4 | прочая ошибка / не удался `git` | см. `logs/encar_snapshot.log` |

## Ограничения

Страница отдаёт максимум 200 свежих объявлений на запрос, поэтому при большой выдаче сборщик делит диапазон пробега пополам (не мельче 3000 км). Широкий поиск Encar (`browse_broad`) в режиме снимка пропускается. Если Encar поменяет вёрстку, сборщик остановится на «пустой странице» и предупредит.
