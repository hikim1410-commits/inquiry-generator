# -*- coding: utf-8 -*-
"""T-A2-1 / T-A3-1: fieldmap 저장(save_minutes_cellmap, v3) + 스키마 검증.

COM·AI·네트워크 불필요 — 기본 pytest로 실행.
"""
import json
import os

import pytest

from src.ai.minutes_template_mapper import (
    save_minutes_cellmap, save_minutes_fieldmap, load_minutes_fieldmap,
)
import src.ai.minutes_template_mapper as mtm
from src.minutes.hwpx_minutes import DEFAULT_CELLS


def _mock_llm(monkeypatch, payload):
    monkeypatch.setattr(mtm.llm, "complete_json",
                        lambda *a, **k: {"ok": True, "data": payload})


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
    assert res["version"] == 3
    fm = load_minutes_fieldmap(tpl)
    assert fm["version"] == 3
    # v3: cell_map·custom_slots.cell 은 항상 [table,row,col] 3요소(표0 승격 저장)
    assert fm["cell_map"] == {"business_name": [0, 2, 2], "meeting_topic": [0, 3, 1]}
    assert fm["custom_slots"] == [{"id": "cs1", "label": "부서", "cell": [0, 1, 2]}]
    # table 기본 0 부여(다중 표 후방호환) — 입력에 table 없어도 정규화본엔 포함
    assert fm["annotations"] == [{"table": 0, **anns[0]}]


def test_save_cellmap_is_standard_recalc(tpl):
    # DEFAULT_CELLS는 (table,row,col) 3-tuple(T3) — v3부터는 cell_map 저장도
    # 항상 3요소이므로 그대로 표준 좌표로 사용한다.
    standard = {k: list(v) for k, v in DEFAULT_CELLS.items()}
    res = save_minutes_cellmap(tpl, standard)
    assert res["is_standard"] is True
    # 2요소([row,col], 표0 생략) 입력도 _norm_cell_map이 표0으로 승격하므로
    # 여전히 표준으로 인식되어야 한다(is_standard_map 2/3요소 겸용).
    standard_2elem = {k: [v[1], v[2]] for k, v in DEFAULT_CELLS.items()}
    res2 = save_minutes_cellmap(tpl, standard_2elem)
    assert res2["is_standard"] is True
    res3 = save_minutes_cellmap(tpl, {"business_name": [9, 9]})
    assert res3["is_standard"] is False


def test_save_cellmap_drops_unknown_slot(tpl):
    res = save_minutes_cellmap(tpl, {"bogus_slot": [1, 1], "content": [6, 1]})
    assert "bogus_slot" not in res["cell_map"]
    assert res["cell_map"]["content"] == [0, 6, 1]
    assert "content" not in res["unmapped"]
    assert "business_name" in res["unmapped"]


# ── 후방호환: v1 → load → v3 저장 ─────────────────────────────────────────────

def test_load_v1_without_custom_slots(tpl):
    # save_minutes_fieldmap은 이제 항상 v3(3요소)로 저장하므로, 과거 앱 버전이
    # 남긴 실제 v1 파일(2요소·custom_slots 없음)은 직접 재현해 후방호환을 검증한다.
    path = mtm._fieldmap_path(tpl)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "cell_map": {"business_name": [1, 1]},
                   "unmapped": ["content"]}, f)
    fm = load_minutes_fieldmap(tpl)
    assert fm.get("version") == 1
    assert "custom_slots" not in fm        # v1엔 없음 — 견고 로드(무변환 통과)
    # v3로 다시 저장하면 2요소 cell_map이 표0 승격되어 3요소로 정규화된다
    res = save_minutes_cellmap(tpl, fm["cell_map"])
    assert res["cell_map"] == {"business_name": [0, 1, 1]}
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
    assert res["custom_slots"][0]["cell"] == [0, 1, 2]   # 2요소 → 표0 승격
    assert len(res["warnings"]) >= 4


def test_annotations_bad_coords_ignored(tpl):
    anns = [
        {"row": "x", "col": 1, "label": "좌표오류"},
        {"row": 5, "col": 2, "label": 99},          # 라벨 타입
        {"row": 6, "col": 1, "label": "정상"},
    ]
    res = save_minutes_cellmap(tpl, {}, None, anns)
    assert [(a["row"], a["col"]) for a in res["annotations"]] == [(6, 1)]


# ── T5: fieldmap v3 저장 경로 — 좌표 항상 [table,row,col] 3요소 ──────────────

