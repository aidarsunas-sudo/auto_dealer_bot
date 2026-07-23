# -*- coding: utf-8 -*-
"""
shorts_generator.py — монтирует вертикальный Reels/Shorts-ролик (9:16) из
1-2 готовых ВИДЕОклипов + озвучка + СИНХРОННЫЕ субтитры + фоновая музыка.

Чем отличается от video_generator.py:
    video_generator.py  — слайдшоу из ФОТО объявления (Ken Burns по кадрам).
    shorts_generator.py — монтаж из готовых ВИДЕОфрагментов (напр. проходка
                          по машине), с субтитрами, синхронными по СЛОВАМ с
                          озвучкой (тайминги из edge-tts, не на глаз).

Технология — сырой ffmpeg (subprocess), как и в video_generator.py; MoviePy
НЕ используется (лишняя зависимость — весь проект уже на ffmpeg). Общие
низкоуровневые кирпичи переиспользуются напрямую из video_generator.py
(_run_ffmpeg / _find_fonts / _get_audio_duration / _add_audio / ASSETS_MUSIC_
DIR) — микширование музыки с дакингом под голос там уже отлажено, не дублируем.

Отказоустойчивость (graceful fallback) на каждом шаге, как требует ТЗ:
    - ffmpeg не установлен          -> None + явный лог (не падение).
    - клип битый/не читается         -> пропускаем его; если валидных нет -> None.
    - озвучка (TTS) не собралась     -> ролик без голоса и без субтитров.
    - тайминги слов не пришли        -> ролик с голосом, но без субтитров.
    - музыки нет в assets/music/     -> только голос (или тишина) — см. _add_audio.
Ни один из этих случаев не роняет генерацию — ролик всё равно собирается из
того, что доступно.

ГЕЙТ ПУБЛИКАЦИИ. Эта функция — только МОНТАЖ, не публикация. Как и
video_generator.generate_videos() (которая принимает уже провалидированный
ValidatedCar), в ЛЮБОМ пути публикации car_data ОБЯЗАН быть результатом
post_validator.validate_post_before_publish() (проверка пробега/цены/года —
"железные правила" из CLAUDE.md), а не сырым словарём из парсера. Сам монтаж
эти лимиты не перепроверяет (композиция != публикация), поэтому для
тестов/экспериментов допустим обычный dict, но перед реальной выкладкой
гейт должен отработать выше по стеку.

Запуск как модуль (демо/регресс — см. test_shorts.py):
    from shorts_generator import generate_shorts
    out = generate_shorts(car_data, ["clip1.mp4", "clip2.mp4"], (30, 45))
"""

import logging
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union

import config
import tts_generator
from video_generator import (
    ASSETS_MUSIC_DIR,      # noqa: F401  (используется косвенно через _add_audio)
    _add_audio,
    _find_fonts,
    _get_audio_duration,
    _run_ffmpeg,
    ffmpeg_available,
)

logger = logging.getLogger("shorts_generator")


# ─────────────────────────── ffprobe / валидация входа ───────────────────────

def _probe_video(path: Path) -> Optional[dict]:
    """Проверяет, что файл — читаемое видео с видеопотоком и длительностью > 0.
    Возвращает {"duration": float} или None, если файл битый/не видео."""
    if not path.exists() or path.stat().st_size == 0:
        logger.warning(f"shorts_generator: файл отсутствует или пуст: {path}")
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as error:
        logger.warning(f"shorts_generator: ffprobe не смог прочитать {path} ({error})")
        return None
    if result.returncode != 0:
        logger.warning(f"shorts_generator: {path} не читается ffprobe: {result.stderr[-300:]}")
        return None

    out = result.stdout.split()
    has_video = "video" in out
    duration = 0.0
    for token in out:
        try:
            duration = float(token)
            break
        except ValueError:
            continue
    if not has_video or duration <= 0:
        logger.warning(f"shorts_generator: {path} без видеопотока или нулевой длины — пропуск")
        return None
    return {"duration": duration}


def _target_duration(clip_durations: list) -> float:
    """Целевая длительность ролика в пределах [SHORTS_MIN_SEC, SHORTS_MAX_SEC].
    Меньше нижней границы -> тянем до минимума (зацикливанием), больше верхней
    -> обрезаем до максимума."""
    total = sum(clip_durations)
    return float(max(config.SHORTS_MIN_SEC, min(config.SHORTS_MAX_SEC, total)))


# ─────────────────────────── нормализация и склейка видео ────────────────────

