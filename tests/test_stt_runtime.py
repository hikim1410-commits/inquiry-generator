# -*- coding: utf-8 -*-
"""src/stt/runtime.py — 실제 네트워크/엔진 없이 설치 상태·다운로드 계약을 검증한다."""
import errno
import io
import os
import tarfile
import urllib.error

import pytest

from src.stt import runtime


@pytest.fixture(autouse=True)
def _isolate_model_dir(tmp_path, monkeypatch):
    """테스트가 프로젝트 루트에 stt-models 를 만들지 않게 격리한다."""
    monkeypatch.setattr(
        runtime, "data_path",
        lambda *parts: str(os.path.join(str(tmp_path), *parts)))


def _seg_tar_bytes(payload=b"fake-seg-onnx"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        info = tarfile.TarInfo(name=f"{runtime.SEG_DIR}/{runtime.SEG_ONNX}")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class _FakeResp:
    def __init__(self, payload, headers=None, error=None):
        self._payload = payload
        self._off = 0
        self._error = error
        self.headers = headers if headers is not None else {
            "Content-Length": str(len(payload)),
        }

    def read(self, n=-1):
        if self._error and self._off > 0:
            raise self._error
        if n is None or n < 0:
            n = len(self._payload) - self._off
        chunk = self._payload[self._off:self._off + n]
        self._off += len(chunk)
        if self._error and self._off > 0:
            raise self._error
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_urlopen(monkeypatch, payloads, calls=None):
    """URL → bytes. calls 리스트가 있으면 호출 URL 을 기록한다."""
    def fake(url):
        if calls is not None:
            calls.append(url)
        if url not in payloads:
            raise urllib.error.URLError(f"unexpected url: {url}")
        val = payloads[url]
        if isinstance(val, Exception):
            raise val
        return _FakeResp(val)
    monkeypatch.setattr(runtime, "_urlopen", fake)


def _plant_models(root, seg=b"seg", emb=b"emb"):
    seg_dir = os.path.join(root, runtime.SEG_DIR)
    os.makedirs(seg_dir, exist_ok=True)
    with open(os.path.join(seg_dir, runtime.SEG_ONNX), "wb") as f:
        f.write(seg)
    with open(os.path.join(root, runtime.EMB_NAME), "wb") as f:
        f.write(emb)


# ====== import / 미설치 ======

def test_import_runtime_does_not_need_engines():
    """모듈 import 만으로 엔진을 끌어오면 미설치 PC 에서 부팅이 막힌다."""
    import src.stt.runtime as m
    assert callable(m.status) and callable(m.ensure_models) and callable(m.model_dir)


def test_status_uninstalled_returns_false_flags(monkeypatch):
    monkeypatch.setattr(runtime, "_engine_installed", lambda: False)
    monkeypatch.setattr(runtime, "_diarize_installed", lambda: False)
    st = runtime.status()
    assert st["ok"] is True
    assert st["engine_installed"] is False
    assert st["diarize_installed"] is False
    assert st["models"]["segmentation"]["present"] is False
    assert st["models"]["embedding"]["present"] is False
    assert st["total_bytes"] == 0
    assert st["error"] == ""


# ====== model_dir ======

def test_model_dir_writable_under_data_path(tmp_path):
    d = runtime.model_dir()
    assert os.path.isabs(d)
    assert os.path.isdir(d)
    assert d == os.path.abspath(os.path.join(str(tmp_path), "stt-models"))
    probe = os.path.join(d, "probe.txt")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("ok")
    assert os.path.isfile(probe)


# ====== 다운로드 흐름 (urllib 차단) ======

def test_ensure_models_downloads_and_extracts(monkeypatch):
    seg = _seg_tar_bytes()
    emb = b"fake-emb-onnx"
    calls = []
    _install_fake_urlopen(monkeypatch, {
        runtime.SEG_URL: seg,
        runtime.EMB_URL: emb,
    }, calls=calls)

    r = runtime.ensure_models()
    assert r["ok"] is True
    assert r["error"] == ""
    assert r["downloaded"] == ["segmentation", "embedding"]
    assert calls == [runtime.SEG_URL, runtime.EMB_URL]

    root = runtime.model_dir()
    assert os.path.isfile(os.path.join(root, runtime.SEG_DIR, runtime.SEG_ONNX))
    with open(os.path.join(root, runtime.EMB_NAME), "rb") as f:
        assert f.read() == emb
    # 부분 파일·원본 tar 가 정상 파일로 남지 않는다
    assert not os.path.exists(os.path.join(root, runtime.SEG_ARCHIVE))
    assert not os.path.exists(os.path.join(root, runtime.SEG_ARCHIVE + ".part"))
    assert not os.path.exists(os.path.join(root, runtime.EMB_NAME + ".part"))

    st = runtime.status()
    assert st["models"]["segmentation"]["present"] is True
    assert st["models"]["embedding"]["present"] is True
    assert st["total_bytes"] == len(b"fake-seg-onnx") + len(emb)


def test_ensure_models_idempotent_skips_download(monkeypatch):
    root = runtime.model_dir()
    _plant_models(root)
    calls = []

    def boom(url):
        calls.append(url)
        raise urllib.error.URLError("should not be called")

    monkeypatch.setattr(runtime, "_urlopen", boom)
    r = runtime.ensure_models()
    assert r["ok"] is True
    assert r["downloaded"] == []
    assert calls == []


def test_ensure_models_interrupted_leaves_no_valid_file(monkeypatch):
    def boom(url):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(runtime, "_urlopen", boom)
    r = runtime.ensure_models()
    assert r["ok"] is False
    assert "중단" in r["error"]

    root = runtime._model_root()
    assert not os.path.isfile(os.path.join(root, runtime.SEG_DIR, runtime.SEG_ONNX))
    assert not os.path.isfile(os.path.join(root, runtime.EMB_NAME))
    assert not os.path.exists(os.path.join(root, runtime.SEG_ARCHIVE))
    # 임시 파일이 정상 모델로 오인되지 않는다
    st = runtime.status()
    assert st["models"]["segmentation"]["present"] is False
    assert st["models"]["embedding"]["present"] is False


def test_partial_download_removed_on_drop(monkeypatch):
    err = urllib.error.URLError("connection reset")

    def fake(url):
        payload = b"partial-bytes-only"
        return _FakeResp(payload, headers={"Content-Length": "100000"}, error=err)

    monkeypatch.setattr(runtime, "_urlopen", fake)
    r = runtime.ensure_models()
    assert r["ok"] is False
    assert "중단" in r["error"]

    root = runtime._model_root()
    leftovers = []
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            leftovers.extend(os.path.join(dirpath, n) for n in files)
    # .part 포함 어떤 파일도 정상 모델로 남지 않아야 한다
    assert leftovers == []
    assert runtime.status()["models"]["segmentation"]["present"] is False


def test_part_file_is_not_treated_as_present():
    root = runtime.model_dir()
    with open(os.path.join(root, runtime.EMB_NAME + ".part"), "wb") as f:
        f.write(b"incomplete")
    os.makedirs(os.path.join(root, runtime.SEG_DIR), exist_ok=True)
    with open(os.path.join(root, runtime.SEG_DIR, runtime.SEG_ONNX + ".part"), "wb") as f:
        f.write(b"incomplete")
    with open(os.path.join(root, runtime.EMB_NAME), "wb") as f:
        f.write(b"")  # 0바이트는 미완료
    st = runtime.status()
    assert st["models"]["segmentation"]["present"] is False
    assert st["models"]["embedding"]["present"] is False
    assert st["total_bytes"] == 0


def test_ensure_models_disk_full_message(monkeypatch):
    monkeypatch.setattr(runtime, "_urlopen",
                        lambda url: _FakeResp(b"x" * 32))

    def boom(_path):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(runtime, "_open_write", boom)
    r = runtime.ensure_models()
    assert r["ok"] is False
    assert "디스크" in r["error"]
    root = runtime._model_root()
    assert not os.path.isfile(os.path.join(root, runtime.EMB_NAME))
    assert runtime.status()["models"]["segmentation"]["present"] is False


def test_ensure_models_permission_message(monkeypatch):
    def boom(path, exist_ok=False):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(os, "makedirs", boom)
    r = runtime.ensure_models()
    assert r["ok"] is False
    assert "권한" in r["error"]


def test_on_progress_called(monkeypatch):
    seen = []
    _install_fake_urlopen(monkeypatch, {
        runtime.SEG_URL: _seg_tar_bytes(),
        runtime.EMB_URL: b"emb-bytes",
    })
    r = runtime.ensure_models(on_progress=lambda d, t: seen.append((d, t)))
    assert r["ok"] is True
    assert seen  # 최소 한 번은 호출
    for done, total in seen:
        assert isinstance(done, int) and isinstance(total, int)
        assert done >= 0
        if total:
            assert done <= total


def test_status_reports_bytes_when_present():
    root = runtime.model_dir()
    _plant_models(root, seg=b"abcde", emb=b"xy")
    st = runtime.status()
    assert st["ok"] is True
    assert st["models"]["segmentation"]["bytes"] == 5
    assert st["models"]["embedding"]["bytes"] == 2
    assert st["total_bytes"] == 7
    assert os.path.isabs(st["models"]["segmentation"]["path"])
    assert os.path.isabs(st["models"]["embedding"]["path"])
