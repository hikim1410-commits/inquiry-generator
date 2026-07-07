# -*- coding: utf-8 -*-
"""회의록 HWPX 스캐너 — build_minutes 라운드트립 + 비양식 graceful + 사이드카 연동."""
import json
import os
import zipfile

import pytest

from src.minutes import build_minutes
from src.scan import hwpx_scan as hx

SAMPLE_DATA = {
    "business_name": "AI 음장 센싱 기반 스마트 홈 보안 모니터링 시스템",
    "meeting_date": "2026. 04. 09.(목) 09:17~09:52",
    "meeting_place": "온라인 화상회의",
    "meeting_topic": "모두의 창업 경진대회 참여 준비 및 창업 활동 현황 논의",
    "participants": ["KIST 김종민 박사", "내비온 장윤화 이사, 김형일 / KST 문준혁"],
    "total_count": 4,
    "sections": [
        {"type": "header", "text": " ■ 주요 회의 내용"},
        {"type": "bullet", "text": "텍스코어 사업 선정 완료"},
        {"type": "sub", "text": "삼성 미래육성 기술재단 멘토링 대기"},
    ],
}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("mn_scan")
    out = str(folder / "회의록_경진대회_260409.hwpx")
    res = build_minutes(SAMPLE_DATA, out_path=out)
    assert res["ok"], res.get("error")
    return folder, out


# ---- 라운드트립: 생성한 파일을 스캔해 동일 메타 복원 ----

def test_roundtrip_fields(built):
    _, out = built
    m = hx.parse_minutes_hwpx(out)
    assert m.error == ""
    assert m.business_name == SAMPLE_DATA["business_name"]
    assert m.date == SAMPLE_DATA["meeting_date"]
    assert m.place == SAMPLE_DATA["meeting_place"]
    assert m.topic == SAMPLE_DATA["meeting_topic"]
    assert m.total_count == 4
    assert m.mtime > 0


def test_roundtrip_date_iso(built):
    _, out = built
    m = hx.parse_minutes_hwpx(out)
    assert m.date_iso == "2026-04-09"


def test_date_to_iso_variants():
    assert hx._date_to_iso("2026. 04. 09.(목) 09:17") == "2026-04-09"
    assert hx._date_to_iso("2026년 6월 1일") == "2026-06-01"
    assert hx._date_to_iso("2026-06-11 14:00") == "2026-06-11"
    assert hx._date_to_iso("일시 미정") == ""
    assert hx._date_to_iso("") == ""


# ---- 비양식/손상 파일 graceful ----

def test_non_form_hwpx_listed_with_error(tmp_path):
    """양식 표가 없는 zip(.hwpx) → 예외 없이 목록 포함 + 양식 불일치."""
    p = tmp_path / "회의록_엉뚱한파일_260101.hwpx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", "<root/>")
    m = hx.parse_minutes_hwpx(str(p))
    assert "양식 불일치" in m.error
    assert m.topic == "엉뚱한파일"     # 파일명 폴백
    assert m.editable is False


def test_not_a_zip_listed_with_error(tmp_path):
    p = tmp_path / "broken.hwpx"
    p.write_bytes(b"this is not a zip")
    m = hx.parse_minutes_hwpx(str(p))
    assert "파일 열기 실패" in m.error
    assert m.topic == "broken"


def test_prvtext_fallback(tmp_path):
    """section0이 비표준이어도 PrvText 포맷이 있으면 메타 복원."""
    p = tmp_path / "회의록_프리뷰만_260301.hwpx"
    prv = ("<회 의 록>\n<사업명><폴백 사업>\n<일  시><2026. 03. 01.(일)>\n"
           "<장  소><본사>\n<회의주제><폴백 주제>\n<참석자><A><(총 3명)>\n")
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Preview/PrvText.txt", prv)
    m = hx.parse_minutes_hwpx(str(p))
    assert m.error == ""
    assert m.business_name == "폴백 사업"
    assert m.topic == "폴백 주제"
    assert m.total_count == 3
    assert m.date_iso == "2026-03-01"


# ---- scan_folder: 사이드카 연동 + 필터링 ----

