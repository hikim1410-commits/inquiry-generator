# -*- coding: utf-8 -*-
"""STT API 표면 — 실제 엔진/모델/오디오 없이 전사 흐름·취소·파일 격리·화자 매핑.

Api()는 실제 config.json 을 읽으므로 대부분의 테스트는 __init__ 을 우회하고 cfg 를 주입한다
(test_receipt_api 와 동일). src.stt.* 는 monkeypatch 로 가짜를 끼운다.
"""
import json
import os
import threading
import time

from src.api import Api
from src.api.core import ApiCore
from src.api.stt import SttApi
from src.store import config_store as cs

_METHODS = (
    "start_transcribe", "transcribe_status", "cancel_transcribe",
    "get_transcript", "update_transcript", "set_speaker_names",
    "transcript_to_minutes", "save_transcript",
    "stt_status", "install_stt_models",
)


def _api():
    api = Api.__new__(Api)
    api.cfg = {}
    api._window = None
    return api


def _touch(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fp:
        fp.write(content)
    return path


def _wait(api, timeout=5.0):
    t = getattr(api, "_stt_thread", None)
    if t is not None:
        t.join(timeout=timeout)
    deadline = time.time() + 0.5
    while api.transcribe_status().get("running") and time.time() < deadline:
        time.sleep(0.01)


def _ok_norm(path):
    return {"ok": True, "wav_path": path, "duration_sec": 10.0, "error": ""}


def _ok_chunk(wav_path, max_sec=1800):
    return [{"path": wav_path, "offset_sec": 0.0}]


def _ok_transcribe(path, opts=None, on_progress=None, should_cancel=None):
    if should_cancel and should_cancel():
        return {"ok": False, "segments": [], "language": "",
                "duration_sec": 0.0, "error": "사용자가 취소했습니다."}
    if on_progress:
        on_progress(5.0, 10.0)
        on_progress(10.0, 10.0)
    return {
        "ok": True,
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "안녕하세요", "speaker": ""},
            {"start": 1.5, "end": 3.0, "text": "hello", "speaker": ""},
        ],
        "language": "ko",
        "duration_sec": 10.0,
        "error": "",
    }


def _ok_diarize(wav_path, num_speakers=-1):
    return {
        "ok": True,
        "turns": [
            {"start": 0.0, "end": 1.5, "speaker": "화자1"},
            {"start": 1.5, "end": 3.0, "speaker": "화자2"},
        ],
        "num_speakers": 2,
        "error": "",
    }


def _patch_engine(monkeypatch, transcribe=_ok_transcribe, normalize=_ok_norm,
                  chunk=_ok_chunk, diarize=_ok_diarize):
    import src.stt.audio as audio
    import src.stt.engine as engine
    import src.stt.diarize as dia
    monkeypatch.setattr(audio, "normalize", normalize)
    monkeypatch.setattr(audio, "chunk", chunk)
    monkeypatch.setattr(engine, "transcribe", transcribe)
    monkeypatch.setattr(dia, "diarize", diarize)


def test_api_exposes_stt_methods():
    for name in _METHODS:
        assert hasattr(Api, name), name
        assert callable(getattr(Api, name))


def test_stt_mixin_sits_before_apicore():
    mro = Api.__mro__
    assert mro.index(SttApi) < mro.index(ApiCore)


def test_api_constructs_without_args(monkeypatch):
    monkeypatch.setattr(cs, "save_config", lambda cfg: None)
    api = Api()
    assert callable(api.start_transcribe)
    assert callable(api.stt_status)


def test_import_stt_api_does_not_need_faster_whisper():
    import src.api.stt as m
    assert callable(m.SttApi.start_transcribe)


def test_start_empty_paths_errors():
    api = _api()
    r = api.start_transcribe({"paths": []})
    assert r["ok"] is False
    assert "음성" in r["error"] or "파일" in r["error"]
    json.dumps(r)


