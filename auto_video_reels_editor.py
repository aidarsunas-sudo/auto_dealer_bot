# -*- coding: utf-8 -*-
"""
auto_video_reels_editor.py — сборка вертикального рилса из отснятого
материала: нарезка на фрагменты, простые переходы, крупные титры, озвучка и
фоновая музыка. Экспорт 1080x1920.

Намеренно БАЗОВЫЙ набор. Убраны shake, вращение, анимированный color
grading, мозаика, частицы и speed ramping — осталось только то, что нужно
для читаемого рилса. Из движения оставлены мягкий зум и панорама: они
проверены и именно они отличают монтаж от статичной проходки.

Конвейер (каждый шаг — отдельный вызов ffmpeg, промежуточные файлы во
временной папке, чистятся автоматически):
    1. probe          — длительность и размер исходника
    2. plan_segments  — раскадровка: фрагменты по SEGMENT_MIN..MAX сек,
                        равномерно по всей длине исходника
    3. render_segment — 1080x1920 + мягкий зум/панорама
    4. join_segments  — склейка: только cut и fade
    5. add_titles     — крупные титры на цветной подложке
    6. voiceover      — edge-tts через tts_generator.synthesize_voiceover()
    7. mix_audio      — голос на полной громкости, музыка фоном
    8. publish        — Telegram + VK через content_reels_publisher

ДЛИТЕЛЬНОСТИ МЕРЯЮТСЯ, А НЕ СЧИТАЮТСЯ. join_segments берёт фактическую
длину каждого фрагмента через ffprobe и возвращает измеренную длину
результата. Так было не всегда: пока смещения xfade считались по плановым
значениям, расхождение в доли секунды уводило смещение за конец куска, и
ролик молча схлопывался с 43.8 c до 6.67 c. Титры и звук раскладываются по
измеренной длине.

ОЗВУЧКА НЕ ЗАВИСИТ от config.VOICE_ENABLED — этот флаг гасит голос в
shorts_generator/video_generator, но здесь озвучка заявлена как функция
инструмента, поэтому включена по умолчанию и выключается своим --no-voice.
Текст берётся из --voiceover, иначе DEFAULT_VOICEOVER; к Claude инструмент
не обращается вообще, чтобы на пустых фактах он не выдумал характеристики.

СОДЕРЖАНИЕ. Рилсы не привязаны к объявлениям: ни цены, ни года, ни пробега
на экране нет, титры и озвучка не утверждают ничего о конкретной машине.
Поэтому гейта validate_post_before_publish() здесь нет — проверять нечего
(«железные правила» из CLAUDE.md про пробег/цену/год относятся к постам о
КОНКРЕТНОМ объявлении). Если понадобится привязать рилс к лоту, гейт должен
встать в build_reel() ДО первого вызова ffmpeg.

ПУБЛИКАЦИЯ ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНА: без --publish инструмент только
монтирует. Каналы и токены — CONTENT_* из .env, см. content_reels_publisher.

Запуск:
    python auto_video_reels_editor.py --input video.mp4
    python auto_video_reels_editor.py --input video.mp4 \
        --voiceover "текст озвучки" --titles "ТИТР ОДИН|ТИТР ДВА"
    python auto_video_reels_editor.py --all --limit 3
"""

import argparse
import json
import logging
import math
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import tts_generator
from content_reels_publisher import (
    DEFAULT_CAPTION,
    VIDEO_EXTENSIONS,
    ContentReelsPublisher,
    setup_logging,
)
from video_generator import ASSETS_MUSIC_DIR, _find_fonts, ffmpeg_available

logger = logging.getLogger("reels_editor")

SOURCE_DIR = Path(r"C:\Users\Xiaomi\ВИДЕО для КОНТЕНТА")
OUTPUT_DIR = Path(r"C:\Users\Xiaomi\auto_dealer_bot\output_reels")

WIDTH, HEIGHT, FPS = 1080, 1920, 30

SEGMENT_MIN_SEC = 3.0
SEGMENT_MAX_SEC = 5.0
REEL_MIN_SEC = 25.0
REEL_MAX_SEC = 45.0

