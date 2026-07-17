# -*- coding: utf-8 -*-
"""
video_generator.py — собирает промо-ролик из фото объявления (те же самые
photos, что уходят в Telegram/VK-пост) поверх ffmpeg: Ken Burns (медленный
зум по фото), плавные перекрёстные переходы (xfade), титры (drawtext) и
случайный фоновый трек из assets/music/.

Два формата за один вызов generate_videos():
    - horizontal (1280x720, 20-30 сек) — для ленты Telegram/VK
    - vertical   (1080x1920, до 15 сек) — под VK Клипы (см. vk_publisher.py
      про то, что сама загрузка В Клипы через API недоступна — грузится
      как обычное видео, см. докстринг vk_publisher.upload_video_clip())

Не требует отдельного шага "скачать фото" от вызывающего кода — сам качает
их (переиспользует vk_publisher._download_photo() — тот же браузерный
User-Agent + Referer + проверка content-type/размера, что уже отработан
на всех источниках, см. test_vk_photo_download.py, не дублируем логику).

ffmpeg — ВНЕШНЯЯ зависимость, не пакет pip. ffmpeg_available() — проверка
перед использованием; если ffmpeg не установлен, generate_videos()
возвращает None и логирует явную причину, а не падает и не ломает
публикацию текста/фото (видео — необязательное дополнение к посту, см.
agent_publisher.py: VIDEO_ENABLED).
"""

import logging
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import config
import tts_generator
from vk_publisher import _download_photo
from post_validator import ValidatedCar

logger = logging.getLogger("video_generator")

ASSETS_MUSIC_DIR = Path(__file__).resolve().parent / "assets" / "music"

FPS = 25
SEC_PER_CLIP = 3.5          # Ken Burns на кадр, см. ТЗ (3-4 сек)
TRANSITION_SEC = 0.5        # длительность перекрёстного перехода (xfade)
AUDIO_FADE_OUT_SEC = 1.5
# Озвучка (config.VOICE_ENABLED) — музыка приглушается на время реплики,
# см. _add_audio(). -12 дБ — верхняя (самая громкая) граница заданного
# диапазона -12..-15 дБ — по фидбэку 2026-07-17 середина (-13 дБ) звучала
# слишком тихо.
DUCK_DB = -12
DUCK_RAMP_SEC = 0.5          # плавный переход громкости музыки, не резкий скачок
VOICE_TAIL_RESERVE_SEC = AUDIO_FADE_OUT_SEC + 2.0  # музыка одна на хвосте, см. ТЗ "2-3 сек"

HORIZONTAL = {"name": "horizontal", "w": 1280, "h": 720, "min_sec": 20, "max_sec": 30, "fill_mode": "crop"}
# fill_mode="blur_pad" — исходные фото объявлений почти всегда горизонтальные
# (4:3/16:9), жёсткий crop до 9:16 вертикального формата вырезает машину по
# бокам (найдено на живом просмотре 2026-07-17) — вместо этого показываем
# фото целиком (letterbox), а пустые поля сверху/снизу заполняем размытой
# растянутой копией того же фото, а не чёрными полосами (см. _make_clip()).
VERTICAL = {"name": "vertical", "w": 1080, "h": 1920, "min_sec": None, "max_sec": 15, "fill_mode": "blur_pad"}

# Windows (arial есть почти всегда) / Linux GitHub Actions (нужен apt-get
# install fonts-dejavu-core, см. .github/workflows/*.yml) — оба поддерживают
# кириллицу и знак ₽. Первая найденная на диске пара (bold, regular) — в ход.
_FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]

