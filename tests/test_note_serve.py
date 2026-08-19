# -*- coding: utf-8 -*-
"""노트 오디오 서빙 + 노트 메타 API.

앱의 실제 녹음 파일에 의존하지 않는다. 임시 wav 바이트로 HTTP Range(206)를 확인한다.
"""
import http.client
import json
import os
import time
from urllib.parse import urlparse

import pytest

from src.api import Api
from src.note import serve


def _api():
    api = Api.__new__(Api)
    api.cfg = {}
    api._window = None
    return api


def _touch(path, content=b"x" * 256):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fp:
        fp.write(content)
    return path


def _http(url, method="GET", headers=None, timeout=3):
    """로컬 서버에 직접 요청. 시스템 프록시를 타지 않는다."""
    parsed = urlparse(url)
    last_err = None
    hdrs = headers or {}
    for _ in range(30):
        conn = http.client.HTTPConnection(
            parsed.hostname, parsed.port, timeout=timeout)
        try:
            conn.request(method, parsed.path or "/", headers=hdrs)
            resp = conn.getresponse()
            body = resp.read()
            out_headers = {k.lower(): v for k, v in resp.getheaders()}
            status = resp.status
            conn.close()
            return status, out_headers, body
        except (ConnectionRefusedError, OSError, http.client.HTTPException) as exc:
            last_err = exc
            try:
                conn.close()
            except Exception:
                pass
            time.sleep(0.05)
    raise last_err


@pytest.fixture(autouse=True)
def _stop_audio_server():
    yield
    serve.shutdown()


def test_register_same_path_same_token(tmp_path):
    path = _touch(str(tmp_path / "a.wav"))
    t1 = serve.register(path)
    t2 = serve.register(path)
    assert t1
    assert t1 == t2
    slash = path.replace("\\", "/")
    t3 = serve.register(slash)
    assert t3 == t1
    url = serve.audio_url(t1)
    assert url.startswith("http://127.0.0.1:")
    assert f"/audio/{t1}" in url


def test_unregistered_token_is_404(tmp_path):
    path = _touch(str(tmp_path / "a.wav"))
    serve.register(path)
    info = serve.ensure_server()
    assert info["ok"] is True
    url = serve.audio_url("0" * 32)
    status, _headers, _body = _http(url)
    assert status == 404


def test_range_request_returns_206_and_content_range(tmp_path):
    payload = bytes(range(256))
    path = _touch(str(tmp_path / "clip.wav"), payload)
    token = serve.register(path)
    info = serve.ensure_server()
    assert info["ok"] is True
    url = serve.audio_url(token)

    status, headers, body = _http(url, headers={"Range": "bytes=0-10"})
    assert status == 206, f"Range 응답이 206이 아님: {status} {headers}"
    cr = headers.get("content-range") or ""
    assert cr.lower().startswith("bytes "), cr
    assert "0-10/" in cr
    assert body == payload[:11]
    assert headers.get("accept-ranges", "").lower() == "bytes"

    full_status, _fh, full_body = _http(url)
    assert full_status in (200, 206)
    if full_status == 200:
        assert full_body == payload


def test_ensure_server_is_idempotent():
    a = serve.ensure_server()
    b = serve.ensure_server()
    assert a["ok"] is True
    assert b["ok"] is True
    assert a["base_url"] == b["base_url"]
    assert a["base_url"].startswith("http://127.0.0.1:")
    assert "0.0.0.0" not in a["base_url"]
    httpd = serve._httpd
    assert httpd is not None
    assert httpd.server_address[0] == "127.0.0.1"
    assert serve._thread is not None
    assert serve._thread.daemon is True