# Движение в кадре. Только зум и панорама — проверенные, ничего не ломают.
EFFECT_POOL = ["zoom_in", "zoom_out", "pan_right", "pan_left"]
ZOOM_LEVEL = 0.12

# Переходы: только рез и кроссфейд. xfade требует duration > 0, поэтому
# "cut" — предельно короткий кроссфейд (0.08 c ≈ 2 кадра, на глаз это рез).
CUT_SEC = 0.08
FADE_SEC = 0.35
TRANSITION_POOL = ["cut", "cut", "cut", "fade"]

# Звук: голос на полной громкости, музыка фоном.
VOICE_VOLUME = 1.0
MUSIC_VOLUME = 0.15
MUSIC_ONLY_VOLUME = 0.50      # без голоса музыку прижимать нечем
DUCK_THRESHOLD = 0.05
DUCK_RATIO = 8
DUCK_ATTACK_MS = 20
DUCK_RELEASE_MS = 400

# Титры: крупные и читаемые с телефона.
TITLE_SIZE = 120
TITLE_SEC = 3.0
TITLE_IN_SEC = 0.3
TITLE_OUT_SEC = 0.2
TITLE_SIDE_MARGIN = 30
TITLE_BOX_BORDER = 22
TITLE_CHAR_EM = 0.66      # средняя ширина заглавной кириллицы в долях кегля
TITLE_MIN_SIZE = 56

# Короткие — чтобы реально уместиться в кадр при 120px (см. _fit_font_size).
DEFAULT_TITLES = ["АВТО ИЗ КОРЕИ", "ПОД ЗАКАЗ", "ПИШИ В ДИРЕКТ"]

TITLE_STYLES = [
    {"fontcolor": "white",  "box": "red@0.80",    "y_frac": 0.18},
    {"fontcolor": "black",  "box": "yellow@0.90", "y_frac": 0.50},
    {"fontcolor": "white",  "box": "black@0.80",  "y_frac": 0.80},
]

DEFAULT_VOICEOVER = (
    "Привозим автомобили из Кореи под заказ. "
    "Выкуп, доставка, растаможка и оформление — под ключ, "
    "срок от сорока пяти до шестидесяти дней. "
    "Пик плюс Авто, Казань — напишите, подберём вариант под вас."
)


# ─────────────────────────── ffmpeg-обвязка ──────────────────────────────────

def _run(args: list, timeout: int = 300, cwd: Optional[Path] = None) -> bool:
    """Вызов ffmpeg. False при ошибке/таймауте — исключение наружу не идёт."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                cwd=str(cwd) if cwd else None)
    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg превысил таймаут {timeout}с")
        return False
    if result.returncode != 0:
        logger.error(f"ffmpeg ошибка ({result.returncode}): {result.stderr[-1500:]}")
        return False
    return True


def probe(path: Path) -> Optional[dict]:
    """{'duration', 'width', 'height'} или None, если файл не видео / битый."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(result.stdout)
        stream = (data.get("streams") or [{}])[0]
        duration = float(data.get("format", {}).get("duration", 0))
    except Exception as error:
        logger.warning(f"ffprobe не смог прочитать {path.name} ({error})")
        return None

    if duration <= 0 or not stream.get("width"):
        logger.warning(f"{path.name}: нет видеопотока или нулевая длина")
        return None
    return {"duration": duration, "width": stream["width"], "height": stream["height"]}