def _normalize_clip(src: Path, dst: Path, w: int, h: int, fps: int, cap_sec: float) -> bool:
    """Приводит клип к единому виду WxH (9:16) методом blur-pad: машина видна
    целиком (letterbox), пустые поля — размытая растянутая копия того же кадра
    (не чёрные полосы). Звук исходника выбрасывается (свой звук добавим потом),
    fps/pix_fmt/sar унифицируются, чтобы клипы потом склеились concat'ом."""
    vf = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=20:5[bgb];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )
    return _run_ffmpeg([
        "-t", f"{cap_sec:.2f}", "-i", str(src),
        "-filter_complex", vf, "-map", "[v]",
        "-r", str(fps), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
        str(dst),
    ], timeout=180)


def _build_base_video(norm_clips: list, dst: Path, target_dur: float, fps: int, tmp_dir: Path) -> float:
    """Склеивает нормализованные клипы в один ролик РОВНО target_dur секунд:
    последовательность повторяется столько раз, сколько нужно, чтобы покрыть
    целевую длину, затем обрезается по -t. Возвращает фактическую длительность
    (target_dur при успехе) или 0.0 при ошибке."""
    per_seq = sum(_probe_video(c)["duration"] for c in norm_clips)
    if per_seq <= 0:
        return 0.0
    repeats = max(1, math.ceil(target_dur / per_seq))

    list_path = tmp_dir / "concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for _ in range(repeats):
            for clip in norm_clips:
                f.write(f"file '{clip.as_posix()}'\n")

    ok = _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-t", f"{target_dur:.2f}", "-r", str(fps), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
        str(dst),
    ], timeout=240)
    return target_dur if ok else 0.0


# ─────────────────────────── субтитры (ASS из таймингов слов) ────────────────