def test_scan_folder_sidecar_editable(built):
    folder, out = built
    jpath = os.path.splitext(out)[0] + ".minutes.json"
    with open(jpath, "w", encoding="utf-8") as fp:
        json.dump({"schema_version": 1, "data": SAMPLE_DATA}, fp, ensure_ascii=False)
    try:
        metas = hx.scan_folder(str(folder))
        assert len(metas) == 1
        assert metas[0].editable is True
        assert metas[0].json_path == jpath
    finally:
        os.remove(jpath)


def test_scan_folder_skips_non_hwpx(tmp_path):
    (tmp_path / "기타.txt").write_text("x", encoding="utf-8")
    (tmp_path / "견적.hwp").write_bytes(b"x")
    assert hx.scan_folder(str(tmp_path)) == []


def test_scan_folder_missing_dir():
    assert hx.scan_folder(r"C:\존재하지않는폴더\xyz") == []


# ---- A-6-3: scan_hwpx_grid cellSpan 추출 + 좌표 라운드트립 ----

from src.minutes.hwpx_minutes import TEMPLATE_MINUTES, _find_cell, _HP  # noqa: E402
from xml.etree import ElementTree as ET  # noqa: E402


def test_grid_cellspan_merged_counts():
    """실측 양식: colspan=2 셀 5개, colspan=3 셀 1개 (A-6-3 수정1)."""
    g = hx.scan_hwpx_grid(TEMPLATE_MINUTES)
    assert g["ok"], g.get("error")
    # 모든 셀에 colspan/rowspan 키가 있어야 함 (기본 1)
    for c in g["cells"]:
        assert c["colspan"] >= 1 and c["rowspan"] >= 1
    span2 = [c for c in g["cells"] if c["colspan"] == 2]
    span3 = [c for c in g["cells"] if c["colspan"] == 3]
    assert len(span2) == 5, f"colspan=2 기대 5, 실제 {len(span2)}"
    assert len(span3) == 1, f"colspan=3 기대 1, 실제 {len(span3)}"


def test_grid_keys_backward_compatible():
    """기존 키(row,col,text) 유지 + colspan/rowspan 추가만."""
    g = hx.scan_hwpx_grid(TEMPLATE_MINUTES)
    c = g["cells"][0]
    assert set(["row", "col", "text", "colspan", "rowspan"]).issubset(c.keys())


def test_grid_roundtrip_find_cell():
    """그리드 좌표 전부가 _find_cell로 실셀을 찾는다 (A-6-3 수정2)."""
    with zipfile.ZipFile(TEMPLATE_MINUTES) as zf:
        root = ET.parse(zf.open("Contents/section0.xml")).getroot()
    tbl = root.find(f".//{_HP}tbl")
    g = hx.scan_hwpx_grid(TEMPLATE_MINUTES)
    for c in g["cells"]:
        assert _find_cell(tbl, c["row"], c["col"]) is not None, \
            f"그리드 좌표 ({c['row']},{c['col']})가 실셀 아님"


def test_grid_no_nested_table_leak():
    """중첩 사진표 셀이 grid에 새지 않는다 (외부 표 직계만 순회).

    중첩표는 (0,0)/(0,1) 좌표를 갖는데, 외부 표 row0은 colSpan=3 단일셀이라
    (0,1)이 존재하지 않는다 → (0,1)이 grid에 있으면 누수.
    """
    g = hx.scan_hwpx_grid(TEMPLATE_MINUTES)
    coords = {(c["row"], c["col"]) for c in g["cells"]}
    assert (0, 1) not in coords, "중첩표 셀이 grid로 누수됨"
    assert len(g["cells"]) == 14


# ── 이미지-핀 기하: 실측 양식으로 colW/rowH/bbox/hit-test 정답 검증 ──────────────

def _cell(cells, r, c):
    return next(x for x in cells if x["row"] == r and x["col"] == c)


def test_geometry_colw_rowh_table_size():
    """실측 정답: colW=[6220,33293,8389], rowH=[2925,1801×4,5462,53965]."""
    g = hx.scan_hwpx_grid(TEMPLATE_MINUTES)
    assert g["col_w"] == [6220, 33293, 8389]
    assert g["row_h"] == [2925, 1801, 1801, 1801, 1801, 5462, 53965]
    assert g["table_w"] == 47902
    assert g["table_h"] == 69556


