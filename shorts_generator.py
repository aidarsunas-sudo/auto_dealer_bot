# -*- coding: utf-8 -*-
"""
shorts_generator.py — монтирует вертикальный Reels/Shorts-ролик (9:16) из
1-2 готовых ВИДЕОклипов + озвучка + СИНХРОННЫЕ субтитры + фоновая музыка +
реальный монтаж (переходы, speed ramping, beat sync).

Чем отличается от video_generator.py:
    video_generator.py  — слайдшоу из ФОТО объявления (Ken Burns по кадрам).
    shorts_generator.py — монтаж из готовых ВИДЕОфрагментов (напр. проходка
                          по машине), с субтитрами, синхронными по СЛОВАМ с
                          озвучкой (тайминги из edge-tts, не на глаз).

Технология — сырой ffmpeg (subprocess), как и в video_generator.py; MoviePy
НЕ используется (лишняя зависимость — весь проект уже на ffmpeg). Общие
низкоуровневые кирпичи переиспользуются напрямую из video_generator.py
(_run_ffmpeg / _find_fonts / ffmpeg_available / ASSETS_MUSIC_DIR) — но
СВЕДЕНИЕ ЗВУКА здесь СВОЁ (см. _mix_audio()), не video_generator._add_audio():
та функция гасит музыку только на ОЦЕНЁННОЕ окно длительности голоса и
возвращает её на 100% громкости на остаток ролика — для короткого
слайдшоу-формата video_generator это нормально (там это стилистический приём
"музыка одна на хвосте"), но для 30-45-секундного Shorts с одной короткой
репликой это и значило "почти весь ролик — просто громкая музыка без
дакинга". Здесь музыка держится на фиксированной фоновой громкости ВЕСЬ
ролик, плюс sidechaincompress — точечный дополнительный дакинг по РЕАЛЬНОЙ
огибающей голоса, а не по оценённому окну.

Монтаж (см. build_reel-эквивалент внутри generate_shorts, шаги 1-7):
    1. Валидация клипов (ffprobe)
    2. Нормализация к 9:16 (blur-pad)
    3. Склейка в базовый таймлайн нужной длины (repeat+trim)
    4. Реальный монтаж: разбивка на сегменты SHORTS_SEGMENT_MIN..MAX_SEC,
       часть сегментов ускоряется (SHORTS_SPEEDUP_MIN..MAX), стыки —
       переходы crossfade/zoom-in/slide/blur (SHORTS_TRANSITION_MIN..MAX_SEC),
       по возможности подогнанные под биты музыки (см. _detect_beats()).
    5. Озвучка (ТОЛЬКО если передан voiceover_text — см. ниже) + тайминги
       слов -> анимированные субтитры (scale-in + fade-in, полупрозрачная
       подложка).
    6. Титры (заголовок/цена) — та же анимация, что и субтитры.
    7. Сведение звука: _mix_audio() (дакинг, см. выше).

ОЗВУЧКА. Раньше при config.VOICE_ENABLED=false (дефолт) параметр
voiceover_text игнорировался ЦЕЛИКОМ — вызывающий код (content_reels_
publisher.py) обходил это, временно взводя config.VOICE_ENABLED=True в
памяти процесса (см. её комментарий у _generate_reel). Теперь наличие
ГОЛОСА решает ТОЛЬКО voiceover_text: передан текст -> синтезируем и озвучку,
и субтитры; НЕ передан — ролик без голоса, без обращения к Claude за
текстом (не выдумываем факты о машине на пустом месте). config.VOICE_ENABLED
для ЭТОЙ функции больше не проверяется — это осознанное отличие от
video_generator.py, где голос всегда генерируется ИЗ ДАННЫХ объявления и
там общий кем-то один раз включаемый флаг уместен.

Отказоустойчивость (graceful fallback) на каждом шаге, как и раньше:
    - ffmpeg не установлен          -> None + явный лог (не падение).
    - клип битый/не читается         -> пропускаем его; если валидных нет -> None.
    - озвучка (TTS) не собралась     -> ролик без голоса и без субтитров.
    - тайминги слов не пришли        -> ролик с голосом, но без субтитров.
    - музыки нет в assets/music/     -> только голос (или тишина).
    - pydub не установлен/анализ бит -> монтаж без beat sync, обычные интервалы.
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

Запуск как CLI (ручной тестовый прогон одного видео, без публикации —
публикация целиком в content_reels_publisher.py, сюда не добавлена):
    python shorts_generator.py --input "видео.mp4"
    python shorts_generator.py --input "видео.mp4" --voiceover "текст озвучки"
"""

