# -*- coding: utf-8 -*-
"""회의록 관리 API 레벨 — generate 사이드카 / scan stats / delete 양 모드 / load.

Api()는 실제 config.json을 읽으므로 __init__을 우회하고 cfg를 직접 주입한다
(test_config_doc_types와 동일 패턴). 전부 COM 불필요 — 기본 pytest로 실행.
"""
import json
import os
import xml.etree.ElementTree as ET
import zipfile
from datetime import date as _date

import pytest

from src.api import Api

_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _cell_texts(hwpx_path, row, col):
    """생성된 HWPX의 (row,col) 셀 텍스트 목록 — cell_map 적용 검증용."""
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        with zf.open("Contents/section0.xml") as f:
            root = ET.parse(f).getroot()
    for tbl in root.findall(f".//{_HP}tbl"):
        for tr in tbl.findall(f"{_HP}tr"):
            for tc in tr.findall(f"{_HP}tc"):
                addr = tc.find(f"{_HP}cellAddr")
                if (addr is not None
                        and addr.attrib.get("rowAddr") == str(row)
                        and addr.attrib.get("colAddr") == str(col)):
                    return [t.text or "" for t in tc.findall(f".//{_HP}t")]
    return []


def _api(minutes_folder=""):
    api = Api.__new__(Api)
    api.cfg = {
        "last_folder": "",
        "doc_types": {"quote": {"folder": ""},
                      "minutes": {"folder": minutes_folder}},
    }
    return api


SAMPLE = {
    "business_name": "테스트 사업",
    "meeting_date": "2026. 04. 09.(목) 09:17~09:52",
    "meeting_place": "본사 회의실",
    "meeting_topic": "API 레벨 검증 회의",
    "participants": ["내비온 김형일"],
    "total_count": 1,
    "sections": [{"type": "header", "text": " ■ 주요 회의 내용"},
                 {"type": "bullet", "text": "검증 항목 점검"}],
}


@pytest.fixture()
def workdir(tmp_path):
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _no_config_disk(monkeypatch, tmp_path):
    """preset CRUD가 cs.save_config로 실제 config.json을 덮어쓰지 않도록 차단 +
    양식 복사 폴더를 tmp로 격리(앱 폴더 오염 방지)."""
    from src.store import config_store as cs
    monkeypatch.setattr(cs, "save_config", lambda cfg: None)
    pdir = str(tmp_path / "_app_minutes_tpl")
    os.makedirs(pdir, exist_ok=True)
    monkeypatch.setattr(cs, "_presets_dir", lambda: pdir)


def test_generate_minutes_writes_sidecar(workdir):
    api = _api(workdir)
    r = api.generate_minutes({"data": SAMPLE})
    assert r["ok"], r.get("error")
    assert os.path.isfile(r["path"])
    assert r["json_path"].endswith(".minutes.json")
    assert os.path.isfile(r["json_path"])
    with open(r["json_path"], encoding="utf-8") as fp:
        store = json.load(fp)
    assert store["data"] == SAMPLE


def test_generate_requires_topic(workdir):
    api = _api(workdir)
    r = api.generate_minutes({"data": {"meeting_topic": ""}})
    assert not r["ok"]


def test_scan_minutes_folder_stats(workdir):
    api = _api(workdir)
    # 1건: 과거 날짜(2026-04) + 사이드카(editable)
    r1 = api.generate_minutes({"data": SAMPLE})
    assert r1["ok"]
    # 1건: 이번 달 날짜 + 사이드카 제거(편집 불가)
    today = _date.today()
    cur = dict(SAMPLE, meeting_topic="이번달 회의",
               meeting_date=f"{today.year}. {today.month:02d}. 15.(월)")
    r2 = api.generate_minutes({"data": cur})
    assert r2["ok"]
    os.remove(r2["json_path"])

    r = api.scan_minutes_folder()
    assert r["ok"]
    assert r["folder"] == workdir
    assert r["stats"]["total"] == 2
    assert r["stats"]["this_month"] == 1
    assert r["stats"]["editable"] == 1
    topics = {m["topic"] for m in r["minutes"]}
    assert topics == {"API 레벨 검증 회의", "이번달 회의"}


