---
name: channel-post-automation
description: Автопостинг объявлений по авто из Кореи в Telegram (@auto_generals) и VK — парсинг, фильтры, сборка поста (фото-альбом + опциональное видео), публикация через GitHub Actions. Триггеры — «автопостинг», «разместить посты в Telegram/VK», «запустить постинг авто», «test_post», «test_video», «проверить видео-пост», «почему канал молчит».
---

# channel-post-automation

Рабочий, боевой механизм лидогенерации: парсит объявления с корейских площадок,
фильтрует их, собирает пост (текст + фото-альбом, иногда + видео) и публикует в
Telegram-канал `@auto_generals` и в VK. Всё ниже сверено grep'ом с реальным кодом
репозитория `auto_dealer_bot` (не по памяти) — если что-то разошлось с кодом,
верить коду, не этому файлу.

## Что делает механизм

Пайплайн (см. докстринг в начале `main_pipeline.py`):

```
сбор (4 источника без логина: Encar/KBChachacha/Charancha/OnBid)
  → дедуп/фильтр актуальности/пробега (published_log.py)
  → фильтр недавно опубликованных моделей (candidate_diversity.filter_recent_models)
  → round-robin разнообразие
  → расчёт цены под ключ (post_text_builder.prepare_post_fields)
  → жёсткий гейт публикации (post_validator.validate_post_before_publish)
  → генерация текста поста (lead_post_builder.build_lead_post)
  → (опционально) генерация видео (video_generator.py)
  → публикация в Telegram + VK (agent_publisher.publish_post)
```

## Архитектура и точки входа

Два НЕЗАВИСИМЫХ workflow, оба на расписании `cron: "0 */6 * * *"` (каждые 6 часов,
UTC — Казань = UTC+3):

- **`.github/workflows/main_pipeline.yml`** — запускает `main_pipeline.py`
  (targeted+broad поиск по `target_models.json`/`broad_search_config.json`,
  CTA-лидоген с `@korea_auto_kazan_bot`). `workflow_dispatch:` есть, но **без
  входных параметров** — ручной запуск = обычный боевой прогон, dry-run через
  UI недоступен.
- **`.github/workflows/run_bot.yml`** — запускает `main.py` (WATCHLIST/
  USED_CAR_SEARCHES, формат "цена изменилась"/"новое поступление"). Имеет
  `workflow_dispatch.inputs`: **`test_post`** и **`test_video`**, оба `type: boolean`,
  `default: false` (секция `workflow_dispatch.inputs` в начале `run_bot.yml`).

Оба пути публикации сходятся в одной точке — `agent_publisher.publish_post()`:
собирает фото/видео, шлёт в Telegram (`telegram_publisher.py`) и в VK
(`vk_publisher.py`), причём вызов `send_to_vk()` внутри `publish_post()` обёрнут
в `try/except` — сбой VK не мешает уже отправленному Telegram.

## ВИДЕО — не путать два генератора

Это самое частое место путаницы, проверено отдельно:

| | `video_generator.py` | `shorts_generator.py` / `content_reels_publisher.py` |
|---|---|---|
| Подключён к CI? | **Да** — единственный видео-путь в `run_bot.yml`/`main_pipeline.yml` | **Нет** — ни одного упоминания ни в одном `.yml` workflow (проверено grep'ом) |
| Вызывается из | `agent_publisher._generate_video_safe()` → `video_generator.generate_videos()` | Только вручную: `python content_reels_publisher.py --publish` |
| Вход | ФОТО объявления (Ken Burns slideshow) | Готовые ВИДЕОклипы (монтаж, а не слайдшоу) |
| Титры | `drawtext` (заголовок/цена/CTA) | То же + анимация |
| Субтитры | **Нет** | Да — ASS из пословных таймингов edge-tts (`shorts_generator._build_subtitles`) |
| Beat sync | Нет | Да, через `pydub`/`_detect_beats()` |
| Аудио-микс | Чистый ffmpeg `filter_complex` (`video_generator._add_audio`, без pydub) | Свой `_mix_audio()` с sidechaincompress, тоже ffmpeg, но beat sync — pydub |

**Вывод**: если просят «проверить видео-пост» в контексте `run_bot.yml`/`test_video`
— речь всегда о `video_generator.py`. Там **нет субтитров**, и это не баг — субтитры
физически есть только во втором, отключённом от CI инструменте.

Управляется через `config.py`: `VIDEO_ENABLED` (default `"true"`), `VOICE_ENABLED`
(default `"false"`), `VIDEO_EVERY_N_POSTS` (default `3`), `VIDEO_POST_MODE`
(default `"same"`) — ищите по имени переменной, это самые верхнеуровневые
настройки в файле.

## VK — тип токена (критично, главный урок)

Два принципиально разных типа токена, и только один реально работает для этого
пайплайна — проверено дважды вживую (2026-07-06 и повторно 2026-07-28):

- **Токен СООБЩЕСТВА** (простой ключ из «Сообщество → Работа с API → Ключ
  доступа», ИЛИ токен через Standalone OAuth-приложение без нужных прав) —
  **НЕ работает для медиа**: `photos.getWallUploadServer` падает с
  `error 27: Group authorization failed: method is unavailable with group auth`,
  `video.save` — с `error 5`. Это **платформенное ограничение самого VK API**
  (не баг кода, никакой параметр это не обходит) — см.
  `references/vk_token_history.md` за полной хронологией обоих инцидентов.
  **Не использовать этот тип токена для данного пайплайна.**
- **Токен ПОЛЬЗОВАТЕЛЯ** (выпущен через клиентское приложение Kate Mobile,
  права `photos,video,wall,groups,offline,docs`) от аккаунта-**админа**
  сообщества — единственный рабочий вариант, подтверждено боевым прогоном
  (фото-альбом + видеокарусель, `wall.post` успешно).

**Механика в коде** (`vk_publisher.py`, сверено построчно):
- `config.VK_OWNER_ID` — ID сообщества, хранится **отрицательным** числом.
- `send_to_vk()` строит `wall.post` с `owner_id` как есть (уже отрицательный)
  и `from_group=1`.
- `_upload_one_photo()` (`photos.getWallUploadServer`/`photos.saveWallPhoto`)
  и `_video_save()`/`upload_video_attachment()` (`video.save`) — все берут
  `group_id = abs(owner_id)` (положительный).
- Токен читается из `config.VK_TOKEN` (env `VK_TOKEN`) во всех этих вызовах.

**Где хранится**: GitHub Secrets `VK_TOKEN` (прод/GHA) + локальный `.env`
(для смоук-теста — оба должны быть синхронизированы одним и тем же токеном,
иначе локальный тест проверяет не то, что реально используется на GHA).

**Риск бана** — user-токен привязан к личному аккаунту, а VK такие аккаунты
уже банил (история этого проекта: коды 5 `user is blocked`/18 `User was
deleted or banned`). Митигация — выпускать токен с **отдельного сервисного
аккаунта-админа** сообщества, а не с основного личного профиля, чтобы бан не
задел человека, который реально им пользуется.

## Безопасность токена

- Все вызовы VK API — **только POST**, `access_token` в теле запроса
  (`data=`), **никогда GET с токеном в query-строке URL** — иначе при сетевой
  ошибке токен целиком попадает в текст исключения/трейсбек. Реальный
  инцидент 2026-07-28: одноразовый диагностический скрипт (не в репозитории)
  использовал `requests.get(params={"access_token": ...})`, токен утёк в чат
  через текст `ConnectionError`. Токен был немедленно отозван и заменён.
  Продовый `vk_publisher.py` изначально был безопасен (`_api_call()`,
  `_video_save()` — оба POST с телом), утечка была только в разовом скрипте.
- **Маскирование в логах** — `vk_publisher._mask_secret()` (добавлено после
  инцидента) заменяет любую подстроку вида `vk1.a....` на `vk1.a.***MASKED***`
  перед логированием текста исключения, применяется в обработчиках ошибок
  `_api_call()` и `_video_save()` — защита на случай, если токен всё же
  окажется в строке (например, будущая правка случайно вернёт GET).
- При компрометации токена — немедленная ротация (см. рунбук ниже), не
  дожидаясь планового цикла.

## Рабочий процесс / рунбук VK-токена

1. **Смоук-тест без публикации** — POST-ом дёрнуть `photos.getWallUploadServer`
   и `video.save` с `group_id` (см. `references/vk_token_history.md` за
   готовым безопасным сниппетом). Ожидать `ok` по обоим, без `error 27/5/18`.
   В вывод — только имя метода и код/текст ошибки VK, **никогда не токен и не
   полный текст сетевого исключения** (при сетевой ошибке печатать только
   `type(error).__name__`, не `str(error)`).
2. **Боевая проверка** — `gh workflow run run_bot.yml -f test_post=true -f
   test_video=true` (см. «Как запускать и что проверять» выше). В логе искать
   дополнительно к трём стандартным строкам: `VK: видео загружено` (дважды —
   горизонталь и вертикаль) и `VK: пост опубликован (post_id=...)` — обе
   логирует `send_to_vk()`/`upload_video_attachment()` в `vk_publisher.py`.
   Job должен остаться зелёным.
3. **Подтверждение без утечки** — `wall.getById` (POST, тот же токен) по
   `-{group_id}_{post_id}` из лога — сверить дату (совпадает с временем
   прогона), текст (префикс `[ТЕСТ] `) и `attachments` (`photo`×N + `video`).
   Не открывать сам пост в браузере — VK требует логин для прямых пермалинков
   на конкретный пост, публичный анонимный просмотр (как у `t.me/s/...`)
   не работает.
4. **Ротация токена** — новый user-токен (OAuth Kate Mobile, `revoke=1`) →
   завершить сессии старого (VK → Настройки → Безопасность → «Завершить все
   сеансы») → обновить `VK_TOKEN` и в GitHub Secrets, и в локальном `.env` →
   повторить смоук-тест (шаг 1) прежде чем считать ротацию завершённой.

## Как запускать и что проверять

**Тестовый прогон** (форс-публикация одного `[ТЕСТ] `-поста в оба канала,
дедуп игнорируется, см. `test_post.py`):

```bash
gh workflow run run_bot.yml -R aidarsunas-sudo/auto_dealer_bot -f test_post=true -f test_video=true
```

(`test_video` работает только вместе с `test_post=true` — временно включает
`VIDEO_ENABLED`/`VOICE_ENABLED` на этот один прогон, не трогая плановые настройки.)

**Плановый прогон** — ничего запускать не нужно, идёт сам по `cron`; ручной триггер
без флагов — `gh workflow run main_pipeline.yml` (боевой, реальные посты).

**В логе прогона искать** (все три строки — дословные, из кода):
- `Ignoring audioop-lts: markers 'python_version >= "3.13"' don't match your environment` — pip чистый (см. «Гочи» ниже).
- `✅ Альбом из N фото успешно отправлен в Telegram` — логирует `send_media_group_to_telegram()` в `telegram_publisher.py`.
- `✅ Видео успешно отправлено в Telegram` — логирует `send_video_to_telegram()` в `agent_publisher.py`, только если `test_video=true`/сработал `VIDEO_EVERY_N_POSTS`.

**Принцип: прод — источник истины.** Сеть и ffmpeg на раннере GitHub Actions
отличаются от локальной машины в обе стороны (encar-CDN отдавал заглушки без
браузерных заголовков именно локально; VK-видео-CDN `mycdn.me` наоборот упирался
в connect timeout только локально, на GHA грузится нормально — см. `CLAUDE.md`).
Локальный прогон годится для быстрой диагностики логики, но финальное
подтверждение — всегда реальный `workflow_dispatch` на GHA.

## Гейты и exit code

`post_validator.py` — жёсткий код-гейт `validate_post_before_publish()` перед
ЛЮБОЙ публикацией: фото (`config.MIN_PHOTOS_PER_POST`), название без хангыля,
год в `[MIN_YEAR, MAX_YEAR]`, пробег, цена (абсолютная/относительная/vs
заводская/vs медиана партии). Каждый отказ пишется в `rejected_posts.log`
(путь — переменная `REJECTED_LOG_PATH` в начале `post_validator.py`) через
функцию `_log_rejection()` — это единственное место записи, **ни разу не
рядом с обработкой исключений**.

Шаг `Проверить rejected_posts.log` в обоих workflow (`main_pipeline.yml`,
`run_bot.yml`) читает этот файл: непустой файл → `::warning::` в лог +
запись в `$GITHUB_STEP_SUMMARY`, job остаётся зелёным — легитимный отказ
(например, переоценённый лот) не поломка, а нормальный рабочий вывод гейта.
См. `references/rejected_posts_gate.md` за точным текстом шага.

**Настоящие сбои валят job независимо от этого гейта**: `main_pipeline.py`/
`main.py` — необработанное исключение логируется, шлёт уведомление в
`ADMIN_CHAT_ID` и **re-raise'ится**, поэтому предыдущий шаг («Запустить
main_pipeline») сам падает с ненулевым exit code — отдельный, независимый
сигнал, гейт `rejected_posts.log` его не подменяет и не глушит.

