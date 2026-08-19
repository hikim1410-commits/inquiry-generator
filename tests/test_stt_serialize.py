# -*- coding: utf-8 -*-
"""src/stt/serialize.py — 엔진/모델/녹음 없이 순수 변환만 검증한다."""
import copy
import json
import math

import pytest

from src.stt import serialize


SEG_A = {
    "start": 12.3,
    "end": 19.8,
    "text": "오늘 안건은 라이다 센서 단가입니다.",
    "speaker": "화자1",
}
SEG_B = {
    "start": 31.0,
    "end": 40.2,
    "text": "TRL 기준으로 보면 level 5 정도입니다.",
    "speaker": "화자2",
}
SEG_NO_SPEAKER = {
    "start": 754.0,
    "end": 760.0,
    "text": "배경 안내 멘트",
    "speaker": "",
}


def test_import_serialize_has_contract_functions():
    assert callable(serialize.format_ts)
    assert callable(serialize.to_plain_text)
    assert callable(serialize.to_transcript_json)
    assert callable(serialize.apply_speaker_names)
    assert callable(serialize.to_minutes_input)


# ====== format_ts ======

@pytest.mark.parametrize(
    "sec, expected",
    [
        (0, "00:00"),
        (12.3, "00:12"),
        (59.9, "00:59"),
        (60, "01:00"),
        (754, "12:34"),
        (754.0, "12:34"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3725.0, "1:02:05"),
        (-1, "00:00"),
        (-0.1, "00:00"),
        (None, "00:00"),
    ],
)
def test_format_ts_contract_cases(sec, expected):
    assert serialize.format_ts(sec) == expected


def test_format_ts_rejects_nan_inf_and_bool():
    assert serialize.format_ts(float("nan")) == "00:00"
    assert serialize.format_ts(math.inf) == "00:00"
    assert serialize.format_ts(True) == "00:00"


# ====== to_plain_text ======

def test_to_plain_text_mixed_speakers():
    text = serialize.to_plain_text([SEG_A, SEG_NO_SPEAKER, SEG_B])
    lines = text.split("\n")
    assert lines[0] == "[00:12] 화자1: 오늘 안건은 라이다 센서 단가입니다."
    assert lines[1] == "[12:34] 배경 안내 멘트"
    assert lines[2] == "[00:31] 화자2: TRL 기준으로 보면 level 5 정도입니다."


def test_to_plain_text_empty_and_none():
    assert serialize.to_plain_text([]) == ""
    assert serialize.to_plain_text(None) == ""


def test_to_plain_text_hour_timestamp():
    seg = {"start": 3725.0, "end": 3730.0, "text": "마무리", "speaker": "화자1"}
    assert serialize.to_plain_text([seg]) == "[1:02:05] 화자1: 마무리"


# ====== apply_speaker_names ======

def test_apply_speaker_names_maps_and_keeps_unknown():
    segs = [dict(SEG_A), dict(SEG_B), dict(SEG_NO_SPEAKER)]
    out = serialize.apply_speaker_names(segs, {"화자1": "내비온 김형일"})
    assert out[0]["speaker"] == "내비온 김형일"
    assert out[1]["speaker"] == "화자2"
    assert out[2]["speaker"] == ""
    assert out[0]["text"] == SEG_A["text"]


def test_apply_speaker_names_does_not_mutate_original():
    segs = [dict(SEG_A), dict(SEG_B)]
    snapshot = copy.deepcopy(segs)
    out = serialize.apply_speaker_names(segs, {"화자1": "내비온 김형일", "화자2": "KIST 김종민"})
    assert segs == snapshot
    assert segs[0] is not out[0]
    assert segs[1] is not out[1]
    assert segs[0]["speaker"] == "화자1"
    assert out[0]["speaker"] == "내비온 김형일"
    assert out is not segs