def test_scan_includes_orphan_sidecar(workdir):
    api = _api(workdir)
    r1 = api.generate_minutes({"data": SAMPLE})
    os.remove(r1["path"])  # hwpx만 삭제 → 고아 사이드카
    r = api.scan_minutes_folder()
    assert r["stats"]["total"] == 1
    m = r["minutes"][0]
    assert m["source"] == "json"
    assert m["editable"] is True
    assert m["topic"] == SAMPLE["meeting_topic"]


def test_scan_empty_folder_config():
    api = _api("")
    r = api.scan_minutes_folder()
    assert r["ok"]
    assert r["folder"] == ""
    assert r["minutes"] == []


def test_load_minutes_roundtrip(workdir):
    api = _api(workdir)
    r1 = api.generate_minutes({"data": SAMPLE})
    r = api.load_minutes(r1["json_path"])
    assert r["ok"]
    assert r["data"] == SAMPLE


def test_load_minutes_missing():
    api = _api("")
    r = api.load_minutes(r"C:\없는경로\x.minutes.json")
    assert not r["ok"]


def test_delete_minutes_json_only(workdir):
    api = _api(workdir)
    r1 = api.generate_minutes({"data": SAMPLE})
    r = api.delete_minutes({"path": r1["path"], "json_path": r1["json_path"],
                            "also_files": False})
    assert r["ok"]
    assert not os.path.exists(r1["json_path"])
    assert os.path.exists(r1["path"])      # hwpx는 남음


def test_delete_minutes_also_files(workdir):
    api = _api(workdir)
    r1 = api.generate_minutes({"data": SAMPLE})
    r = api.delete_minutes({"path": r1["path"], "json_path": r1["json_path"],
                            "also_files": True})
    assert r["ok"]
    assert not os.path.exists(r1["json_path"])
    assert not os.path.exists(r1["path"])
    assert len(r["removed"]) == 2


def test_delete_minutes_nothing():
    api = _api("")
    r = api.delete_minutes({"path": r"C:\없는경로\x.hwpx", "json_path": "",
                            "also_files": False})
    assert not r["ok"]


def test_get_minutes_template():
    api = _api("")
    r = api.get_minutes_template()
    assert r["ok"]
    assert r["exists"] is True
    assert r["name"].endswith(".hwpx")


# ── 양식 스캔 fieldmap 캐시 보호 (AI 실패 시 기존 매핑 보존) ──────────────────

def test_scan_minutes_template_ai_failure_keeps_existing_fieldmap(workdir):
    """API 키 없음 등으로 AI 매핑이 실패해도, 이전의 정상 fieldmap을
    빈 매핑으로 덮어쓰지 않아야 한다 (재스캔 중 일시 오류 보호)."""
    import shutil
    from src.minutes.hwpx_minutes import TEMPLATE_MINUTES
    from src.ai.minutes_template_mapper import (save_minutes_fieldmap,
                                                load_minutes_fieldmap)
    tpl = os.path.join(workdir, "커스텀양식.hwpx")
    shutil.copy2(TEMPLATE_MINUTES, tpl)
    good = {"cell_map": {"business_name": [2, 2], "meeting_topic": [3, 1]},
            "unmapped": []}
    save_minutes_fieldmap(tpl, good)

    api = _api(workdir)          # AI 키 없음 → map_minutes_cells 실패 경로
    r = api.scan_minutes_template(tpl)
    assert r["ok"] and r.get("ai_error")
    kept = load_minutes_fieldmap(tpl)
    # save_minutes_fieldmap은 v3 저장 시 표0 승격([table,row,col])하므로
    # 원본 2요소 입력과 비교하려면 동일하게 승격한 형태로 대조한다.
    assert kept["cell_map"] == {"business_name": [0, 2, 2], "meeting_topic": [0, 3, 1]}


def test_scan_minutes_template_first_scan_caches_even_on_ai_failure(workdir):
    """기존 캐시가 없으면 AI 실패라도 fieldmap을 생성해 둔다 (스캔 사실 기록)."""
    import shutil
    from src.minutes.hwpx_minutes import TEMPLATE_MINUTES
    from src.ai.minutes_template_mapper import load_minutes_fieldmap
    tpl = os.path.join(workdir, "신규양식.hwpx")
    shutil.copy2(TEMPLATE_MINUTES, tpl)

    api = _api(workdir)
    r = api.scan_minutes_template(tpl)
    assert r["ok"] and r.get("ai_error")
    fm = load_minutes_fieldmap(tpl)
    assert fm and fm["cell_map"] == {}


# ── T-A2-2: scan_minutes_grid / save_minutes_cellmap / scan_template grid ────

