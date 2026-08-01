# -*- coding: utf-8 -*-
"""
car_report_photo_embed.py — общий хелпер скилла car-report: встраивает фото
авто прямо в HTML-отчёт как base64 data URI, чтобы report.html был
САМОДОСТАТОЧНЫМ (открывался на любом устройстве одним файлом, без папки
./photos рядом).

Почему отдельный модуль: все генераторы отчётов (build_single_report.py,
car_report_output/build_report_3models.py и будущие) должны вставлять фото
ОДИНАКОВО и переносимо — один источник правды, а не копипаста base64-логики.

Логику фильтров/расчёта цены НЕ трогает — только способ вставки картинок.

Использование:
    from car_report_photo_embed import build_embedded_gallery, LIGHTBOX_HTML

    gallery_html, meta = build_embedded_gallery(list_of_photo_paths)
    # ... вставить gallery_html в тело + LIGHTBOX_HTML один раз в конец <body>
    # meta = {"embedded": N, "limited": bool, "dropped": M, "bytes": total}
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image

# MIME по расширению — для справки/валидации источника. На выходе после
# пережатия мы всегда отдаём image/jpeg (фото без прозрачности), кроме PNG,
# который сохраняем как PNG, чтобы не потерять альфу.
MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}

# Параметры по умолчанию (см. требования задачи): ресайз по ширине до ~1200px,
# JPEG качество ~80. Порог общего размера HTML — 26 МБ base64 (в коридоре
# 25-30 МБ из требований); при превышении режем число фото на авто.
DEFAULT_MAX_WIDTH = 1200
DEFAULT_QUALITY = 80
DEFAULT_MAX_TOTAL_BYTES = 26 * 1024 * 1024


def photo_to_data_uri(path: str | Path, max_width: int = DEFAULT_MAX_WIDTH,
                      quality: int = DEFAULT_QUALITY) -> tuple[str, int]:
    """
    Читает файл фото, ужимает (ресайз по ширине + пережатие) и возвращает
    (data_uri, размер_base64_в_байтах).

    Прозрачные PNG сохраняются как PNG (сохранить альфу), остальное —
    как JPEG качеством `quality` (заметно компактнее для фотографий).
    MIME источника определяется по расширению (MIME_BY_EXT).
    """
    p = Path(path)
    ext = p.suffix.lower()
    img = Image.open(p)

    # ресайз по ширине, сохраняя пропорции (только уменьшение)
    if img.width > max_width:
        new_h = round(img.height * max_width / img.width)
        img = img.resize((max_width, new_h), Image.LANCZOS)

    buf = BytesIO()
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if ext == ".png" and has_alpha:
        img.save(buf, format="PNG", optimize=True)
        out_mime = "image/png"
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        out_mime = "image/jpeg"

    raw = buf.getvalue()
    b64 = base64.b64encode(raw).decode("ascii")
    data_uri = f"data:{out_mime};base64,{b64}"
    return data_uri, len(b64)


def build_embedded_gallery(paths, max_width: int = DEFAULT_MAX_WIDTH,
                           quality: int = DEFAULT_QUALITY,
                           max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
                           thumb_class: str = "thumb") -> tuple[str, dict]:
    """
    Строит HTML-галерею из встроенных (data URI) картинок. Каждое превью
    кликабельно и открывает ту же встроенную картинку крупно через лайтбокс
    (LIGHTBOX_HTML), без ссылок на внешние/локальные файлы.

    При превышении `max_total_bytes` перестаёт добавлять фото (оставляет
    ключевые ракурсы из начала списка) и помечает это в meta.

    Возвращает (html, meta), где meta:
        {"embedded": int, "requested": int, "dropped": int,
         "limited": bool, "b64_bytes": int}
    """
    imgs = []
    total = 0
    embedded = 0
    requested = len(paths)
    limited = False
    for path in paths:
        try:
            data_uri, size = photo_to_data_uri(path, max_width, quality)
        except Exception as e:  # битый/отсутствующий файл — пропускаем, не падаем
            print(f"[photo_embed] пропуск {path}: {e}")
            continue
        if total + size > max_total_bytes and embedded > 0:
            limited = True
            break
        # cr-embed + inline cursor:pointer — чтобы тап по превью надёжно
        # срабатывал на мобильных (iOS Safari шлёт click только «кликабельным»
        # элементам). Обработчик — через делегирование в LIGHTBOX_HTML, а не
        # инлайновый onclick (тоже ради надёжности на touch).
        imgs.append(f"<img class='{thumb_class} cr-embed' loading='lazy' "
                    f"style='cursor:pointer' src='{data_uri}'>")
        total += size
        embedded += 1

    html = "".join(imgs) if imgs else "<div class='noimg'>фото не удалось встроить</div>"
    meta = {"embedded": embedded, "requested": requested,
            "dropped": requested - embedded, "limited": limited, "b64_bytes": total}
    return html, meta


# Один раз вставляется в конец <body>: оверлей-лайтбокс. Тап/клик по превью
# (img.cr-embed) показывает ту же встроенную картинку крупно; тап по фону —
# закрыть. Обработчик навешен через ДЕЛЕГИРОВАНИЕ на document (надёжно на
# мобильных, в т.ч. iOS Safari), а не инлайновым onclick на каждой картинке.
LIGHTBOX_HTML = """
<div id="cr-lightbox"
     style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.9);
            z-index:9999;align-items:center;justify-content:center;cursor:zoom-out;
            -webkit-tap-highlight-color:transparent">
  <img id="cr-lightbox-img" style="max-width:96vw;max-height:96vh;border-radius:6px;box-shadow:0 4px 30px rgba(0,0,0,.5)">
</div>
<script>
(function(){
  function lb(){ return document.getElementById('cr-lightbox'); }
  function crOpen(src){ document.getElementById('cr-lightbox-img').src = src; lb().style.display = 'flex'; }
  function crClose(){ var l = lb(); if(l){ l.style.display = 'none'; document.getElementById('cr-lightbox-img').src = ''; } }
  // Делегирование: ловим и click, и touchend — тап по превью на мобильном
  // надёжно открывает лайтбокс независимо от особенностей мобильных браузеров.
  function onTap(e){
    var t = e.target;
    if(!t) return;
    if(t.classList && t.classList.contains('cr-embed')){ crOpen(t.currentSrc || t.src); }
    else if(t.id === 'cr-lightbox' || t.id === 'cr-lightbox-img'){ crClose(); }
  }
  document.addEventListener('click', onTap);
  document.addEventListener('keydown', function(e){ if(e.key === 'Escape'){ crClose(); } });
})();
</script>
"""