def find_sources(folder: Path) -> list:
    if not folder.exists():
        logger.error(f"папка не найдена: {folder}")
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def pick_music(rng: random.Random) -> Optional[Path]:
    """Случайный трек из assets/music. None — папки нет или она пуста."""
    music_dir = Path(ASSETS_MUSIC_DIR)
    if not music_dir.exists():
        logger.info(f"папки с музыкой нет ({music_dir}) — рилс без фоновой музыки")
        return None
    tracks = [p for p in music_dir.iterdir()
              if p.is_file() and p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg")]
    if not tracks:
        logger.info("в assets/music нет треков — рилс без фоновой музыки")
        return None
    return rng.choice(tracks)


# ─────────────────────────── 2. раскадровка ──────────────────────────────────

def plan_segments(duration: float, seed: Optional[int] = None) -> list:
    """
    Раскадровка: [{"start", "dur", "effect", "transition"}].

    Фрагменты раскидываются по ВСЕЙ длине исходника — рилс показывает разные
    планы, а не первые 30 секунд. Если исходник короче нужного, окна идут по
    кругу: на разных эффектах один и тот же кусок читается по-разному.
    """
    rng = random.Random(seed)
    target = min(REEL_MAX_SEC, max(REEL_MIN_SEC, duration))
    average = (SEGMENT_MIN_SEC + SEGMENT_MAX_SEC) / 2
    count = max(3, math.ceil(target / average))

    segments = []
    for index in range(count):
        seg_dur = round(rng.uniform(SEGMENT_MIN_SEC, SEGMENT_MAX_SEC), 2)
        if duration <= seg_dur:
            start, seg_dur = 0.0, min(seg_dur, duration)
        else:
            base = (duration - seg_dur) * (index / max(1, count - 1))
            start = max(0.0, min(duration - seg_dur, base + rng.uniform(-0.5, 0.5)))

        effect = rng.choice(EFFECT_POOL)
        if segments and effect == segments[-1]["effect"]:
            effect = rng.choice([x for x in EFFECT_POOL if x != effect])

        segments.append({
            "start": round(start, 2),
            "dur": round(seg_dur, 2),
            "effect": effect,
            "transition": rng.choice(TRANSITION_POOL) if index else None,
        })

    return _trim_to_max(segments)


def _transition_savings(segments: list) -> float:
    """xfade накладывает куски друг на друга, поэтому итог короче суммы."""
    return sum(CUT_SEC if s["transition"] == "cut" else FADE_SEC for s in segments[1:])


def _trim_to_max(segments: list) -> list:
    """Подрезает раскадровку под REEL_MAX_SEC: сначала укорачиваем последний
    фрагмент, и только если он упирается в SEGMENT_MIN_SEC — выбрасываем."""
    while segments:
        total = sum(s["dur"] for s in segments) - _transition_savings(segments)
        excess = total - REEL_MAX_SEC
        if excess <= 0.01:
            return segments
        last = segments[-1]
        if last["dur"] - excess >= SEGMENT_MIN_SEC:
            last["dur"] = round(last["dur"] - excess, 2)
            return segments
        if len(segments) <= 3:
            last["dur"] = round(max(SEGMENT_MIN_SEC, last["dur"] - excess), 2)
            return segments
        segments.pop()
    return segments


# ─────────────────────────── 3. рендер фрагмента ─────────────────────────────

_FILL = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}"


def _effect_filter(effect: str, seg_dur: float) -> str:
    """Мягкое движение в кадре, приведённом к 1080x1920.

    zoom_* — zoompan обязательно с d=1: при d>1 фильтр размножает кадры и
    вместо видео получается слайдшоу.
    pan_* — увеличенный кадр и движение окна crop по x; выражения с t в crop
    допустимы ТОЛЬКО для x/y — в w/h они вычисляются один раз (проверено:
    'iw/(1+0.12*t/3)' в w даёт "Error when evaluating the expression").
    """
    frames = max(1, int(seg_dur * FPS))
    step = ZOOM_LEVEL / frames
    peak = 1 + ZOOM_LEVEL

    if effect == "zoom_in":
        return (f"{_FILL},zoompan=z='min(1+{step:.6f}*in,{peak:.3f})':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={FPS}")
    if effect == "zoom_out":
        return (f"{_FILL},zoompan=z='max({peak:.3f}-{step:.6f}*in,1.0)':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={FPS}")

    over_w, over_h = int(WIDTH * 1.15), int(HEIGHT * 1.15)
    template = "0.15+0.7*t/{d}" if effect == "pan_right" else "0.85-0.7*t/{d}"
    expression = template.format(d=max(0.1, seg_dur))
    return (f"scale={over_w}:{over_h}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT}:x='(iw-ow)*({expression})':y='(ih-oh)/2'")