## Зависимости-гочи

Строка `audioop-lts; python_version >= "3.13"` в конце `requirements.txt` (см.
`references/audioop_marker.md`). Без маркера `pip install` падал НАСМЕРТЬ на
раннере (Python 3.12.13) целиком, блокируя оба workflow до старта любого
кода пайплайна — PyPI сейчас публикует только версии `audioop-lts`,
требующие Python≥3.13. На 3.12 модуль `audioop` есть в стандартной библиотеке,
поэтому рабочий видео-путь (`video_generator.py`+`tts_generator.py`) в этом
пакете вообще не нуждается — он не использует `pydub` нигде. `pydub` нужен
ТОЛЬКО `shorts_generator._detect_beats()` (анализ битов музыки, не в CI).

## Известные проблемы (не баги механизма)

- **VK — РЕШЕНО (2026-07-28)**: см. раздел «VK — тип токена» выше и
  `references/vk_token_history.md`. Если снова увидите `ошибка 5 — user is
  blocked`/`ошибка 18 — User was deleted or banned` — это блокировка ИМЕННО
  того личного аккаунта, с которого выпущен текущий user-токен (не
  платформенное ограничение) — чинится перевыпуском токена с
  аккаунта-админа (см. рунбук ротации выше), в идеале с отдельного
  сервисного аккаунта, чтобы дальнейшие баны не задевали основной профиль.
  Изоляция TG↔VK подтверждена в бою: падение VK не блокирует Telegram (тот
  же `try/except` вокруг `send_to_vk()` в `publish_post()`).
- **`onbid` не отдаёт broad-результаты** — внешняя проблема площадки/резолвера,
  не механизма (см. `candidate_diversity.check_source_health()`).

## Секреты

Только из `.env` (локально, в `.gitignore`) или GitHub Secrets
(`Settings → Secrets and variables → Actions`): `BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`VK_TOKEN`, `VK_OWNER_ID`, `ADMIN_CHAT_ID`, `ANTHROPIC_API_KEY` и т.д. (полный
список — блок `env:` шага «Тестовая публикация»/«Запустить main_pipeline» в
`run_bot.yml`/`main_pipeline.yml`). Никогда не выводить значения в чат, код
или логи.