import argparse
import logging
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Union

import config
import tts_generator
from video_generator import (
    ASSETS_MUSIC_DIR,
    _find_fonts,
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


# ─────────────────────────── beat sync (RMS через pydub) ─────────────────────

def _detect_beats(music_path: Optional[Path], max_dur: float) -> list:
    """
    Таймкоды (сек) битов музыки, для подгонки резов/переходов монтажа под
    ритм (см. _plan_edit_segments). librosa НЕ используется (тяжёлая цепочка
    зависимостей — numba/audioread/soundfile, весь проект и так на голом
    ffmpeg) — вместо неё лёгкий RMS-анализ через pydub: энергия по окнам
    config.SHORTS_BEAT_WINDOW_MS, локальные всплески выше среднего в
    config.SHORTS_BEAT_RMS_FACTOR раз считаются битами, не ближе
    config.SHORTS_BEAT_MIN_GAP_SEC друг к другу (иначе соседние доли
    схлопнутся в один "спам"-список).

    Возвращает [] (НЕ ошибка), если: музыки нет, pydub не установлен, анализ
    не удался (битый файл и т.п.) — тогда _plan_edit_segments режет по
    обычному плану длительностей, без подгонки под биты.
    """
    if music_path is None:
        return []
    try:
        from pydub import AudioSegment
    except ImportError:
        logger.info("shorts_generator: pydub не установлен — монтаж без beat sync "
                    "(pip install pydub, если нужен реальный анализ битов)")
        return []

    try:
        audio = AudioSegment.from_file(music_path)[: int(max_dur * 1000) + 1000]
        window_ms = config.SHORTS_BEAT_WINDOW_MS
        chunks = [audio[i:i + window_ms] for i in range(0, len(audio), window_ms)]
        rms = [c.rms for c in chunks if len(c) > 0]
        if not rms:
            return []
        mean_rms = sum(rms) / len(rms)
        threshold = mean_rms * config.SHORTS_BEAT_RMS_FACTOR

        beats = []
        last_beat = -config.SHORTS_BEAT_MIN_GAP_SEC
        for i, value in enumerate(rms):
            t = i * window_ms / 1000
            if value >= threshold and t - last_beat >= config.SHORTS_BEAT_MIN_GAP_SEC:
                beats.append(round(t, 2))
                last_beat = t
        logger.info(f"shorts_generator: beat sync — найдено {len(beats)} битов (RMS-анализ, "
                    f"{len(rms)} окон по {window_ms}мс)")
        return beats
    except Exception as error:
        logger.warning(f"shorts_generator: анализ битов не удался ({error}) — монтаж без beat sync")
        return []


# ─────────────────────────── монтаж: сегменты, speed ramp, переходы ──────────

# Запрошенные типы переходов -> реальные имена ffmpeg xfade. "slide" —
# семейство из 4 направлений, конкретное направление выбирается случайно
# в момент планирования (не при склейке — чтобы результат был воспроизводим
# при одном и том же random.Random(seed), если он когда-нибудь понадобится).
_TRANSITION_NAMES = ["crossfade", "zoom-in", "slide", "blur"]
_SLIDE_DIRECTIONS = ["slideleft", "slideright", "slideup", "slidedown"]

# Мягкое движение в кадре (уже нормализованном к WxH) — тот же приём, что и
# в video_generator.py/старых экспериментах: zoompan ОБЯЗАТЕЛЬНО с d=1 (при
# d>1 фильтр размножает кадры, и вместо видео получается слайдшоу); pan_* —
# увеличенный кадр и движение окна crop по x (выражения с t допустимы ТОЛЬКО
# в x/y, не в w/h — в w/h они вычисляются один раз при инициализации фильтра).
_EFFECT_POOL = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
_MICRO_ZOOM_LEVEL = 0.12

# Инфляция базового таймлайна ПЕРЕД нарезкой на сегменты: speed ramping и
# переходы забирают экранное время у итогового ролика (кусок на 1.5x
# показывается в 1.5 раза быстрее, переход перекрывает соседние сегменты),
# поэтому нарезаем из базы, которая заведомо длиннее конечной цели — тратим
# на неё лишние повторы уже имеющихся клипов (дёшево), а не подгоняем план
# "на глаз" под точную цифру. Итоговая длительность в любом случае
# ИЗМЕРЯЕТСЯ по факту (см. _plan_edit_segments/_join_edit_segments), а не
# считается — план лишь решает, где остановиться.
_BASE_INFLATION = 1.6


def _resolve_transition(name: str, rng: random.Random) -> str:
    if name == "crossfade":
        return "fade"
    if name == "zoom-in":
        return "zoomin"
    if name == "slide":
        return rng.choice(_SLIDE_DIRECTIONS)
    if name == "blur":
        return "hblur"
    return "fade"


def _plan_edit_segments(base_duration: float, target_duration: float,
                        beats: list, rng: random.Random) -> list:
    """
    Планирует нарезку базового таймлайна (base_duration секунд, уже
    склеенного из повторов исходных клипов) на сегменты, покрывающие
    ЭКРАННУЮ (после speed ramping) длительность ~target_duration.

    Returns:
        [{"start", "dur", "speed", "transition", "transition_dur", "effect"}, ...]
        start/dur — координаты в БАЗОВОМ таймлайне (сколько сырого материала
        вырезать), speed — множитель скорости этого сегмента (1.0 — обычная),
        transition/transition_dur — переход ПЕРЕД этим сегментом (None у
        самого первого), effect — лёгкое движение в кадре (zoom/pan).
    """
    segments = []
    base_t = 0.0     # позиция в БАЗОВОМ (сыром) таймлайне
    screen_t = 0.0   # накопленная ЭКРАННАЯ длительность плана (оценка)
    prev_effect = None

    while screen_t < target_duration - 0.2 and base_t < base_duration - 0.05:
        remaining_base = base_duration - base_t
        raw_dur = min(rng.uniform(config.SHORTS_SEGMENT_MIN_SEC, config.SHORTS_SEGMENT_MAX_SEC),
                      remaining_base)

        speed = 1.0
        if rng.random() < config.SHORTS_SPEEDUP_SHARE:
            speed = rng.uniform(config.SHORTS_SPEEDUP_MIN, config.SHORTS_SPEEDUP_MAX)
        screen_dur = raw_dur / speed

        # Подгонка КОНЦА сегмента (в его собственной экранной шкале) под
        # ближайший бит — сдвигаем raw_dur так, чтобы screen_t + screen_dur
        # попал на бит, если это укладывается в допустимую длину сегмента.
        if beats:
            candidate_screen_end = screen_t + screen_dur
            nearest = min(beats, key=lambda b: abs(b - candidate_screen_end))
            adjusted_screen_dur = nearest - screen_t
            adjusted_raw_dur = adjusted_screen_dur * speed
            if (config.SHORTS_SEGMENT_MIN_SEC <= adjusted_raw_dur <= config.SHORTS_SEGMENT_MAX_SEC
                    and adjusted_raw_dur <= remaining_base and adjusted_screen_dur > 0.3):
                raw_dur = adjusted_raw_dur
                screen_dur = adjusted_screen_dur

        effect = rng.choice(_EFFECT_POOL)
        if effect == prev_effect:
            effect = rng.choice([e for e in _EFFECT_POOL if e != effect])
        prev_effect = effect

        transition = transition_dur = None
        if segments:
            transition = _resolve_transition(rng.choice(_TRANSITION_NAMES), rng)
            transition_dur = rng.uniform(config.SHORTS_TRANSITION_MIN_SEC, config.SHORTS_TRANSITION_MAX_SEC)
            screen_dur -= transition_dur  # xfade перекрывает стык — эффективный прирост меньше

        segments.append({
            "start": round(base_t, 2), "dur": round(raw_dur, 2), "speed": round(speed, 3),
            "transition": transition, "transition_dur": transition_dur, "effect": effect,
        })
        base_t += raw_dur
        screen_t += max(0.0, screen_dur)

    return segments


def _micro_motion_filter(effect: str, seg_dur: float, w: int, h: int, fps: int) -> str:
    frames = max(1, int(seg_dur * fps))
    step = _MICRO_ZOOM_LEVEL / frames
    peak = 1 + _MICRO_ZOOM_LEVEL

    if effect == "zoom_in":
        return (f"zoompan=z='min(1+{step:.6f}*in,{peak:.3f})':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}")
    if effect == "zoom_out":
        return (f"zoompan=z='max({peak:.3f}-{step:.6f}*in,1.0)':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}")

    over_w, over_h = int(w * 1.15), int(h * 1.15)
    template = "0.15+0.7*t/{d}" if effect == "pan_right" else "0.85-0.7*t/{d}"
    expr = template.format(d=max(0.1, seg_dur))
    return (f"scale={over_w}:{over_h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='(iw-ow)*({expr})':y='(ih-oh)/2'")


def _render_edit_segment(src: Path, dst: Path, segment: dict, w: int, h: int, fps: int) -> bool:
    """Вырезает один сегмент из базового видео: опциональный speed ramping
    (setpts) + лёгкое движение в кадре (для визуального разнообразия даже на
    зацикленном исходнике — иначе повтор того же куска выглядел бы идентично)."""
    vf_parts = []
    if abs(segment["speed"] - 1.0) > 0.01:
        vf_parts.append(f"setpts=PTS/{segment['speed']:.4f}")
    vf_parts.append(_micro_motion_filter(segment["effect"], segment["dur"], w, h, fps))
    return _run_ffmpeg([
        "-ss", f"{segment['start']:.2f}", "-t", f"{segment['dur']:.2f}", "-i", str(src),
        "-vf", ",".join(vf_parts), "-r", str(fps), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
        str(dst),
    ], timeout=180)


def _join_edit_segments(segment_files: list, segment_plan: list, dst: Path, fps: int) -> float:
    """
    Склеивает отрендеренные сегменты цепочкой xfade (переходы из плана).
    Возвращает ИЗМЕРЕННУЮ длину результата, 0.0 при ошибке.

    Смещения xfade считаются по ФАКТИЧЕСКОЙ длине отрендеренных файлов, а не
    по плану — рассинхрон в доли секунды между планом и реальным файлом (из-за
    округления кадров при setpts/zoompan) иначе увёл бы смещение за конец
    куска, и склейка либо оборвалась бы, либо схлопнулась.
    """
    if len(segment_files) == 1:
        ok = _run_ffmpeg(["-i", str(segment_files[0]), "-c", "copy", str(dst)], timeout=120)
        if not ok:
            return 0.0
        info = _probe_video(dst)
        return info["duration"] if info else 0.0

    actual = []
    for path in segment_files:
        info = _probe_video(path)
        if info is None:
            return 0.0
        actual.append(info["duration"])

    inputs = []
    for path in segment_files:
        inputs += ["-i", str(path)]

    steps = []
    cumulative = actual[0]
    label = "0:v"
    for index in range(1, len(segment_files)):
        plan = segment_plan[index]
        trans_name = plan["transition"] or "fade"
        trans_dur = min(plan["transition_dur"] or 0.3, actual[index] / 2, cumulative / 2)
        trans_dur = max(0.05, trans_dur)
        offset = max(0.0, cumulative - trans_dur)
        out_label = f"v{index}"
        steps.append(f"[{label}][{index}:v]xfade=transition={trans_name}:"
                     f"duration={trans_dur:.2f}:offset={offset:.2f}[{out_label}]")
        label = out_label
        cumulative = offset + actual[index]

    ok = _run_ffmpeg(inputs + [
        "-filter_complex", ";".join(steps), "-map", f"[{label}]",
        "-r", str(fps), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
        str(dst),
    ], timeout=900)
    if not ok:
        return 0.0

    info = _probe_video(dst)
    measured = info["duration"] if info else round(cumulative, 2)
    if info and abs(measured - cumulative) > 0.5:
        logger.warning(f"shorts_generator: склейка — расчёт {cumulative:.1f}c, файл {measured:.1f}c "
                       f"— беру фактическую длину")
    return round(measured, 2)


def _edit_base_video(base: Path, base_duration: float, target_duration: float,
                     music_path: Optional[Path], w: int, h: int, fps: int, tmp_dir: Path) -> tuple:
    """
    Реальный монтаж поверх плоской базы: сегменты + speed ramping + переходы,
    по возможности подогнанные под биты музыки. Возвращает (Path, длительность)
    смонтированного видео, либо (base, base_duration) если монтаж не удался
    ни на одном сегменте (тогда используется исходная плоская база — ролик
    всё равно собирается, просто без этого слоя эффектов).
    """
    beats = _detect_beats(music_path, target_duration)
    rng = random.Random()
    plan = _plan_edit_segments(base_duration, target_duration, beats, rng)

    if len(plan) <= 1:
        logger.info("shorts_generator:   монтаж: материала мало для сегментации — беру как есть")
        return base, base_duration

    rendered, rendered_plan = [], []
    for index, segment in enumerate(plan):
        dst = tmp_dir / f"edit_seg_{index:02d}.mp4"
        if _render_edit_segment(base, dst, segment, w, h, fps):
            rendered.append(dst)
            rendered_plan.append(segment)
        else:
            logger.warning(f"shorts_generator:   сегмент {index} ({segment['effect']}) не собрался — пропуск")

    if not rendered:
        logger.warning("shorts_generator:   ни один сегмент не собрался — беру исходную склейку без монтажа")
        return base, base_duration

    joined = tmp_dir / "edited.mp4"
    total = _join_edit_segments(rendered, rendered_plan, joined, fps)
    if total <= 0:
        logger.warning("shorts_generator:   склейка сегментов не удалась — беру исходную склейку без монтажа")
        return base, base_duration

    speedup_count = sum(1 for s in rendered_plan if abs(s["speed"] - 1.0) > 0.01)
    logger.info(f"shorts_generator:   монтаж: {len(rendered)} сегмент(ов), {speedup_count} ускорено, "
                f"{'с beat sync' if beats else 'без beat sync'} -> {total:.1f} c")
    return joined, total


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


# Общие тайминги scale+fade-in анимации титров/субтитров (см. ТЗ: 0.8->1.0
# за 0.3с + fade-in) — ОДНО число на оба вида титров, чтобы не разъезжались.
_TITLE_SCALE_IN_SEC = 0.3
_TITLE_SCALE_FROM = 0.8


def _build_subtitles(timings: list, words_per_line: int, ass_path: Path, w: int, h: int) -> bool:
    """Строит ASS-субтитры из пословных таймингов edge-tts: слова группируются
    по words_per_line, каждая группа — одно событие (start = начало первого
    слова группы, end = конец последнего; это и есть перенос слов — группа
    показывается разом, а не по одному). Возвращает False, если таймингов
    нет (субтитры просто не добавятся).

    BorderStyle=3 (вместо прежнего 1) — это НЕ обводка текста, а сплошная
    (здесь полупрозрачная через альфу в BackColour) подложка-плашка под
    текстом. Плюс на каждую строку — ASS override-теги: \\fad — fade-in/out,
    \\t(...) — анимация трансформации (масштаб 0.8 -> 1.0 за первые 300мс
    показа строки, время в \\t отсчитывается от начала САМОЙ строки, не ролика).
    """
    if not timings:
        return False

    font_size = max(36, int(h * 0.038))
    margin_v = int(h * 0.14)
    scale_from_pct = int(_TITLE_SCALE_FROM * 100)
    scale_ms = int(_TITLE_SCALE_IN_SEC * 1000)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # BorderStyle=3 -> подложка сплошным прямоугольником (BackColour),
        # альфа &H60 (~62% непрозрачности) даёт "полупрозрачную подложку".
        f"Style: Sub,Arial,{font_size},&H00FFFFFF,&H00000000,&H60000000,"
        f"-1,0,3,2,0,2,60,60,{margin_v},204\n\n"
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
        tags = (rf"{{\fscx{scale_from_pct}\fscy{scale_from_pct}\fad({scale_ms},0)"
                rf"\t(0,{scale_ms},\fscx100\fscy100)}}")
        lines.append(f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Sub,,0,0,0,,{tags}{text}")

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


def _wrap_text(text: str, max_chars_per_line: int, max_lines: int = 2) -> str:
    """Перенос слов простым жадным заполнением строк — drawtext сам не
    переносит текст, поэтому длинные титры нужно разбить на '\\n' руками.
    На последней допустимой строке перестаём переносить и дописываем весь
    остаток в неё целиком — длиннее, зато ни одно слово не теряется."""
    words = text.split()
    if not words:
        return text
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(lines) == max_lines - 1:
            current = candidate
            continue
        if len(candidate) <= max_chars_per_line or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def _animated_drawtext(textfile_name: str, font_esc: str, base_size: int, color: str,
                       box_color: str, y_expr: str) -> str:
    """drawtext с полупрозрачной подложкой (box) и имитацией scale+fade-in:
    drawtext не умеет transform, поэтому "рост" имитируется РОСТОМ КЕГЛЯ
    (fontsize как выражение от t, 0.8 -> 1.0 за _TITLE_SCALE_IN_SEC) — текст
    центрируется по актуальной text_w/text_h на каждом кадре, поэтому кегль,
    растущий из центра, на глаз читается как масштабирование. alpha —
    параллельный fade-in за то же время."""
    d = _TITLE_SCALE_IN_SEC
    fontsize_expr = f"'if(lt(t,{d}),{base_size}*({_TITLE_SCALE_FROM}+{1 - _TITLE_SCALE_FROM}*t/{d}),{base_size})'"
    alpha_expr = f"'if(lt(t,{d}),t/{d},1)'"
    return (
        f"drawtext=fontfile='{font_esc}':textfile='{textfile_name}':"
        f"fontcolor={color}:fontsize={fontsize_expr}:"
        f"box=1:boxcolor={box_color}:boxborderw=18:"
        f"borderw=3:bordercolor=black@0.6:line_spacing=6:"
        f"x=(w-text_w)/2:y={y_expr}:alpha={alpha_expr}"
    )


def _burn_overlays(video_in: Path, video_out: Path, tmp_dir: Path,
                   title_text: str, price_text: str, ass_name: Optional[str],
                   bold_font: str, regular_font: str, w: int, h: int) -> bool:
    """Накладывает заголовок (сверху) и цену (под ним) — с полупрозрачной
    подложкой и scale+fade-in анимацией (см. _animated_drawtext) — и, если
    есть, субтитры (ass, своя анимация — см. _build_subtitles). Текст
    оверлеев пишем в файлы и подаём через textfile= — так не нужно
    экранировать двоеточия/запятые/кавычки в самом тексте."""
    bold_esc = bold_font.replace("\\", "/").replace(":", "\\:")
    reg_esc = regular_font.replace("\\", "/").replace(":", "\\:")

    filters = []
    if title_text:
        wrapped = _wrap_text(title_text, max_chars_per_line=max(8, int(w / (int(h * 0.045) * 0.62))))
        (tmp_dir / "title.txt").write_text(wrapped, encoding="utf-8")
        filters.append(_animated_drawtext("title.txt", bold_esc, int(h * 0.045),
                                          "white", "black@0.45", f"{int(h * 0.06)}"))
    if price_text:
        wrapped = _wrap_text(price_text, max_chars_per_line=max(8, int(w / (int(h * 0.038) * 0.62))))
        (tmp_dir / "price.txt").write_text(wrapped, encoding="utf-8")
        filters.append(_animated_drawtext("price.txt", reg_esc, int(h * 0.038),
                                          "yellow", "black@0.45", f"{int(h * 0.115)}"))
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


# ─────────────────────────── звук: дакинг музыки под голос ───────────────────

def _pick_music(rng: random.Random) -> Optional[Path]:
    if not ASSETS_MUSIC_DIR.exists():
        return None
    tracks = [p for p in ASSETS_MUSIC_DIR.iterdir()
             if p.is_file() and p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg")]
    return rng.choice(tracks) if tracks else None


def _mix_audio(video_in: Path, video_out: Path, duration: float,
              voice_path: Optional[Path], music_path: Optional[Path]) -> bool:
    """
    Голос — config.SHORTS_VOICE_VOLUME (0.7), музыка фоном —
    config.SHORTS_MUSIC_VOLUME_WITH_VOICE (0.3) НА ВЕСЬ ролик (не только
    "пока идёт оценённая реплика", как в video_generator._add_audio() —
    это и был баг "просто громкая музыка", см. докстринг модуля), плюс
    sidechaincompress — дополнительный точечный дакинг по РЕАЛЬНОЙ огибающей
    голоса. Без голоса — музыка на config.SHORTS_MUSIC_VOLUME_NO_VOICE (0.6),
    единственный звук в ролике, поэтому громче фоновой роли.
    """
    if music_path is None and voice_path is None:
        logger.info("shorts_generator: ни музыки, ни голоса — ролик без звука")
        return _run_ffmpeg(["-i", str(video_in), "-c", "copy", "-movflags", "+faststart",
                           str(video_out)], timeout=120)

    # Индексы входов ffmpeg считает по -i, а "-stream_loop -1" — опция
    # СЛЕДУЮЩЕГО входа, а не отдельный вход: видео 0, музыка 1, голос 2.
    inputs = ["-i", str(video_in)]
    graph, music_label, voice_label = [], None, None
    next_index = 1
    if music_path is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]
        music_label = f"{next_index}:a"
        next_index += 1
    if voice_path is not None:
        inputs += ["-i", str(voice_path)]
        voice_label = f"{next_index}:a"
        next_index += 1

    fade_start = max(0.0, duration - 1.5)

    if voice_label and music_label:
        # edge-tts отдаёт МОНО: без приведения к стерео amix схлопывает в
        # один канал и всю музыку тоже.
        graph.append(f"[{voice_label}]volume={config.SHORTS_VOICE_VOLUME},"
                     f"aformat=channel_layouts=stereo,apad,atrim=0:{duration:.2f},asplit=2[voice][sc]")
        graph.append(f"[{music_label}]volume={config.SHORTS_MUSIC_VOLUME_WITH_VOICE},"
                     f"atrim=0:{duration:.2f},afade=t=out:st={fade_start:.2f}:d=1.5[music]")
        graph.append("[music][sc]sidechaincompress=threshold=0.05:ratio=8:"
                     "attack=20:release=400[ducked]")
        graph.append("[voice][ducked]amix=inputs=2:duration=first:"
                     "dropout_transition=0:normalize=0[a]")
    elif voice_label:
        graph.append(f"[{voice_label}]volume={config.SHORTS_VOICE_VOLUME},"
                     f"aformat=channel_layouts=stereo,apad,atrim=0:{duration:.2f}[a]")
    else:
        graph.append(f"[{music_label}]volume={config.SHORTS_MUSIC_VOLUME_NO_VOICE},"
                     f"atrim=0:{duration:.2f},afade=t=out:st={fade_start:.2f}:d=1.5[a]")

    return _run_ffmpeg(inputs + [
        "-filter_complex", ";".join(graph),
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration:.2f}", "-shortest", "-movflags", "+faststart",
        str(video_out),
    ], timeout=600)


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
        voiceover_text: готовый текст озвучки. ЕСТЬ текст -> синтезируем
            голос и субтитры; НЕТ (None/пусто) -> ролик БЕЗ голоса — за
            текстом для озвучки эта функция никуда не обращается (не
            выдумывает факты о машине на пустом месте, см. докстринг модуля
            про то, что решение больше не зависит от config.VOICE_ENABLED).

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
    w, h, fps = config.SHORTS_WIDTH, config.SHORTS_HEIGHT, config.SHORTS_FPS

    # 1. Валидация входных клипов
    logger.info(f"shorts_generator: [1/7] проверяю {len(videos)} входных клип(а/ов)")
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

    rng = random.Random()

    with tempfile.TemporaryDirectory(prefix="shorts_") as td:
        tmp_dir = Path(td)

        # 2. Нормализация клипов к 9:16
        logger.info(f"shorts_generator: [2/7] нормализую клипы к {w}x{h} @ {fps}fps (blur-pad)")
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

        # 3. Целевая длительность и базовая склейка (сырой материал, инфляция
        # компенсирует то, что монтаж на шаге 4 отъест часть экранного времени
        # speed ramping'ом и переходами — см. _BASE_INFLATION).
        target = float(max(min_sec, min(max_sec, sum(d for _, d in valid))))
        base_target = min(target * _BASE_INFLATION, max_sec * _BASE_INFLATION)
        logger.info(f"shorts_generator: [3/7] целевая длительность {target:.1f} c, склеиваю базу "
                    f"({base_target:.1f} c сырья)")
        base = tmp_dir / "base.mp4"
        base_actual = _build_base_video(norm_clips, base, base_target, fps, tmp_dir)
        if base_actual <= 0:
            logger.error("shorts_generator: склейка базового видео не удалась — отмена")
            return None

        # 4. Реальный монтаж: сегменты + speed ramping + переходы + beat sync
        logger.info("shorts_generator: [4/7] монтаж (переходы/speed ramping/beat sync)")
        music_path = _pick_music(rng)
        edited, actual = _edit_base_video(base, base_actual, target, music_path, w, h, fps, tmp_dir)

        # 5. Озвучка + тайминги слов -> субтитры (ТОЛЬКО если есть текст)
        logger.info("shorts_generator: [5/7] озвучка и субтитры")
        voice_path = None
        ass_name = None
        if voiceover_text:
            vp = tmp_dir / "voice.mp3"
            timings = tts_generator.synthesize_with_word_timings(voiceover_text, vp)
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
            logger.info("shorts_generator:   voiceover_text не передан — ролик без голоса "
                        "(текст не выдумывается)")

        # 6. Титры + субтитры (burn), с анимацией (см. _burn_overlays)
        logger.info("shorts_generator: [6/7] накладываю титры и субтитры")
        texted = tmp_dir / "texted.mp4"
        title_text = _title_text(fields) if bold_font else ""
        price_text = _price_text(fields) if regular_font else ""
        if not _burn_overlays(edited, texted, tmp_dir, title_text, price_text,
                              ass_name, bold_font, regular_font, w, h):
            logger.warning("shorts_generator:   наложение оверлеев не удалось — беру видео без них")
            texted = edited

        # 7. Сведение звука (дакинг — см. _mix_audio)
        logger.info("shorts_generator: [7/7] музыка + голос (ducking)")
        if output_path:
            out = Path(output_path)
        else:
            slug = f"{fields.get('brand','')}_{fields.get('model','')}".strip("_").replace(" ", "_") or "shorts"
            out_dir = Path(config.SHORTS_OUTPUT_DIR)
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"shorts_{slug}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)

        if not _mix_audio(texted, out, actual, voice_path, music_path):
            logger.error("shorts_generator: финальное сведение аудио не удалось — отмена")
            return None

    logger.info(f"shorts_generator: === готово: {out} ({actual:.1f} c) ===")
    return out


# ─────────────────────────── CLI: ручной тестовый прогон ─────────────────────

def main() -> int:
    """
    Ручной тестовый прогон одного видео БЕЗ публикации (публикация целиком
    в content_reels_publisher.py — этот CLI её не касается, только монтаж).
    По умолчанию результат уходит в output_reels/ рядом с проектом.

        python shorts_generator.py --input "C:\\...\\видео.mp4"
        python shorts_generator.py --input клип.mp4 --voiceover "текст"
    """
    parser = argparse.ArgumentParser(description="Тестовый прогон shorts_generator.py (только монтаж)")
    parser.add_argument("--input", required=True, nargs="+", help="1-2 видеофайла")
    parser.add_argument("--voiceover", default=None,
                        help="текст озвучки; без него — БЕЗ голоса (текст не выдумывается)")
    parser.add_argument("--output", default=None, help="путь к .mp4 (по умолчанию output_reels/...)")
    parser.add_argument("--brand", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    car_data = {"brand": args.brand, "model": args.model}
    if args.output:
        output_path = Path(args.output)
    else:
        out_dir = Path("output_reels")
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"reel_{Path(args.input[0]).stem}.mp4"

    result = generate_shorts(car_data, args.input, output_path=output_path, voiceover_text=args.voiceover)
    if result:
        print(f"\nГотово: {result}")
        return 0
    print("\nНе собралось — см. лог выше.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