def test_full_flow_assigns_speakers(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.m4a"))
    _patch_engine(monkeypatch)
    r = api.start_transcribe({"paths": [p]})
    assert r["ok"] is True
    _wait(api)
    st = api.transcribe_status()
    assert st["ok"] is True
    assert st["running"] is False
    assert st["phase"] == "done"
    tr = api.get_transcript()
    json.dumps(tr)
    assert tr["ok"] is True
    segs = tr["segments"]
    assert [s["text"] for s in segs] == ["안녕하세요", "hello"]
    assert [s["speaker"] for s in segs] == ["화자1", "화자2"]
    assert all(s["speaker"] for s in segs)


def test_cancel_propagates(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    started = threading.Event()

    def slow_transcribe(path, opts=None, on_progress=None, should_cancel=None):
        started.set()
        for _ in range(400):
            if should_cancel and should_cancel():
                return {"ok": False, "segments": [], "language": "",
                        "duration_sec": 0.0, "error": "사용자가 취소했습니다."}
            time.sleep(0.01)
        return _ok_transcribe(path, opts, on_progress, should_cancel)

    _patch_engine(monkeypatch, transcribe=slow_transcribe)
    assert api.start_transcribe({"paths": [p]})["ok"]
    assert started.wait(2)
    c = api.cancel_transcribe()
    assert c["ok"] is True
    _wait(api)
    st = api.transcribe_status()
    assert st["running"] is False
    assert st["cancelled"] is True or "취소" in (st.get("error") or "")
    assert "취소" in (st.get("error") or st.get("phase") or "")


def test_one_file_failure_does_not_block_the_rest(tmp_path, monkeypatch):
    api = _api()
    bad = _touch(str(tmp_path / "bad.mp3"))
    good = _touch(str(tmp_path / "good.mp3"))

    def normalize(path):
        if os.path.basename(path) == "bad.mp3":
            return {"ok": False, "wav_path": "", "duration_sec": 0.0,
                    "error": "파일이 손상되었거나 지원하지 않는 형식입니다."}
        return _ok_norm(path)

    _patch_engine(monkeypatch, normalize=normalize)
    assert api.start_transcribe({"paths": [bad, good]})["ok"]
    _wait(api)
    tr = api.get_transcript()
    files = {f["name"]: f for f in tr["files"]}
    assert files["bad.mp3"]["ok"] is False
    assert "손상" in files["bad.mp3"]["error"]
    assert files["good.mp3"]["ok"] is True
    assert [s["text"] for s in tr["segments"]] == ["안녕하세요", "hello"]
    st = api.transcribe_status()
    assert st["phase"] == "done"
    assert st["running"] is False


def test_speaker_names_apply_to_whole_transcript(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    _patch_engine(monkeypatch)
    api.start_transcribe({"paths": [p]})
    _wait(api)
    r = api.set_speaker_names({"화자1": "내비온 김형일", "화자2": "KIST 김종민"})
    assert r["ok"] is True
    speakers = [s["speaker"] for s in r["segments"]]
    assert speakers == ["내비온 김형일", "KIST 김종민"]
    # 원본 화자 라벨은 매핑을 바꿔도 다시 적용된다
    r2 = api.set_speaker_names({"mapping": {"화자1": "홍길동"}})
    assert r2["segments"][0]["speaker"] == "홍길동"
    assert r2["segments"][1]["speaker"] == "화자2"


def test_update_transcript_edits_text(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    _patch_engine(monkeypatch)
    api.start_transcribe({"paths": [p]})
    _wait(api)
    r = api.update_transcript({"index": 0, "text": "안녕하십니까"})
    assert r["ok"] is True
    assert r["segments"][0]["text"] == "안녕하십니까"
    assert r["segments"][1]["text"] == "hello"
    bad = api.update_transcript({"index": 99, "text": "x"})
    assert bad["ok"] is False


def test_transcript_to_minutes_returns_description(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    _patch_engine(monkeypatch)
    api.start_transcribe({"paths": [p]})
    _wait(api)
    api.set_speaker_names({"화자1": "내비온 김형일"})
    r = api.transcript_to_minutes()
    json.dumps(r)
    assert r["ok"] is True
    assert r["description"] == r["text"]
    assert "[회의 전사본]" in r["text"]
    assert "내비온 김형일" in r["text"]
    assert "hello" in r["text"]


def test_save_transcript_writes_sidecars(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "회의.m4a"))
    _patch_engine(monkeypatch)
    api.start_transcribe({"paths": [p]})
    _wait(api)
    r = api.save_transcript({"folder": str(tmp_path), "stem": "회의"})
    assert r["ok"] is True
    assert os.path.isfile(r["json_path"])
    assert os.path.isfile(r["txt_path"])
    assert r["json_path"].endswith(".transcript.json")
    assert r["txt_path"].endswith(".transcript.txt")
    with open(r["json_path"], encoding="utf-8") as f:
        body = json.load(f)
    assert body["segments"]
    with open(r["txt_path"], encoding="utf-8") as f:
        txt = f.read()
    assert "안녕하세요" in txt


def test_save_transcript_without_result_errors():
    api = _api()
    r = api.save_transcript({"folder": "C:\\"})
    assert r["ok"] is False
    assert "결과" in r["error"]


def test_second_start_while_running_is_busy(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    gate = threading.Event()
    release = threading.Event()

    def blocked(path, opts=None, on_progress=None, should_cancel=None):
        gate.set()
        release.wait(timeout=3)
        return _ok_transcribe(path, opts, on_progress, should_cancel)

    _patch_engine(monkeypatch, transcribe=blocked)
    assert api.start_transcribe({"paths": [p]})["ok"]
    assert gate.wait(2)
    second = api.start_transcribe({"paths": [p]})
    assert second["ok"] is False
    assert "진행" in second["error"]
    release.set()
    _wait(api)


def test_stt_status_wraps_runtime(monkeypatch):
    api = _api()
    import src.stt.runtime as runtime
    monkeypatch.setattr(runtime, "status", lambda: {
        "ok": True, "engine_installed": False, "diarize_installed": False,
        "models": {}, "total_bytes": 0, "error": "",
    })
    r = api.stt_status()
    json.dumps(r)
    assert r["ok"] is True
    assert r["engine_installed"] is False


def test_install_stt_models_uses_ensure_models(monkeypatch):
    api = _api()
    import src.stt.runtime as runtime
    seen = {"cb": False}

    def ensure(on_progress=None):
        if on_progress:
            on_progress(10, 100)
            seen["cb"] = True
        return {"ok": True, "downloaded": ["segmentation"], "error": ""}

    monkeypatch.setattr(runtime, "ensure_models", ensure)
    r = api.install_stt_models()
    json.dumps(r)
    assert r["ok"] is True
    assert r["downloaded"] == ["segmentation"]
    assert seen["cb"] is True


def test_update_transcript_accepts_string_index(tmp_path, monkeypatch):
    """UI 는 data-idx 문자열을 그대로 보낸다."""
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    _patch_engine(monkeypatch)
    api.start_transcribe({"paths": [p]})
    _wait(api)
    r = api.update_transcript({"index": "0", "text": "안녕하십니까"})
    assert r["ok"] is True
    assert r["segments"][0]["text"] == "안녕하십니까"


def test_transcript_times_are_float_seconds(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    _patch_engine(monkeypatch)
    api.start_transcribe({"paths": [p]})
    _wait(api)
    segs = api.get_transcript()["segments"]
    assert segs[0]["start"] == 0.0
    assert segs[0]["end"] == 1.5
    assert isinstance(segs[0]["start"], float)
    assert isinstance(segs[0]["end"], float)
    assert segs[0]["ts"]  # UI 표시용 문자열은 경계에서만
    assert segs[0]["speaker_orig"] == "화자1"


def test_opts_reach_engine_and_diarize(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    seen = {}

    def transcribe(path, opts=None, on_progress=None, should_cancel=None):
        seen["opts"] = dict(opts or {})
        return _ok_transcribe(path, opts, on_progress, should_cancel)

    def diarize(wav_path, num_speakers=-1):
        seen["num_speakers"] = num_speakers
        return _ok_diarize(wav_path, num_speakers)

    _patch_engine(monkeypatch, transcribe=transcribe, diarize=diarize)
    api.start_transcribe({
        "paths": [p],
        "opts": {"model": "small", "language": "ko", "num_speakers": 2},
    })
    _wait(api)
    assert seen["opts"]["model"] == "small"
    assert seen["opts"]["language"] == "ko"
    assert seen["num_speakers"] == 2


def test_install_rejected_while_running(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))
    gate = threading.Event()
    release = threading.Event()

    def blocked(path, opts=None, on_progress=None, should_cancel=None):
        gate.set()
        release.wait(timeout=3)
        return _ok_transcribe(path, opts, on_progress, should_cancel)

    _patch_engine(monkeypatch, transcribe=blocked)
    assert api.start_transcribe({"paths": [p]})["ok"]
    assert gate.wait(2)
    r = api.install_stt_models()
    assert r["ok"] is False
    assert "진행" in r["error"]
    release.set()
    _wait(api)


def test_public_error_is_korean(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.wav"))

    def boom(*a, **k):
        raise RuntimeError("not a korean message")

    _patch_engine(monkeypatch, normalize=boom)
    api.start_transcribe({"paths": [p]})
    _wait(api)
    files = {f["name"]: f for f in api.get_transcript()["files"]}
    err = files["a.wav"]["error"]
    assert files["a.wav"]["ok"] is False
    assert "not a korean" not in err
    assert err  # 한국어 안내


def test_chunk_offsets_shift_segment_times(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "long.wav"))

    def chunk(wav_path, max_sec=1800):
        return [
            {"path": wav_path, "offset_sec": 0.0},
            {"path": wav_path, "offset_sec": 1800.0},
        ]

    def transcribe(path, opts=None, on_progress=None, should_cancel=None):
        return {
            "ok": True,
            "segments": [{"start": 0.0, "end": 1.0, "text": "조각", "speaker": ""}],
            "language": "ko",
            "duration_sec": 1.0,
            "error": "",
        }

    def diarize(wav_path, num_speakers=-1):
        return {"ok": True, "turns": [], "num_speakers": 0, "error": ""}

    _patch_engine(monkeypatch, transcribe=transcribe, chunk=chunk, diarize=diarize)
    api.start_transcribe({"paths": [p]})
    _wait(api)
    segs = api.get_transcript()["segments"]
    assert [s["start"] for s in segs] == [0.0, 1800.0]
    assert [s["text"] for s in segs] == ["조각", "조각"]


def test_multi_file_timestamps_are_monotonic(tmp_path, monkeypatch):
    """파일 경계에서 타임스탬프가 0으로 되돌아가면 검색·근거 점프의
    이분탐색이 깨진다 — 두 번째 파일은 첫 파일 길이(10s)만큼 밀려야 한다."""
    api = _api()
    p1 = _touch(str(tmp_path / "a.wav"))
    p2 = _touch(str(tmp_path / "b.wav"))
    _patch_engine(monkeypatch)
    api.start_transcribe({"paths": [p1, p2]})
    _wait(api)
    segs = api.get_transcript()["segments"]
    starts = [s["start"] for s in segs]
    assert len(starts) == 4
    assert starts == sorted(starts)
    assert starts[2] == 10.0 and starts[3] == 11.5