def _ass_ts(sec: float) -> str:
    """Формат времени ASS: H:MM:SS.cs (сотые доли секунды)."""
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def _build_subtitles(timings: list, words_per_line: int, ass_path: Path, w: int, h: int) -> bool:
    """Строит ASS-субтитры из пословных таймингов edge-tts: слова группируются
    по words_per_line, каждая группа — одно событие (start = начало первого
    слова группы, end = конец последнего). Возвращает False, если таймингов
    нет (субтитры просто не добавятся)."""
    if not timings:
        return False

    font_size = max(36, int(h * 0.038))
    margin_v = int(h * 0.14)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Sub,Arial,{font_size},&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,1,4,1,2,60,60,{margin_v},204\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = []
    for i in range(0, len(timings), words_per_line):
        group = timings[i:i + words_per_line]
        start = group[0]["start"]
        end = group[-1]["end"]
        if end <= start:
            end = start + 0.6
        text = _ass_escape(" ".join(g["word"] for g in group))
        lines.append(f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Sub,,0,0,0,,{text}")

    ass_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return True


# ─────────────────────────── burn текстовых оверлеев ─────────────────────────

def _run_ffmpeg_cwd(args: list, cwd: Path, timeout: int = 180) -> bool:
    """Как video_generator._run_ffmpeg, но с рабочей директорией — нужно для
    фильтров ass=/textfile=, которым на Windows проще передавать basename из
    cwd, чем экранировать двоеточие диска в абсолютном пути."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True, cwd=str(cwd))
    except subprocess.TimeoutExpired:
        logger.error(f"shorts_generator: ffmpeg превысил таймаут {timeout}с")
        return False
    if result.returncode != 0:
        logger.error(f"shorts_generator: ffmpeg ошибка ({result.returncode}): {result.stderr[-2000:]}")
        return False
    return True


def _burn_overlays(video_in: Path, video_out: Path, tmp_dir: Path,
                   title_text: str, price_text: str, ass_name: Optional[str],
                   bold_font: str, regular_font: str, w: int, h: int) -> bool:
    """Накладывает заголовок (сверху) и цену (под ним) через drawtext и, если
    есть, субтитры (ass). Текст оверлеев пишем в файлы и передаём через
    textfile= — так не нужно экранировать двоеточия/запятые/кавычки в самом
    тексте. Шрифты — общие с video_generator (кириллица + ₽)."""
    # fontfile в фильтре тоже требует экранированного двоеточия диска на Windows.
    bold_esc = bold_font.replace("\\", "/").replace(":", "\\:")
    reg_esc = regular_font.replace("\\", "/").replace(":", "\\:")

    filters = []
    if title_text:
        (tmp_dir / "title.txt").write_text(title_text, encoding="utf-8")
        filters.append(
            f"drawtext=fontfile='{bold_esc}':textfile='title.txt':"
            f"fontcolor=white:fontsize={int(h * 0.045)}:borderw=4:bordercolor=black@0.9:"
            f"x=(w-text_w)/2:y={int(h * 0.06)}"
        )
    if price_text:
        (tmp_dir / "price.txt").write_text(price_text, encoding="utf-8")
        filters.append(
            f"drawtext=fontfile='{reg_esc}':textfile='price.txt':"
            f"fontcolor=yellow:fontsize={int(h * 0.038)}:borderw=4:bordercolor=black@0.9:"
            f"x=(w-text_w)/2:y={int(h * 0.115)}"
        )
    if ass_name:
        filters.append(f"ass={ass_name}")

    if not filters:
        # Нечего накладывать — просто копия (перекодирование не нужно).
        return _run_ffmpeg(["-i", str(video_in), "-c", "copy", str(video_out)], timeout=120)

    vf = ",".join(filters)
    return _run_ffmpeg_cwd([
        "-i", str(video_in), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
        str(video_out),
    ], cwd=tmp_dir, timeout=240)


# ─────────────────────────── поля для озвучки ────────────────────────────────

def _derive_fields(car_data: dict) -> dict:
    """Собирает поля для tts_generator из car_data. Если доступен полноценный
    post_text_builder.prepare_post_fields (единый источник цифр) — берём его,
    чтобы озвучка и текст поста не расходились. Если он не сработал (нет курсов/
    данных) — минимальный набор из car_data, без выдуманных чисел."""
    try:
        from post_text_builder import prepare_post_fields
        from rates import fetch_cbr_rates
        fields, _reason = prepare_post_fields(car_data, fetch_cbr_rates())
        if fields:
            return fields
    except Exception as error:
        logger.info(f"shorts_generator: prepare_post_fields недоступен ({error}) — минимальные поля")

    cc, hp = car_data.get("cc"), car_data.get("hp")
    mileage = car_data.get("mileage_km")
    return {
        "brand": car_data.get("brand", ""),
        "model": car_data.get("model", ""),
        "title": f"{car_data.get('brand', '')} {car_data.get('model', '')}".strip(),
        "year": car_data.get("year", ""),
        "engine_display": f"{cc / 1000:.1f} л, {hp} л.с." if cc and hp else "",
        "mileage_km": f"{mileage:,} км".replace(",", " ") if isinstance(mileage, int) else (mileage or ""),
        "transmission": car_data.get("transmission", ""),
        "price_label": "Стоимость под ключ",
        "total_rub": car_data.get("total_rub", ""),
        "car_id": str(car_data.get("external_id", "")),
    }


def _title_text(fields: dict) -> str:
    parts = [str(fields.get("title") or f"{fields.get('brand','')} {fields.get('model','')}").strip(),
             str(fields.get("year") or "").strip()]
    return " ".join(p for p in parts if p)


def _price_text(fields: dict) -> str:
    total = fields.get("total_rub")
    if not total:
        return ""
    label = fields.get("price_label", "Под ключ")
    return f"{label}: {total} ₽" if "₽" not in str(total) else f"{label}: {total}"


# ─────────────────────────── публичная функция ───────────────────────────────

def generate_shorts(
    car_data: Union[dict, object],
    videos: list,
    duration_range: Optional[tuple] = None,
    output_path: Optional[Union[str, Path]] = None,
    fields: Optional[dict] = None,
    voiceover_text: Optional[str] = None,
) -> Optional[Path]:
    """
    Собирает вертикальный Reels/Shorts-ролик из 1-2 видеоклипов.

    Args:
        car_data: словарь объявления (или ValidatedCar — см. раздел «ГЕЙТ
            ПУБЛИКАЦИИ» в докстринге модуля). Нужен только для озвучки/титров.
        videos: список путей к исходным видеоклипам (1-2 шт.; лишние
            игнорируются, битые — пропускаются).
        duration_range: (min_sec, max_sec) итогового ролика. По умолчанию —
            (config.SHORTS_MIN_SEC, config.SHORTS_MAX_SEC).
        output_path: куда сохранить .mp4. По умолчанию —
            config.SHORTS_OUTPUT_DIR / shorts_<slug>.mp4.
        fields: готовые поля для озвучки (если уже посчитаны выше по стеку);
            если None — собираются из car_data через _derive_fields().
        voiceover_text: готовый текст озвучки (в обход Claude). Нужен для
            детерминированных регресс-тестов и для случая, когда текст уже
            подготовлен выше по стеку. Если None — текст генерируется через
            tts_generator.generate_voiceover_text() (нужен ANTHROPIC_API_KEY).

    Returns:
        Path к готовому .mp4, либо None при неустранимом сбое (нет ffmpeg,
        нет ни одного валидного клипа).
    """
    logger.info("shorts_generator: === старт генерации Shorts ===")

    if not ffmpeg_available():
        logger.error("shorts_generator: ffmpeg не установлен — ролик не собрать (нужен ffmpeg в PATH)")
        return None

    if duration_range:
        min_sec, max_sec = int(duration_range[0]), int(duration_range[1])
    else:
        min_sec, max_sec = config.SHORTS_MIN_SEC, config.SHORTS_MAX_SEC
    if min_sec > max_sec:
        min_sec, max_sec = max_sec, min_sec
    # временно применим переданный диапазон через config-подобные локальные значения
    w, h, fps = config.SHORTS_WIDTH, config.SHORTS_HEIGHT, config.SHORTS_FPS

    # 1. Валидация входных клипов
    logger.info(f"shorts_generator: [1/6] проверяю {len(videos)} входных клип(а/ов)")
    valid = []
    for v in videos:
        p = Path(v)
        info = _probe_video(p)
        if info:
            valid.append((p, info["duration"]))
            logger.info(f"shorts_generator:   OK {p.name} ({info['duration']:.1f} c)")
    if not valid:
        logger.error("shorts_generator: нет ни одного валидного видеоклипа — отмена")
        return None

    fonts = _find_fonts()
    if fonts is None:
        logger.warning("shorts_generator: шрифт с кириллицей не найден — титры/субтитры без текста-оверлея")
    bold_font, regular_font = fonts if fonts else ("", "")

    if fields is None:
        fields = _derive_fields(car_data if isinstance(car_data, dict) else {})

    with tempfile.TemporaryDirectory(prefix="shorts_") as td:
        tmp_dir = Path(td)

        # 2. Нормализация клипов к 9:16
        logger.info(f"shorts_generator: [2/6] нормализую клипы к {w}x{h} @ {fps}fps (blur-pad)")
        norm_clips = []
        for i, (src, dur) in enumerate(valid):
            dst = tmp_dir / f"norm_{i}.mp4"
            cap = min(dur, float(max_sec))
            if _normalize_clip(src, dst, w, h, fps, cap):
                norm_clips.append(dst)
            else:
                logger.warning(f"shorts_generator:   клип {src.name} не нормализовался — пропуск")
        if not norm_clips:
            logger.error("shorts_generator: ни один клип не удалось нормализовать — отмена")
            return None

        # 3. Целевая длительность и склейка
        target = float(max(min_sec, min(max_sec, sum(d for _, d in valid))))
        logger.info(f"shorts_generator: [3/6] целевая длительность {target:.1f} c, склеиваю")
        base = tmp_dir / "base.mp4"
        actual = _build_base_video(norm_clips, base, target, fps, tmp_dir)
        if actual <= 0:
            logger.error("shorts_generator: склейка базового видео не удалась — отмена")
            return None

        # 4. Озвучка + тайминги слов -> субтитры
        logger.info("shorts_generator: [4/6] озвучка и субтитры")
        voice_path = None
        ass_name = None
        if config.VOICE_ENABLED:
            duration_sec = max(8, int(target - 3))
            if voiceover_text:
                text = voiceover_text
                logger.info("shorts_generator:   текст озвучки передан напрямую (в обход Claude)")
            else:
                text = tts_generator.generate_voiceover_text(
                    fields, min_words=25, max_words=45, duration_sec=duration_sec)
            if text:
                vp = tmp_dir / "voice.mp3"
                timings = tts_generator.synthesize_with_word_timings(text, vp)
                if timings is not None:
                    voice_path = vp
                    ass_path = tmp_dir / "subs.ass"
                    if _build_subtitles(timings, config.SHORTS_SUBTITLE_WORDS_PER_LINE, ass_path, w, h):
                        ass_name = ass_path.name
                        logger.info("shorts_generator:   субтитры построены из таймингов слов")
                    else:
                        logger.info("shorts_generator:   таймингов слов нет — ролик без субтитров")
                else:
                    logger.warning("shorts_generator:   озвучка не синтезировалась — ролик без голоса")
            else:
                logger.warning("shorts_generator:   текст озвучки не сгенерирован — ролик без голоса")
        else:
            logger.info("shorts_generator:   VOICE_ENABLED=false — ролик без голоса и субтитров")

        # 5. Титры + субтитры (burn)
        logger.info("shorts_generator: [5/6] накладываю титры и субтитры")
        texted = tmp_dir / "texted.mp4"
        title_text = _title_text(fields) if bold_font else ""
        price_text = _price_text(fields) if regular_font else ""
        if not _burn_overlays(base, texted, tmp_dir, title_text, price_text,
                              ass_name, bold_font, regular_font, w, h):
            logger.warning("shorts_generator:   наложение оверлеев не удалось — беру видео без них")
            texted = base

        # 6. Аудио (музыка + дакинг под голос) — переиспользуем video_generator._add_audio
        logger.info("shorts_generator: [6/6] музыка + микс озвучки (ducking)")
        if output_path:
            out = Path(output_path)
        else:
            slug = f"{fields.get('brand','')}_{fields.get('model','')}".strip("_").replace(" ", "_") or "shorts"
            out_dir = Path(config.SHORTS_OUTPUT_DIR)
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"shorts_{slug}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)

        if not _add_audio(texted, out, actual, voice_path=voice_path):
            logger.error("shorts_generator: финальное сведение аудио не удалось — отмена")
            return None

    logger.info(f"shorts_generator: === готово: {out} ({actual:.1f} c) ===")
    return out
