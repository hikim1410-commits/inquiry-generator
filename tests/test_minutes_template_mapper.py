# -*- coding: utf-8 -*-
"""T-A2-1 / T-A3-1: fieldmap v2 저장(save_minutes_cellmap) + 스키마 검증.

COM·AI·네트워크 불필요 — 기본 pytest로 실행.
"""
import os

import pytest

from src.ai.minutes_template_mapper import (
    save_minutes_cellmap, save_minutes_fieldmap, load_minutes_fieldmap,
    auto_label_cells,
)
import src.ai.minutes_template_mapper as mtm
from src.minutes.hwpx_minutes import DEFAULT_CELLS


# ── auto_label_cells: AI 모킹(llm.complete_json) ──────────────────────────────

_AUTO_GRID = [
    {"table": 0, "row": 0, "col": 0, "text": "회사명"},
    {"table": 0, "row": 0, "col": 1, "text": ""},
    {"table": 0, "row": 1, "col": 0, "text": "제품명"},
    {"table": 0, "row": 1, "col": 1, "text": ""},
]


def _mock_llm(monkeypatch, pins):
    monkeypatch.setattr(mtm.llm, "complete_json",
                        lambda *a, **k: {"ok": True, "data": {"pins": pins}})


def test_auto_label_drops_nonexistent_and_dupes(monkeypatch):
    _mock_llm(monkeypatch, [
        {"table": 0, "row": 0, "col": 1, "label": "회사명"},
        {"table": 0, "row": 1, "col": 1, "label": "제품명"},
        {"table": 0, "row": 9, "col": 9, "label": "없는칸"},     # (a) 존재X
        {"table": 0, "row": 0, "col": 1, "label": "중복좌표"},   # (b) 중복
        {"table": 0, "row": 1, "col": 1, "label": "  "},          # 빈 라벨
    ])
    r = auto_label_cells(_AUTO_GRID, "gemini", "fakekey", "m")
    assert r["ok"]
    coords = [(p["table"], p["row"], p["col"]) for p in r["pins"]]
    assert coords == [(0, 0, 1), (0, 1, 1)]      # 존재·고유·라벨 있는 핀만
    assert r["pins"][0]["label"] == "회사명"      # 첫 중복(둘째 '중복좌표' 거부)


def test_auto_label_no_key():
    r = auto_label_cells(_AUTO_GRID, "gemini", "", "m")   # (c) 키 없음
    assert r["ok"] is False
    assert r["pins"] == []
    assert r.get("error")


def test_auto_label_ai_failure(monkeypatch):
    monkeypatch.setattr(mtm.llm, "complete_json",
                        lambda *a, **k: {"ok": False, "error": "boom"})
    r = auto_label_cells(_AUTO_GRID, "gemini", "fakekey", "m")
    assert r["ok"] is False
    assert r["pins"] == []


@pytest.fixture()
def tpl(tmp_path):
    # 실제 hwpx 불필요 — 저장 경로 계산만 사용(파일 존재 가정 안 함)
    return str(tmp_path / "커스텀양식.hwpx")


# ── T-A2-1: 저장 + 라운드트립 ────────────────────────────────────────────────

def test_save_cellmap_roundtrip(tpl):
    cell_map = {"business_name": [2, 2], "meeting_topic": [3, 1]}
    custom = [{"id": "cs1", "label": "부서", "cell": [1, 2]}]
    anns = [{"row": 4, "col": 1, "label": "검토", "comment": "확인필요", "slot": "content"}]
    res = save_minutes_cellmap(tpl, cell_map, custom, anns)
    assert res["version"] == 2
    fm = load_minutes_fieldmap(tpl)
    assert fm["version"] == 2
    assert fm["cell_map"] == {"business_name": [2, 2], "meeting_topic": [3, 1]}
    assert fm["custom_slots"] == custom
    # table 기본 0 부여(다중 표 후방호환) — 입력에 table 없어도 정규화본엔 포함
    assert fm["annotations"] == [{"table": 0, **anns[0]}]


