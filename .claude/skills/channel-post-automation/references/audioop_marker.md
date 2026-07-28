# audioop-lts — версионный маркер (последняя строка requirements.txt)

```
# Beat sync в shorts_generator.py (_detect_beats, монтаж под ритм музыки) —
# лёгкий RMS-анализ, НЕ librosa (та тянет numba/audioread/soundfile, а весь
# проект и так на голом ffmpeg). audioop-lts обязателен вместе с pydub на
# Python 3.13+: модуль audioop убран из стандартной библиотеки (PEP 594),
# pydub его ещё не заместил — без backport'а import pydub падает с
# ModuleNotFoundError: audioop/pyaudioop. Если pydub не установлен —
# _detect_beats() просто отключает beat sync (не ошибка, см. её докстринг).
pydub
audioop-lts; python_version >= "3.13"
```

## Почему маркер обязателен

Коммит `4d9583f` (2026-07-23) добавил `audioop-lts` БЕЗ маркера версии. PyPI
сейчас публикует для этого пакета только релизы, требующие Python≥3.13 — на
раннере GitHub Actions (Python 3.12.13) `pip install -r requirements.txt`
падал целиком:

```
ERROR: Could not find a version that satisfies the requirement audioop-lts (from versions: none)
ERROR: No matching distribution found for audioop-lts
```

Это блокировало ОБА workflow (`main_pipeline.yml`, `run_bot.yml`) до старта
любого кода пайплайна — с 2026-07-24 по 2026-07-28 публикация в Telegram/VK
не выходила вообще. Фикс — коммит `36974e8`.

## Почему на Python 3.12 это не регрессия для звука/видео

Модуль `audioop` есть в стандартной библиотеке Python вплоть до 3.12
включительно (удалён только в 3.13, PEP 594). Рабочий видео-путь
(`video_generator.py` + `tts_generator.py`, см. `SKILL.md` — раздел
«ВИДЕО») **вообще не импортирует `pydub`** — TTS через `edge-tts` (async, без
pydub), аудио-микс (озвучка+музыка+дакинг) через чистый ffmpeg
`filter_complex`. `pydub` (и, следовательно, `audioop`) нужен ТОЛЬКО
`shorts_generator._detect_beats()` — анализ битов музыки для монтажа клипов,
код, не подключённый к CI (см. `SKILL.md`).

Проверка на раннере (Python 3.12.13, из реального лога прогона):
```
Ignoring audioop-lts: markers 'python_version >= "3.13"' don't match your environment
```
