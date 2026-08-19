# -*- coding: utf-8 -*-
"""노트 사이드카 저장/로드 + 회의록 초안 입력 직렬화.

핵심 불변식:
1. load는 어떤 상태의 파일이 와도 §6.2 전체 형을 돌려준다 (호출부가 .get 체인을 안 쓴다).
2. created_at은 재저장으로 덮이지 않는다.
3. 근거 시각이 없는 항목은 (00:00)이 아니라 괄호 자체가 없다.
"""
import json
import os

from src.ai.note import NO_EVIDENCE
from src.note import serialize as S
from src.note import store as ST


class TestSidecarPath:
    def test_from_hwpx(self, tmp_path):
        p = str(tmp_path / "2026-08-19_협의3차.hwpx")
        assert ST.sidecar_path(p).endswith("2026-08-19_협의3차.note.json")

    def test_double_extension_siblings_resolve_to_same_base(self, tmp_path):
        base = str(tmp_path / "회의")
        for src in (base + ".hwpx", base + ".minutes.json",
                    base + ".transcript.json", base + ".note.json",
                    base + ".transcript.txt"):
            assert ST.sidecar_path(src) == base + ".note.json", src

    def test_plain_json_without_known_tail_is_kept(self, tmp_path):
        p = str(tmp_path / "자료.json")
        assert ST.sidecar_path(p) == str(tmp_path / "자료.note.json")


class TestSaveLoad:
    def test_roundtrip(self, tmp_path):
        base = str(tmp_path / "회의.hwpx")
        note = ST.empty_note()
        note["title"] = "기술이전 협의 3차"
        note["duration_sec"] = 3721
        note["speakers"] = [{"key": "화자1", "name": "김형일", "org": "내비온"}]
        note["tags"] = ["기술이전"]
        path = ST.save_note(base, note)
        assert os.path.exists(path)
        got = ST.load_note(base)
        assert got["title"] == "기술이전 협의 3차"
        assert got["speakers"][0]["name"] == "김형일"
        assert got["duration_sec"] == 3721.0
        assert got["created_at"]  # 저장 시 자동 기입

    def test_created_at_survives_resave(self, tmp_path):
        base = str(tmp_path / "회의.hwpx")
        ST.save_note(base, {"title": "1차"})
        first = ST.load_note(base)["created_at"]
        ST.save_note(base, {"title": "2차"})
        again = ST.load_note(base)
        assert again["created_at"] == first
        assert again["title"] == "2차"

    def test_missing_file_yields_empty_note(self, tmp_path):
        assert ST.load_note(str(tmp_path / "없음.hwpx")) == ST.empty_note()

    def test_corrupt_existing_file_does_not_block_save(self, tmp_path):
        base = str(tmp_path / "회의.hwpx")
        with open(ST.sidecar_path(base), "w", encoding="utf-8") as fp:
            fp.write("{깨진 json")
        ST.save_note(base, {"title": "복구"})
        assert ST.load_note(base)["title"] == "복구"

    def test_partial_file_is_filled_to_full_shape(self, tmp_path):
        base = str(tmp_path / "회의.hwpx")
        with open(ST.sidecar_path(base), "w", encoding="utf-8") as fp:
            json.dump({"title": "손으로 쓴 노트"}, fp, ensure_ascii=False)
        got = ST.load_note(base)
        assert set(got) == set(ST.empty_note())
        assert got["source"]["kind"] == "text"
        assert got["links"]["hwpx"] == ""


class TestNormalize:
    def test_bad_types_are_dropped_not_raised(self):
        got = ST.normalize_note({
            "duration_sec": "몰라",
            "speakers": ["문자열", {"name": "키없음"}, {"key": "화자1"}],
            "bookmarks": [{"t_ms": -5, "seg_id": "x"}],
            "tags": ["a", "a", ""],
            "source": {"kind": "video"},
        })
        assert got["duration_sec"] == 0.0
        assert [s["key"] for s in got["speakers"]] == ["화자1"]
        assert got["bookmarks"][0]["t_ms"] == 0 and got["bookmarks"][0]["id"] == "b1"
        assert got["tags"] == ["a"]
        assert got["source"]["kind"] == "text"   # 미지의 kind는 text로

    def test_non_dict_input(self):
        assert ST.normalize_note(None) == ST.empty_note()


