# -*- coding: utf-8 -*-
"""
build_video.py — вертикальный ролик 1080x1920 (MP4) по лоту; вызывается из publish_lot.py.

ГЕЙТ (CLAUDE.md): build_video() принимает ТОЛЬКО ValidatedCar — результат
post_validator.validate_post_before_publish() (publish_lot вызывает его до сборки видео); на голом
dict поднимает TypeError, как video_generator.generate_videos().

Что делает
  - 6-8 фото лота по порядку. Каждое — ЦЕЛИКОМ (вписано в рамку 1080x810, без обрезки машины)
    поверх сильно размытой и затемнённой копии того же фото на весь экран.
  - Лёгкий наезд: фото плавно растёт от (1 - ZOOM) до 100% рамки, ZOOM = config.BUILD_VIDEO_ZOOM_PCT
    (по умолчанию 3%, не больше 5%). Рост идёт ДО полной ширины экрана, а не за неё — края фото
    никогда не обрезаются.
  - Длина: есть голосовая запись — длина ролика равна длине записи; нет записи — 20-30 секунд
    (config.BUILD_VIDEO_TARGET_SEC, по умолчанию 25), звук — тихая музыка из assets/music (нет
    музыки — ролик без звука).
  - Голос очищается (clean_voice: DeepFilterNet, если установлен, иначе ffmpeg afftdn+EQ) и
    нормализуется до -16 LUFS. Под голос по умолчанию подкладывается очень тихая музыка (music=False
    отключает).
  - Смена кадров привязана к фразам: (1) заданные секунды cuts; (2) границы фраз транскрипции, если
    установлен faster-whisper; (3) иначе — паузы в речи, ближайшие к равным долям. В случаях 2-3
    BuildResult.needs_confirmation=True: оператор подтверждает или задаёт секунды сам.
  - HeyGen-аватары в основной путь не входят: avatar=True -> NotImplementedError (флаг --avatar в
    publish_lot оставлен, по умолчанию выключен).

Результат: output/<lot_id>.mp4 (config.VIDEO_OUTPUT_DIR).
"""

import logging
import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config
from post_validator import ValidatedCar
from video_generator import ASSETS_MUSIC_DIR, ffmpeg_available

logger = logging.getLogger("build_video")

W, H = 1080, 1920
FPS = 25
PHOTO_BOX_W, PHOTO_BOX_H = 1080, 810
MIN_PHOTOS, MAX_PHOTOS = 6, 8
MIN_NO_VOICE_SEC, MAX_NO_VOICE_SEC = 20.0, 30.0
MIN_SEGMENT_SEC = 1.5          # каждое фото показывается не короче
TARGET_LUFS = -16
_MUSIC_SUFFIXES = (".mp3", ".m4a", ".wav", ".ogg")
_DEEP_FILTER_NAMES = ("deep-filter", "deep-filter.exe", "deepFilter", "deepFilter.exe")


class BuildVideoError(Exception):
    """Ролик собрать не удалось (нет ffmpeg, ошибка кодирования)."""


@dataclass
class BuildResult:
    path: Path
    duration: float
    cut_points: list                       # секунды смены кадров (n-1 значений)
    cut_source: str                        # manual | asr | pauses | equal
    needs_confirmation: bool               # смены кадров предложены автоматически — оператор подтверждает
    audio_method: str                      # описание обработки звука
    music_track: Optional[str]
    loudness_lufs: Optional[float]
    size_mb: float
    transcript: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ── ffmpeg / ffprobe ───────────────────────────────────────────────────────

def _run(cmd: list, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def _ffmpeg(args: list, timeout: int = 600) -> None:
    result = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args, timeout)
    if result.returncode != 0:
        raise BuildVideoError(f"ffmpeg завершился с ошибкой ({result.returncode}): {result.stderr[-1500:]}")


def probe_duration(path) -> float:
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], 60)
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise BuildVideoError(f"не удалось определить длительность {path}")


def measure_lufs(path) -> Optional[float]:
    """Интегральная громкость файла (EBU R128), LUFS; нет звука — None."""
    result = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"], 300)
    found = re.findall(r"\n\s+I:\s+(-?[\d.]+) LUFS", result.stderr)
    return float(found[-1]) if found else None