def test_save_cellmap_v3_roundtrip(tmp_path):
    from src.ai.minutes_template_mapper import save_minutes_cellmap, load_minutes_fieldmap
    tpl = str(tmp_path / "t.hwpx"); open(tpl, "w").close()
    r = save_minutes_cellmap(tpl,
        cell_map={"business_name": [1, 1], "meeting_date": [1, 2, 1]},   # 2+3 혼합 입력
        custom_slots=[{"id": "c3_0", "label": "부서", "cell": [3, 0]},
                      {"id": "c1_2_0", "label": "작성자", "cell": [1, 2, 0]}],
        annotations=[])
    fm = load_minutes_fieldmap(tpl)
    assert fm["version"] == 3
    assert fm["cell_map"]["business_name"] == [0, 1, 1]      # 2요소 → 표0 승격 저장
    assert fm["cell_map"]["meeting_date"] == [1, 2, 1]
    cells = {s["id"]: s["cell"] for s in fm["custom_slots"]}
    assert cells["c3_0"] == [0, 3, 0]
    assert cells["c1_2_0"] == [1, 2, 0]


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


# ── T6: 그리드 직렬화 + 병합 인접성 순수 함수 ──────────────────────────────────

def _cell(t, r, c, text="", cs=1, rs=1):
    return {"table": t, "row": r, "col": c, "text": text,
            "colspan": cs, "rowspan": rs}


def test_serialize_grid_merge_tags():
    from src.ai.minutes_template_mapper import _serialize_grid
    s = _serialize_grid([_cell(0, 0, 0, "회 의 록", cs=3),
                         _cell(0, 1, 0, "사업명"), _cell(0, 1, 1, "", cs=2)])
    assert "[표0]" in s
    assert "(0,0)+cs3: 회 의 록" in s
    assert "(1,0): 사업명" in s            # span 1은 태그 생략
    assert "(1,1)+cs2: (빈 셀)" in s


def test_neighbor_left_respects_colspan():
    from src.ai.minutes_template_mapper import _neighbor_left
    cells = [_cell(0, 2, 0, "성명", cs=2), _cell(0, 2, 2, "")]
    assert _neighbor_left(cells, cells[1])["text"] == "성명"   # col+colspan == 2


def test_neighbor_above_respects_rowspan():
    from src.ai.minutes_template_mapper import _neighbor_above
    cells = [_cell(0, 1, 0, "비고", rs=2), _cell(0, 3, 0, "")]
    assert _neighbor_above(cells, cells[1])["text"] == "비고"  # row+rowspan == 3


def test_neighbor_left_none_when_ambiguous():
    from src.ai.minutes_template_mapper import _neighbor_left
    cells = [_cell(0, 0, 0, "a"), _cell(0, 1, 0, "b"),
             _cell(0, 0, 1, "", rs=2)]                          # 왼쪽 이웃 2개(모호)
    assert _neighbor_left(cells, cells[2]) is None


# ── T7: map_minutes_form — 통합 AI 호출(표준 슬롯 + 커스텀 핀) + 후검증 ────────

def _grid_std():
    """참석자류 최소 그리드: (1,0)라벨 → (1,1)빈칸, (2,0)커스텀 라벨 → (2,1)빈칸."""
    return [_cell(0, 0, 0, "회 의 록", cs=2),
            _cell(0, 1, 0, "사업명"), _cell(0, 1, 1, ""),
            _cell(0, 2, 0, "작성자"), _cell(0, 2, 1, ""),
            _cell(0, 3, 0, "일 시"), _cell(0, 3, 1, "")]


def test_map_form_splits_slots_and_pins(monkeypatch):
    from src.ai.minutes_template_mapper import map_minutes_form
    _mock_llm(monkeypatch, {
        "slots": [{"slot": "business_name", "table": 0, "row": 1, "col": 1},
                  {"slot": "meeting_date", "table": 0, "row": 3, "col": 1}],
        "pins": [{"table": 0, "row": 2, "col": 1, "label": "작성자"}]})
    r = map_minutes_form(_grid_std(), "gemini", "KEY", "m")
    assert r["ok"]
    assert r["cell_map"] == {"business_name": [0, 1, 1], "meeting_date": [0, 3, 1]}
    assert r["pins"] == [{"table": 0, "row": 2, "col": 1, "label": "작성자"}]
    assert set(r["unmapped"]) == {"meeting_place", "meeting_topic",
                                  "participants", "total_count", "content"}

def test_map_form_cross_dedup_slot_wins(monkeypatch):
    from src.ai.minutes_template_mapper import map_minutes_form
    _mock_llm(monkeypatch, {
        "slots": [{"slot": "business_name", "table": 0, "row": 1, "col": 1}],
        "pins": [{"table": 0, "row": 1, "col": 1, "label": "사업명"}]})
    r = map_minutes_form(_grid_std(), "gemini", "KEY", "m")
    assert r["cell_map"]["business_name"] == [0, 1, 1]
    assert r["pins"] == []                       # 표준 우선 — 커스텀 폐기