def render_segment(src: Path, dst: Path, segment: dict) -> bool:
    """Фрагмент: вырезать, привести к 1080x1920, наложить движение. Звук
    исходника выбрасывается — свой добавим в конце."""
    return _run([
        "-ss", f"{segment['start']:.2f}", "-t", f"{segment['dur']:.2f}", "-i", str(src),
        "-vf", f"{_effect_filter(segment['effect'], segment['dur'])},setsar=1",
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
        str(dst),
    ], timeout=300)


# ─────────────────────────── 4. склейка ──────────────────────────────────────

def join_segments(segment_files: list, segment_plan: list, dst: Path) -> float:
    """
    Склеивает фрагменты цепочкой xfade (только cut и fade). Возвращает
    ИЗМЕРЕННУЮ длину результата, 0.0 при ошибке.

    Смещения считаются по фактической длине файлов, а не по плану: фильтры
    могут отдать длительность, отличающуюся от заказанной, и тогда смещение
    уезжает за конец куска — цепочка рвётся, а ролик молча схлопывается.
    """
    if len(segment_files) == 1:
        ok = _run(["-i", str(segment_files[0]), "-c", "copy", str(dst)], timeout=120)
        return segment_plan[0]["dur"] if ok else 0.0

    actual = []
    for path, planned in zip(segment_files, segment_plan):
        info = probe(path)
        actual.append(info["duration"] if info else planned["dur"])

    inputs = []
    for path in segment_files:
        inputs += ["-i", str(path)]

    steps = []
    cumulative = actual[0]
    label = "0:v"
    for index in range(1, len(segment_files)):
        kind = segment_plan[index]["transition"] or "cut"
        trans_dur = CUT_SEC if kind == "cut" else FADE_SEC
        # переход не длиннее половины любого из склеиваемых кусков
        trans_dur = min(trans_dur, actual[index] / 2, cumulative / 2)
        offset = max(0.0, cumulative - trans_dur)
        out_label = f"v{index}"
        steps.append(f"[{label}][{index}:v]xfade=transition=fade:"
                     f"duration={trans_dur:.2f}:offset={offset:.2f}[{out_label}]")
        label = out_label
        cumulative = offset + actual[index]

    ok = _run(inputs + [
        "-filter_complex", ";".join(steps), "-map", f"[{label}]",
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
        str(dst),
    ], timeout=900)
    if not ok:
        return 0.0

    info = probe(dst)
    measured = info["duration"] if info else round(cumulative, 2)
    if abs(measured - cumulative) > 0.5:
        logger.warning(f"  склейка: расчёт {cumulative:.1f} c, файл {measured:.1f} c — "
                       f"беру фактическую длину")
    return round(measured, 2)


# ─────────────────────────── 5. титры ────────────────────────────────────────

def _usable_title_width() -> int:
    return WIDTH - 2 * (TITLE_SIDE_MARGIN + TITLE_BOX_BORDER)


def _fit_title(title: str, preferred: int = TITLE_SIZE) -> tuple:
    """Подгоняет титр под ширину кадра: (текст, кегль).

    drawtext не переносит строки, поэтому длинный титр уезжает за оба края
    вместе с подложкой. Сначала уменьшаем кегль (при TITLE_SIZE=120 в строку
    влезает около 12 символов), но не ниже TITLE_MIN_SIZE — иначе титр не
    прочитать с телефона. Если и на минимальном кегле не помещается, текст
    обрезается: видимый обрезанный титр честнее, чем уехавший за края.
    """
    text = title.strip()
    usable = _usable_title_width()
    length = max(1, len(text))
    size = max(TITLE_MIN_SIZE, min(preferred, int(usable / (length * TITLE_CHAR_EM))))

    max_chars = int(usable / (size * TITLE_CHAR_EM))
    if length > max_chars:
        cut = text[:max_chars].rstrip()
        logger.warning(f"титр не помещается в кадр даже на {size}px — обрезаю: "
                       f"{text!r} -> {cut!r}")
        text = cut
    return text, size


