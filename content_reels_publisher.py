# -*- coding: utf-8 -*-
"""
content_reels_publisher.py — ПОЛНОСТЬЮ ОТДЕЛЬНОЕ приложение: монтаж рилсов из
папки с отснятым материалом и публикация в СВОИ каналы.

Не импортирует ничего из main_pipeline.py, agent_publisher.py и
vk_publisher.py и не трогает их состояние (published_log.json,
yandex_queue.json, rejected_posts.log). Отправка в Telegram и VK написана
здесь заново, на своих токенах — чтобы контентный канал нельзя было
случайно перепутать с лидогенерирующим.

Единственная общая часть с проектом — shorts_generator.generate_shorts()
(монтаж), как и требовалось: переписывать ffmpeg-конвейер второй раз смысла
нет. Он, в свою очередь, тянет config/video_generator/tts_generator — это
неизбежно и на каналы не влияет.

ТОКЕНЫ. Имена переменных ниже — те, что были заказаны, но значения читаются
из окружения (.env), а не вписаны в файл: .py лежит в git, .env в
.gitignore. Впиши в .env:
    CONTENT_TELEGRAM_TOKEN=123456:AA...
    CONTENT_TELEGRAM_CHANNEL=@my_content_channel
    CONTENT_VK_TOKEN=vk1.a....
    CONTENT_VK_GROUP_ID=123456789
Пока переменные пустые, инструмент работает, но публикацию пропускает с
явным сообщением — монтаж при этом идёт как обычно.

ПУБЛИКАЦИЯ ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНА: run() работает в dry_run, пока явно не
передан dry_run=False. Прогон идёт по всей папке, публикация в канал
необратима — молчаливая отправка десятка рилсов не должна быть поведением
по умолчанию.

СОДЕРЖАНИЕ РОЛИКОВ. Рилсы этого инструмента не привязаны к объявлениям: на
экран не выводится ни цена, ни год, ни пробег (см. _neutral_fields()), в
озвучке — только пресет из VIDEO_TYPES (таблица ниже в этом же файле),
заданный вручную текст или FALLBACK_VOICEOVER. Пустые факты в Claude не
отдаются никогда: на пустом промпте он
додумает характеристики, которых у нас нет. Поэтому валидировать здесь
нечего — «железные правила» из CLAUDE.md (пробег/цена/год) относятся к
постам о КОНКРЕТНОМ объявлении, а этот инструмент таких утверждений не
делает. Если понадобится привязать рилс к лоту — гейт
validate_post_before_publish() должен встать в montage_video() ДО вызова
generate_shorts(), как это сделано в shorts_integrator.py.

Запуск:
    python content_reels_publisher.py                 # dry-run, 1 ролик
    python content_reels_publisher.py --publish       # в свои каналы
    python content_reels_publisher.py --limit 3 --publish
"""

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import requests
from dotenv import load_dotenv

from shorts_generator import generate_shorts

load_dotenv()

# ─────────────────────────── параметры подключения ───────────────────────────
# Значения — из .env (файл в .gitignore). В сам .py токены не вписывать:
# он под git, а секреты по правилам проекта живут только в .env и GitHub Secrets.
CONTENT_TELEGRAM_TOKEN = os.getenv("CONTENT_TELEGRAM_TOKEN", "")
CONTENT_TELEGRAM_CHANNEL = os.getenv("CONTENT_TELEGRAM_CHANNEL", "")
CONTENT_VK_TOKEN = os.getenv("CONTENT_VK_TOKEN", "")
CONTENT_VK_GROUP_ID = os.getenv("CONTENT_VK_GROUP_ID", "")

# ─────────────────────────── прочие настройки ────────────────────────────────
SOURCE_DIR = Path(r"C:\Users\Xiaomi\ВИДЕО для КОНТЕНТА")
OUTPUT_DIR = Path(r"C:\Users\Xiaomi\auto_dealer_bot\output_reels")
LOG_PATH = Path(__file__).resolve().parent / "content_reels.log"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".m4v", ".mkv", ".webm")
DEFAULT_CAPTION = "Автомобили из Кореи под заказ — Pick+ Auto, Казань."

