# -*- coding: utf-8 -*-
"""전사 세그먼트 → 사이드카 JSON / 사람용 텍스트 / 회의록 AI 입력.

순수 변환만 한다. 파일 I/O·엔진 호출은 호출자 몫이다.
시각은 초(float)로 받고, 표시용 문자열은 여기서만 만든다.
"""
import math
from datetime import datetime

SCHEMA_VERSION = 1

# .transcript.json 의 meta 에 항상 두는 키 (없으면 빈 값).
_META_SOURCE = "source"
_META_DURATION = "duration_sec"
_META_MODEL = "model"
_META_LANGUAGE = "language"


def format_ts(sec) -> str:
    """초 → 표시용 시각. 1시간 미만 mm:ss, 이상 h:mm:ss.

    소수점은 버린다(12.3 → 00:12). 음수·None·비숫자는 00:00.
    """
    total = _nonneg_int_sec(sec)
    if total is None:
        return "00:00"
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def to_plain_text(segments) -> str:
    """사람이 읽는 .transcript.txt 본문. 한 줄에 세그먼트 하나.

    화자가 있으면 "[mm:ss] 화자1: 발화", 없으면 "[mm:ss] 발화".
    """
    lines = []
    for seg in _iter_segments(segments):
        ts = format_ts(seg.get("start"))
        text = str(seg.get("text") or "")
        speaker = str(seg.get("speaker") or "").strip()
        if speaker:
            lines.append(f"[{ts}] {speaker}: {text}")
        else:
            lines.append(f"[{ts}] {text}")
    return "\n".join(lines)


def to_transcript_json(segments, meta) -> dict:
    """회의록 근거 원본(.transcript.json) 본문.

    스키마 버전을 넣어 이후 형식 변경 시 옛 파일을 구분한다.
    """
    segs = [_segment_public(s) for s in _iter_segments(segments)]
    return {
        "schema_version": SCHEMA_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "meta": _normalize_meta(meta),
        "speakers": _unique_speakers(segs),
        "segments": segs,
    }


def apply_speaker_names(segments, mapping) -> list:
    """{"화자1": "내비온 김형일"} 매핑을 세그먼트에 일괄 반영.

    매핑에 없는 화자는 그대로 둔다. 원본 리스트·딕셔너리는 변형하지 않는다.
    """
    names = mapping if isinstance(mapping, dict) else {}
    out = []
    for seg in _iter_segments(segments):
        new = dict(seg)
        speaker = new.get("speaker")
        if speaker in names:
            new["speaker"] = names[speaker]
        out.append(new)
    return out


def to_minutes_input(segments, speaker_names=None) -> str:
    """minutes_draft(description=...) 에 넣을 전사본 텍스트.

    참석자 줄은 MINUTES_SCHEMA.participants(기관·실명 귀속)를 AI가
    채우도록 화자 목록을 앞에 둔다. 본문에 없는 일시·장소·사업명은 쓰지 않는다.
    """
    segs = apply_speaker_names(segments, speaker_names) if speaker_names else list(
        _iter_segments(segments)
    )
    attendees = ", ".join(_unique_speakers(segs))
    lines = [
        "[회의 전사본]",
        f"참석자(화자): {attendees}".rstrip(),
    ]
    body = to_plain_text(segs)
    if body:
        lines.append("")
        lines.append(body)
    return "\n".join(lines)


# ── 내부 ─────────────────────────────────────────────────────────────────────


def _nonneg_int_sec(sec):
    """표시용으로 쓸 비음수 정수 초. 쓸 수 없으면 None."""
    if sec is None or isinstance(sec, bool):
        return None
    try:
        value = float(sec)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value) or value < 0:
        return None
    return int(value)


def _as_float(value, default=0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return x


def _iter_segments(segments):
    if not isinstance(segments, (list, tuple)):
        return []
    return [s for s in segments if isinstance(s, dict)]


def _segment_public(seg) -> dict:
    """계약 Segment 네 키만. 사이드카가 스키마를 벗어나지 않게 한다."""
    return {
        "start": _as_float(seg.get("start")),
        "end": _as_float(seg.get("end")),
        "text": str(seg.get("text") or ""),
        "speaker": str(seg.get("speaker") or ""),
    }


def _unique_speakers(segments) -> list:
    seen = []
    for seg in _iter_segments(segments):
        speaker = str(seg.get("speaker") or "").strip()
        if speaker and speaker not in seen:
            seen.append(speaker)
    return seen


def _normalize_meta(meta) -> dict:
    src = meta if isinstance(meta, dict) else {}
    out = {
        _META_SOURCE: str(src.get(_META_SOURCE) or src.get("filename") or ""),
        _META_DURATION: _as_float(src.get(_META_DURATION)),
        _META_MODEL: str(src.get(_META_MODEL) or ""),
        _META_LANGUAGE: str(src.get(_META_LANGUAGE) or ""),
    }
    for key, value in src.items():
        if key in out or key == "filename":
            continue
        out[key] = value
    return out