# Три варианта Ken Burns (все — плавный зум-ин с разным центром), чтобы
# ролик не выглядел как один и тот же эффект на каждом кадре подряд —
# чередуются по индексу фото. Оставлены только зум-ин варианты (не
# зум-аут/панорама) — это самый надёжный, отработанный рецепт zoompan;
# более экзотические варианты чаще дают дёрганое видео на разных
# версиях ffmpeg, а перепроверять зоопарк версий сейчас не в приоритете.
_KEN_BURNS_VARIANTS = [
    # центр
    ("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    # верхний левый угол
    ("0", "0"),
    # нижний правый угол
    ("iw-iw/zoom", "ih-ih/zoom"),
]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _find_fonts() -> Optional[tuple]:
    for bold, regular in _FONT_CANDIDATES:
        if Path(bold).exists() and Path(regular).exists():
            return bold, regular
    logger.error("video_generator: не нашёл шрифт с кириллицей ни по одному "
                 "из известных путей (Windows: arial, Linux: DejaVu Sans — "
                 "нужен apt-get install fonts-dejavu-core на GitHub Actions)")
    return None


def _run_ffmpeg(args: list, timeout: int = 120) -> bool:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)
    except subprocess.TimeoutExpired:
        logger.error(f"video_generator: ffmpeg превысил таймаут {timeout}с ({' '.join(cmd[:6])}...)")
        return False
    if result.returncode != 0:
        logger.error(f"video_generator: ffmpeg завершился с ошибкой ({result.returncode}): "
                     f"{result.stderr[-2000:]}")
        return False
    return True


def _pick_photos(photo_paths: list, n: int) -> list:
    """Если фото меньше, чем нужно кадров, — зацикливаем список, а не
    сокращаем ролик до одного кадра."""
    if not photo_paths:
        return []
    return [photo_paths[i % len(photo_paths)] for i in range(n)]


def _download_photos(photo_urls: list, n: int, tmp_dir: Path) -> list:
    """Скачивает до n уникальных фото (без повторов при скачивании — цикл
    по URL уже сделан в _pick_photos ПОСЛЕ, здесь просто качаем каждую
    уникальную ссылку один раз, чтобы не дёргать сеть повторно за одно и
    то же фото)."""
    paths = []
    for i, url in enumerate(photo_urls[:n]):
        data = _download_photo(url)
        if data is None:
            logger.warning(f"video_generator: не удалось скачать фото {url} — пропускаю")
            continue
        path = tmp_dir / f"photo_{i}.jpg"
        path.write_bytes(data)
        paths.append(path)
    return paths


def _make_blur_pad_composite(photo_path: Path, out_path: Path, w: int, h: int) -> bool:
    """
    Разово превращает горизонтальное фото в статичный кадр WxH под
    вертикальный формат: сама машина видна ЦЕЛИКОМ (вписана по высоте/
    ширине, без обрезки), пустые поля сверху-снизу или по бокам заполнены
    растянутой и размытой копией того же фото, а не чёрными полосами —
    приём из Reels/Shorts для горизонтальных источников.

    Инцидент 2026-07-17: жёсткий crop горизонтального фото под 9:16
    обрезал машину по бокам (см. FINAL_STATE.md) — эта функция и есть
    фикс, применяется ТОЛЬКО для fill_mode="blur_pad" (сейчас — только
    вертикальный формат, см. VERTICAL).

    Результат передаётся в _make_clip() как обычное фото — он уже WxH,
    поэтому дальнейший scale+crop+zoompan там ничего не обрезает
    (аспект уже совпадает), только зумит.
    """
    filter_complex = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"gblur=sigma=30,eq=brightness=-0.15[bg];"
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    return _run_ffmpeg(["-i", str(photo_path), "-filter_complex", filter_complex, "-frames:v", "1", str(out_path)])


def _make_clip(photo_path: Path, out_path: Path, w: int, h: int, duration: float, variant_idx: int,
                fill_mode: str = "crop") -> bool:
    """Один Ken Burns-клип из одного фото. fill_mode="crop" — заливает
    кадр обрезкой (годится, когда исходное фото близко по пропорциям к
    целевым — см. HORIZONTAL). fill_mode="blur_pad" — сначала готовит
    статичную подложку без обрезки видимого содержимого (см.
    _make_blur_pad_composite(), см. VERTICAL) и зумит уже её."""
    x_expr, y_expr = _KEN_BURNS_VARIANTS[variant_idx % len(_KEN_BURNS_VARIANTS)]
    frames = int(round(duration * FPS))

    source = photo_path
    if fill_mode == "blur_pad":
        composite_path = out_path.with_name(f"{out_path.stem}_composite.jpg")
        if not _make_blur_pad_composite(photo_path, composite_path, w, h):
            return False
        source = composite_path

    vf = (
        f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"zoompan=z='min(zoom+0.0015,1.3)':d={frames}:s={w}x{h}:fps={FPS}:"
        f"x='{x_expr}':y='{y_expr}'"
    )
    return _run_ffmpeg([
        "-loop", "1", "-i", str(source),
        "-vf", vf,
        "-t", str(duration),
        # yuv420p — иначе libx264 иногда сам выбирает 4:4:4 (полноцветный,
        # без субдискретизации цветности) вслед за входным фильтром
        # (gblur/eq в _make_blur_pad_composite), а это НЕ воспроизводится
        # почти нигде за пределами настольного ffmpeg/VLC — ни в Telegram,
        # ни на мобильных декодерах (найдено 2026-07-17 через ffprobe на
        # реальном опубликованном ролике — profile "High 4:4:4 Predictive"
        # вместо стандартного 4:2:0). yuv420p — универсально поддерживаемый
        # формат, качество Ken Burns-клипа от него не страдает заметно.
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ])


