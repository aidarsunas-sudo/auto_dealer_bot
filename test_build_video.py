# -*- coding: utf-8 -*-
"""
test_build_video.py — самотест build_video.py (python test_build_video.py; нужен ffmpeg, сеть не нужна).

Быстрая часть: планирование смен кадров и ограничения. Реальная часть: три настоящих сборки ffmpeg на
цветных фото 4:3 с яркой рамкой — по пикселям проверяется, что фото НЕ обрезается (рамка видна на всех
кадрах), наезд не больше 5%, смены кадров попадают в заданные секунды, длина ролика равна длине записи
(или 20-30 с без записи), звук (музыка/голос) и громкость.
"""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image

import build_video as bv
import config
import post_validator as pv
from post_validator import ValidatedCar

COLORS = [(220, 40, 40), (40, 180, 60), (40, 80, 220), (230, 200, 40), (40, 200, 200), (140, 60, 180)]
BORDER = (255, 0, 255)


def _check(name: str, condition: bool) -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


def _raises(exc, fn) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


def _car() -> ValidatedCar:
    car = {"brand": "Kia", "model": "K5", "year": config.MIN_YEAR, "mileage_km": 12345, "lot_id": "heydealer:TEST0001",
           "photos": [f"https://example.com/{i}.jpg" for i in range(config.MIN_PHOTOS_PER_POST)]}
    validated, reason = pv.validate_post_before_publish(car)
    assert validated is not None, reason
    return validated


def _make_photos(folder: Path, n: int = 6) -> list:
    paths = []
    for i in range(n):
        image = Image.new("RGB", (800, 600), BORDER)
        image.paste(Image.new("RGB", (760, 560), COLORS[i % len(COLORS)]), (20, 20))
        path = folder / f"p{i}.jpg"
        image.save(path, "JPEG", quality=95)
        paths.append(path)
    return paths


def _make_voice(path: Path, seconds: float = 14.0) -> None:
    # «речь» из гармоник с модуляцией слогов; тишина 0,6 с каждые 3 с (паузы между фразами)
    expr = ("0.35*(sin(2*PI*180*t)+0.5*sin(2*PI*360*t)+0.3*sin(2*PI*540*t))*(0.6+0.4*sin(2*PI*4*t))*gt(mod(t,3),0.6)")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={seconds}",
                    "-ac", "1", str(path)], check=True, capture_output=True)


def _frame(video: Path, t: float, folder: Path) -> Image.Image:
    out = folder / f"f_{t:.2f}.png"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out)],
                   check=True, capture_output=True)
    return Image.open(out).convert("RGB")


def _close(pixel, color, tol=45) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(pixel, color))


def _left_border_x(image: Image.Image) -> int:
    """x левого края фото = первая магентовая точка рамки на средней строке."""
    row = image.size[1] // 2
    for x in range(image.size[0] // 2):
        if _close(image.getpixel((x, row)), BORDER, 60):
            return x
    return -1


def _streams(video: Path) -> dict:
    import json
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,pix_fmt", "-of", "json", str(video)],
                         capture_output=True, text=True).stdout
    items = json.loads(out).get("streams", [])
    return {"video": [f"{s['width']},{s['height']},{s['pix_fmt']}" for s in items if s.get("codec_type") == "video"],
            "audio": [s for s in items if s.get("codec_type") == "audio"]}


