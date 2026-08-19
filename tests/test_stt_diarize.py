# -*- coding: utf-8 -*-
"""src/stt/diarize.py — sherpa-onnx/모델/녹음 파일 없이 계약을 검증한다."""
import os
import wave

from src.stt import diarize


def _seg(start, end, text="", speaker=""):
    return {"start": start, "end": end, "text": text, "speaker": speaker}


def _turn(start, end, speaker):
    return {"start": start, "end": end, "speaker": speaker}


def _write_wav(path, nframes, rate=16000, sample=0):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        if sample == 0:
            wf.writeframes(b"\x00\x00" * int(nframes))
        else:
            import array
            buf = array.array("h", [int(sample)] * int(nframes))
            wf.writeframes(buf.tobytes())
    return path


# ====== import / 미설치 ======

def test_import_diarize_does_not_need_sherpa():
    """모듈 import 만으로 sherpa_onnx 를 끌어오면 미설치 PC 에서 부팅이 막힌다."""
    import src.stt.diarize as m
    assert callable(m.available) and callable(m.diarize)
    assert callable(m.assign) and callable(m.merge)


def test_available_false_when_missing(monkeypatch):
    monkeypatch.setattr(diarize, "_import_sherpa", lambda: None)
    assert diarize.available() is False


def test_diarize_sherpa_missing(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "a.wav"), nframes=10, sample=100)
    monkeypatch.setattr(diarize, "_import_sherpa", lambda: None)
    r = diarize.diarize(p)
    assert r["ok"] is False
    assert r["turns"] == []
    assert r["num_speakers"] == 0
    assert "sherpa" in r["error"].lower() or "화자 분리 엔진" in r["error"]


# ====== assign (순수 함수) ======

def test_assign_picks_longest_overlap_on_boundary():
    """4~9초 세그먼트는 0~5보다 5~10과 더 많이 겹친다 → 화자2."""
    turns = [_turn(0.0, 5.0, "화자1"), _turn(5.0, 10.0, "화자2")]
    segs = [_seg(0.5, 2.0, "가"), _seg(6.0, 9.0, "나"), _seg(4.0, 9.0, "경계")]
    got = diarize.assign(segs, turns)
    assert [g["speaker"] for g in got] == ["화자1", "화자2", "화자2"]
    assert [g["text"] for g in got] == ["가", "나", "경계"]
    assert got[2]["start"] == 4.0 and got[2]["end"] == 9.0


def test_assign_empty_when_no_overlap():
    turns = [_turn(0.0, 5.0, "화자1"), _turn(5.0, 10.0, "화자2")]
    got = diarize.assign([_seg(20.0, 21.0, "다")], turns)
    assert got[0]["speaker"] == ""
    assert got[0]["text"] == "다"


def test_assign_empty_turns_no_exception():
    segs = [_seg(0.5, 2.0, "가"), _seg(6.0, 9.0, "나")]
    got = diarize.assign(segs, [])
    assert [g["speaker"] for g in got] == ["", ""]
    assert [g["text"] for g in got] == ["가", "나"]
    got_none = diarize.assign(segs, None)
    assert [g["speaker"] for g in got_none] == ["", ""]


def test_assign_skips_non_dict_and_inverted_interval():
    """비-dict 는 가짜 세그먼트를 만들지 않고, end<start 는 억지 배정하지 않는다."""
    turns = [_turn(0.0, 10.0, "화자1")]
    got = diarize.assign(["bad", None, _seg(5.0, 1.0, "뒤집힘")], turns)
    assert len(got) == 1
    assert got[0]["text"] == "뒤집힘"
    assert got[0]["speaker"] == ""


def test_assign_zero_based_ids_become_korean_labels():
    """sherpa 원본 ID(0, 1)는 화자1, 화자2 로 바꾼다."""
    turns = [_turn(0.0, 5.0, 0), _turn(5.0, 10.0, 1)]
    got = diarize.assign([_seg(0.5, 2.0, "가"), _seg(6.0, 9.0, "나")], turns)
    assert [g["speaker"] for g in got] == ["화자1", "화자2"]