def test_unregistered_path_escape_does_not_serve(tmp_path):
    secret = _touch(str(tmp_path / "secret.wav"), b"secret" * 20)
    allowed = _touch(str(tmp_path / "ok.wav"), b"ok" * 40)
    token = serve.register(allowed)
    serve.ensure_server()
    # 토큰이 아닌 경로 조각은 라우트가 파일로 열지 않는다.
    url = serve.audio_url(os.path.basename(secret))
    status, _h, body = _http(url)
    assert status == 404
    assert not body.startswith(b"secret")
    ok_status, _oh, ok_body = _http(serve.audio_url(token))
    assert ok_status in (200, 206)
    assert ok_body.startswith(b"ok")


def test_shutdown_does_not_raise():
    serve.ensure_server()
    serve.shutdown()
    serve.shutdown()


def test_get_audio_url_without_session():
    api = _api()
    r = api.get_audio_url()
    json.dumps(r)
    assert r["ok"] is False
    assert "url" in r
    assert r.get("url") == ""
    assert "오디오" in (r.get("error") or "")


def test_get_audio_url_and_range_after_session(tmp_path):
    payload = b"RIFF" + (b"\x00" * 252)
    wav = _touch(str(tmp_path / "회의.wav"), payload)
    api = _api()
    api._stt_state()
    api._stt_put_session(
        segments=[{"start": 0.0, "end": 1.0, "text": "안녕", "speaker": "화자1"}],
        files=[{
            "ok": True, "name": "회의.wav", "path": wav,
            "duration_sec": 12.5, "segments": [],
        }],
        meta={"duration_sec": 12.5, "title": "회의", "memo": "", "keywords": []},
    )
    r = api.get_audio_url()
    json.dumps(r)
    assert r["ok"] is True
    assert r["url"].startswith("http://127.0.0.1:")
    assert "/audio/" in r["url"]
    assert r["duration_sec"] == 12.5
    status, headers, body = _http(r["url"], headers={"Range": "bytes=0-3"})
    assert status == 206, f"세션 오디오 Range 가 206이 아님: {status} {headers}"
    assert (headers.get("content-range") or "").lower().startswith("bytes ")
    assert body == payload[:4]


def test_note_meta_roundtrip_and_sidecar(tmp_path):
    api = _api()
    empty = api.get_note_meta()
    json.dumps(empty)
    assert empty["ok"] is True
    assert empty["title"] == ""
    assert empty["memo"] == ""
    assert empty["keywords"] == []
    assert empty["created_at"] == ""
    assert empty["duration_sec"] == 0.0

    wav = _touch(str(tmp_path / "착수.wav"))
    api._stt_state()
    api._stt_put_session(
        segments=[{"start": 0.0, "end": 1.0, "text": "시작", "speaker": "화자1"}],
        files=[{"ok": True, "name": "착수.wav", "path": wav, "duration_sec": 3.0}],
        meta={
            "duration_sec": 3.0, "title": "착수", "memo": "",
            "keywords": [], "created_at": "2026-08-20T10:06:00",
        },
    )
    u = api.update_note_meta({
        "title": "서울대행사 착수",
        "memo": "예산 확인",
        "keywords": ["SMK", "경북대", ""],
    })
    json.dumps(u)
    assert u["ok"] is True

    meta = api.get_note_meta()
    json.dumps(meta)
    assert meta["ok"] is True
    assert meta["title"] == "서울대행사 착수"
    assert meta["memo"] == "예산 확인"
    assert meta["keywords"] == ["SMK", "경북대"]
    assert meta["created_at"] == "2026-08-20T10:06:00"
    assert meta["duration_sec"] == 3.0

    saved = api.save_transcript({"folder": str(tmp_path), "stem": "착수"})
    assert saved["ok"] is True
    with open(saved["json_path"], encoding="utf-8") as f:
        body = json.load(f)
    assert body["meta"]["title"] == "서울대행사 착수"
    assert body["meta"]["memo"] == "예산 확인"
    assert body["meta"]["keywords"] == ["SMK", "경북대"]


def test_update_note_meta_rejects_non_dict():
    api = _api()
    r = api.update_note_meta("제목")
    json.dumps(r)
    assert r["ok"] is False