def test_map_form_rejects_nonexistent_coord(monkeypatch):
    from src.ai.minutes_template_mapper import map_minutes_form
    _mock_llm(monkeypatch, {
        "slots": [{"slot": "business_name", "table": 0, "row": 9, "col": 9}],
        "pins": [{"table": 3, "row": 0, "col": 0, "label": "유령"}]})
    r = map_minutes_form(_grid_std(), "gemini", "KEY", "m")
    assert "business_name" in r["unmapped"]      # 실존성 탈락 → unmapped
    assert r["pins"] == []

def test_map_form_pin_requires_blank_cell(monkeypatch):
    from src.ai.minutes_template_mapper import map_minutes_form
    _mock_llm(monkeypatch, {
        "slots": [],
        "pins": [{"table": 0, "row": 3, "col": 0, "label": "일시"}]})  # 라벨 칸(텍스트 有)
    r = map_minutes_form(_grid_std(), "gemini", "KEY", "m")
    assert r["pins"] == []

def test_map_form_slot_allows_sample_text(monkeypatch):
    """표준 슬롯 입력 칸은 견본 텍스트 허용 — '(총 N명)' 관례."""
    from src.ai.minutes_template_mapper import map_minutes_form
    grid = _grid_std() + [_cell(0, 4, 0, "참석자"), _cell(0, 4, 1, "(총 N명)")]
    _mock_llm(monkeypatch, {
        "slots": [{"slot": "total_count", "table": 0, "row": 4, "col": 1}], "pins": []})
    r = map_minutes_form(grid, "gemini", "KEY", "m")
    assert r["cell_map"]["total_count"] == [0, 4, 1]

def test_map_form_unknown_slot_and_dup_coord(monkeypatch):
    from src.ai.minutes_template_mapper import map_minutes_form
    _mock_llm(monkeypatch, {
        "slots": [{"slot": "alien_slot", "table": 0, "row": 1, "col": 1}],
        "pins": [{"table": 0, "row": 2, "col": 1, "label": "작성자"},
                 {"table": 0, "row": 2, "col": 1, "label": "중복"}]})
    r = map_minutes_form(_grid_std(), "gemini", "KEY", "m")
    assert r["cell_map"] == {}
    assert [p["label"] for p in r["pins"]] == ["작성자"]

def test_map_form_pin_blank_label_dropped(monkeypatch):
    """빈 라벨(공백만)인 핀은 좌표가 유효해도 버려진다 (T11: 구 커스텀 라벨링 함수의
    빈 라벨 필터링 검증을 map_minutes_form 대상으로 이관)."""
    from src.ai.minutes_template_mapper import map_minutes_form
    _mock_llm(monkeypatch, {
        "slots": [],
        "pins": [{"table": 0, "row": 2, "col": 1, "label": "   "}]})
    r = map_minutes_form(_grid_std(), "gemini", "KEY", "m")
    assert r["pins"] == []

def test_map_form_no_key():
    from src.ai.minutes_template_mapper import map_minutes_form
    r = map_minutes_form(_grid_std(), "gemini", "", "m")
    assert not r["ok"] and r["cell_map"] == {} and r["pins"] == []

def test_map_form_ai_call_failure(monkeypatch):
    """키는 있으나 AI 호출 자체가 실패(네트워크·파싱 오류 등)하면 ok:False + 빈 결과
    (T11: 구 커스텀 라벨링 함수의 AI 호출 실패 검증을 map_minutes_form 대상으로 이관 —
    no-key와는 별개인 '호출 후 실패' 경로)."""
    from src.ai.minutes_template_mapper import map_minutes_form
    monkeypatch.setattr(mtm.llm, "complete_json",
                        lambda *a, **k: {"ok": False, "error": "boom"})
    r = map_minutes_form(_grid_std(), "gemini", "KEY", "m")
    assert r["ok"] is False
    assert r["cell_map"] == {} and r["pins"] == []

def test_map_form_adjacency_warning_not_removal(monkeypatch):
    """인접 라벨 없는 핀은 제거 대신 경고 유지(사용자가 지울 수 있게)."""
    from src.ai.minutes_template_mapper import map_minutes_form
    grid = _grid_std() + [_cell(0, 9, 5, "")]     # 고립된 빈 셀
    _mock_llm(monkeypatch, {
        "slots": [], "pins": [{"table": 0, "row": 9, "col": 5, "label": "고아"}]})
    r = map_minutes_form(grid, "gemini", "KEY", "m")
    assert [p["label"] for p in r["pins"]] == ["고아"]
    assert any("인접" in w for w in r["warnings"])