def title_schedule(titles: list, total_dur: float) -> list:
    """Когда какой титр показывать: [{"text","style","start","end"}]."""
    clean = [t.strip() for t in titles if t and t.strip()]
    if not clean:
        return []

    schedule = []
    slot = total_dur / max(1, len(clean))
    for index, text in enumerate(clean):
        start = round(0.6 + index * slot, 2)
        if start + TITLE_SEC > total_dur:
            start = max(0.0, round(total_dur - TITLE_SEC - 0.2, 2))
        schedule.append({
            "text": text,
            "style": TITLE_STYLES[index % len(TITLE_STYLES)],
            "start": start,
            "end": round(start + TITLE_SEC, 2),
        })
    return schedule


def add_titles(src: Path, dst: Path, tmp_dir: Path, titles: list, total_dur: float) -> bool:
    """
    Крупные титры на цветной подложке, с плавным появлением и уходом.

    Текст пишется в файлы и подаётся через textfile= — так не нужно
    экранировать двоеточия и кавычки в самом тексте. Путь к шрифту
    экранируется вручную: двоеточие после буквы диска ffmpeg иначе считает
    разделителем параметров. Запятые внутри выражений тоже экранируются —
    при объединении drawtext'ов через "," они разорвали бы цепочку фильтров.
    """
    fonts = _find_fonts()
    if not fonts:
        logger.warning("шрифт с кириллицей не найден — рилс без титров")
        return _run(["-i", str(src), "-c", "copy", str(dst)], timeout=120)

    bold = fonts[0].replace("\\", "/").replace(":", "\\:")
    filters = []

    for index, item in enumerate(title_schedule(titles, total_dur)):
        text, size = _fit_title(item["text"])
        name = f"title_{index}.txt"
        (tmp_dir / name).write_text(text, encoding="utf-8")

        style, start, end = item["style"], item["start"], item["end"]
        in_end = round(start + TITLE_IN_SEC, 2)
        out_start = round(end - TITLE_OUT_SEC, 2)

        alpha = (f"if(lt(t,{start}),0,"
                 f"if(lt(t,{in_end}),(t-{start})/{TITLE_IN_SEC},"
                 f"if(lt(t,{out_start}),1,"
                 f"if(lt(t,{end}),({end}-t)/{TITLE_OUT_SEC},0))))")

        filters.append(
            f"drawtext=fontfile='{bold}':textfile='{name}':"
            f"fontcolor={style['fontcolor']}:fontsize={size}:"
            f"box=1:boxcolor={style['box']}:boxborderw={TITLE_BOX_BORDER}:"
            f"borderw=3:bordercolor=black@0.6:"
            f"x=(w-text_w)/2:y=h*{style['y_frac']}-text_h/2:"
            f"alpha='{alpha}':enable='between(t,{start},{end})'"
        )

    if not filters:
        return _run(["-i", str(src), "-c", "copy", str(dst)], timeout=120)

    return _run([
        "-i", str(src), "-vf", ",".join(filters),
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
        str(dst),
    ], timeout=900, cwd=tmp_dir)


# ─────────────────────────── 6-7. звук ───────────────────────────────────────

def make_voiceover(text: str, dst: Path) -> Optional[Path]:
    """edge-tts через tts_generator (там же silero-фолбэк и ударения в
    марках). None — ни один движок не сработал, рилс останется с музыкой."""
    if not text:
        return None
    if tts_generator.synthesize_voiceover(text, dst):
        return dst
    logger.warning("озвучка не синтезировалась — рилс будет только с музыкой")
    return None