def test_assign_does_not_mutate_inputs():
    segs = [_seg(0.5, 2.0, "가")]
    turns = [_turn(0.0, 5.0, "화자1")]
    diarize.assign(segs, turns)
    assert segs[0]["speaker"] == ""
    assert turns[0]["speaker"] == "화자1"


def test_assign_touching_boundary_is_not_overlap():
    """맞닿기만 한 구간(끝=시작)은 겹침 0 → 빈 화자."""
    turns = [_turn(0.0, 5.0, "화자1")]
    got = diarize.assign([_seg(5.0, 8.0, "뒤")], turns)
    assert got[0]["speaker"] == ""


# ====== merge (순수 함수) ======

def test_merge_same_speaker_three_into_one():
    segs = [
        _seg(0.0, 1.0, "가", "화자1"),
        _seg(1.0, 2.5, "나", "화자1"),
        _seg(2.5, 4.0, "다", "화자1"),
    ]
    got = diarize.merge(segs)
    assert len(got) == 1
    assert got[0]["start"] == 0.0
    assert got[0]["end"] == 4.0
    assert got[0]["text"] == "가 나 다"
    assert got[0]["speaker"] == "화자1"


def test_merge_does_not_join_when_speaker_changes():
    segs = [
        _seg(0.0, 1.0, "가", "화자1"),
        _seg(1.0, 2.0, "나", "화자2"),
        _seg(2.0, 3.0, "다", "화자1"),
    ]
    got = diarize.merge(segs)
    assert [g["speaker"] for g in got] == ["화자1", "화자2", "화자1"]
    assert [g["text"] for g in got] == ["가", "나", "다"]
    assert got[0]["start"] == 0.0 and got[0]["end"] == 1.0
    assert got[2]["start"] == 2.0 and got[2]["end"] == 3.0


def test_merge_does_not_join_empty_speakers():
    segs = [
        _seg(0.0, 1.0, "가", ""),
        _seg(1.0, 2.0, "나", ""),
        _seg(2.0, 3.0, "다", "화자1"),
        _seg(3.0, 4.0, "라", "화자1"),
    ]
    got = diarize.merge(segs)
    assert len(got) == 3
    assert got[0]["speaker"] == "" and got[0]["text"] == "가"
    assert got[1]["speaker"] == "" and got[1]["text"] == "나"
    assert got[2]["speaker"] == "화자1"
    assert got[2]["text"] == "다 라"
    assert got[2]["start"] == 2.0 and got[2]["end"] == 4.0


def test_merge_empty_input():
    assert diarize.merge([]) == []
    assert diarize.merge(None) == []


def test_merge_does_not_mutate_inputs():
    segs = [_seg(0.0, 1.0, "가", "화자1"), _seg(1.0, 2.0, "나", "화자1")]
    diarize.merge(segs)
    assert segs[0]["end"] == 1.0
    assert segs[0]["text"] == "가"


# ====== diarize() 엔진 경로 (가짜) ======

def test_diarize_missing_file():
    r = diarize.diarize(os.path.join("C:\\", "no-such-stt-diarize.wav"))
    assert r["ok"] is False
    assert "찾을 수 없" in r["error"]


def test_diarize_runtime_missing(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "a.wav"), nframes=10, sample=100)
    monkeypatch.setattr(diarize, "_import_sherpa", lambda: object())

    def _boom():
        raise ImportError("no runtime")

    monkeypatch.setattr(diarize, "_get_model_dir", _boom)
    r = diarize.diarize(p)
    assert r["ok"] is False
    assert "모델" in r["error"]


def test_diarize_models_missing(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "a.wav"), nframes=10, sample=100)
    monkeypatch.setattr(diarize, "_import_sherpa", lambda: object())
    monkeypatch.setattr(diarize, "_get_model_dir", lambda: str(tmp_path))
    r = diarize.diarize(p)
    assert r["ok"] is False
    assert "모델" in r["error"]


class _FakeSeg:
    def __init__(self, start, end, speaker):
        self.start = start
        self.end = end
        self.speaker = speaker


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def sort_by_start_time(self):
        return list(self._items)

    def __iter__(self):
        return iter(self._items)