def test_geometry_cell_bboxes():
    """사업명(1,1)·참석자(5,1)·총인원(5,2)·회의내용(6,1) bbox 일치."""
    cells = hx.scan_hwpx_grid(TEMPLATE_MINUTES)["cells"]
    expect = {
        (1, 1): (6220, 2925, 41682, 1801),
        (5, 1): (6220, 10129, 33293, 5462),
        (5, 2): (39513, 10129, 8389, 5462),
        (6, 1): (6220, 15591, 41682, 53965),
    }
    for (r, c), (x, y, w, h) in expect.items():
        cell = _cell(cells, r, c)
        assert (cell["x"], cell["y"], cell["w"], cell["h"]) == (x, y, w, h), \
            f"({r},{c}) bbox 불일치: {(cell['x'], cell['y'], cell['w'], cell['h'])}"


def test_geometry_normalized_consistent():
    """정규화 nx,ny,nw,nh가 절대 bbox/table 크기와 일치(렌더 비율 정확성)."""
    g = hx.scan_hwpx_grid(TEMPLATE_MINUTES)
    tw, th = g["table_w"], g["table_h"]
    for cell in g["cells"]:
        assert abs(cell["nx"] - cell["x"] / tw) < 1e-9
        assert abs(cell["ny"] - cell["y"] / th) < 1e-9
        assert abs(cell["nw"] - cell["w"] / tw) < 1e-9
        assert abs(cell["nh"] - cell["h"] / th) < 1e-9


def test_hit_test_pin_to_cell():
    """핀→셀 역추적(단일 표=table 0): (0.48,0.18)→(0,5,1), (0.91,0.18)→(0,5,2),
       (0.56,0.61)→(0,6,1), (0.5,0.05)→(0,1,1)."""
    cells = hx.scan_hwpx_grid(TEMPLATE_MINUTES)["cells"]
    assert hx.hit_test_cell(cells, 0.48, 0.18) == (0, 5, 1)
    assert hx.hit_test_cell(cells, 0.91, 0.18) == (0, 5, 2)
    assert hx.hit_test_cell(cells, 0.56, 0.61) == (0, 6, 1)
    assert hx.hit_test_cell(cells, 0.5, 0.05) == (0, 1, 1)


def test_hit_test_edges_and_outside():
    """엣지(우/하단)·코너는 마지막 포함셀로 귀속, 음수는 None."""
    cells = hx.scan_hwpx_grid(TEMPLATE_MINUTES)["cells"]
    assert hx.hit_test_cell(cells, 1.0, 1.0) == (0, 6, 1)   # 우하단 코너
    assert hx.hit_test_cell(cells, 0.0, 0.0) == (0, 0, 0)   # 좌상단
    assert hx.hit_test_cell(cells, -0.1, 0.5) is None


# ── 다중 표 발주처 양식 실측 (경로 존재 시에만) ──────────────────────────────

_MULTI = r"C:\Users\eicic.AIDEN-DESKTOP\Downloads\인터비즈 바이오 상담일지_그래핀스퀘어케미칼.hwpx"


@pytest.mark.skipif(not os.path.isfile(_MULTI), reason="다중 표 실측 파일 없음")
def test_multi_table_scan_all_tables():
    """발주처 양식: 최상위 표 10개(중첩 5 제외)·전부 수집(첫 표만 아님)."""
    g = hx.scan_hwpx_grid(_MULTI)
    assert g["ok"], g.get("error")
    # 문서 흐름의 최상위 표만(중첩 사진표는 부모 셀로 흡수) → 10개
    assert len(g["tables"]) == 10, f"표 {len(g['tables'])}개 (기대 10 최상위)"
    # 첫 표 1개만 잡히던 버그 회귀 방지: 셀이 표 1개분(<=2)보다 훨씬 많아야 함
    assert len(g["cells"]) > 50
    # 표 인덱스 0..9 연속 부여
    assert sorted({c["table"] for c in g["cells"]}) == list(range(10))
    # 본문 표(14셀)와 헤더 표(2셀)가 번갈아 — 다양한 표 크기 수집 확인
    sizes = [t["row_cnt"] for t in g["tables"]]
    assert max(sizes) >= 5 and min(sizes) >= 1


