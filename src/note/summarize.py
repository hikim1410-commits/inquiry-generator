# -*- coding: utf-8 -*-
"""전사본 → 노트 요약 실행 (PRD_회의록노트 §7.4).

청킹의 목적은 LLM 입력 상한 방어가 **아니다**(60분 회의 ≈ 3만~5만 자, 상한의
수십분의 일). 로컬 모델 호환성과 lost-in-the-middle 완화를 위한 안전장치다.
그래서 기본 임계값을 넘지 않으면 한 번에 요약하고 chunked=False로 둔다.

분할 경계는 **화자 전환·무음 구간**이며 세그먼트 중간을 자르지 않는다.
부분 요약들은 사람이 읽는 텍스트로 되돌려 한 번 더 요약해 병합한다.
"""
from __future__ import annotations

from src.ai.note import NOTE_MERGE_DIRECTIVE, summarize_note
from src.note import serialize as note_serialize
from src.stt import serialize as stt_serialize

# 한 번에 넘길 전사본 글자 수. 이 값을 넘으면 구간 분할한다.
# ponytail: 로컬 모델 호환 목적의 보수적 고정값. 프로바이더별 실측이 쌓이면 설정으로 뺀다.
CHUNK_CHARS = 20_000

# 경계를 찾으려고 상한을 넘겨도 되는 여유분.
_SLACK = 0.1

# 이 이상 벌어진 침묵은 화자 전환과 동급의 경계로 본다(초).
_GAP_SEC = 2.0


def split_segments(segments, chunk_chars: int = CHUNK_CHARS) -> list:
    """세그먼트 목록 → 구간 목록. 각 구간은 세그먼트 리스트.

    글자 수가 chunk_chars를 넘으면 끊되, 여유분 안에서 화자 전환·무음 구간을
    만나면 거기서 끊는다. 경계를 못 찾으면 세그먼트 경계에서 끊는다.
    """
    segs = [s for s in (segments or []) if isinstance(s, dict)]
    if not segs:
        return []
    limit = max(1, int(chunk_chars))
    slack = limit + int(limit * _SLACK)

    chunks, cur, size = [], [], 0
    for i, seg in enumerate(segs):
        cur.append(seg)
        size += len(str(seg.get("text") or ""))
        if size < limit:
            continue
        # 상한을 넘었다. 여유분 안에 경계가 있으면 그때까지 더 담는다.
        if size <= slack and i + 1 < len(segs) and not _is_boundary(seg, segs[i + 1]):
            continue
        chunks.append(cur)
        cur, size = [], 0
    if cur:
        chunks.append(cur)
    return chunks


def summarize(segments, provider: str, api_key: str, model: str,
              timeout: int = 120, directive=None,
              chunk_chars: int = CHUNK_CHARS, on_progress=None) -> dict:
    """전사 세그먼트 → 노트 요약.

    반환: {ok, summary?, chunked, chunks, error?, model_error?}
    on_progress(done, total): 구간 요약 진행 콜백(분할된 경우만 total>1).
    """
    chunks = split_segments(segments, chunk_chars)
    if not chunks:
        return {"ok": False, "error": "요약할 전사본이 없습니다.",
                "chunked": False, "chunks": 0}

    if len(chunks) == 1:
        _tick(on_progress, 0, 1)
        r = summarize_note(provider, stt_serialize.to_plain_text(chunks[0]),
                           api_key, model, timeout=timeout, directive=directive)
        _tick(on_progress, 1, 1)
        r.setdefault("chunked", False)
        r["chunks"] = 1
        return r

    total = len(chunks)
    partials = []
    for i, chunk in enumerate(chunks):
        _tick(on_progress, i, total)
        r = summarize_note(provider, stt_serialize.to_plain_text(chunk),
                           api_key, model, timeout=timeout, directive=directive)
        if not r.get("ok"):
            r["chunked"] = True
            r["chunks"] = total
            return r
        partials.append(r["summary"])
    _tick(on_progress, total, total)

    merged = summarize_note(provider, _partials_text(partials),
                            api_key, model, timeout=timeout,
                            directive=NOTE_MERGE_DIRECTIVE)
    merged["chunked"] = True
    merged["chunks"] = total
    return merged


# ── 내부 ─────────────────────────────────────────────────────────────────────


def _is_boundary(cur, nxt) -> bool:
    """다음 세그먼트가 화자 전환이거나 긴 침묵 뒤면 경계."""
    if str(cur.get("speaker") or "") != str(nxt.get("speaker") or ""):
        return True
    try:
        return float(nxt.get("start") or 0.0) - float(cur.get("end") or 0.0) >= _GAP_SEC
    except (ValueError, TypeError):
        return False


def _partials_text(partials) -> str:
    """부분 요약들 → 병합 단계 입력 텍스트. t_ms는 표기로 보존된다."""
    blocks = []
    for i, p in enumerate(partials, 1):
        body = note_serialize.to_minutes_input(p)
        blocks.append("## 구간 %d\n%s" % (i, body if body else "(요약 없음)"))
    return "\n\n".join(blocks)


def _tick(cb, done, total):
    if not callable(cb):
        return
    try:
        cb(done, total)
    except Exception:
        pass  # 진행 표시 실패가 요약을 막지 않는다
