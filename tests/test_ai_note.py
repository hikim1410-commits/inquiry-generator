# -*- coding: utf-8 -*-
"""회의 노트 AI 요약 — 스키마·프롬프트·정규화 (네트워크 무의존).

핵심 불변식:
1. 사용자 directive는 str.format을 절대 통과하지 않는다 ({} 포함 안전).
2. 데이터 블록(전사본)은 directive와 무관하게 항상 첨부된다.
3. 근거 시각이 없거나 깨진 항목은 NO_EVIDENCE(-1)로 떨어진다 — 조용히 0이 되지 않는다.
"""
import unittest.mock as mock

from src.ai import note as N


class TestPrompt:
    def test_default_contains_directive_and_data(self):
        p = N.build_note_prompt("[00:12] 화자1: 단가 이야기를 했다")
        assert p.startswith("너는 회의 전사본을 정리하는 회의 기록 전문가다.")
        assert "추측 금지" in p
        assert "## 전사본" in p and "단가 이야기를 했다" in p

    def test_custom_directive_replaces_head_keeps_data(self):
        p = N.build_note_prompt("전사본 본문", directive="너는 테스트 봇이다.")
        assert p.startswith("너는 테스트 봇이다.")
        assert "회의 기록 전문가" not in p
        assert "## 전사본" in p and "전사본 본문" in p

    def test_braces_in_user_text_are_safe(self):
        # 사용자 텍스트가 .format을 통과하면 여기서 KeyError가 난다
        p = N.build_note_prompt("{transcript} {0} {알수없음}",
                                directive="지침 {mystery}")
        assert "{mystery}" in p and "{알수없음}" in p


class TestNormalizeEvidence:
    def test_missing_or_broken_t_ms_becomes_no_evidence(self):
        out = N._normalize_note({
            "decisions": [
                {"text": "시각 있음", "t_ms": 9000},
                {"text": "시각 없음"},
                {"text": "시각 음수", "t_ms": -5},
                {"text": "시각 문자열", "t_ms": "몰라"},
            ],
        })
        assert [d["t_ms"] for d in out["decisions"]] == [
            9000, N.NO_EVIDENCE, N.NO_EVIDENCE, N.NO_EVIDENCE]

    def test_zero_t_ms_is_kept(self):
        # 회의 첫 발언은 0ms다. 근거 미확인과 섞이면 안 된다.
        out = N._normalize_note({"decisions": [{"text": "첫 발언", "t_ms": 0}]})
        assert out["decisions"][0]["t_ms"] == 0


class TestNormalizeShape:
    def test_empty_input_yields_full_shape(self):
        out = N._normalize_note({})
        assert out == {"one_liner": "", "topics": [], "keywords": [],
                       "decisions": [], "action_items": [], "open_issues": []}

    def test_non_dict_input_does_not_raise(self):
        assert N._normalize_note(None)["topics"] == []

    def test_action_item_owner_due_default_empty(self):
        out = N._normalize_note({"action_items": [{"task": "계약 초안 송부"}]})
        row = out["action_items"][0]
        assert row["owner"] == "" and row["due"] == ""
        assert row["t_ms"] == N.NO_EVIDENCE

    def test_blank_rows_are_dropped(self):
        out = N._normalize_note({
            "action_items": [{"task": "   "}, {"task": "실제 할 일"}],
            "decisions": [{"text": ""}, "문자열아님", {"text": "실제 결정"}],
            "topics": [{"title": "", "points": []}, {"title": "주제", "points": ["요지"]}],
        })
        assert len(out["action_items"]) == 1
        assert len(out["decisions"]) == 1
        assert len(out["topics"]) == 1

    def test_newlines_collapse_to_single_line(self):
        out = N._normalize_note({"one_liner": "앞줄\n뒷줄\t탭"})
        assert out["one_liner"] == "앞줄 뒷줄 탭"

    def test_keywords_dedup_and_trim(self):
        out = N._normalize_note({"keywords": ["라이다", "라이다", " KIST ", ""]})
        assert out["keywords"] == ["라이다", "KIST"]


class TestSummarizeNote:
    def test_missing_api_key_short_circuits(self):
        r = N.summarize_note("gemini", "전사본", "", "model-x")
        assert r["ok"] is False and "API 키" in r["error"]

    def test_success_returns_normalized_summary(self):
        raw = {"one_liner": "라이다 단가 합의",
               "action_items": [{"task": "초안 송부", "t_ms": "3000"}]}
        with mock.patch.object(N, "complete_json",
                               return_value={"ok": True, "data": raw}) as m:
            r = N.summarize_note("gemini", "[00:03] 화자1: ...", "key", "model-x")
        assert r["ok"] is True
        assert r["summary"]["one_liner"] == "라이다 단가 합의"
        assert r["summary"]["action_items"][0]["t_ms"] == 3000
        assert m.call_args.kwargs["schema"] is N.NOTE_SUMMARY_SCHEMA

    def test_llm_error_passes_through(self):
        with mock.patch.object(N, "complete_json",
                               return_value={"ok": False, "error": "타임아웃"}):
            r = N.summarize_note("gemini", "전사본", "key", "model-x")
        assert r["ok"] is False and r["error"] == "타임아웃"


class TestSchemaContract:
    def test_all_evidence_bearing_items_require_t_ms(self):
        props = N.NOTE_SUMMARY_SCHEMA["properties"]
        for key in ("topics", "decisions", "action_items", "open_issues"):
            item = props[key]["items"]
            assert "t_ms" in item["properties"], key
            assert "t_ms" in item["required"], key


class TestStringIterableGuard:
    def test_string_points_and_keywords_are_dropped_not_split(self):
        # 문자열은 iterable이라 가드 없이는 글자 단위로 쪼개진다
        out = N._normalize_note({
            "topics": [{"title": "t", "t_ms": 5000, "points": "가나다"}],
            "keywords": "키워드",
        })
        assert out["topics"][0]["points"] == []
        assert out["keywords"] == []