def test_save_cellmap_is_standard_recalc(tpl):
    # DEFAULT_CELLS는 (table,row,col) 3-tuple(T3)이지만 save_minutes_cellmap의
    # cell_map 저장 포맷은 아직 v2([row,col] 2요소, 표0 전제 — 3요소화는 T5에서).
    # 여기서는 표0 좌표(row,col)만 뽑아 표준 좌표를 구성한다.
    standard = {k: [v[1], v[2]] for k, v in DEFAULT_CELLS.items()}
    res = save_minutes_cellmap(tpl, standard)
    assert res["is_standard"] is True
    res2 = save_minutes_cellmap(tpl, {"business_name": [9, 9]})
    assert res2["is_standard"] is False


def test_save_cellmap_drops_unknown_slot(tpl):
    res = save_minutes_cellmap(tpl, {"bogus_slot": [1, 1], "content": [6, 1]})
    assert "bogus_slot" not in res["cell_map"]
    assert res["cell_map"]["content"] == [6, 1]
    assert "content" not in res["unmapped"]
    assert "business_name" in res["unmapped"]


# ── 후방호환: v1 → load → v2 저장 ─────────────────────────────────────────────

def test_load_v1_without_custom_slots(tpl):
    save_minutes_fieldmap(tpl, {"cell_map": {"business_name": [1, 1]},
                                "unmapped": ["content"]})
    fm = load_minutes_fieldmap(tpl)
    assert fm.get("version") == 1
    assert "custom_slots" not in fm        # v1엔 없음 — 견고 로드
    # v2로 다시 저장해도 기존 cell_map 보존
    res = save_minutes_cellmap(tpl, fm["cell_map"])
    assert res["cell_map"] == {"business_name": [1, 1]}
    assert res["custom_slots"] == []
    assert res["annotations"] == []


# ── T-A3-1: 스키마 검증 ──────────────────────────────────────────────────────

def test_annotations_one_pin_per_cell(tpl):
    anns = [
        {"row": 2, "col": 1, "label": "첫핀"},
        {"row": 2, "col": 1, "label": "둘째핀(거부)"},
        {"row": 3, "col": 1, "label": "다른셀"},
    ]
    res = save_minutes_cellmap(tpl, {}, None, anns)
    coords = [(a["row"], a["col"]) for a in res["annotations"]]
    assert coords == [(2, 1), (3, 1)]      # 중복 (2,1) 둘째는 거부
    assert res["annotations"][0]["label"] == "첫핀"
    assert any("1셀=1핀" in w for w in res["warnings"])


def test_custom_slots_invalid_items_ignored(tpl):
    custom = [
        {"id": "ok1", "label": "정상", "cell": [1, 2]},
        {"id": "", "label": "빈id", "cell": [2, 2]},          # 무시
        {"id": "bad", "label": 123, "cell": [3, 3]},          # 라벨 타입
        {"id": "badcell", "label": "셀오류", "cell": ["a"]},  # 좌표 오류
        {"id": "ok1", "label": "중복id", "cell": [4, 4]},     # 중복 id
    ]
    res = save_minutes_cellmap(tpl, {}, custom, None)
    assert [s["id"] for s in res["custom_slots"]] == ["ok1"]
    assert res["custom_slots"][0]["cell"] == [1, 2]
    assert len(res["warnings"]) >= 4


def test_annotations_bad_coords_ignored(tpl):
    anns = [
        {"row": "x", "col": 1, "label": "좌표오류"},
        {"row": 5, "col": 2, "label": 99},          # 라벨 타입
        {"row": 6, "col": 1, "label": "정상"},
    ]
    res = save_minutes_cellmap(tpl, {}, None, anns)
    assert [(a["row"], a["col"]) for a in res["annotations"]] == [(6, 1)]


# ── T3: 좌표 3요소화 — is_standard_map 2/3요소 판정 ──────────────────────────

def test_is_standard_map_2elem_and_3elem():
    from src.ai.minutes_template_mapper import is_standard_map
    std2 = {"business_name": [1, 1], "meeting_date": [2, 1], "meeting_place": [3, 1],
            "meeting_topic": [4, 1], "participants": [5, 1], "total_count": [5, 2],
            "content": [6, 1]}
    std3 = {k: [0] + v for k, v in std2.items()}
    assert is_standard_map(std2) is True     # 기존 v1/v2 파일 형태
    assert is_standard_map(std3) is True     # v3 형태
    assert is_standard_map({**std3, "content": [1, 6, 1]}) is False  # 표1이면 비표준