def mix_audio(video_in: Path, video_out: Path, duration: float,
              voice_path: Optional[Path], music_path: Optional[Path]) -> bool:
    """
    Голос на полной громкости, музыка фоном (MUSIC_VOLUME), плюс
    sidechaincompress — музыка дополнительно прижимается под речь и
    возвращается в паузах.

    Свой микс, а не video_generator._add_audio(): там баланс подобран под
    слайдшоу из фото. Музыка зацикливается (-stream_loop -1), если трек
    короче ролика, и обрезается по длине видео.
    """
    if music_path is None and voice_path is None:
        logger.info("ни музыки, ни голоса — оставляю ролик без звука")
        return _run(["-i", str(video_in), "-c", "copy", str(video_out)], timeout=180)

    # Индексы входов ffmpeg считает по -i, а "-stream_loop -1" — это опция
    # следующего входа, а не отдельный вход: видео 0, музыка 1, голос 2.
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
        # edge-tts отдаёт МОНО: без приведения к стерео amix схлопывает в один
        # канал и всю музыку тоже (проверено — на выходе было channels=1).
        graph.append(f"[{voice_label}]volume={VOICE_VOLUME},"
                     f"aformat=channel_layouts=stereo,"
                     f"apad,atrim=0:{duration:.2f},asplit=2[voice][sc]")
        graph.append(f"[{music_label}]volume={MUSIC_VOLUME},atrim=0:{duration:.2f},"
                     f"afade=t=out:st={fade_start:.2f}:d=1.5[music]")
        graph.append(f"[music][sc]sidechaincompress=threshold={DUCK_THRESHOLD}:"
                     f"ratio={DUCK_RATIO}:attack={DUCK_ATTACK_MS}:"
                     f"release={DUCK_RELEASE_MS}[ducked]")
        graph.append("[voice][ducked]amix=inputs=2:duration=first:"
                     "dropout_transition=0:normalize=0[a]")
    elif voice_label:
        graph.append(f"[{voice_label}]volume={VOICE_VOLUME},"
                     f"aformat=channel_layouts=stereo,apad,atrim=0:{duration:.2f}[a]")
    else:
        graph.append(f"[{music_label}]volume={MUSIC_ONLY_VOLUME},atrim=0:{duration:.2f},"
                     f"afade=t=out:st={fade_start:.2f}:d=1.5[a]")

    return _run(inputs + [
        "-filter_complex", ";".join(graph),
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration:.2f}", "-shortest",
        str(video_out),
    ], timeout=600)


# ─────────────────────────── сборка целиком ──────────────────────────────────

def build_reel(src: Path, output_path: Optional[Path] = None, titles: Optional[list] = None,
               voiceover: Optional[str] = None, with_voice: bool = True,
               seed: Optional[int] = None) -> Optional[Path]:
    """
    Полный монтаж одного рилса. Возвращает путь к .mp4 или None.

    Args:
        src: исходный видеофайл.
        output_path: куда сохранить (по умолчанию OUTPUT_DIR/reel_<имя>.mp4).
        titles: тексты титров (по умолчанию DEFAULT_TITLES).
        voiceover: текст озвучки (по умолчанию DEFAULT_VOICEOVER).
        with_voice: False — смонтировать без голоса, только музыка.
        seed: фиксирует раскадровку — один и тот же исходник даст один и тот
            же рилс (для повторяемых прогонов).
    """
    if not ffmpeg_available():
        logger.error("ffmpeg не найден в PATH — монтаж невозможен")
        return None

    src = Path(src)
    info = probe(src)
    if info is None:
        logger.error(f"{src.name}: не читается как видео — пропуск")
        return None

    titles = DEFAULT_TITLES if titles is None else titles
    if output_path is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"reel_{src.stem}.mp4"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    music_path = pick_music(rng)
    plan = plan_segments(info["duration"], seed=seed)
    logger.info(f"{src.name}: исходник {info['duration']:.1f} c "
                f"({info['width']}x{info['height']}) -> {len(plan)} фрагмент(ов)")

    with tempfile.TemporaryDirectory(prefix="reels_") as td:
        tmp_dir = Path(td)

        rendered, rendered_plan = [], []
        for index, segment in enumerate(plan):
            dst = tmp_dir / f"seg_{index:02d}.mp4"
            if render_segment(src, dst, segment):
                rendered.append(dst)
                rendered_plan.append(segment)
            else:
                logger.warning(f"  фрагмент {index} ({segment['effect']}) не собрался — пропуск")
        if not rendered:
            logger.error(f"{src.name}: ни один фрагмент не собрался — отмена")
            return None
        rendered_plan[0]["transition"] = None
        logger.info(f"  фрагментов готово: {len(rendered)}/{len(plan)}")

        joined = tmp_dir / "joined.mp4"
        total = join_segments(rendered, rendered_plan, joined)
        if total <= 0:
            logger.error(f"{src.name}: склейка не удалась — отмена")
            return None
        logger.info(f"  склеено: {total:.1f} c")

        titled = tmp_dir / "titled.mp4"
        if not add_titles(joined, titled, tmp_dir, titles, total):
            logger.warning("  титры не наложились — беру ролик без них")
            titled = joined
        else:
            logger.info(f"  титров: {len(title_schedule(titles, total))}")

        voice_path = None
        if with_voice:
            voice_path = make_voiceover(voiceover or DEFAULT_VOICEOVER, tmp_dir / "voice.mp3")

        if not mix_audio(titled, output_path, total, voice_path, music_path):
            logger.error(f"{src.name}: сведение звука не удалось — отмена")
            return None

    logger.info(f"{src.name}: готово -> {output_path} ({total:.1f} c)")
    return output_path


