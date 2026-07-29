# -*- coding: utf-8 -*-
"""template_mapper — 견적서 커스텀 필드 AI 매핑·fieldmap 캐시 (G004 보강).

실 AI 호출 없음 — llm.complete_json은 mock.
"""
import json
import os
from unittest import mock

from src.ai import llm
from src.ai import template_mapper as tm


# ---------- map_unknown_fields ----------

def test_empty_input_short_circuits_without_ai():
    with mock.patch.object(llm, "complete_json") as cj:
        r = tm.map_unknown_fields([])
    assert r == {"ok": True, "field_map": {}, "unmapped": []}
    assert cj.call_count == 0


def test_no_api_key_returns_all_unmapped():
    r = tm.map_unknown_fields(["recv_org"], api_key="")
    assert r["ok"] is False
    assert r["field_map"] == {}
    assert r["unmapped"] == ["recv_org"]


def test_success_maps_fields_and_prompt_contains_inputs():
    data = {"field_map": {"recv_org": "recv"}, "unmapped": ["mystery"]}
    with mock.patch.object(llm, "complete_json",
                           return_value={"ok": True, "data": data}) as cj:
        r = tm.map_unknown_fields(["recv_org", "mystery"],
                                  provider="gemini", api_key="k")
    assert r == {"ok": True, "field_map": {"recv_org": "recv"},
                 "unmapped": ["mystery"]}
    prompt = cj.call_args.args[3]
    assert "recv_org" in prompt and "mystery" in prompt
    assert "recv:" in prompt  # 표준 슬롯 카탈로그 포함
    # field_map은 동적 키 객체 → strict 스키마 미사용 계약
    assert cj.call_args.kwargs.get("schema") is None


def test_none_values_in_data_fall_back_to_empty():
    data = {"field_map": None, "unmapped": None}
    with mock.patch.object(llm, "complete_json",
                           return_value={"ok": True, "data": data}):
        r = tm.map_unknown_fields(["f1"], api_key="k")
    assert r == {"ok": True, "field_map": {}, "unmapped": []}


def test_llm_error_keeps_fields_unmapped():
    with mock.patch.object(llm, "complete_json",
                           return_value={"ok": False, "error": "쿼터 초과"}):
        r = tm.map_unknown_fields(["f1", "f2"], api_key="k")
    assert r["ok"] is False
    assert "쿼터 초과" in r["error"]
    assert r["unmapped"] == ["f1", "f2"]


# ---------- fieldmap 캐시 ----------

def test_fieldmap_path_replaces_extension():
    assert tm._fieldmap_path(r"C:\t\양식.hwp") == r"C:\t\양식.fieldmap.json"


def test_save_then_load_roundtrip(tmp_path):
    tpl = str(tmp_path / "양식.hwp")
    scan = {"is_standard": False, "max_labor": 3, "max_exp": 5}
    mapped = {"field_map": {"a": "recv"}, "unmapped": ["b"]}
    path = tm.save_fieldmap(tpl, scan, mapped)
    assert path == str(tmp_path / "양식.fieldmap.json")
    fm = tm.load_fieldmap(tpl)
    assert fm["version"] == 1
    assert fm["template"] == "양식.hwp"
    assert fm["is_standard"] is False
    assert fm["max_labor"] == 3 and fm["max_exp"] == 5
    assert fm["field_map"] == {"a": "recv"} and fm["unmapped"] == ["b"]


def test_save_defaults_when_scan_sparse(tmp_path):
    tpl = str(tmp_path / "t.hwp")
    tm.save_fieldmap(tpl, {}, {})
    fm = tm.load_fieldmap(tpl)
    assert fm["is_standard"] is False
    assert fm["max_labor"] == 4 and fm["max_exp"] == 8
    assert fm["field_map"] == {} and fm["unmapped"] == []


def test_load_missing_returns_empty(tmp_path):
    assert tm.load_fieldmap(str(tmp_path / "없음.hwp")) == {}


def test_load_corrupted_json_returns_empty(tmp_path):
    tpl = str(tmp_path / "t.hwp")
    with open(tm._fieldmap_path(tpl), "w", encoding="utf-8") as f:
        f.write("{깨진 json")
    assert tm.load_fieldmap(tpl) == {}