def _copy_template(workdir, name="격자양식.hwpx"):
    import shutil
    from src.minutes.hwpx_minutes import TEMPLATE_MINUTES
    tpl = os.path.join(workdir, name)
    shutil.copy2(TEMPLATE_MINUTES, tpl)
    return tpl


def test_scan_minutes_grid_offline(workdir):
    """AI 키 없이도 grid(colspan/rowspan 포함) 반환 — 오프라인 전용 경로."""
    tpl = _copy_template(workdir)
    api = _api(workdir)               # AI 키 없음
    r = api.scan_minutes_grid(tpl)
    assert r["ok"], r.get("error")
    assert r["cells"], "셀이 비어있음"
    for c in r["cells"]:
        assert "colspan" in c and "rowspan" in c
    # 병합셀 존재(표준 양식엔 colspan>1 셀이 있음)
    assert any(c["colspan"] > 1 for c in r["cells"])


def test_scan_minutes_grid_missing_file(workdir):
    api = _api(workdir)
    r = api.scan_minutes_grid(os.path.join(workdir, "없음.hwpx"))
    assert not r["ok"]


def test_save_minutes_cellmap_api_roundtrip(workdir):
    from src.ai.minutes_template_mapper import load_minutes_fieldmap
    tpl = _copy_template(workdir, "저장양식.hwpx")
    api = _api(workdir)
    cell_map = {"business_name": [2, 2]}
    custom = [{"id": "dept", "label": "부서", "cell": [1, 2]}]
    anns = [{"row": 4, "col": 1, "label": "검토", "comment": "x"}]
    r = api.save_minutes_cellmap(tpl, cell_map, custom, anns)
    assert r["ok"], r.get("error")
    fm = load_minutes_fieldmap(tpl)
    assert fm["version"] == 3
    assert fm["cell_map"] == {"business_name": [0, 2, 2]}
    assert fm["custom_slots"] == [{"id": "dept", "label": "부서", "cell": [0, 1, 2]}]
    assert fm["annotations"] == [{"table": 0, **anns[0]}]   # table 기본 0 부여


def test_auto_label_minutes_form_fills_nxny(monkeypatch, workdir):
    """auto_label_minutes_form: grid 조회 → auto_label_cells 호출 → nx,ny 채움.
    auto_label_cells를 모킹해 첫 셀에 핀을 만들고, API가 해당 셀 중심을 채우는지 확인."""
    import src.ai.minutes_template_mapper as mtm
    tpl = _copy_template(workdir, "자동양식.hwpx")
    api = _api(workdir)

    def fake_auto(grid_cells, provider, api_key, model, timeout=30):
        c = grid_cells[0]
        return {"ok": True, "pins": [{"table": c.get("table", 0), "row": c["row"],
                                      "col": c["col"], "label": "자동라벨"}]}

    monkeypatch.setattr(mtm, "auto_label_cells", fake_auto)
    r = api.auto_label_minutes_form(tpl)
    assert r["ok"], r.get("error")
    assert len(r["pins"]) == 1
    p = r["pins"][0]
    assert p["label"] == "자동라벨"
    assert "nx" in p and "ny" in p
    assert 0.0 <= p["nx"] <= 1.0 and 0.0 <= p["ny"] <= 1.0


def test_auto_label_minutes_form_no_key(workdir):
    """AI 키 없으면 ok:False + 빈 pins (안내)."""
    tpl = _copy_template(workdir, "노키양식.hwpx")
    api = _api(workdir)               # AI 키 없음
    r = api.auto_label_minutes_form(tpl)
    assert not r["ok"]
    assert r.get("pins") == []


def test_auto_label_minutes_form_missing_file(workdir):
    api = _api(workdir)
    r = api.auto_label_minutes_form(os.path.join(workdir, "없음.hwpx"))
    assert not r["ok"]


def test_scan_minutes_template_includes_grid(workdir):
    tpl = _copy_template(workdir, "AI양식.hwpx")
    api = _api(workdir)              # AI 실패 경로지만 grid는 항상 포함
    r = api.scan_minutes_template(tpl)
    assert r["ok"]
    assert "grid" in r and r["grid"].get("ok")
    assert any(c["colspan"] > 1 for c in r["grid"]["cells"])


# ── T-B2-1: Preset CRUD + gallery_autoshow ───────────────────────────────────

