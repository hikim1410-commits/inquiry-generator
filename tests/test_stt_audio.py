# -*- coding: utf-8 -*-
"""src/stt/audio.py — 실제 엔진/모델 없이 표준화·분할 계약을 검증한다."""
import array
import os
import wave

import pytest

from src.stt import audio


def _write_wav(path, nframes, rate=16000, sample=0):
    """표준 wave 모듈로 PCM s16le mono wav 를 만든다."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        if sample == 0:
            wf.writeframes(b"\x00\x00" * int(nframes))
        else:
            buf = array.array("h", [int(sample)] * int(nframes))
            wf.writeframes(buf.tobytes())
    return path


class _FakeContainer:
    def __init__(self, duration=None, audio_count=1):
        self.duration = duration
        self.streams = type("S", (), {"audio": [object()] * audio_count})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeAv:
    def __init__(self, opener):
        self._opener = opener

    def open(self, *args, **kwargs):
        return self._opener(*args, **kwargs)


# ====== import / 미설치 ======

def test_import_audio_does_not_need_av():
    """모듈 import 만으로 av 를 끌어오면 미설치 PC 에서 부팅이 막힌다."""
    import src.stt.audio as m
    assert callable(m.probe) and callable(m.normalize) and callable(m.chunk)


def test_probe_av_missing(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "a.wav"), nframes=10)
    monkeypatch.setattr(audio, "_import_av", lambda: None)
    r = audio.probe(p)
    assert r["ok"] is False
    assert r["has_audio"] is False
    assert "PyAV" in r["error"]
    assert r["error"]  # 사용자에게 보일 한국어 안내


def test_normalize_av_missing(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "a.wav"), nframes=10)
    monkeypatch.setattr(audio, "_import_av", lambda: None)
    r = audio.normalize(p)
    assert r["ok"] is False
    assert r["wav_path"] == ""
    assert "PyAV" in r["error"]


# ====== 경로 / 손상 ======

def test_probe_missing_file():
    r = audio.probe(os.path.join("C:\\", "no-such-stt-file-xyz.m4a"))
    assert r["ok"] is False
    assert "찾을 수 없" in r["error"]


def test_normalize_missing_file():
    r = audio.normalize("")
    assert r["ok"] is False
    assert "찾을 수 없" in r["error"]


def test_probe_corrupt_file(monkeypatch, tmp_path):
    p = str(tmp_path / "broken.mp4")
    with open(p, "wb") as f:
        f.write(b"not a media file")

    def _boom(*a, **k):
        raise OSError("Invalid data found")

    monkeypatch.setattr(audio, "_import_av", lambda: _FakeAv(_boom))
    r = audio.probe(p)
    assert r["ok"] is False
    assert "손상" in r["error"]


def test_normalize_corrupt_file(monkeypatch, tmp_path):
    p = str(tmp_path / "broken.mp4")
    with open(p, "wb") as f:
        f.write(b"not a media file")

    def _boom(*a, **k):
        raise OSError("Invalid data found")

    monkeypatch.setattr(audio, "_import_av", lambda: _FakeAv(_boom))
    r = audio.normalize(p)
    assert r["ok"] is False
    assert "손상" in r["error"]


# ====== probe 메타 (디코딩 없이) ======

def test_probe_reads_duration_and_audio_flag(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "a.wav"), nframes=10)
    monkeypatch.setattr(
        audio, "_import_av",
        lambda: _FakeAv(lambda *a, **k: _FakeContainer(duration=3_792_000_000, audio_count=1)))
    r = audio.probe(p)
    assert r["ok"] is True
    assert r["has_audio"] is True
    assert r["duration_sec"] == pytest.approx(3792.0)
    assert r["error"] == ""


def test_probe_video_without_audio(monkeypatch, tmp_path):
    p = str(tmp_path / "silent-video.mp4")
    with open(p, "wb") as f:
        f.write(b"fake-mp4")
    monkeypatch.setattr(
        audio, "_import_av",
        lambda: _FakeAv(lambda *a, **k: _FakeContainer(duration=5_000_000, audio_count=0)))
    r = audio.probe(p)
    assert r["ok"] is True
    assert r["has_audio"] is False
    assert r["duration_sec"] == pytest.approx(5.0)


# ====== normalize 안내 구분 ======

def test_normalize_no_audio_track(monkeypatch, tmp_path):
    p = str(tmp_path / "video-only.mp4")
    with open(p, "wb") as f:
        f.write(b"fake-mp4")
    monkeypatch.setattr(audio, "_import_av", lambda: object())
    monkeypatch.setattr(
        audio, "_decode_pcm16_mono",
        lambda av, path: (b"", 0.0, False, True))
    r = audio.normalize(p)
    assert r["ok"] is False
    assert "오디오 트랙" in r["error"]
    assert "무음" not in r["error"]
    assert "손상" not in r["error"]


def test_normalize_silent_file(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "silence.wav"), nframes=1600)
    monkeypatch.setattr(audio, "_import_av", lambda: object())
    monkeypatch.setattr(
        audio, "_decode_pcm16_mono",
        lambda av, path: (b"\x00\x00" * 1600, 0.1, True, True))
    r = audio.normalize(p)
    assert r["ok"] is False
    assert "무음" in r["error"] or "음성" in r["error"]
    assert "오디오 트랙" not in r["error"]
    assert r["wav_path"] == ""


def test_normalize_writes_temp_wav(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "src.wav"), nframes=4, sample=1000)
    pcm = array.array("h", [0, 1000, -1000, 0]).tobytes()
    monkeypatch.setattr(audio, "_import_av", lambda: object())
    monkeypatch.setattr(
        audio, "_decode_pcm16_mono",
        lambda av, path: (pcm, 4 / 16000, True, False))
    r = audio.normalize(p)
    assert r["ok"] is True
    assert r["error"] == ""
    assert os.path.isfile(r["wav_path"])
    assert r["wav_path"] != p
    with wave.open(r["wav_path"], "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 4
    os.remove(r["wav_path"])


def test_pcm16_has_signal():
    assert audio._pcm16_has_signal(b"") is False
    assert audio._pcm16_has_signal(b"\x00\x00\x00\x00") is False
    assert audio._pcm16_has_signal(array.array("h", [0, 1, 0]).tobytes()) is True


# ====== chunk 경계 (순수 로직 + 실제 wav) ======

def test_chunk_offsets_4000_sec_three_pieces():
    """4000초를 1800초로 나누면 3조각, offset 0 / 1800 / 3600."""
    assert audio._chunk_offsets(4000, 1800) == [0.0, 1800.0, 3600.0]
    assert audio._chunk_offsets(1800, 1800) == [0.0]
    assert audio._chunk_offsets(1799.9, 1800) == [0.0]
    assert audio._chunk_offsets(1800.1, 1800) == [0.0, 1800.0]


def test_chunk_short_returns_original(tmp_path):
    p = _write_wav(str(tmp_path / "short.wav"), nframes=16000, rate=16000)
    pieces = audio.chunk(p, max_sec=1800)
    assert pieces == [{"path": p, "offset_sec": 0.0}]


def test_chunk_splits_4000_sec_into_three(tmp_path):
    """rate=1, 4000 프레임 = 4000초. 파일은 수 KB 이라 실제 경로를 태울 수 있다."""
    p = _write_wav(str(tmp_path / "long.wav"), nframes=4000, rate=1)
    pieces = audio.chunk(p, max_sec=1800)
    assert [x["offset_sec"] for x in pieces] == [0.0, 1800.0, 3600.0]
    assert len(pieces) == 3
    assert all(x["path"] != p for x in pieces)
    expected_frames = [1800, 1800, 400]
    for piece, n in zip(pieces, expected_frames):
        with wave.open(piece["path"], "rb") as wf:
            assert wf.getnframes() == n
            assert wf.getframerate() == 1
        # 원본은 그대로 남아 있어야 한다 (모듈이 지우면 안 됨)
    assert os.path.isfile(p)


def test_chunk_missing_file_returns_empty():
    assert audio.chunk(os.path.join("C:\\", "no-such-chunk.wav")) == []


def test_chunk_split_failure_does_not_leave_temp_files(tmp_path, monkeypatch):
    """분할 도중 실패하면 이미 만든 stt_chunk_ 임시 파일을 지운다.

    지우지 않으면 호출자가 경로를 받지 못해 조각이 임시 폴더에 쌓인다.
    """
    import tempfile

    p = _write_wav(str(tmp_path / "long.wav"), nframes=4000, rate=1)
    created = []
    orig_mk = audio.tempfile.mkstemp

    def spy(*a, **k):
        fd, path = orig_mk(*a, **k)
        created.append(path)
        return fd, path

    monkeypatch.setattr(audio.tempfile, "mkstemp", spy)
    orig_open = audio.wave.open
    writes = {"n": 0}

    def wrap_open(path, mode="r"):
        if "w" in str(mode):
            writes["n"] += 1
            if writes["n"] >= 2:
                raise OSError("simulated write fail")
        return orig_open(path, mode)

    monkeypatch.setattr(audio.wave, "open", wrap_open)
    pieces = audio.chunk(p, max_sec=1800)
    assert pieces == [{"path": p, "offset_sec": 0.0}]
    assert created, "조각 임시 파일이 하나 이상 만들어져야 실패 경로를 탄다"
    assert all(not os.path.exists(x) for x in created)
    # 원본은 그대로
    assert os.path.isfile(p)
    leftover = [f for f in os.listdir(tempfile.gettempdir())
                if f.startswith("stt_chunk_") and
                os.path.join(tempfile.gettempdir(), f) in created]
    assert leftover == []


def test_write_temp_wav_removes_file_on_write_failure(monkeypatch):
    created = []
    orig_mk = audio.tempfile.mkstemp

    def spy(*a, **k):
        fd, path = orig_mk(*a, **k)
        created.append(path)
        return fd, path

    monkeypatch.setattr(audio.tempfile, "mkstemp", spy)

    def boom(*a, **k):
        raise OSError("simulated write fail")

    monkeypatch.setattr(audio.wave, "open", boom)
    try:
        audio._write_temp_wav(b"\x00\x00")
        assert False, "예외가 나야 한다"
    except OSError:
        pass
    assert created
    assert all(not os.path.exists(x) for x in created)
