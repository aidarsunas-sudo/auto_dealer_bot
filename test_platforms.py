# -*- coding: utf-8 -*-
"""test_platforms.py — самотест platforms.py (python test_platforms.py). Без сети: HTTP подменяется."""

import os
import tempfile
from pathlib import Path

import platforms as pl


def _check(name, condition):
    print(f"{'OK  ' if condition else 'FAIL'} {name}")
    return bool(condition)


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, get_map=None, post_resp=None, post_error=None):
        self.get_map, self.post_resp, self.post_error = get_map or {}, post_resp, post_error
        self.posts, self.gets = [], []

    def get(self, url, **kw):
        self.gets.append(url)
        for suffix, resp in self.get_map.items():
            if url.endswith(suffix):
                return resp
        return Resp(404)

    def post(self, url, **kw):
        self.posts.append((url, kw))
        if self.post_error:
            raise self.post_error
        return self.post_resp


def run():
    results = []
    os.environ["UPLOAD_POST_API_KEY"] = "k"
    os.environ["UPLOAD_POST_USER"] = "pickplus"
    video = Path(tempfile.mkdtemp()) / "v.mp4"
    video.write_bytes(b"x" * 100)

    results.append(_check("parse: по умолчанию telegram", pl.parse_platforms(None) == ["telegram"]))
    results.append(_check("parse: порядок и дубли", pl.parse_platforms("telegram, instagram,instagram") == ["telegram", "instagram"]))
    try:
        pl.parse_platforms("tiktok"); bad = False
    except pl.PlatformError:
        bad = True
    results.append(_check("parse: неизвестная площадка -> ошибка", bad))

    profiles = {"profiles": [{"username": "pickplus", "social_accounts": {"instagram": {"x": 1}, "youtube": ""}}]}
    sess = FakeSession({"/uploadposts/me": Resp(200, {"plan": "free", "limit": 10, "used": 3}),
                        "/uploadposts/users": Resp(200, profiles)})
    st = pl.check_status(sess)
    results.append(_check("status: Instagram подключён, YouTube нет", st.connected == {"instagram": True, "youtube": False}))
    results.append(_check("status: остаток = лимит - использовано", st.remaining == 7))
    results.append(_check("status: проверка только GET (ничего не публикуется)", not sess.posts))

    plans = {p.channel: p for p in pl.plan_platforms(["telegram", "instagram", "youtube"], st)}
    results.append(_check("plan: telegram пропускается", "telegram" not in plans))
    results.append(_check("plan: instagram готов, youtube нет с причиной", plans["instagram"].ready and not plans["youtube"].ready
                          and "не подключён" in plans["youtube"].reason))
    empty = pl.UploadStatus(True, {"instagram": True, "youtube": True}, remaining=0)
    results.append(_check("plan: лимит исчерпан -> не готовы", not any(p.ready for p in pl.plan_platforms(["instagram", "youtube"], empty))))

    saved = os.environ.pop("UPLOAD_POST_API_KEY")
    nokey = pl.check_status(FakeSession())
    results.append(_check("status: нет ключа -> configured False, без обращений в сеть", not nokey.configured))
    results.append(_check("publish без ключа -> pending_manual", pl.publish_video("instagram", video, caption="c", session=FakeSession()).status == "pending_manual"))
    os.environ["UPLOAD_POST_API_KEY"] = saved

    down = pl.check_status(FakeSession(get_map={}))
    results.append(_check("status: 404 -> ошибка, площадки не готовы", all(not p.ready for p in pl.plan_platforms(["instagram"], down))))

    ok = FakeSession(post_resp=Resp(200, {"success": True, "results": {"instagram": {"url": "https://www.instagram.com/reel/ABC/"}}}))
    r = pl.publish_video("instagram", video, caption="подпись", session=ok)
    url, kw = ok.posts[0]
    results.append(_check("instagram: published + ссылка", r.status == "published" and r.url == "https://www.instagram.com/reel/ABC/"))
    results.append(_check("instagram: REELS и подпись, ключ в заголовке", kw["data"]["media_type"] == "REELS" and kw["data"]["instagram_title"] == "подпись"
                          and kw["headers"]["Authorization"].startswith("Apikey ") and url.endswith("/upload")))

    yt = FakeSession(post_resp=Resp(200, {"success": True}))
    r = pl.publish_video("youtube", video, caption="c", title="T #Shorts", description="D #Shorts", session=yt)
    results.append(_check("youtube: published, название и описание переданы", r.status == "published"
                          and yt.posts[0][1]["data"]["youtube_title"] == "T #Shorts" and yt.posts[0][1]["data"]["privacy_status"] == "public"))
    yt2 = FakeSession(post_resp=Resp(200, {"success": True}))
    pl.publish_video("youtube", video, caption="c", title="T", test_mode=True, session=yt2)
    results.append(_check("youtube: тест -> private", yt2.posts[0][1]["data"]["privacy_status"] == "private"))

    r429 = pl.publish_video("instagram", video, caption="c", session=FakeSession(post_resp=Resp(429)))
    results.append(_check("429 -> pending_manual (лимит)", r429.status == "pending_manual" and "лимит" in r429.reason))
    r500 = pl.publish_video("instagram", video, caption="c", session=FakeSession(post_resp=Resp(500)))
    results.append(_check("HTTP 500 -> pending_manual", r500.status == "pending_manual"))
    rex = pl.publish_video("instagram", video, caption="c", session=FakeSession(post_error=ConnectionError("x")))
    results.append(_check("сетевая ошибка -> pending_manual, без исключения", rex.status == "pending_manual" and "ConnectionError" in rex.reason))
    rf = pl.publish_video("instagram", video, caption="c", session=FakeSession(post_resp=Resp(200, {"success": False, "message": "bad"})))
    results.append(_check("success=false -> pending_manual с причиной", rf.status == "pending_manual" and rf.reason == "bad"))
    try:
        pl.publish_video("telegram", video, caption="c"); bad = False
    except pl.PlatformError:
        bad = True
    results.append(_check("telegram через upload-post запрещён", bad))

    title = pl.youtube_title("t", "Kia Ray Signature (The New Ray)", 2023)
    results.append(_check("youtube_title: #Shorts и <=100", title.endswith("#Shorts") and len(title) <= 100))
    long_title = pl.youtube_title("t", "A" * 200, 2023)
    results.append(_check("youtube_title: длинное режется до 100 с #Shorts", len(long_title) <= 100 and long_title.endswith("#Shorts")))
    results.append(_check("youtube_description: добавляет #Shorts", "#Shorts" in pl.youtube_description("текст")))
    results.append(_check("instagram_caption: лимит 2200", len(pl.instagram_caption("я" * 3000)) == 2200))

    bad_count = results.count(False)
    print(f"\ntest_platforms: {len(results) - bad_count} OK / {bad_count} FAIL")
    raise SystemExit(1 if bad_count else 0)


if __name__ == "__main__":
    run()
