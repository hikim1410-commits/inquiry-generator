# -*- coding: utf-8 -*-
"""전사본 → 노트 요약 실행·청킹·병합 (네트워크 무의존).

핵심 불변식:
1. 임계값 이하면 LLM을 한 번만 부르고 chunked=False.
2. 분할은 세그먼트 중간을 자르지 않고, 여유분 안에서 화자 전환·무음을 경계로 삼는다.
3. 구간 하나라도 실패하면 병합을 시도하지 않고 그대로 실패를 올린다.
"""
import unittest.mock as mock

from src.ai.note import NOTE_MERGE_DIRECTIVE
from src.note import summarize as SU


def _segs(n, chars=100, speaker="화자1", start=0.0):
    return [{"start": start + i, "end": start + i + 0.9,
             "text": "가" * chars, "speaker": speaker} for i in range(n)]


class TestSplit:
    def test_empty(self):
        assert SU.split_segments([]) == []
        assert SU.split_segments(None) == []

    def test_under_limit_is_single_chunk(self):
        assert len(SU.split_segments(_segs(10, 100), chunk_chars=5000)) == 1

    def test_over_limit_splits(self):
        chunks = SU.split_segments(_segs(30, 100), chunk_chars=1000)
        assert len(chunks) > 1
        assert sum(len(c) for c in chunks) == 30      # 세그먼트 유실 없음

    def test_never_splits_inside_a_segment(self):
        chunks = SU.split_segments(_segs(9, 500), chunk_chars=1000)
        flat = [s for c in chunks for s in c]
        assert flat == _segs(9, 500)

    def test_extends_within_slack_to_reach_speaker_change(self):
        # 3개(각 400자, 같은 화자) 뒤 화자가 바뀐다. 상한 1000 → 3번째에서 넘지만
        # 여유분(1100) 안이고 4번째가 같은 화자면 더 담고, 화자가 바뀌면 끊는다.
        segs = _segs(3, 400, "화자1") + _segs(3, 400, "화자2", start=10.0)
        chunks = SU.split_segments(segs, chunk_chars=1000)
        assert [s["speaker"] for s in chunks[0]] == ["화자1"] * 3

    def test_long_silence_counts_as_boundary(self):
        segs = _segs(3, 400, "화자1")
        segs += _segs(2, 400, "화자1", start=100.0)   # 앞과 큰 간격
        chunks = SU.split_segments(segs, chunk_chars=1000)
        assert len(chunks[0]) == 3


class TestSummarize:
    def test_no_segments(self):
        r = SU.summarize([], "gemini", "key", "m")
        assert r["ok"] is False and r["chunks"] == 0

    def test_single_chunk_calls_llm_once(self):
        with mock.patch.object(SU, "summarize_note",
                               return_value={"ok": True, "summary": {"one_liner": "요약"}}) as m:
            r = SU.summarize(_segs(5, 100), "gemini", "key", "m", chunk_chars=5000)
        assert m.call_count == 1
        assert r["ok"] and r["chunked"] is False and r["chunks"] == 1

    def test_multi_chunk_calls_per_chunk_plus_merge(self):
        calls = []

        def fake(provider, text, key, model, timeout=120, directive=None):
            calls.append(directive)
            return {"ok": True, "summary": {"one_liner": "부분"}}

        with mock.patch.object(SU, "summarize_note", side_effect=fake):
            r = SU.summarize(_segs(30, 100), "gemini", "key", "m", chunk_chars=1000)
        assert r["ok"] and r["chunked"] is True
        assert len(calls) == r["chunks"] + 1          # 구간별 + 병합 1회
        assert calls[-1] == NOTE_MERGE_DIRECTIVE      # 마지막이 병합 지침

    def test_chunk_failure_aborts_before_merge(self):
        seq = [{"ok": True, "summary": {}}, {"ok": False, "error": "타임아웃"}]
        with mock.patch.object(SU, "summarize_note", side_effect=seq) as m:
            r = SU.summarize(_segs(30, 100), "gemini", "key", "m", chunk_chars=1000)
        assert r["ok"] is False and r["error"] == "타임아웃"
        assert r["chunked"] is True
        assert m.call_count == 2                      # 병합까지 가지 않는다

    def test_progress_callback(self):
        seen = []
        with mock.patch.object(SU, "summarize_note",
                               return_value={"ok": True, "summary": {}}):
            SU.summarize(_segs(30, 100), "gemini", "key", "m",
                         chunk_chars=1000, on_progress=lambda d, t: seen.append((d, t)))
        assert seen[-1][0] == seen[-1][1] > 1

    def test_broken_progress_callback_does_not_break_summary(self):
        def boom(d, t):
            raise RuntimeError("UI 죽음")

        with mock.patch.object(SU, "summarize_note",
                               return_value={"ok": True, "summary": {}}):
            r = SU.summarize(_segs(5, 100), "gemini", "key", "m",
                             chunk_chars=5000, on_progress=boom)
        assert r["ok"] is True

    def test_merge_input_preserves_timestamps(self):
        seen = {}

        def fake(provider, text, key, model, timeout=120, directive=None):
            if directive == NOTE_MERGE_DIRECTIVE:
                seen["text"] = text
                return {"ok": True, "summary": {}}
            return {"ok": True,
                    "summary": {"decisions": [{"text": "단가 확정", "t_ms": 252000}]}}

        with mock.patch.object(SU, "summarize_note", side_effect=fake):
            SU.summarize(_segs(30, 100), "gemini", "key", "m", chunk_chars=1000)
        assert "## 구간 1" in seen["text"]
        assert "단가 확정 (04:12)" in seen["text"]