@pytest.mark.skipif(not os.path.isfile(_MULTI), reason="다중 표 실측 파일 없음")
def test_multi_table_cells_distinct_by_table():
    """서로 다른 표의 셀이 (table,row,col)로 구분된다 (동일 row,col이 표마다 존재)."""
    g = hx.scan_hwpx_grid(_MULTI)
    # 같은 (row,col)이 둘 이상의 표에 등장 — table 없이는 충돌
    from collections import Counter
    rc = Counter((c["row"], c["col"]) for c in g["cells"])
    assert any(v >= 2 for v in rc.values()), "표 간 (row,col) 중복이 없음 — 다중 표 아님?"
    # (table,row,col) 풀키는 유일
    keys = [(c["table"], c["row"], c["col"]) for c in g["cells"]]
    assert len(keys) == len(set(keys)), "(table,row,col) 중복 — 셀 구분 실패"


@pytest.mark.skipif(not os.path.isfile(_MULTI), reason="다중 표 실측 파일 없음")
def test_multi_table_hit_test_lower_table():
    """전체 캔버스 정규화 좌표 → hit_test가 '아래쪽' 표의 셀을 올바른 table로 반환.

    캔버스는 세로 스택이라 ny가 클수록 뒤쪽(아래) 표. 마지막 표 한 셀의 중심을
    찍으면 그 table 인덱스로 역추적돼야 한다(0번 표가 아님).
    """
    g = hx.scan_hwpx_grid(_MULTI)
    last = g["tables"][-1]["table"]
    # 마지막 표의 임의 셀 중심
    cell = next(c for c in g["cells"] if c["table"] == last)
    cx = cell["nx"] + cell["nw"] / 2
    cy = cell["ny"] + cell["nh"] / 2
    hit = hx.hit_test_cell(g["cells"], cx, cy)
    assert hit is not None
    assert hit == (last, cell["row"], cell["col"]), \
        f"하단 표 핀 역추적 실패: {hit} (기대 {(last, cell['row'], cell['col'])})"
    # 캔버스가 세로로 길다(다중 표 스택) — ratio>1
    assert g["canvas"]["ratio"] > 1


def test_geometry_cellsz_fallback():
    """cellSz 누락(병합만 덮인 칸) 폴백: 제약 전파로 정확 복원.

    가운데 컬럼(col1)을 colSpan==1 셀에서 학습 못 하게 width를 지워도,
    전폭(colSpan=3) 셀과 양옆 컬럼으로부터 33293을 역산해야 한다.
    """
    cells = [
        {"row": 0, "col": 0, "colspan": 3, "rowspan": 1, "width": 47902, "height": 2925},
        {"row": 1, "col": 0, "colspan": 1, "rowspan": 1, "width": 6220, "height": 5462},
        {"row": 1, "col": 1, "colspan": 1, "rowspan": 1, "width": None, "height": 5462},
        {"row": 1, "col": 2, "colspan": 1, "rowspan": 1, "width": 8389, "height": 5462},
    ]
    geo = hx.compute_table_geometry(cells)
    assert geo["col_w"] == [6220, 33293, 8389]
    assert geo["table_w"] == 47902


# ── 다중표 픽스처 계약 (T1) ─────────────────────────────────────────────────

def test_multi_table_fixture_two_tables(tmp_path, make_multi_table_tpl):
    tpl = make_multi_table_tpl(str(tmp_path))
    g = hx.scan_hwpx_grid(tpl)
    assert g["ok"], g.get("error")
    assert len(g["tables"]) == 2            # 표0 + 표1 (중첩 사진표는 미포함)
    t1_cells = [c for c in g["cells"] if c["table"] == 1]
    assert t1_cells, "표1 셀이 스캔되지 않음"
    # 표1도 표0과 동일 구조(7×3) — (1,1) 셀 존재
    assert any(c["row"] == 1 and c["col"] == 1 for c in t1_cells)