class _FakeSherpa:
    class OfflineSpeakerSegmentationPyannoteModelConfig:
        def __init__(self, model):
            self.model = model

    class OfflineSpeakerSegmentationModelConfig:
        def __init__(self, pyannote):
            self.pyannote = pyannote

    class SpeakerEmbeddingExtractorConfig:
        def __init__(self, model):
            self.model = model

    class FastClusteringConfig:
        last = None

        def __init__(self, num_clusters, threshold):
            self.num_clusters = num_clusters
            self.threshold = threshold
            _FakeSherpa.FastClusteringConfig.last = self

    class OfflineSpeakerDiarizationConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def validate(self):
            return True

    class OfflineSpeakerDiarization:
        def __init__(self, config):
            self.config = config
            self.sample_rate = 16000

        def process(self, audio):
            assert audio is not None
            return _FakeResult([
                _FakeSeg(0.0, 1.5, 0),
                _FakeSeg(1.5, 3.0, 1),
            ])


def _plant_models(root):
    seg_dir = os.path.join(root, "sherpa-onnx-pyannote-segmentation-3-0")
    os.makedirs(seg_dir, exist_ok=True)
    with open(os.path.join(seg_dir, "model.onnx"), "wb") as f:
        f.write(b"onnx")
    with open(os.path.join(root, diarize.EMB_NAME), "wb") as f:
        f.write(b"onnx")


def test_model_paths_rejects_empty_files(tmp_path):
    """0바이트 파일을 설치된 모델로 보면 runtime.status 와 어긋난다."""
    root = str(tmp_path)
    seg_dir = os.path.join(root, "sherpa-onnx-pyannote-segmentation-3-0")
    os.makedirs(seg_dir, exist_ok=True)
    open(os.path.join(seg_dir, "model.onnx"), "wb").close()
    open(os.path.join(root, diarize.EMB_NAME), "wb").close()
    assert diarize._model_paths(root) == ("", "")
    _plant_models(root)
    seg, emb = diarize._model_paths(root)
    assert os.path.isfile(seg) and os.path.getsize(seg) > 0
    assert os.path.isfile(emb) and os.path.getsize(emb) > 0


def test_diarize_fake_engine_korean_labels(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "talk.wav"), nframes=1600, sample=1000)
    _plant_models(str(tmp_path))
    monkeypatch.setattr(diarize, "_import_sherpa", lambda: _FakeSherpa)
    monkeypatch.setattr(diarize, "_get_model_dir", lambda: str(tmp_path))
    r = diarize.diarize(p, num_speakers=-1)
    assert r["ok"] is True
    assert r["error"] == ""
    assert r["num_speakers"] == 2
    assert [t["speaker"] for t in r["turns"]] == ["화자1", "화자2"]
    assert r["turns"][0]["start"] == 0.0
    assert r["turns"][1]["end"] == 3.0
    last = _FakeSherpa.FastClusteringConfig.last
    assert last is not None
    assert last.num_clusters == -1


def test_diarize_num_speakers_passed_to_cluster(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "talk.wav"), nframes=1600, sample=1000)
    _plant_models(str(tmp_path))
    monkeypatch.setattr(diarize, "_import_sherpa", lambda: _FakeSherpa)
    monkeypatch.setattr(diarize, "_get_model_dir", lambda: str(tmp_path))
    r = diarize.diarize(p, num_speakers=3)
    assert r["ok"] is True
    assert _FakeSherpa.FastClusteringConfig.last.num_clusters == 3


def test_diarize_silent_wav(monkeypatch, tmp_path):
    p = _write_wav(str(tmp_path / "silence.wav"), nframes=1600, sample=0)
    _plant_models(str(tmp_path))
    monkeypatch.setattr(diarize, "_import_sherpa", lambda: _FakeSherpa)
    monkeypatch.setattr(diarize, "_get_model_dir", lambda: str(tmp_path))
    r = diarize.diarize(p)
    assert r["ok"] is False
    assert "무음" in r["error"] or "음성" in r["error"]