class TestFormatTs:
    def test_under_hour_is_mm_ss(self):
        assert S.format_ts(252000) == "04:12"

    def test_over_hour_includes_hours(self):
        assert S.format_ts(3721000) == "1:02:01"

    def test_no_evidence_is_blank(self):
        assert S.format_ts(NO_EVIDENCE) == ""
        assert S.format_ts(None) == ""

    def test_zero_is_shown(self):
        assert S.format_ts(0) == "00:00"


class TestToMinutesInput:
    def _summary(self):
        return {
            "one_liner": "라이다 단가와 기술료 배분 합의",
            "topics": [{"title": "단가 협의", "t_ms": 252000,
                        "points": ["양산 기준 단가 18만원 선에서 접점",
                                   "초도 물량 500대 조건부"]}],
            "decisions": [{"text": "초도 500대 조건부 단가 18만원", "t_ms": 543000}],
            "action_items": [{"task": "계약 초안 송부", "owner": "김형일",
                              "due": "9/4", "t_ms": 3318000}],
            "open_issues": [{"text": "기술료 지급 시점", "t_ms": NO_EVIDENCE}],
        }

    def test_full_shape(self):
        speakers = [{"key": "화자1", "name": "김형일", "org": "내비온"},
                    {"key": "화자2", "name": "김종민", "org": "KIST"}]
        out = S.to_minutes_input(self._summary(), speakers)
        assert "[회의 요약]" in out and "라이다 단가와 기술료 배분 합의" in out
        assert "[참석자]\n내비온 김형일 / KIST 김종민" in out
        assert "■ 단가 협의 (04:12)" in out
        assert "  - 초도 물량 500대 조건부" in out
        assert "- 초도 500대 조건부 단가 18만원 (09:03)" in out
        assert "- 계약 초안 송부 — 김형일 — 9/4 (55:18)" in out

    def test_missing_evidence_has_no_parenthesis(self):
        out = S.to_minutes_input(self._summary())
        assert "- 기술료 지급 시점" in out
        assert "기술료 지급 시점 (" not in out

    def test_empty_sections_are_omitted(self):
        out = S.to_minutes_input({"one_liner": "한 줄뿐"})
        assert out == "[회의 요약]\n한 줄뿐"

    def test_action_item_without_owner_due(self):
        out = S.to_minutes_input({"action_items": [{"task": "확인", "t_ms": 1000}]})
        assert out == "[할 일]\n- 확인 (00:01)"

    def test_memo_appended(self):
        out = S.to_minutes_input({"one_liner": "요약"}, memo="개인 메모")
        assert out.endswith("[사용자 메모]\n개인 메모")

    def test_garbage_input_does_not_raise(self):
        assert S.to_minutes_input(None) == ""
        assert S.to_minutes_input({"topics": ["문자열"], "decisions": [None]}) == ""

    def test_output_is_far_shorter_than_transcript(self):
        # §6.4 목적: 전사본 원문의 1/20 이하
        out = S.to_minutes_input(self._summary())
        assert len(out) < 600


class TestReviewRegressions:
    def test_dotted_stem_is_not_truncated(self, tmp_path):
        # '정례회의 2026.08' 처럼 점이 든 무확장 베이스 — '.08'은 확장자가 아니다
        base = str(tmp_path / "정례회의 2026.08")
        assert ST.sidecar_path(base) == base + ".note.json"
        # 전사본 경로 역조회도 같은 노트 경로로 수렴해야 형제 파일 짝이 맞는다
        assert ST.sidecar_path(base + ".transcript.json") == base + ".note.json"

    def test_dotted_stem_roundtrip(self, tmp_path):
        base = str(tmp_path / "정례회의 2026.08")
        ST.save_note(base, {"title": "점 포함"})
        assert ST.load_note(base)["title"] == "점 포함"

    def test_load_broken_json_returns_empty_note(self, tmp_path):
        base = str(tmp_path / "회의.hwpx")
        with open(ST.sidecar_path(base), "w", encoding="utf-8") as fp:
            fp.write("{깨진 json")
        assert ST.load_note(base) == ST.empty_note()