# ─────────────────────────── 8. публикация ───────────────────────────────────

def publish_reel(video_path: Path, caption: str, dry_run: bool = True) -> dict:
    """Публикация в свои каналы через content_reels_publisher (CONTENT_*
    токены из .env). Сбой одного канала не мешает другому."""
    publisher = ContentReelsPublisher(SOURCE_DIR, OUTPUT_DIR, caption)
    return {
        "telegram": publisher.publish_to_telegram(video_path, dry_run=dry_run),
        "vk": publisher.publish_to_vk(video_path, dry_run=dry_run),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка вертикального рилса из отснятого материала")
    parser.add_argument("--input", default=None, help="один исходный видеофайл")
    parser.add_argument("--all", action="store_true", help="обработать всю папку с материалом")
    parser.add_argument("--source", default=str(SOURCE_DIR), help="папка с материалом (для --all)")
    parser.add_argument("--limit", type=int, default=1, help="сколько файлов взять при --all")
    parser.add_argument("--output", default=None, help="путь к готовому .mp4 (только с --input)")
    parser.add_argument("--titles", default=None,
                        help="титры через | (например \"АВТО ИЗ КОРЕИ|ПОД ЗАКАЗ\")")
    parser.add_argument("--voiceover", default=None,
                        help="текст озвучки; без него берётся DEFAULT_VOICEOVER")
    parser.add_argument("--no-voice", action="store_true", help="смонтировать без голоса")
    parser.add_argument("--caption", default=DEFAULT_CAPTION, help="подпись к посту")
    parser.add_argument("--seed", type=int, default=None, help="зафиксировать монтаж")
    parser.add_argument("--publish", action="store_true",
                        help="публиковать по-настоящему (без флага — только монтаж)")
    args = parser.parse_args()

    setup_logging()

    if args.input:
        sources = [Path(args.input)]
    elif args.all:
        sources = find_sources(Path(args.source))[:args.limit]
    else:
        parser.error("нужен либо --input <файл>, либо --all")
        return 1

    if not sources:
        print("Нечего монтировать.")
        return 1

    titles = args.titles.split("|") if args.titles else None
    dry_run = not args.publish
    if dry_run:
        print("Публикация выключена (нет --publish) — только монтаж.\n")

    results = []
    for src in sources:
        output = Path(args.output) if (args.output and args.input) else None
        video = build_reel(src, output_path=output, titles=titles,
                           voiceover=args.voiceover, with_voice=not args.no_voice,
                           seed=args.seed)
        record = {"source": src.name, "video": str(video) if video else None,
                  "telegram": False, "vk": False}
        if video:
            record.update(publish_reel(video, args.caption, dry_run=dry_run))
        results.append(record)

    print("\n" + "=" * 50)
    for record in results:
        status = (f"TG={'ok' if record['telegram'] else '-'} "
                  f"VK={'ok' if record['vk'] else '-'}") if record["video"] else "не собран"
        print(f"{record['source']} -> {record['video'] or '(нет)'} | {status}")
    return 0 if any(r["video"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