def test_apply_speaker_names_empty_mapping_and_none_segments():
    segs = [dict(SEG_A)]
    out = serialize.apply_speaker_names(segs, {})
    assert out[0]["speaker"] == "화자1"
    assert out[0] is not segs[0]
    assert serialize.apply_speaker_names(None, {"화자1": "X"}) == []
    assert serialize.apply_speaker_names(segs, None)[0]["speaker"] == "화자1"


# ====== to_transcript_json ======

def test_to_transcript_json_required_keys_and_json_dumps():
    meta = {
        "source": "회의녹음.m4a",
        "duration_sec": 3792,
        "model": "medium",
        "language": "ko",
        "engine": "faster-whisper",
    }
    payload = serialize.to_transcript_json([SEG_A, SEG_B, SEG_A], meta)
    assert payload["schema_version"] == serialize.SCHEMA_VERSION
    assert payload["schema_version"] == 1
    assert isinstance(payload["created"], str) and payload["created"]
    assert payload["meta"]["source"] == "회의녹음.m4a"
    assert payload["meta"]["duration_sec"] == pytest.approx(3792.0)
    assert payload["meta"]["model"] == "medium"
    assert payload["meta"]["language"] == "ko"
    assert payload["meta"]["engine"] == "faster-whisper"
    assert payload["speakers"] == ["화자1", "화자2"]
    assert payload["segments"] == [
        {"start": 12.3, "end": 19.8, "text": SEG_A["text"], "speaker": "화자1"},
        {"start": 31.0, "end": 40.2, "text": SEG_B["text"], "speaker": "화자2"},
        {"start": 12.3, "end": 19.8, "text": SEG_A["text"], "speaker": "화자1"},
    ]
    dumped = json.dumps(payload, ensure_ascii=False)
    loaded = json.loads(dumped)
    assert loaded["schema_version"] == 1
    assert loaded["speakers"] == ["화자1", "화자2"]
    assert len(loaded["segments"]) == 3


def test_to_transcript_json_empty_meta_and_segments():
    payload = serialize.to_transcript_json([], None)
    dumped = json.dumps(payload)
    assert payload["segments"] == []
    assert payload["speakers"] == []
    assert payload["meta"]["source"] == ""
    assert payload["meta"]["duration_sec"] == 0.0
    assert payload["meta"]["model"] == ""
    assert payload["meta"]["language"] == ""
    assert json.loads(dumped)["schema_version"] == 1


def test_to_transcript_json_drops_extra_segment_keys():
    seg = dict(SEG_A)
    seg["confidence"] = 0.91
    payload = serialize.to_transcript_json([seg], {"filename": "a.wav"})
    assert payload["meta"]["source"] == "a.wav"
    assert set(payload["segments"][0]) == {"start", "end", "text", "speaker"}


# ====== to_minutes_input ======

def test_to_minutes_input_applies_speaker_names():
    text = serialize.to_minutes_input(
        [SEG_A, SEG_B],
        speaker_names={"화자1": "내비온 김형일", "화자2": "KIST 김종민"},
    )
    assert text.startswith("[회의 전사본]\n참석자(화자): 내비온 김형일, KIST 김종민\n")
    assert "[00:12] 내비온 김형일: 오늘 안건은 라이다 센서 단가입니다." in text
    assert "[00:31] KIST 김종민: TRL 기준으로 보면 level 5 정도입니다." in text
    assert "화자1" not in text
    assert "화자2" not in text


def test_to_minutes_input_empty_segments_no_exception():
    text = serialize.to_minutes_input([])
    assert text == "[회의 전사본]\n참석자(화자):"
    assert serialize.to_minutes_input(None) == "[회의 전사본]\n참석자(화자):"


def test_to_minutes_input_unmapped_keeps_label():
    text = serialize.to_minutes_input([SEG_A, SEG_NO_SPEAKER])
    assert "참석자(화자): 화자1" in text
    assert "[00:12] 화자1: 오늘 안건은 라이다 센서 단가입니다." in text
    assert "[12:34] 배경 안내 멘트" in text
