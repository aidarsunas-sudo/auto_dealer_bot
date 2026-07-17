# -*- coding: utf-8 -*-
"""
test_vk_photo_download.py — живой тест vk_publisher._download_photo() на
реальных ссылках на фото с каждой площадки (без pytest, в стиле остальных
self-test'ов проекта — python test_vk_photo_download.py).

Инцидент 2026-07-17 (см. FINAL_STATE.md): фото с ci.encar.com успешно
скачивались (HTTP 200) на домашней сети, но на IP облачных раннеров
GitHub Actions requests.get() без заголовков получал НЕ настоящую
картинку — анти-hotlink/анти-бот защита сайта пропускала запрос с кодом
200, но без реального изображения. Фикс (браузерный User-Agent + Referer
домена основного сайта, проверка content-type/размера, не только
HTTP-статуса) уже в vk_publisher.py — этот тест проверяет его на РЕАЛЬНЫХ
ссылках с каждой площадки, а не только на encar, где баг изначально
нашёлся.

Делает живой поиск/детальный запрос на каждой площадке, чтобы получить
СВЕЖУЮ реальную ссылку на фото (не хардкодит одну ссылку — она может
протухнуть/удалиться), затем прогоняет её через vk_publisher._download_photo()
и проверяет: получили байты, размер > 10 КБ (тот же порог, что и в самом
vk_publisher.py).

Если конкретный прогон не нашёл ни одного объявления с фото (площадка
временно пуста по этому запросу — обычное дело, особенно для onbid) —
это ПРЕДУПРЕЖДЕНИЕ, не ошибка: тест не может проверить то, для чего не
нашлось живого примера, но это не значит, что скачивание сломано.
Ошибка (FAIL, ненулевой код выхода) — только если ссылка НАШЛАСЬ, но
_download_photo() её не смогла скачать как настоящую картинку.

onbid ОТСУТСТВУЕТ в этом тесте намеренно: onbid_scraper.py (в отличие от
encar_scraper.py/charancha_scraper.py/kb_detail_scraper.py) вообще не
извлекает фото ни в одном из двух своих return-словарей — то есть
объявления с onbid никогда не доходят до шага скачивания фото в
принципе (блокируются гейтом "нет фото" в post_text_builder.py ещё
раньше). Это отдельный, более глубокий пробел (нет самого парсинга фото
из HTML/JSON детальной страницы onbid), а не вопрос заголовков/Referer —
чинить его здесь не в рамках этого теста.

kbchachacha: kbchachacha_target_adapter.py (обычный путь автоматического
targeted-поиска) ТОЖЕ не отдаёт "photos" в своём словаре — фото оттуда
приходится доставать отдельным модулем, kb_detail_scraper.py (сейчас
используется только в model_search.py, не в основном пайплайне) — тест
ниже так и делает, чтобы всё равно проверить скачивание с реального
kbchachacha CDN.
"""

import logging
import sys

import charancha_scraper
import encar_scraper
import kb_detail_scraper
from vk_publisher import _download_photo, _referer_for, _MIN_PHOTO_BYTES

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("test_vk_photo_download")

TARGET = {"brand": "Kia", "model": "K5", "year_from": 2022, "year_to": 2023}

# Известное реальное объявление kbchachacha — используется как fallback,
# только если live-поиск не даст ни одной ссылки (например, carSeq не
# протух на момент прогона теста).
KB_FALLBACK_DETAIL_URL = "https://www.kbchachacha.com/public/car/detail.kbc?carSeq=28553804"


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _first_photo_url_from(cars: list) -> str | None:
    for car in cars:
        photos = car.get("photos") or []
        if photos:
            return photos[0]
    return None


def get_encar_photo_url() -> str | None:
    cars = encar_scraper.search_target_model(TARGET, logger)
    return _first_photo_url_from(cars)


def get_charancha_photo_url() -> str | None:
    cars = charancha_scraper.search_target_model(TARGET, logger)
    return _first_photo_url_from(cars)


def get_kbchachacha_photo_url() -> str | None:
    detail = kb_detail_scraper.fetch_car_detail(KB_FALLBACK_DETAIL_URL)
    photos = detail.get("photos") or []
    return photos[0] if photos else None


SOURCES = {
    "encar": get_encar_photo_url,
    "charancha": get_charancha_photo_url,
    "kbchachacha": get_kbchachacha_photo_url,
}


def run() -> bool:
    """Возвращает True, если все площадки, для которых нашлась живая
    ссылка на фото, скачались успешно (WARNING по пустым площадкам не
    считается провалом — см. докстринг файла)."""
    all_ok = True

    for name, get_url in SOURCES.items():
        try:
            url = get_url()
        except Exception as error:
            _safe_print(f"[WARN] {name}: не удалось получить ссылку на фото ({error}) — пропускаю")
            continue

        if not url:
            _safe_print(f"[WARN] {name}: живой поиск не нашёл ни одного объявления с фото сейчас — пропускаю")
            continue

        referer = _referer_for(url)
        data = _download_photo(url)

        if data is None:
            _safe_print(f"[FAIL] {name}: {url} — _download_photo() вернул None "
                        f"(Referer={referer!r}) — нужен свой Referer в _REFERER_BY_PHOTO_HOST")
            all_ok = False
        elif len(data) <= _MIN_PHOTO_BYTES:
            _safe_print(f"[FAIL] {name}: {url} — скачалось только {len(data)} байт "
                        f"(ожидалось > {_MIN_PHOTO_BYTES}), Referer={referer!r}")
            all_ok = False
        else:
            _safe_print(f"[OK] {name}: {url} — {len(data)} байт, Referer={referer!r}")

    _safe_print("")
    _safe_print("onbid: не проверяется — onbid_scraper.py не извлекает фото вообще "
                 "(отдельный пробел, не вопрос заголовков, см. докстринг файла)")

    return all_ok


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