def _concat_with_xfade(clip_paths: list, out_path: Path, clip_duration: float) -> float:
    """Склеивает клипы xfade-переходами (перекрёстное затухание), считает
    итоговую длительность результата. Возвращает итоговую длительность,
    или 0.0 при ошибке ffmpeg."""
    if len(clip_paths) == 1:
        shutil.copyfile(clip_paths[0], out_path)
        return clip_duration

    inputs = []
    for p in clip_paths:
        inputs += ["-i", str(p)]

    filters = []
    running_label = "0"
    running_dur = clip_duration
    for i in range(1, len(clip_paths)):
        offset = max(running_dur - TRANSITION_SEC, 0)
        out_label = f"v{i}" if i < len(clip_paths) - 1 else "vout"
        filters.append(
            f"[{running_label}][{i}]xfade=transition=fade:duration={TRANSITION_SEC}:offset={offset:.3f}[{out_label}]"
        )
        running_label = out_label
        running_dur = running_dur + clip_duration - TRANSITION_SEC

    filter_complex = ";".join(filters)
    ok = _run_ffmpeg(
        inputs + ["-filter_complex", filter_complex, "-map", "[vout]",
                   "-pix_fmt", "yuv420p", "-an", str(out_path)],
        timeout=150,
    )
    return running_dur if ok else 0.0


def _clip_windows(n_clips: int, clip_duration: float) -> list:
    """Возвращает [(start, end), ...] — примерное окно видимости каждого
    исходного кадра в итоговом склеенном таймлайне (для синхронизации
    титров с drawtext: enable='between(t,start,end)'). Не кадрово-точное
    (перекрёстные переходы смешивают соседние кадры) — для промо-титров
    достаточно приблизительного совпадения."""
    windows = []
    start = 0.0
    for i in range(n_clips):
        end = start + clip_duration
        windows.append((start, end))
        start = end - TRANSITION_SEC
    return windows


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def _escape_font_path(path: str) -> str:
    """fontfile= в ffmpeg-фильтрах — Windows-путь вида "C:\\Windows\\Fonts\\
    arial.ttf" ломает парсер filter_complex дважды: обратный слэш — это
    escape-символ самого ffmpeg (не путей), а двоеточие после буквы диска
    совпадает с разделителем опций фильтра. Прямые слэши ffmpeg на Windows
    прекрасно понимает как разделитель пути — конвертируем и экранируем
    оставшееся двоеточие."""
    return path.replace("\\", "/").replace(":", "\\:")


def _fit_font_size(text: str, frame_w: int, max_size: int, min_size: int = 16,
                    margin_ratio: float = 0.90, char_width_ratio: float = 0.56) -> int:
    """
    Подбирает размер шрифта так, чтобы строка text уместилась по ширине
    кадра — drawtext сам не переносит и не сжимает текст. Оценка ширины
    символа как доля от fontsize (~0.56, грубая эмпирика для большинства
    sans-serif шрифтов что на кириллице, что на латинице) — не идеально
    точно, но достаточно, чтобы не вылезать за края кадра.

    Инцидент 2026-07-17 (см. FINAL_STATE.md): размер шрифта считался как
    доля от ВЫСОТЫ кадра — для горизонтального 1280x720 это случайно
    работало (высота там и есть более узкое измерение), но для
    вертикального 1080x1920 давало шрифт, посчитанный от 1920 и вылезающий
    далеко за пределы ширины 1080 на длинных строках (цена, подпись
    канала). Теперь размер всегда явно ограничен шириной кадра.
    """
    if not text:
        return max_size
    max_width_px = frame_w * margin_ratio
    fitted = int(max_width_px / (len(text) * char_width_ratio))
    return max(min_size, min(max_size, fitted))


