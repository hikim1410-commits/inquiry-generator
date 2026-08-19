# -*- coding: utf-8 -*-
"""로컬 오디오 HTTP 서빙 — webview <audio> 가 원본 파일을 재생·seek 하게 한다.

file:// 직접 참조와 pywebview 내장 http_server 는 배제한다(교차 드라이브·프로토콜
결함). bottle.static_file 이 Range 헤더를 파싱해 206 Partial Content 를 돌려준다.
새 파이썬 의존성은 쓰지 않는다 — bottle 은 pywebview 의 기존 의존성이다.

공개 계약: register / audio_url / ensure_server / shutdown.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import threading
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from src.logutil import log as _log

_AUDIO_MIME = {
    ".wav": "audio/wav",
    ".wave": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp4": "audio/mp4",
    ".mov": "video/quicktime",
    ".webm": "audio/webm",
}

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range",
    "Access-Control-Expose-Headers": (
        "Accept-Ranges, Content-Length, Content-Range, Content-Type"),
}

_lock = threading.RLock()
_tokens = {}          # token -> {folder, name, path}
_path_to_token = {}   # normcase(abspath) -> token
_app = None
_httpd = None
_thread = None
_port = None
_base_url = ""
_atexit_done = False


class _LoopbackServer(WSGIServer):
    allow_reuse_address = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 — WSGI 훅 시그니처
        return


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _token_for(norm: str) -> str:
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def _lookup(token: str):
    with _lock:
        rec = _tokens.get(token)
        return dict(rec) if rec else None


def _import_bottle():
    try:
        import bottle
        from bottle import Bottle, HTTPError, HTTPResponse, static_file
        return bottle, Bottle, HTTPError, HTTPResponse, static_file
    except ImportError:
        return None, None, None, None, None


def _build_app():
    bottle, Bottle, HTTPError, HTTPResponse, static_file = _import_bottle()
    if Bottle is None:
        return None
    app = Bottle()

    @app.hook("after_request")
    def _add_cors():
        for key, value in _CORS.items():
            bottle.response.set_header(key, value)

    @app.route("/audio/<token>", method=["OPTIONS"])
    def _options(token):
        return HTTPResponse(status=204)

    @app.route("/audio/<token>", method=["GET", "HEAD"])
    def _audio(token):
        rec = _lookup(token)
        if not rec:
            raise HTTPError(404, "Not found")
        folder = rec["folder"]
        name = rec["name"]
        expected = rec["path"]
        actual = os.path.abspath(os.path.join(folder, name))
        if _norm_path(actual) != _norm_path(expected):
            raise HTTPError(404, "Not found")
        if not os.path.isfile(actual):
            raise HTTPError(404, "Not found")
        ext = os.path.splitext(name)[1].lower()
        mime = _AUDIO_MIME.get(ext, True)
        return static_file(name, root=folder, mimetype=mime, headers=dict(_CORS))

    return app


def register(path: str) -> str:
    """오디오 절대경로를 등록하고 토큰(문자열)을 돌려준다. 같은 경로면 같은 토큰."""
    raw = "" if path is None else str(path).strip()
    if not raw:
        return ""
    abs_path = os.path.abspath(raw)
    key = _norm_path(abs_path)
    with _lock:
        token = _path_to_token.get(key)
        if token:
            return token
        token = _token_for(key)
        folder, name = os.path.split(abs_path)
        _tokens[token] = {"folder": folder, "name": name, "path": abs_path}
        _path_to_token[key] = token
        return token


def audio_url(token: str) -> str:
    """'http://127.0.0.1:<port>/audio/<token>' 형태의 URL."""
    info = ensure_server()
    base = (info.get("base_url") or "").rstrip("/")
    if not base:
        with _lock:
            base = (_base_url or "http://127.0.0.1:0").rstrip("/")
    tok = "" if token is None else str(token)
    return f"{base}/audio/{tok}"


def ensure_server() -> dict:
    """{"ok", "base_url", "error"} — 서버가 없으면 띄우고, 있으면 그대로 둔다(멱등)."""
    global _app, _httpd, _thread, _port, _base_url, _atexit_done
    with _lock:
        if _httpd is not None and _thread is not None and _thread.is_alive():
            return {"ok": True, "base_url": _base_url, "error": ""}
        bottle, Bottle, _he, _hr, _sf = _import_bottle()
        if Bottle is None:
            return {
                "ok": False, "base_url": "",
                "error": "오디오 서버를 시작하지 못했습니다.",
            }
        try:
            if _app is None:
                _app = _build_app()
            if _app is None:
                return {
                    "ok": False, "base_url": "",
                    "error": "오디오 서버를 시작하지 못했습니다.",
                }
            httpd = make_server(
                "127.0.0.1", 0, _app,
                server_class=_LoopbackServer,
                handler_class=_QuietHandler,
            )
            host, port = httpd.server_address[:2]
            if host not in ("127.0.0.1", "localhost"):
                try:
                    httpd.server_close()
                except Exception:
                    pass
                return {
                    "ok": False, "base_url": "",
                    "error": "오디오 서버를 시작하지 못했습니다.",
                }
            thread = threading.Thread(
                target=_serve_forever, args=(httpd,),
                name="note-audio", daemon=True)
            _httpd = httpd
            _thread = thread
            _port = int(port)
            _base_url = f"http://127.0.0.1:{_port}"
            if not _atexit_done:
                atexit.register(shutdown)
                _atexit_done = True
            thread.start()
            _log(f"노트 오디오 서버 {_base_url}")
            return {"ok": True, "base_url": _base_url, "error": ""}
        except Exception as exc:
            _httpd = None
            _thread = None
            _port = None
            _base_url = ""
            _log(f"노트 오디오 서버 시작 실패: {exc}")
            return {
                "ok": False, "base_url": "",
                "error": "오디오 서버를 시작하지 못했습니다.",
            }


def _serve_forever(httpd):
    try:
        httpd.serve_forever()
    except Exception as exc:
        _log(f"노트 오디오 서버 종료: {exc}")


def shutdown() -> None:
    """앱 종료 시 정리. 실패해도 예외를 밖으로 던지지 않는다."""
    global _httpd, _thread, _port, _base_url
    httpd = None
    thread = None
    try:
        with _lock:
            httpd = _httpd
            thread = _thread
            _httpd = None
            _thread = None
            _port = None
            _base_url = ""
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive() and (
                thread is not threading.current_thread()):
            thread.join(timeout=2.0)
    except Exception:
        pass
