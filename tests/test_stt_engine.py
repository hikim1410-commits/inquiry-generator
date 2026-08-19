# -*- coding: utf-8 -*-
"""src/stt/engine.py — 실제 faster-whisper/모델/녹음 없이 전사 계약을 검증한다."""
import os

import pytest

from src.stt import engine


class _Seg:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class _Info:
    def __init__(self, language="ko", duration=12.5):
        self.language = language
        self.duration = duration


def _model(segments=None, detected_language="ko", duration=12.5,
           init_error=None, transcribe_error=None):
    """WhisperModel 대체. 생성·transcribe 인자를 클래스 변수에 기록한다."""
    segs = list(segments if segments is not None else [
        _Seg(0.0, 1.5, "  안녕하세요  "),
        _Seg(1.5, 3.0, "hello world"),
    ])

    class FakeModel:
        last_init = None
        last_transcribe = None

        def __init__(self, model_size_or_path, device="cpu", compute_type="int8"):
            if init_error is not None:
                raise init_error
            FakeModel.last_init = {
                "model": model_size_or_path,
                "device": device,
                "compute_type": compute_type,
            }

        def transcribe(self, audio, language=None, vad_filter=False, **kwargs):
            if transcribe_error is not None:
                raise transcribe_error
            FakeModel.last_transcribe = {
                "audio": audio,
                "language": language,
                "vad_filter": vad_filter,
            }
            FakeModel.last_transcribe.update(kwargs)

            def gen():
                for s in segs:
                    yield s

            info_lang = language if language else detected_language
            return gen(), _Info(language=info_lang, duration=duration)

    return FakeModel


def _wav(tmp_path, name="a.wav"):
    p = str(tmp_path / name)
    with open(p, "wb") as f:
        f.write(b"RIFF")
    return p


# ====== import / 미설치 ======

def test_import_engine_does_not_need_faster_whisper():
    """모듈 import 만으로 faster_whisper 를 끌어오면 미설치 PC 에서 부팅이 막힌다."""
    import src.stt.engine as m
    assert callable(m.available) and callable(m.transcribe)


def test_available_false_when_missing(monkeypatch):
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: None)
    assert engine.available() is False


def test_available_true_when_importable(monkeypatch):
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: _model())
    assert engine.available() is True


def test_available_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("dll load failed")

    monkeypatch.setattr(engine, "_import_whisper_model", boom)
    assert engine.available() is False


def test_transcribe_library_missing_korean(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: None)
    r = engine.transcribe(p)
    assert r["ok"] is False
    assert r["segments"] == []
    assert "faster-whisper" in r["error"]
    assert "설치" in r["error"]
    assert r["error"] == engine.ERR_NO_LIB


def test_transcribe_model_missing_distinct_from_library(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    err = type("LocalEntryNotFoundError", (Exception,), {})(
        "Cannot find an appropriate cached snapshot folder")
    monkeypatch.setattr(engine, "_import_whisper_model",
                        lambda: _model(init_error=err))
    r = engine.transcribe(p)
    assert r["ok"] is False
    assert "모델" in r["error"]
    assert r["error"] == engine.ERR_NO_MODEL
    assert r["error"] != engine.ERR_NO_LIB
    assert "faster-whisper" not in r["error"]


# ====== 경로 ======

def test_transcribe_missing_file():
    r = engine.transcribe(os.path.join("C:\\", "no-such-stt-engine-xyz.wav"))
    assert r["ok"] is False
    assert "찾을 수 없" in r["error"]
    assert r["segments"] == []


def test_transcribe_empty_path():
    r = engine.transcribe("")
    assert r["ok"] is False
    assert "찾을 수 없" in r["error"]


# ====== 세그먼트 정규화 / 진행률 / 취소 / 옵션 ======

def test_transcribe_normalizes_segments_and_empty_speaker(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    Fake = _model()
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: Fake)
    r = engine.transcribe(p)
    assert r["ok"] is True
    assert r["error"] == ""
    assert r["language"] == "ko"
    assert r["duration_sec"] == pytest.approx(12.5)
    assert r["segments"] == [
        {"start": 0.0, "end": 1.5, "text": "안녕하세요", "speaker": ""},
        {"start": 1.5, "end": 3.0, "text": "hello world", "speaker": ""},
    ]
    assert all(s["speaker"] == "" for s in r["segments"])
    assert Fake.last_init == {
        "model": "medium", "device": "cpu", "compute_type": "int8",
    }
    assert Fake.last_transcribe["vad_filter"] is True
    assert Fake.last_transcribe["language"] is None
    assert Fake.last_transcribe["audio"] == p


def test_transcribe_on_progress_called(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: _model())
    seen = []
    r = engine.transcribe(p, on_progress=lambda d, t: seen.append((d, t)))
    assert r["ok"] is True
    assert seen[0] == (0.0, 12.5)
    assert (1.5, 12.5) in seen
    assert (3.0, 12.5) in seen
    assert seen[-1][1] == pytest.approx(12.5)


def test_transcribe_cancel_immediately(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    Fake = _model()
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: Fake)
    r = engine.transcribe(p, should_cancel=lambda: True)
    assert r["ok"] is False
    assert r["error"] == "사용자가 취소했습니다."
    assert r["segments"] == []
    assert Fake.last_init is None


def test_transcribe_cancel_during_segments(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: _model())
    progress = []

    def on_progress(done, total):
        progress.append(done)

    def should_cancel():
        return len(progress) >= 2

    r = engine.transcribe(p, on_progress=on_progress, should_cancel=should_cancel)
    assert r["ok"] is False
    assert r["error"] == "사용자가 취소했습니다."
    assert r["segments"] == []


def test_transcribe_language_override(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    Fake = _model(detected_language="en")
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: Fake)
    r = engine.transcribe(p, opts={"language": "ko"})
    assert r["ok"] is True
    assert Fake.last_transcribe["language"] == "ko"
    assert r["language"] == "ko"


def test_transcribe_opts_model_device_compute(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    Fake = _model()
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: Fake)
    r = engine.transcribe(p, opts={
        "model": "small", "device": "cpu", "compute_type": "int8",
        "language": "",
    })
    assert r["ok"] is True
    assert Fake.last_init["model"] == "small"
    assert Fake.last_init["device"] == "cpu"
    assert Fake.last_init["compute_type"] == "int8"
    assert Fake.last_transcribe["language"] is None


def test_transcribe_generic_error_korean(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    monkeypatch.setattr(
        engine, "_import_whisper_model",
        lambda: _model(transcribe_error=RuntimeError("cuda boom")))
    r = engine.transcribe(p)
    assert r["ok"] is False
    assert r["error"] == engine.ERR_TRANSCRIBE
    assert "cuda" not in r["error"]


def test_transcribe_dict_segments_also_normalize(monkeypatch, tmp_path):
    p = _wav(tmp_path)
    Fake = _model(segments=[
        {"start": 4.0, "end": 6.25, "text": "  네  "},
    ])
    monkeypatch.setattr(engine, "_import_whisper_model", lambda: Fake)
    r = engine.transcribe(p)
    assert r["ok"] is True
    assert r["segments"] == [
        {"start": 4.0, "end": 6.25, "text": "네", "speaker": ""},
    ]