def test_preset_list_seeds_builtin(workdir):
    api = _api(workdir)
    r = api.list_minutes_presets()
    assert r["ok"]
    assert r["presets"][0]["is_builtin"] is True
    assert r["presets"][0]["active"] is True          # 초기엔 내장 활성
    assert r["gallery_autoshow"] is True


def test_preset_add_select_reflects_template(workdir):
    from src.store import config_store as cs
    tpl = _copy_template(workdir, "내양식.hwpx")
    api = _api(workdir)
    add = api.add_minutes_preset(tpl, "내 회의록")
    assert add["ok"], add.get("error")
    pid = add["preset"]["id"]
    assert add["preset"]["template_path"] != tpl       # 앱 폴더로 복사됨
    sel = api.select_minutes_preset(pid)
    assert sel["ok"]
    assert cs.get_minutes_tpl(api.cfg) == add["preset"]["template_path"]


def test_preset_add_missing_file(workdir):
    api = _api(workdir)
    r = api.add_minutes_preset(os.path.join(workdir, "없음.hwpx"))
    assert not r["ok"]


def test_preset_delete_builtin_rejected(workdir):
    api = _api(workdir)
    assert not api.delete_minutes_preset("builtin")["ok"]


def test_preset_rename_builtin_rejected(workdir):
    api = _api(workdir)
    assert not api.rename_minutes_preset("builtin", "x")["ok"]


def test_preset_rename_then_delete_falls_back(workdir):
    from src.store import config_store as cs
    tpl = _copy_template(workdir, "양식2.hwpx")
    api = _api(workdir)
    pid = api.add_minutes_preset(tpl)["preset"]["id"]
    rn = api.rename_minutes_preset(pid, "새 이름")
    assert rn["ok"] and rn["preset"]["name"] == "새 이름"
    api.select_minutes_preset(pid)
    assert api.delete_minutes_preset(pid)["ok"]
    assert cs.get_minutes_tpl(api.cfg) == ""           # 활성 삭제 → 내장 폴백
    assert pid not in [p["id"] for p in api.list_minutes_presets()["presets"]]


def test_preset_delete_also_files(workdir):
    tpl = _copy_template(workdir, "양식3.hwpx")
    api = _api(workdir)
    add = api.add_minutes_preset(tpl)
    stored = add["preset"]["template_path"]
    assert os.path.isfile(stored)
    r = api.delete_minutes_preset(add["preset"]["id"], also_files=True)
    assert r["ok"]
    assert not os.path.isfile(stored)                  # 사본 파일까지 제거


def test_set_gallery_autoshow(workdir):
    from src.store import config_store as cs
    api = _api(workdir)
    r = api.set_minutes_gallery_autoshow(False)
    assert r["ok"] and r["gallery_autoshow"] is False
    assert cs.get_minutes_gallery_autoshow(api.cfg) is False


# ── 작업1: 편집기 저장 매핑 재로드 (load_minutes_cellmap 라운드트립) ──────────

def test_load_minutes_cellmap_roundtrip(workdir):
    """save_minutes_cellmap로 저장한 cell_map·custom_slots·annotations가
    load_minutes_cellmap로 동일하게 복원된다 (앱 재시작 후 편집기 재오픈 시나리오)."""
    tpl = _copy_template(workdir, "로드양식.hwpx")
    api = _api(workdir)
    cell_map = {"business_name": [2, 2]}
    custom = [{"id": "dept", "label": "부서", "cell": [1, 2]}]
    anns = [{"row": 4, "col": 1, "label": "검토", "comment": "x"}]
    sv = api.save_minutes_cellmap(tpl, cell_map, custom, anns)
    assert sv["ok"], sv.get("error")

    r = api.load_minutes_cellmap(tpl)
    assert r["ok"], r.get("error")
    assert r["has_fieldmap"] is True
    assert r["version"] == 3
    assert r["cell_map"] == {"business_name": [0, 2, 2]}
    assert r["custom_slots"] == [{"id": "dept", "label": "부서", "cell": [0, 1, 2]}]
    assert r["annotations"] == [{"table": 0, **anns[0]}]   # table 기본 0 부여


def test_load_minutes_cellmap_no_fieldmap(workdir):
    """저장본이 없으면 has_fieldmap=False + 빈 격자 (AI 제안 폴백 트리거 신호)."""
    tpl = _copy_template(workdir, "빈양식.hwpx")
    api = _api(workdir)
    r = api.load_minutes_cellmap(tpl)
    assert r["ok"]
    assert r["has_fieldmap"] is False
    assert r["cell_map"] == {}
    assert r["custom_slots"] == [] and r["annotations"] == []