# Готовая озвучка по типу кадров. Тип определяется ключевыми словами В ИМЕНИ
# ФАЙЛА, поэтому исходники надо называть осмысленно ("салон_1.mov",
# "трасса.mov"); у IMG_7270.MOV не совпадёт ничего и возьмётся FALLBACK.
# Копия таблицы из shorts_integrator.VIDEO_TYPES — намеренно: этот инструмент
# автономен, единственный его импорт из проекта — shorts_generator.
VIDEO_TYPES = {
    "comfort": {
        "keywords": ["салон", "сидения", "сиденья", "багажник", "интерьер"],
        "voiceover": "Потрясающий комфорт. Просторный салон, мягкие сидения, "
                     "просторный багажник. Путешествуй с удовольствием!",
    },
    "dynamics": {
        "keywords": ["разгон", "скорость", "дорога", "трасса"],
        "voiceover": "Мощность и динамика. Острые ощущения на каждом повороте. "
                     "Почувствуй настоящую свободу!",
    },
    "quality": {
        "keywords": ["детали", "крыша", "кузов", "капот"],
        # Без упоминания страны: поток проекта — корейские марки, и
        # "немецкое качество" было бы прямой ложью в озвучке.
        "voiceover": "Каждая деталь идеальна. Надежность на долгие годы.",
    },
    "lifestyle": {
        "keywords": ["закат", "пейзаж", "путешествие", "город"],
        "voiceover": "Новое поступление в каталог! Найди свой стиль жизни. "
                     "Заходи в @auto_generals!",
    },
}

# Если ни один пресет не подошёл. Пустой текст отдавать нельзя: generate_shorts
# на пустом voiceover_text сам идёт к Claude, а на пустых фактах Claude
# додумает характеристики, которых у нас нет. Эта строка не утверждает ничего
# о конкретной машине, поэтому безопасна для любого кадра.
FALLBACK_VOICEOVER = "Автомобили из Кореи под заказ. Пик плюс Авто, Казань."

TELEGRAM_API = "https://api.telegram.org"
VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"
VK_VIDEO_NAME_MAX = 60

logger = logging.getLogger("content_reels")


def setup_logging(verbose: bool = True) -> None:
    """Свой файл лога (content_reels.log) + консоль. Формат текстовый, как у
    main_pipeline.log — журнал читают глазами, не парсят."""
    handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