def run() -> None:
    results = []

    # ── планирование смен кадров (быстро) ───────────────────────────────
    results.append(_check("cuts заданы верно: возвращаются как есть (источник manual)",
                          bv.plan_cuts(6, 14.0, cuts=[2.5, 5.0, 7.5, 10.0, 12.0]) == ([2.5, 5.0, 7.5, 10.0, 12.0], "manual")))
    for label, bad in (("мало значений", [2.5, 5.0]), ("не по возрастанию", [5.0, 2.5, 7.5, 10.0, 12.0]),
                       ("фото короче 1,5 с", [2.5, 5.0, 7.5, 10.0, 13.2]), ("смена за пределами записи", [2.5, 5.0, 7.5, 10.0, 15.0])):
        results.append(_check(f"cuts неверны ({label}) -> ValueError", _raises(ValueError, lambda b=bad: bv.plan_cuts(6, 14.0, cuts=b))))
    pauses = [(0.0, 0.6), (3.0, 3.6), (6.0, 6.6), (9.0, 9.6), (12.0, 12.6)]
    cuts, source = bv.plan_cuts(6, 14.0, pauses=pauses)
    results.append(_check("без cuts и транскрипции: смены по паузам, ближайшим к равным долям, источник pauses",
                          source == "pauses" and len(cuts) == 5 and cuts == sorted(cuts) and any(abs(c - 9.3) < 0.01 for c in cuts)))
    results.append(_check("между кадрами не меньше 1,5 с и последняя смена внутри записи",
                          all(b - a >= bv.MIN_SEGMENT_SEC for a, b in zip([0.0] + cuts, cuts + [14.0]))))
    phrases = [(0.0, 2.2, "а"), (2.2, 4.9, "б"), (4.9, 7.4, "в"), (7.4, 9.5, "г"), (9.5, 11.8, "д"), (11.8, 14.0, "е")]
    cuts2, source2 = bv.plan_cuts(6, 14.0, phrases=phrases, pauses=pauses)
    results.append(_check("есть транскрипция: смены на границах фраз (asr важнее пауз)", source2 == "asr" and cuts2 == [2.2, 4.9, 7.4, 9.5, 11.8]))
    cuts3, source3 = bv.plan_cuts(6, 14.0, pauses=[])
    results.append(_check("ни паузы, ни фраз: равные доли, источник equal", source3 == "equal" and cuts3 == [2.33, 4.67, 7.0, 9.33, 11.67]))

    # ── ограничения ───────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        photos = _make_photos(folder, 8)
        results.append(_check("голый dict вместо ValidatedCar -> TypeError (гейт)", _raises(TypeError, lambda: bv.build_video({"lot_id": "x"}, photos[:6]))))
        results.append(_check("5 фото -> ValueError", _raises(ValueError, lambda: bv.build_video(_car(), photos[:5], output_dir=folder))))
        results.append(_check("9 фото -> ValueError", _raises(ValueError, lambda: bv.build_video(_car(), photos + [photos[0]], output_dir=folder))))
        results.append(_check("нет файла фото -> ValueError", _raises(ValueError, lambda: bv.build_video(_car(), photos[:5] + [folder / "нет.jpg"], output_dir=folder))))
        results.append(_check("--avatar (HeyGen) в основной путь не входит -> NotImplementedError",
                              _raises(NotImplementedError, lambda: bv.build_video(_car(), photos[:6], avatar=True))))

        # ── реальная сборка 1: без записи, без музыки ─────────────────
        real_music, real_target = bv.ASSETS_MUSIC_DIR, config.BUILD_VIDEO_TARGET_SEC
        empty_music = folder / "no_music"
        empty_music.mkdir()
        bv.ASSETS_MUSIC_DIR = empty_music
        config.BUILD_VIDEO_TARGET_SEC = 25
        out_dir = folder / "output"
        try:
            res = bv.build_video(_car(), photos[:6], output_dir=out_dir)
        finally:
            bv.ASSETS_MUSIC_DIR = real_music
        streams = _streams(res.path)
        results.append(_check("сборка без записи и музыки: output/<lot_id>.mp4 = heydealer_TEST0001.mp4", res.path == out_dir / "heydealer_TEST0001.mp4" and res.path.is_file()))
        results.append(_check("1080x1920, yuv420p, длина 20-30 с (25), звука нет, предупреждение про отсутствие звука",
                              streams["video"] and "1080,1920,yuv420p" in streams["video"][0] and 20 <= res.duration <= 30 and not streams["audio"]
                              and any("без звука" in w for w in res.warnings)))
        # фото целиком и наезд: рамка видна на всех кадрах; ширина растёт от ~97% до 100%
        first, last = _frame(res.path, 0.1, folder), _frame(res.path, res.cut_points[0] - 0.1, folder)
        left0, left1 = _left_border_x(first), _left_border_x(last)
        results.append(_check(f"фото не обрезается: рамка видна на первом и последнем кадре фото (левый край x={left0} и x={left1})", left0 >= 0 and left1 >= 0))
        width0, width1 = 1080 - 2 * left0, 1080 - 2 * left1
        zoom = width1 / width0 - 1
        results.append(_check(f"наезд лёгкий: ширина фото {width0}->{width1} px, рост {zoom:.1%} (не больше 5%, не меньше 2%)", 0.02 <= zoom <= 0.05 and width1 <= 1080))
        results.append(_check("порядок и смены кадров без записи — равные доли 25 с / 6 фото",
                              res.cut_source == "equal" and not res.needs_confirmation and len(res.cut_points) == 5))
        sample = [(_frame(res.path, (a + b) / 2, folder).getpixel((540, 960)), COLORS[i]) for i, (a, b) in enumerate(zip([0.0] + res.cut_points, res.cut_points + [res.duration]))]
        results.append(_check("все 6 фото показаны по порядку (цвет в центре кадра совпадает)", all(_close(px, c) for px, c in sample)))

        # ── реальная сборка 2: без записи, с музыкой ─────────────────
        music_dir = folder / "music"
        music_dir.mkdir()
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=330:d=8", "-ac", "2",
                        str(music_dir / "тест.mp3")], check=True, capture_output=True)
        bv.ASSETS_MUSIC_DIR = music_dir
        config.BUILD_VIDEO_TARGET_SEC = 20
        try:
            res2 = bv.build_video(_car(), photos[:7], output_dir=folder / "output2")
        finally:
            bv.ASSETS_MUSIC_DIR = real_music
            config.BUILD_VIDEO_TARGET_SEC = real_target
        streams2 = _streams(res2.path)
        results.append(_check("с музыкой и без записи: звук есть (музыка тест.mp3), длина 20 с (по нижней границе), музыка зациклена на всю длину",
                              streams2["audio"] and res2.music_track == "тест.mp3" and abs(res2.duration - 20.0) < 0.3))
        results.append(_check(f"музыка тихая: {res2.loudness_lufs} LUFS (цель −26, допуск ±3)", res2.loudness_lufs is not None and -29 <= res2.loudness_lufs <= -23))

        # ── реальная сборка 3: с голосовой записью, заданные секунды ─
        voice = folder / "voice.wav"
        _make_voice(voice, 14.0)
        bv.ASSETS_MUSIC_DIR = empty_music
        try:
            cuts_wanted = [2.5, 5.0, 7.5, 10.0, 12.0]
            res3 = bv.build_video(_car(), photos[:6], voice=voice, cuts=cuts_wanted, output_dir=folder / "output3")
            res4 = bv.build_video(_car(), photos[:6], voice=voice, output_dir=folder / "output4")
        finally:
            bv.ASSETS_MUSIC_DIR = real_music
        results.append(_check(f"длина ролика равна длине записи (14,0 с): {res3.duration:.2f} с", abs(res3.duration - 14.0) < 0.15))
        results.append(_check("метод очистки голоса назван (DeepFilterNet или ffmpeg), музыки под голосом нет (папка пуста)",
                              ("DeepFilterNet" in res3.audio_method or "ffmpeg" in res3.audio_method) and res3.music_track is None))
        results.append(_check(f"громкость голоса ≈ −16 LUFS: {res3.loudness_lufs} (допуск ±2)", res3.loudness_lufs is not None and abs(res3.loudness_lufs + 16) <= 2))
        results.append(_check("заданные секунды: источник manual, подтверждение оператора не нужно", res3.cut_source == "manual" and res3.cut_points == cuts_wanted
                              and not res3.needs_confirmation))
        ok_cuts = True
        for k, cut in enumerate(cuts_wanted):
            before, after = _frame(res3.path, cut - 0.25, folder).getpixel((540, 960)), _frame(res3.path, cut + 0.25, folder).getpixel((540, 960))
            ok_cuts = ok_cuts and _close(before, COLORS[k]) and _close(after, COLORS[k + 1])
        results.append(_check("смены кадров происходят ровно в заданные секунды (кадр до и после каждой смены — нужные фото)", ok_cuts))
        results.append(_check(f"без заданных секунд: смены по паузам речи ({res4.cut_source}: {res4.cut_points}), оператору предлагается подтвердить",
                              res4.cut_source in ("pauses", "equal") and res4.needs_confirmation and len(res4.cut_points) == 5))
        results.append(_check("видео с записью: есть звуковая дорожка, формат тот же 1080x1920 yuv420p",
                              _streams(res3.path)["audio"] and "1080,1920,yuv420p" in _streams(res3.path)["video"][0]))

    print()
    if all(results):
        print("=== Все проверки пройдены ===")
    else:
        print("=== ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ — см. выше ===")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