# ── T-C1: 통합 흐름 — 활성 preset cell_map 자동 적용 + 캐시 only(AI 0) ─────────

def test_generate_applies_active_preset_cellmap(workdir):
    """US-012: 활성 preset 상태에서 generate_minutes가 그 preset의 cell_map을
    자동 적용 → 값이 매핑된 셀에 들어간다. business_name을 비표준 셀(3,0)로 재매핑."""
    tpl = _copy_template(workdir, "활성양식.hwpx")
    api = _api(workdir)
    add = api.add_minutes_preset(tpl, "활성 양식")
    assert add["ok"], add.get("error")
    stored = add["preset"]["template_path"]          # 활성 template_path(단일 출처)
    api.select_minutes_preset(add["preset"]["id"])
    # 저장본 매핑: business_name → (3,0) (표준 (1,1) 아님 → is_standard False)
    sv = api.save_minutes_cellmap(stored, {"business_name": [3, 0]})
    assert sv["ok"] and sv["is_standard"] is False

    r = api.generate_minutes({"data": SAMPLE})
    assert r["ok"], r.get("error")
    texts = _cell_texts(r["path"], 3, 0)
    assert any("테스트 사업" in t for t in texts), f"매핑 셀에 값 없음: {texts}"


# ── Drive 자동 업로드 패리티 (견적서 generate()와 동일 패턴) ───────────────────

def test_generate_minutes_drive_auto_upload(monkeypatch, workdir):
    """drive.auto 옵션 ON + 연결됨 → HWPX 1개로 업로드 호출, 결과가 drive에 담김."""
    from src.drive import gdrive
    calls = []
    monkeypatch.setattr(gdrive, "status", lambda: {"connected": True})
    monkeypatch.setattr(gdrive, "upload_files",
                         lambda paths, folder: calls.append(paths) or {"ok": True})

    api = _api(workdir)
    api.cfg["drive"] = {"auto": True, "folder": ""}
    r = api.generate_minutes({"data": SAMPLE})
    assert r["ok"], r.get("error")
    assert calls == [[r["path"]]]        # 회의록은 PDF 없이 HWPX 1개만
    assert r["drive"] == {"ok": True}


def test_generate_minutes_drive_auto_off_skips_upload(monkeypatch, workdir):
    """drive.auto 옵션 OFF → 업로드 함수가 아예 호출되지 않는다."""
    from src.drive import gdrive
    calls = []
    monkeypatch.setattr(gdrive, "status", lambda: {"connected": True})
    monkeypatch.setattr(gdrive, "upload_files", lambda paths, folder: calls.append(paths))

    api = _api(workdir)
    api.cfg["drive"] = {"auto": False, "folder": ""}
    r = api.generate_minutes({"data": SAMPLE})
    assert r["ok"], r.get("error")
    assert calls == []
    assert r["drive"] is None


def test_generate_uses_cache_no_ai_call(monkeypatch, workdir):
    """C-3: 생성 경로는 캐시(load_minutes_fieldmap)만 사용 — 추가 AI 호출 없음.
    AI 함수를 호출 카운터로 monkeypatch해 호출 0을 검증한다."""
    import src.ai.llm as _llm
    import src.ai.minutes_template_mapper as _mtm
    calls = {"ai": 0}

    def _bump_llm(*a, **k):
        calls["ai"] += 1
        return {"ok": False, "error": "no-ai"}

    def _bump_map(*a, **k):
        calls["ai"] += 1
        return {"ok": False, "cell_map": {}, "unmapped": []}

    monkeypatch.setattr(_llm, "complete_json", _bump_llm)
    monkeypatch.setattr(_mtm, "map_minutes_cells", _bump_map)

    tpl = _copy_template(workdir, "캐시양식.hwpx")
    api = _api(workdir)
    add = api.add_minutes_preset(tpl, "캐시 양식")
    stored = add["preset"]["template_path"]
    api.select_minutes_preset(add["preset"]["id"])
    api.save_minutes_cellmap(stored, {"business_name": [3, 0]})

    r = api.generate_minutes({"data": SAMPLE})
    assert r["ok"], r.get("error")
    assert calls["ai"] == 0, "생성 경로에서 AI가 호출됨 (캐시 only 위반)"
