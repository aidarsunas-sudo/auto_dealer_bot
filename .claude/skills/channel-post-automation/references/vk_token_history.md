# VK-токен — хронология и безопасный смоук-тест

## Хронология (два независимых прогона к одному и тому же выводу)

**2026-07-06** — впервые найдено: простой community-токен (ключ доступа из
настроек сообщества) не умеет `photos.getWallUploadServer`/
`photos.saveWallPhoto` (`ошибка 27`). Задокументировано в `FINAL_STATE.md`
(тогда — раздел 5, «известное, вне рамок аудита»).

**2026-07-17** — решено переходом на user-токен (Kate Mobile, права
`photos,wall,groups`, позже добавлено `video`). Задокументировано в
`FINAL_STATE.md` (тогда — раздел 9). Подтверждено боевым `test_post.py`
(`post_id=18`).

**~2026-07-23…07-28** — личный аккаунт, с которого был выпущен этот
user-токен, заблокирован VK (`ошибка 5 — User authorization failed: user is
blocked` на фото, `ошибка 18 — User was deleted or banned` на видео) — по
неизвестной VK-стороне причине, не связанной с кодом. Публикация в VK встала.

**2026-07-28, попытка вернуться на community-токен** — воспроизвело ТОЧНО
ту же проблему 2026-07-06: смоук-тест новым community-токеном (даже с
`is_admin=1`, подтверждённым через `groups.getById`) —

```
photos.getWallUploadServer -> error 27: Group authorization failed: method is unavailable with group auth
video.save -> error 5: User authorization failed
```

Вывод: это не разовая случайность, а системное ограничение VK API для
этого класса токенов — community-токен НЕЛЬЗЯ использовать для медиа
независимо от прав/админства, точка.

**2026-07-28, новый user-токен** — смоук-тест зелёный
(`photos.getWallUploadServer: ok`, `video.save: ok`), боевой прогон
подтвердил фото-альбом + видеокарусель в Telegram и VK (`post_id=43`),
`wall.getById` подтвердил дату/текст/вложения без расхождений.

## Утечка токена и фикс (2026-07-28)

Во время смоук-теста произошла временная сетевая ошибка (DNS не резолвил
`api.vk.com` — из-за активного VPN-клиента на машине разработчика, не
связано с VK/кодом). Диагностический скрипт (одноразовый, не в
репозитории) использовал `requests.get(url, params={"access_token": ...})`
— библиотека `requests` вшивает `params` в URL, и полный токен попал в
текст `ConnectionError`, который был выведен в чат. Токен немедленно
отозван и заменён.

Продовый `vk_publisher.py` **никогда не делал так** — все вызовы VK API там
идут через `requests.post(url, data=...)` (токен в теле, не в URL). После
инцидента добавлено маскирование (`_mask_secret()`) как защита на будущее —
см. раздел «Безопасность токена» в `SKILL.md`.

## Безопасный сниппет смоук-теста (для повторного использования)

Никогда не печатать `str(exception)` целиком при сетевой ошибке — только имя
класса исключения. Никогда не использовать `requests.get(..., params=...)`
с `access_token`.

```python
import os, requests
from dotenv import load_dotenv
load_dotenv(override=True)
tok = os.getenv("VK_TOKEN", "")
group_id = abs(int(os.getenv("VK_OWNER_ID", "0")))

def safe_call(method, params):
    body = {**params, "access_token": tok, "v": "5.199"}
    try:
        r = requests.post(f"https://api.vk.com/method/{method}", data=body, timeout=15)
        data = r.json()
    except requests.RequestException as e:
        print(f"{method}: сетевая ошибка ({type(e).__name__})")  # НЕ str(e)!
        return
    if "error" in data:
        err = data["error"]
        print(f"{method}: ошибка {err.get('error_code')}: {err.get('error_msg')}")
    else:
        print(f"{method}: ok")

safe_call("photos.getWallUploadServer", {"group_id": group_id})
safe_call("video.save", {"group_id": group_id, "name": "smoke-test", "wallpost": 0})
```

Побочный эффект: `video.save` с `wallpost=0` создаёт реальную (но не
опубликованную) запись-заглушку в разделе «Видео» сообщества при каждом
вызове (файл не загружается, только резервируется slot) — можно удалить
вручную, не критично, но не злоупотреблять частыми повторами смоук-теста
без нужды.