# ── звук ──────────────────────────────────────────────────────────────────

def _find_deep_filter() -> Optional[Path]:
    root = Path.home() / ".video-cleanup" / "bin"
    for name in _DEEP_FILTER_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def clean_voice(src, dst) -> str:
    """Очистка голосовой записи -> dst (WAV 48 кГц моно, -16 LUFS). Возвращает описание метода.
    DeepFilterNet (как в скилле /video-cleanup) используется, если бинарник установлен в
    ~/.video-cleanup/bin; иначе — ffmpeg: срез низов 80 Гц, afftdn (шумоподавление), мягкая коррекция,
    компрессия и двухпроходная нормализация. Скачивание DeepFilterNet — только с разрешения владельца."""
    src, dst = Path(src), Path(dst)
    norm = f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=7"
    deep = _find_deep_filter()
    if deep is not None:
        tmp = Path(tempfile.mkdtemp(prefix="bv_df_"))
        try:
            raw = tmp / "in.wav"
            _ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "48000", str(raw)])
            out_dir = tmp / "out"
            out_dir.mkdir()
            done = _run([str(deep), "-o", str(out_dir), str(raw)], 600)
            produced = out_dir / raw.name
            if done.returncode == 0 and produced.is_file():
                _ffmpeg(["-i", str(produced), "-af", f"highpass=f=80,{norm}", "-ar", "48000", "-ac", "1", str(dst)])
                return "DeepFilterNet + нормализация −16 LUFS"
            logger.warning("DeepFilterNet не отработал — использую ffmpeg-цепочку")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    chain = ("highpass=f=80,afftdn=nr=14:nf=-35:tn=1,equalizer=f=250:t=q:w=1.2:g=-2,"
             "equalizer=f=3500:t=q:w=1.0:g=2,acompressor=threshold=-20dB:ratio=2.5:attack=15:release=250," + norm)
    _ffmpeg(["-i", str(src), "-vn", "-af", chain, "-ar", "48000", "-ac", "1", str(dst)])
    return "ffmpeg: afftdn + EQ + компрессия, нормализация −16 LUFS (DeepFilterNet не установлен)"


def detect_pauses(audio, noise_db: int = -35, min_len: float = 0.3) -> list:
    """Паузы в речи [(начало, конец)] через silencedetect."""
    result = _run(["ffmpeg", "-hide_banner", "-i", str(audio), "-af", f"silencedetect=n={noise_db}dB:d={min_len}", "-f", "null", "-"], 300)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", result.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", result.stderr)]
    return [(max(s, 0.0), e) for s, e in zip(starts, ends)]