class ContentReelsPublisher:
    """Монтаж рилсов из папки и публикация в собственные Telegram/VK."""

    def __init__(self, video_folder: Union[str, Path] = SOURCE_DIR,
                 output_folder: Union[str, Path] = OUTPUT_DIR,
                 caption: str = DEFAULT_CAPTION):
        self.video_folder = Path(video_folder)
        self.output_folder = Path(output_folder)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.caption = caption

    # ─────────────────────────── исходники ────────────────────────────────

    def get_videos(self) -> list:
        """Видеофайлы из папки, по имени. Регистр расширения не важен —
        iPhone пишет .MOV заглавными."""
        if not self.video_folder.exists():
            logger.error(f"папка не найдена: {self.video_folder}")
            return []
        files = sorted(p for p in self.video_folder.iterdir()
                       if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
        logger.info(f"найдено клипов: {len(files)} в {self.video_folder}")
        return files

    # ─────────────────────────── монтаж ───────────────────────────────────

    @staticmethod
    def _neutral_fields(title: str = "") -> dict:
        """Поля для рилса без привязки к объявлению: только заголовок.
        Ни цены, ни года, ни пробега — на экран они не попадут."""
        return {
            "brand": "", "model": "", "title": title, "year": "",
            "engine_display": "", "mileage_km": "", "transmission": "",
            "price_label": "", "total_rub": "", "car_id": "",
        }

    @staticmethod
    def classify_clip(clip: Path) -> Optional[str]:
        """Тип кадров по ключевым словам в имени файла, либо None."""
        name = clip.stem.lower()
        for video_type, spec in VIDEO_TYPES.items():
            if any(keyword in name for keyword in spec["keywords"]):
                return video_type
        return None

    def voiceover_for(self, clip: Path) -> str:
        """Текст озвучки под клип. Никогда не возвращает пустую строку — иначе
        generate_shorts пойдёт генерировать текст через Claude на пустых
        фактах (см. FALLBACK_VOICEOVER)."""
        video_type = self.classify_clip(clip)
        if video_type:
            logger.info(f"{clip.name}: озвучка по пресету '{video_type}'")
            return VIDEO_TYPES[video_type]["voiceover"]
        logger.info(f"{clip.name}: тип кадров не распознан — нейтральная озвучка")
        return FALLBACK_VOICEOVER

    def montage_video(self, video_path: Union[str, Path],
                      voiceover: Optional[str] = None) -> Optional[Path]:
        """
        Собирает вертикальный рилс из одного клипа через shorts_generator.

        Args:
            video_path: исходный клип.
            voiceover: готовый текст озвучки. Не задан — берётся пресет по
                имени файла (VIDEO_TYPES); не подошёл ни один —
                FALLBACK_VOICEOVER.

        Returns:
            Path к .mp4, либо None (нет ffmpeg, битый клип).
        """
        clip = Path(video_path)
        text = voiceover or self.voiceover_for(clip)

        output_path = self.output_folder / f"reel_{clip.stem}.mp4"
        logger.info(f"монтирую {clip.name} -> {output_path.name}")

        try:
            return generate_shorts(
                car_data={},
                videos=[clip],
                output_path=output_path,
                fields=self._neutral_fields(self.caption[:18]),
                voiceover_text=text,
            )
        except Exception as error:
            logger.error(f"монтаж {clip.name} не удался ({error})", exc_info=True)
            return None

    # ─────────────────────────── Telegram (свой токен) ────────────────────

    def publish_to_telegram(self, video_path: Path, dry_run: bool = True) -> bool:
        """sendVideo в СВОЙ канал (CONTENT_TELEGRAM_CHANNEL) СВОИМ токеном."""
        if dry_run:
            print("=" * 50)
            print(f"TELEGRAM ТЕСТОВЫЙ РЕЖИМ (ничего не отправлено)")
            print(f"Канал: {CONTENT_TELEGRAM_CHANNEL or '(не задан)'} | Видео: {video_path.name}")
            print("=" * 50)
            return True

        if not CONTENT_TELEGRAM_TOKEN or not CONTENT_TELEGRAM_CHANNEL:
            logger.warning("Telegram: CONTENT_TELEGRAM_TOKEN/CONTENT_TELEGRAM_CHANNEL не заданы в .env "
                           "— публикация пропущена")
            return False

        url = f"{TELEGRAM_API}/bot{CONTENT_TELEGRAM_TOKEN}/sendVideo"
        try:
            with open(video_path, "rb") as video_file:
                response = requests.post(
                    url,
                    data={"chat_id": CONTENT_TELEGRAM_CHANNEL, "caption": self.caption,
                          "supports_streaming": True},
                    files={"video": video_file},
                    timeout=120,
                )
        except (requests.RequestException, OSError) as error:
            logger.error(f"Telegram: не удалось отправить видео ({error})")
            return False

        if response.ok:
            logger.info(f"Telegram: рилс опубликован в {CONTENT_TELEGRAM_CHANNEL}")
            return True
        # В тексте ошибки Telegram токена нет — сам URL не логируем.
        logger.error(f"Telegram: ошибка {response.status_code} — {response.text[:300]}")
        return False

    # ─────────────────────────── VK (свой токен) ──────────────────────────

    def _vk_call(self, method: str, params: dict, timeout: int = 30) -> Optional[dict]:
        """Вызов VK API своим токеном. Возвращает содержимое response или None."""
        try:
            result = requests.post(
                f"{VK_API_BASE}/{method}",
                data={**params, "access_token": CONTENT_VK_TOKEN, "v": VK_API_VERSION},
                timeout=timeout,
            ).json()
        except (requests.RequestException, ValueError) as error:
            logger.error(f"VK: {method} — сетевая ошибка ({error})")
            return None

        if "error" in result:
            error = result["error"]
            logger.error(f"VK: {method} — ошибка {error.get('error_code')}: {error.get('error_msg')}")
            return None
        return result.get("response")

    def publish_to_vk(self, video_path: Path, dry_run: bool = True) -> bool:
        """video.save -> загрузка файла -> wall.post в СВОЮ группу."""
        if dry_run:
            print("=" * 50)
            print(f"VK ТЕСТОВЫЙ РЕЖИМ (ничего не отправлено)")
            print(f"Группа: {CONTENT_VK_GROUP_ID or '(не задана)'} | Видео: {video_path.name}")
            print("=" * 50)
            return True

        if not CONTENT_VK_TOKEN or not CONTENT_VK_GROUP_ID:
            logger.warning("VK: CONTENT_VK_TOKEN/CONTENT_VK_GROUP_ID не заданы в .env — публикация пропущена")
            return False

        try:
            group_id = abs(int(CONTENT_VK_GROUP_ID))
        except (TypeError, ValueError):
            logger.error(f"VK: CONTENT_VK_GROUP_ID не число ({CONTENT_VK_GROUP_ID!r})")
            return False

        saved = self._vk_call("video.save", {
            "group_id": group_id,
            "name": (self.caption[:VK_VIDEO_NAME_MAX] or video_path.stem),
            "description": self.caption,
            "wallpost": 0,
        })
        if not saved or not saved.get("upload_url"):
            logger.error("VK: video.save не вернул upload_url")
            return False

        try:
            with open(video_path, "rb") as f:
                upload = requests.post(
                    saved["upload_url"],
                    files={"video_file": (video_path.name, f, "video/mp4")},
                    timeout=300,  # видео тяжелее фото — короткий таймаут не годится
                )
            upload.raise_for_status()
        except (requests.RequestException, OSError) as error:
            logger.error(f"VK: не удалось загрузить видео ({error})")
            return False

        owner_id, video_id = saved.get("owner_id"), saved.get("video_id")
        if owner_id is None or video_id is None:
            logger.error(f"VK: video.save не вернул owner_id/video_id ({saved!r})")
            return False

        posted = self._vk_call("wall.post", {
            "owner_id": -group_id,
            "message": self.caption,
            "from_group": 1,
            "attachments": f"video{owner_id}_{video_id}",
        })
        if posted is None:
            logger.error("VK: wall.post не прошёл — видео загружено, но пост не создан")
            return False

        logger.info(f"VK: рилс опубликован в группе {group_id} (post_id={posted.get('post_id')})")
        return True

    # ─────────────────────────── цикл ─────────────────────────────────────

    def run(self, dry_run: bool = True, limit: Optional[int] = 1,
            voiceover: Optional[str] = None) -> list:
        """
        Полный цикл: для каждого клипа монтаж -> Telegram -> VK -> лог.

        Args:
            dry_run: по умолчанию True — рилсы соберутся, в каналы ничего не
                уйдёт. Для реальной публикации передать False явно.
            limit: сколько клипов взять (None — все).
            voiceover: единый текст озвучки на прогон.

        Returns:
            Список словарей с результатом по каждому клипу.
        """
        clips = self.get_videos()
        if not clips:
            return []
        if limit is not None:
            clips = clips[:limit]

        if dry_run:
            logger.info(f"DRY-RUN: {len(clips)} рилс(ов) соберутся, в каналы ничего не уйдёт "
                        f"(--publish / dry_run=False для реальной публикации)")

        results = []
        for clip in clips:
            record = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "source_clip": clip.name,
                "video": None,
                "telegram": False,
                "vk": False,
                "error": None,
            }

            video = self.montage_video(clip, voiceover=voiceover)
            if video is None:
                record["error"] = "монтаж не удался (см. лог выше)"
                logger.error(f"{clip.name}: рилс не собран — публикация пропущена")
            else:
                record["video"] = str(video)
                record["telegram"] = self.publish_to_telegram(video, dry_run=dry_run)
                record["vk"] = self.publish_to_vk(video, dry_run=dry_run)
                logger.info(f"{clip.name}: TG={'ok' if record['telegram'] else '-'} "
                            f"VK={'ok' if record['vk'] else '-'}")

            results.append(record)

        return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Монтаж рилсов из папки и публикация в свои Telegram/VK")
    parser.add_argument("--source", default=str(SOURCE_DIR), help="папка с исходными клипами")
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="куда складывать готовые рилсы")
    parser.add_argument("--limit", type=int, default=1, help="сколько клипов взять (0 — все)")
    parser.add_argument("--caption", default=DEFAULT_CAPTION, help="подпись к посту")
    parser.add_argument("--voiceover", default=None, help="готовый текст озвучки")
    parser.add_argument("--publish", action="store_true", help="публиковать по-настоящему (без флага — dry-run)")
    args = parser.parse_args()

    setup_logging()

    publisher = ContentReelsPublisher(args.source, args.output, args.caption)
    results = publisher.run(
        dry_run=not args.publish,
        limit=None if args.limit == 0 else args.limit,
        voiceover=args.voiceover,
    )
    if not results:
        print("Нечего обрабатывать.")
        return 1

    print("\n" + "=" * 50)
    for record in results:
        status = record["error"] or (
            f"TG={'ok' if record['telegram'] else '-'} VK={'ok' if record['vk'] else '-'}"
        )
        print(f"{record['source_clip']} -> {record['video'] or '(не собран)'} | {status}")
    print(f"\nЛог: {LOG_PATH.name}")
    return 0 if any(r["video"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