def _overlay_texts(
    video_in: Path, video_out: Path, w: int, h: int,
    title_text: str, spec_texts: list, price_text: str, cta_text: str,
    windows: list, bold_font: str, regular_font: str,
) -> bool:
    """Один проход drawtext поверх уже склеенного видео: первый кадр —
    крупно модель+год, средние кадры — характеристики по очереди,
    последний кадр — цена под ключ + подпись Pick+ Auto/канал."""
    n = len(windows)
    scale = min(w, h)
    filters = []

    def _trimmed_end(idx: int) -> float:
        """Инцидент 2026-07-17: соседние окна windows[i] пересекаются на
        TRANSITION_SEC (специально — это зона xfade-перехода, см.
        _clip_windows()), а drawtext enable-окна раньше использовали
        границы windows КАК ЕСТЬ — на стыке два соседних текста рисовались
        одновременно, наплывая друг на друга. Текст i-й записи теперь
        гаснет РОВНО в момент начала следующего перехода (=start
        следующего окна), а не в конце своего — ноль нахлёста, ноль
        видимого разрыва (последнее окно ни во что не переходит, торец не
        обрезаем)."""
        end = windows[idx][1]
        # -1 кадр (не 0) запасом: between() в ffmpeg включает ОБЕ границы,
        # без запаса соседние окна на стыке t=границе совпадали бы ровно
        # на 1 кадр (см. коммит-сообщение/FINAL_STATE.md) — визуально это
        # почти незаметный мерцающий кадр, но не ноль, поэтому не оставляем.
        return end if idx == n - 1 else end - TRANSITION_SEC - (1 / FPS)

    title_start, _ = windows[0]
    title_end = _trimmed_end(0)
    title_size = _fit_font_size(title_text, w, max_size=int(scale * 0.09))
    filters.append(
        f"drawtext=fontfile='{_escape_font_path(bold_font)}':text='{_escape_drawtext(title_text)}':"
        f"fontsize={title_size}:fontcolor=white:borderw=3:bordercolor=black@0.7:"
        f"x=(w-text_w)/2:y=h*0.82:"
        f"enable='between(t,{title_start:.2f},{title_end:.2f})'"
    )

    # средние кадры (не первый, не последний) — характеристики по кругу
    middle_idx = list(range(1, n - 1)) if n > 2 else []
    for j, clip_i in enumerate(middle_idx):
        if not spec_texts:
            break
        text = spec_texts[j % len(spec_texts)]
        start, _ = windows[clip_i]
        end = _trimmed_end(clip_i)
        spec_size = _fit_font_size(text, w, max_size=int(scale * 0.05))
        filters.append(
            f"drawtext=fontfile='{_escape_font_path(regular_font)}':text='{_escape_drawtext(text)}':"
            f"fontsize={spec_size}:fontcolor=white:borderw=2:bordercolor=black@0.7:"
            f"x=(w-text_w)/2:y=h*0.85:"
            f"enable='between(t,{start:.2f},{end:.2f})'"
        )

    if n >= 2:
        last_start, last_end = windows[-1]
        price_size = _fit_font_size(price_text, w, max_size=int(scale * 0.07))
        filters.append(
            f"drawtext=fontfile='{_escape_font_path(bold_font)}':text='{_escape_drawtext(price_text)}':"
            f"fontsize={price_size}:fontcolor=white:borderw=3:bordercolor=black@0.7:"
            f"x=(w-text_w)/2:y=h*0.78:"
            f"enable='between(t,{last_start:.2f},{last_end:.2f})'"
        )
        cta_size = _fit_font_size(cta_text, w, max_size=int(scale * 0.045))
        filters.append(
            f"drawtext=fontfile='{_escape_font_path(regular_font)}':text='{_escape_drawtext(cta_text)}':"
            f"fontsize={cta_size}:fontcolor=white:borderw=2:bordercolor=black@0.7:"
            f"x=(w-text_w)/2:y=h*0.88:"
            f"enable='between(t,{last_start:.2f},{last_end:.2f})'"
        )

    vf = ",".join(filters)
    return _run_ffmpeg(["-i", str(video_in), "-vf", vf, "-pix_fmt", "yuv420p", "-an", str(video_out)],
                        timeout=120)