def asr_available() -> bool:
    """Транскрипция речи (faster-whisper) установлена? Без неё смены кадров ставятся по паузам."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe(audio) -> list:
    """[(начало, конец, текст)] фраз записи; без faster-whisper — пустой список."""
    if not asr_available():
        return []
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio), language="ru")
    return [(round(s.start, 2), round(s.end, 2), s.text.strip()) for s in segments]


# ── смена кадров ──────────────────────────────────────────────────────────

def validate_cuts(cuts: list, n_photos: int, duration: float) -> list:
    """Проверка заданных оператором секунд смены кадров: ровно n-1 значений, по возрастанию, каждое
    фото не короче MIN_SEGMENT_SEC, последняя смена внутри записи."""
    cuts = [float(c) for c in cuts]
    if len(cuts) != n_photos - 1:
        raise ValueError(f"для {n_photos} фото нужно {n_photos - 1} секунд смены кадров, передано {len(cuts)}")
    edges = [0.0] + cuts + [duration]
    for index in range(len(edges) - 1):
        if edges[index + 1] - edges[index] < MIN_SEGMENT_SEC:
            raise ValueError(f"фото №{index + 1} получает {edges[index + 1] - edges[index]:.2f} с "
                             f"(минимум {MIN_SEGMENT_SEC}); секунды смены: {cuts}, длина записи {duration:.1f} с")
    return cuts


def plan_cuts(n_photos: int, duration: float, cuts: Optional[list] = None, phrases: Optional[list] = None,
              pauses: Optional[list] = None) -> tuple:
    """(секунды смены кадров, источник). Приоритет: заданные cuts -> границы фраз транскрипции ->
    середины пауз -> равные доли. Автоматические варианты ближайшие к равным долям длины записи."""
    if cuts is not None:
        return validate_cuts(cuts, n_photos, duration), "manual"
    equal = [duration * k / n_photos for k in range(1, n_photos)]
    for candidates, source in (([p[1] for p in (phrases or [])], "asr"), ([(s + e) / 2 for s, e in (pauses or [])], "pauses")):
        picked, last, ok = [], 0.0, bool(candidates)
        window = 0.35 * duration / n_photos
        for k, target in enumerate(equal, start=1):
            options = [c for c in candidates if abs(c - target) <= window and c - last >= MIN_SEGMENT_SEC
                       and duration - c >= MIN_SEGMENT_SEC * (n_photos - k)]
            point = min(options, key=lambda c: abs(c - target)) if options else target
            if point - last < MIN_SEGMENT_SEC or duration - point < MIN_SEGMENT_SEC * (n_photos - k):
                ok = False
                break
            picked.append(round(point, 2))
            last = point
        if ok and any(abs(a - b) > 1e-6 for a, b in zip(picked, equal)):
            return picked, source
    return [round(c, 2) for c in equal], "equal"


# ── сборка ────────────────────────────────────────────────────────────────

def _safe_name(car: dict) -> str:
    raw = car.get("lot_id") or car.get("external_id") or car.get("title") or "lot"
    return re.sub(r"[^0-9A-Za-zА-Яа-я._-]+", "_", str(raw)).strip("_") or "lot"


def _pick_music() -> Optional[Path]:
    if not ASSETS_MUSIC_DIR.exists():
        return None
    tracks = [p for p in ASSETS_MUSIC_DIR.iterdir() if p.suffix.lower() in _MUSIC_SUFFIXES]
    return random.choice(tracks) if tracks else None


def _video_filter(n: int, durations: list, zoom_pct: float) -> str:
    # scale=out_range=tv: JPEG даёт полный диапазон (yuvj420p) — приводим к стандартному yuv420p tv-range
    start = 1.0 - zoom_pct / 100.0
    parts = []
    for i, dur in enumerate(durations):
        parts.append(
            f"[{i}:v]split[b{i}][f{i}];"
            f"[b{i}]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=6,scale={W}:{H},"
            f"eq=brightness=-0.25,drawbox=x=0:y=0:w=iw:h=ih:color=black@0.40:t=fill[bg{i}];"
            f"[f{i}]scale={PHOTO_BOX_W}:{PHOTO_BOX_H}:force_original_aspect_ratio=decrease,"
            f"scale=w='iw*({start:.4f}+{zoom_pct / 100.0:.4f}*min(t/{dur:.3f}\\,1))':h=-2:eval=frame[fg{i}];"
            f"[bg{i}][fg{i}]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame,scale=out_range=tv,format=yuv420p,setsar=1,fps={FPS}[v{i}]"
        )
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]")
    return ";".join(parts)


def build_video(car: ValidatedCar, photos: list, *, voice=None, cuts: Optional[list] = None, music: bool = True,
                output_dir=None, avatar: bool = False) -> BuildResult:
    """
    Собирает output/<lot_id>.mp4. car — ValidatedCar; photos — 6-8 путей к файлам по порядку;
    voice — необязательная голосовая запись; cuts — секунды смены кадров (n-1 значений);
    music — подкладывать тихую музыку (без записи — единственный звук).
    """
    if not isinstance(car, ValidatedCar):
        raise TypeError("build_video() принимает только ValidatedCar (результат успешного "
                        f"validate_post_before_publish()), получен {type(car).__name__}")
    if avatar:
        raise NotImplementedError("режим --avatar (HeyGen) в основной путь не входит: платный сервис, собирается отдельным запуском")
    if not ffmpeg_available():
        raise BuildVideoError("ffmpeg не найден в PATH")
    photos = [Path(p) for p in photos]
    if not MIN_PHOTOS <= len(photos) <= MAX_PHOTOS:
        raise ValueError(f"для ролика нужно {MIN_PHOTOS}-{MAX_PHOTOS} фото, передано {len(photos)}")
    missing = [str(p) for p in photos if not p.is_file()]
    if missing:
        raise ValueError(f"файлы фото не найдены: {missing}")
    zoom = min(max(config.BUILD_VIDEO_ZOOM_PCT, 0.0), 5.0)
    warnings = []
    out_dir = Path(output_dir or config.VIDEO_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{_safe_name(car)}.mp4"
    tmp = Path(tempfile.mkdtemp(prefix="build_video_"))
    try:
        n = len(photos)
        transcript, cleaned, audio_method = [], None, ""
        if voice is not None:
            cleaned = tmp / "voice_clean.wav"
            audio_method = clean_voice(voice, cleaned)
            duration = probe_duration(cleaned)
            phrases = transcribe(cleaned) if cuts is None else []
            transcript = phrases
            cut_points, cut_source = plan_cuts(n, duration, cuts, phrases, detect_pauses(cleaned) if cuts is None else None)
            if cut_source == "equal":
                warnings.append("в записи не нашлось подходящих пауз — кадры меняются через равные промежутки; задайте секунды (cuts)")
        else:
            duration = min(max(config.BUILD_VIDEO_TARGET_SEC, MIN_NO_VOICE_SEC), MAX_NO_VOICE_SEC)
            cut_points, cut_source = [round(duration * k / n, 2) for k in range(1, n)], "equal"
        edges = [0.0] + list(cut_points) + [duration]
        durations = [edges[i + 1] - edges[i] for i in range(n)]

        silent = tmp / "silent.mp4"
        args = []
        for photo, dur in zip(photos, durations):
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur + 0.04:.3f}", "-i", str(photo)]
        _ffmpeg(args + ["-filter_complex", _video_filter(n, durations, zoom), "-map", "[vout]", "-t", f"{duration:.3f}",
                        "-r", str(FPS), "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", str(silent)], 900)

        track = _pick_music() if (music or voice is None) else None
        fade_out = max(duration - 1.5, 0)
        if voice is not None and track is not None:
            audio_filter = (f"[1:a]aformat=channel_layouts=stereo[v];"
                            f"[2:a]aformat=channel_layouts=stereo,loudnorm=I={config.BUILD_VIDEO_MUSIC_UNDER_VOICE_LUFS}:TP=-6:LRA=7,"
                            f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=1,afade=t=out:st={fade_out:.3f}:d=1.5[m];"
                            f"[v][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=7[a]")
            _ffmpeg(["-i", str(silent), "-i", str(cleaned), "-stream_loop", "-1", "-i", str(track), "-filter_complex", audio_filter,
                     "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                     "-t", f"{duration:.3f}", "-movflags", "+faststart", str(final)])
        elif voice is not None:
            _ffmpeg(["-i", str(silent), "-i", str(cleaned), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                     "-t", f"{duration:.3f}", "-movflags", "+faststart", str(final)])
        elif track is not None:
            audio_filter = (f"[1:a]aformat=channel_layouts=stereo,loudnorm=I={config.BUILD_VIDEO_MUSIC_LUFS}:TP=-3:LRA=7,"
                            f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=1,afade=t=out:st={fade_out:.3f}:d=1.5[a]")
            _ffmpeg(["-i", str(silent), "-stream_loop", "-1", "-i", str(track), "-filter_complex", audio_filter,
                     "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                     "-t", f"{duration:.3f}", "-movflags", "+faststart", str(final)])
            audio_method = f"музыка {track.name} (одна, −26 LUFS)"
        else:
            shutil.copyfile(silent, final)
            audio_method = "без звука (в assets/music нет музыки, голосовой записи нет)"
            warnings.append("ролик без звука: положите музыку в assets/music или передайте голосовую запись")
        if voice is not None and track is not None:
            audio_method += f"; под голос тихая музыка {track.name} (−38 LUFS)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return BuildResult(
        path=final, duration=probe_duration(final), cut_points=list(cut_points), cut_source=cut_source,
        needs_confirmation=(voice is not None and cut_source in ("asr", "pauses", "equal")),
        audio_method=audio_method, music_track=track.name if track else None,
        loudness_lufs=measure_lufs(final), size_mb=final.stat().st_size / 1024 / 1024, transcript=transcript, warnings=warnings)