def _get_audio_duration(path: Path) -> float:
    """Реальная длительность аудиофайла (секунды) через ffprobe — точнее,
    чем оценка по числу слов текста озвучки (TTS-движки читают с разным
    темпом), нужна для окна ducking в _add_audio()."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15, text=True)
        return float(result.stdout.strip())
    except Exception as error:
        logger.warning(f"video_generator: не смог определить длительность {path} ({error})")
        return 0.0


def _generate_voice_for_format(fmt: dict, total_dur: float, fields: dict, tmp_dir: Path) -> Optional[Path]:
    """Текст через Claude (tts_generator.generate_voiceover_text) + синтез
    речи (edge/silero). Возвращает None при любом сбое — вызывающий код
    (_generate_one) тогда просто собирает ролик без голоса, см. ТЗ (не
    ломает генерацию видео)."""
    if fmt["name"] == "vertical":
        min_words, max_words = 25, 30  # см. ТЗ: короче под 15-секундный формат
    else:
        min_words, max_words = 40, 60

    duration_sec = max(8, int(total_dur - VOICE_TAIL_RESERVE_SEC))
    text = tts_generator.generate_voiceover_text(
        fields, min_words=min_words, max_words=max_words, duration_sec=duration_sec,
    )
    if not text:
        return None

    voice_path = tmp_dir / f"{fmt['name']}_voice.mp3"
    if not tts_generator.synthesize_voiceover(text, voice_path):
        return None
    return voice_path


def _add_audio(video_in: Path, video_out: Path, duration: float, voice_path: Optional[Path] = None) -> bool:
    """Добавляет случайный трек из assets/music/ и (если передан) голос
    поверх него. Без голоса — прежнее поведение (просто музыка с
    затуханием в конце). С голосом — музыка приглушается до DUCK_DB на
    время реплики (плавно, за DUCK_RAMP_SEC до её конца), после голоса
    возвращается на полную громкость (см. ТЗ: "музыка возвращается на
    полную на 2-3 сек" — обеспечивается через VOICE_TAIL_RESERVE_SEC при
    подборе целевой длины текста, см. _generate_voice_for_format()), в
    самом конце ролика — общее затухание в тишину, как и раньше.

    Если папки с музыкой нет/она пустая — либо голая тишина (нет и
    голоса), либо только голос (без музыки под ним) — обе ветки не
    ошибка, ролик всё равно собирается."""
    tracks = []
    if ASSETS_MUSIC_DIR.exists():
        tracks = [p for p in ASSETS_MUSIC_DIR.iterdir()
                  if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg")]

    fade_start = max(duration - AUDIO_FADE_OUT_SEC, 0)

    # -movflags +faststart на ВСЕХ ветках ниже (в т.ч. чисто видео-ремуксе
    # без звука) — переносит moov-атом (индекс) в начало файла. Без этого
    # ffmpeg по умолчанию кладёт его в конец — плеер (в т.ч. Telegram)
    # должен скачать файл ЦЕЛИКОМ, прежде чем начнёт воспроизведение,
    # вместо потокового старта — найдено 2026-07-17 на реально
    # опубликованном ролике (moov не в первых 2 МБ файла), см. video_preview.

    if not tracks and not voice_path:
        logger.info(f"video_generator: {ASSETS_MUSIC_DIR} пустая/не существует — ролик без звука")
        return _run_ffmpeg([
            "-i", str(video_in), "-c", "copy", "-movflags", "+faststart", str(video_out),
        ], timeout=60)

    if not tracks:
        # только голос, без музыки — затухание в конце, как у обычной музыки
        return _run_ffmpeg([
            "-i", str(video_in), "-i", str(voice_path),
            "-filter_complex", f"[1:a]afade=t=out:st={fade_start:.2f}:d={AUDIO_FADE_OUT_SEC}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-shortest", "-movflags", "+faststart",
            str(video_out),
        ], timeout=60)

    track = random.choice(tracks)

    if not voice_path:
        return _run_ffmpeg([
            "-i", str(video_in), "-i", str(track),
            "-filter_complex", f"[1:a]afade=t=out:st={fade_start:.2f}:d={AUDIO_FADE_OUT_SEC}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-shortest", "-movflags", "+faststart",
            str(video_out),
        ], timeout=60)

    voice_dur = _get_audio_duration(voice_path)
    duck_end = min(voice_dur, max(duration - 0.5, 0)) if voice_dur > 0 else 0
    ramp = min(DUCK_RAMP_SEC, duck_end) if duck_end > 0 else 0
    duck_level = 10 ** (DUCK_DB / 20)  # дБ -> линейный множитель громкости

    if ramp > 0:
        music_volume_expr = (
            f"if(lt(t,{duck_end - ramp:.2f}),{duck_level:.4f},"
            f"if(lt(t,{duck_end:.2f}),{duck_level:.4f}+(1-{duck_level:.4f})*"
            f"(t-{duck_end - ramp:.2f})/{ramp:.2f},1))"
        )
    else:
        music_volume_expr = "1"

    filter_complex = (
        f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{duration:.2f},asetpts=PTS-STARTPTS,"
        f"volume=eval=frame:volume='{music_volume_expr}'[music];"
        f"[2:a]atrim=0:{duration:.2f},asetpts=PTS-STARTPTS,volume=1.0[voice];"
        f"[music][voice]amix=inputs=2:duration=first:dropout_transition=0,"
        f"afade=t=out:st={fade_start:.2f}:d={AUDIO_FADE_OUT_SEC}[a]"
    )
    return _run_ffmpeg([
        "-i", str(video_in), "-i", str(track), "-i", str(voice_path),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-shortest", "-movflags", "+faststart",
        str(video_out),
    ], timeout=60)


def _generate_one(
    photo_paths: list, fmt: dict, tmp_dir: Path,
    title_text: str, spec_texts: list, price_text: str, cta_text: str,
    bold_font: str, regular_font: str, fields: dict,
) -> Optional[Path]:
    target_sec = fmt["max_sec"] if fmt["min_sec"] is None else (fmt["min_sec"] + fmt["max_sec"]) / 2
    n_clips = max(2, round(target_sec / SEC_PER_CLIP))
    photos = _pick_photos(photo_paths, n_clips)
    if not photos:
        logger.error(f"video_generator ({fmt['name']}): нет ни одного скачанного фото — отмена")
        return None

    clip_paths = []
    for i, photo in enumerate(photos):
        clip_path = tmp_dir / f"{fmt['name']}_clip_{i}.mp4"
        if not _make_clip(photo, clip_path, fmt["w"], fmt["h"], SEC_PER_CLIP, i,
                           fill_mode=fmt.get("fill_mode", "crop")):
            return None
        clip_paths.append(clip_path)

    concat_path = tmp_dir / f"{fmt['name']}_concat.mp4"
    total_dur = _concat_with_xfade(clip_paths, concat_path, SEC_PER_CLIP)
    if total_dur <= 0:
        return None

    voice_path = None
    if config.VOICE_ENABLED:
        voice_path = _generate_voice_for_format(fmt, total_dur, fields, tmp_dir)
        if voice_path is None:
            logger.warning(f"video_generator ({fmt['name']}): озвучка не собралась — ролик без голоса")

    windows = _clip_windows(len(clip_paths), SEC_PER_CLIP)
    texted_path = tmp_dir / f"{fmt['name']}_texted.mp4"
    if not _overlay_texts(concat_path, texted_path, fmt["w"], fmt["h"],
                           title_text, spec_texts, price_text, cta_text,
                           windows, bold_font, regular_font):
        return None

    final_path = tmp_dir / f"{fmt['name']}_final.mp4"
    if not _add_audio(texted_path, final_path, total_dur, voice_path=voice_path):
        return None

    return final_path


def generate_videos(car: ValidatedCar, fields: dict, output_dir: Optional[Path] = None) -> Optional[dict]:
    """
    Собирает оба формата ролика (horizontal/vertical) по объявлению.

    Args:
        car: ValidatedCar (post_validator.py) — ОБЯЗАТЕЛЬНО результат
             успешного validate_post_before_publish(), не голый dict (см.
             проверку типа ниже и докстринг ValidatedCar). Читается только
             car["photos"] (те же ссылки, что уходят в Telegram/VK-пост).
        fields: словарь из post_text_builder.prepare_post_fields() — та же
                структура, что использует lead_post_builder.py/
                price_alert_builder.py для текста поста (единый источник
                данных, цена/характеристики не пересчитываются заново).
        output_dir: куда положить итоговые .mp4 (по умолчанию — временная
                    папка, которую вызывающий код должен сам прибрать
                    после публикации; при передаче своей директории она
                    НЕ удаляется автоматически).

    Returns:
        {"horizontal": Path, "vertical": Path} — только успешно собранные
        форматы (частичный успех — ЭТО НЕ ошибка, вызывающий код публикует
        то, что получилось). None, если не получилось собрать НИ ОДНОГО
        формата (ffmpeg не установлен, нет фото, сбой ffmpeg и т.п.) —
        видео просто пропускается, публикация текста/фото это не блокирует.

    Raises:
        TypeError: car — не ValidatedCar. Инцидент 2026-07-17: тестовый
            скрипт вызвал generate_videos() напрямую на сыром результате
            поиска, в обход validate_post_before_publish() — в ролик с
            озвучкой попала машина с пробегом больше config.MAX_MILEAGE_KM.
            Эта проверка делает такой вызов невозможным физически, а не
            "по договорённости" — см. post_validator.ValidatedCar.
    """
    if not isinstance(car, ValidatedCar):
        raise TypeError(
            "video_generator.generate_videos() принимает только ValidatedCar "
            "(результат успешного post_validator.validate_post_before_publish()), "
            f"получен {type(car).__name__} — см. докстринг ValidatedCar про то, "
            "почему это проверяется физически, а не по договорённости"
        )

    if not ffmpeg_available():
        logger.error("video_generator: ffmpeg не найден в PATH — установи "
                      "(Windows: winget install ffmpeg; GitHub Actions: см. "
                      "apt-get install ffmpeg в workflow) — видео пропущено")
        return None

    fonts = _find_fonts()
    if fonts is None:
        return None
    bold_font, regular_font = fonts

    photo_urls = car.get("photos") or []
    if not photo_urls:
        logger.error("video_generator: у объявления нет фото — видео пропущено")
        return None

    title_text = f"{fields.get('title', '')} {fields.get('year', '')}".strip()
    spec_texts = [
        f"Двигатель: {fields.get('engine_display', '')}",
        f"Пробег: {fields.get('mileage_km', '')}",
        f"КПП: {fields.get('transmission', '')}",
    ]
    price_text = f"{fields.get('price_label', 'Стоимость под ключ')}: {fields.get('total_rub', '')} ₽"
    # Специально НЕ fields["cta_bot"] (это @korea_auto_kazan_bot — хэндл
    # лид-бота для расчёта цены, используется в теле поста) — на финальном
    # кадре видео нужен хэндл самого КАНАЛА, где выходит пост.
    cta_text = f"Pick+ Auto | {config.TELEGRAM_CHAT_ID}"

    own_tmp = output_dir is None
    tmp_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="video_gen_"))
    tmp_dir.mkdir(parents=True, exist_ok=True)

    max_photos_needed = max(
        round(HORIZONTAL["max_sec"] / SEC_PER_CLIP),
        round(VERTICAL["max_sec"] / SEC_PER_CLIP),
    )
    photo_paths = _download_photos(photo_urls, max_photos_needed, tmp_dir)
    if not photo_paths:
        logger.error("video_generator: ни одно фото не скачалось — видео пропущено")
        return None

    results = {}
    for fmt in (HORIZONTAL, VERTICAL):
        try:
            path = _generate_one(photo_paths, fmt, tmp_dir, title_text, spec_texts,
                                  price_text, cta_text, bold_font, regular_font, fields)
        except Exception as error:
            logger.error(f"video_generator ({fmt['name']}): неожиданная ошибка ({error})")
            path = None
        if path is not None:
            results[fmt["name"]] = path
        else:
            logger.error(f"video_generator: формат {fmt['name']} не собрался")

    if not results:
        if own_tmp:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    return results
