# -*- coding: utf-8 -*-
"""회의록 HWPX 스캐너 — 한글 실행 없이 zipfile + ElementTree로 메타데이터 추출.

대상 양식: templates/회의록_양식.hwpx (7행×3열 표 — src/minutes/hwpx_minutes.py 참조)
  row 1 col 1: 사업명 / row 2: 일시 / row 3: 장소 / row 4: 회의주제
  row 5 col 2: "(총 N명)"

1차: Contents/section0.xml 셀 직접 파싱 (이 양식으로 만든 모든 파일에 견고)
2차: Preview/PrvText.txt 정규식 (build_minutes가 기록하는 고정 포맷)
폴백: 파일명 stem을 topic으로, error="양식 불일치" — 목록에서 빠지지 않게
      (hwp_scan.parse_hwp의 파일명 폴백 UX와 일관)
"""
import os
import re
import zipfile
from dataclasses import dataclass, asdict
from typing import Optional
from xml.etree import ElementTree as ET

# 셀 탐색 로직·네임스페이스는 생성 엔진과 단일 소스 공유 (중복 금지)
from src.minutes.hwpx_minutes import _HP, _find_cell

_RE_TOTAL = re.compile(r"총\s*(\d+)\s*명")
_RE_DATE_ISO = re.compile(r"(\d{4})[-.\s년]+(\d{1,2})[-.\s월]+(\d{1,2})")
# PrvText 포맷: "<사업명><...>" (hwpx_minutes.build_minutes step 7이 기록)
_RE_PRV = {
    "business_name": re.compile(r"<사업명><([^>]*)>"),
    "date": re.compile(r"<일\s*시><([^>]*)>"),
    "place": re.compile(r"<장\s*소><([^>]*)>"),
    "topic": re.compile(r"<회의주제><([^>]*)>"),
}


@dataclass
class MinutesMeta:
    path: str
    filename: str
    business_name: str = ""
    topic: str = ""
    date: str = ""            # 원문 (예: "2026. 04. 09.(목) 09:17~09:52")
    date_iso: str = ""        # YYYY-MM-DD (통계용 best-effort, 실패 시 "")
    place: str = ""
    total_count: Optional[int] = None
    source: str = "hwpx"
    editable: bool = False    # 같은 베이스명 .minutes.json 존재 여부
    json_path: str = ""
    mtime: float = 0.0
    error: str = ""

    def to_dict(self):
        return asdict(self)


def _cell_text(tbl, row, col) -> str:
    """셀 내 모든 문단의 텍스트를 줄바꿈으로 합쳐 반환."""
    tc = _find_cell(tbl, row, col)
    if tc is None:
        return ""
    sublist = tc.find(f"{_HP}subList")
    if sublist is None:
        return ""
    lines = []
    for p in sublist.findall(f"{_HP}p"):
        parts = [t.text or "" for t in p.findall(f".//{_HP}t")]
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _date_to_iso(date_str: str) -> str:
    m = _RE_DATE_ISO.search(date_str or "")
    if not m:
        return ""
    y, mo, d = m.groups()
    try:
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    except ValueError:
        return ""


def _parse_section0(zf: zipfile.ZipFile, meta: MinutesMeta) -> bool:
    """section0.xml 셀 파싱. 양식 표를 찾아 topic을 채우면 True."""
    try:
        with zf.open("Contents/section0.xml") as fp:
            root = ET.parse(fp).getroot()
    except (KeyError, ET.ParseError):
        return False
    tbl = root.find(f".//{_HP}tbl")
    if tbl is None:
        return False
    meta.business_name = _cell_text(tbl, 1, 1)
    meta.date = _cell_text(tbl, 2, 1)
    meta.place = _cell_text(tbl, 3, 1)
    meta.topic = _cell_text(tbl, 4, 1)
    m = _RE_TOTAL.search(_cell_text(tbl, 5, 2))
    if m:
        meta.total_count = int(m.group(1))
    return bool(meta.topic or meta.business_name)


def _parse_prvtext(zf: zipfile.ZipFile, meta: MinutesMeta) -> bool:
    """Preview/PrvText.txt 정규식 폴백."""
    try:
        with zf.open("Preview/PrvText.txt") as fp:
            text = fp.read().decode("utf-8", errors="replace")
    except KeyError:
        return False
    for field, rx in _RE_PRV.items():
        m = rx.search(text)
        if m:
            setattr(meta, field, m.group(1).strip())
    m = _RE_TOTAL.search(text)
    if m:
        meta.total_count = int(m.group(1))
    return bool(meta.topic or meta.business_name)


def parse_minutes_hwpx(path: str) -> MinutesMeta:
    meta = MinutesMeta(path=path, filename=os.path.basename(path))
    try:
        meta.mtime = os.path.getmtime(path)
    except OSError:
        pass
    try:
        with zipfile.ZipFile(path) as zf:
            ok = _parse_section0(zf, meta)
            if not ok:
                ok = _parse_prvtext(zf, meta)
    except (zipfile.BadZipFile, OSError) as e:
        ok = False
        meta.error = f"파일 열기 실패: {e}"
    if not ok and not meta.error:
        meta.error = "양식 불일치 (내비온 회의록 양식 아님)"
    if not meta.topic:
        # 파일명 기반 best-effort: "회의록_XXX_260409.hwpx" → XXX
        stem = os.path.splitext(meta.filename)[0]
        parts = stem.split("_")
        meta.topic = parts[1] if len(parts) >= 2 and parts[0] == "회의록" else stem
    meta.date_iso = _date_to_iso(meta.date)
    return meta


def _resolve_dims(dim, cells, addr_key, span_key, size_key):
    """dim(컬럼 너비/행 높이) 리스트의 None 칸을 채운다 (cellSz 누락 폴백).

    1차: 스팬 셀로 제약 전파 — 어떤 병합셀의 span 안에 미지의 칸이 정확히 1개면
         그 칸 = 셀크기 − 알려진 칸 합 (실측 구조에서 정확).
    2차: 그래도 남으면 전폭 셀(span==칸수) 기준 남은 크기를 균등 분배(엣지케이스).
    None이 없으면 즉시 종료(정상 양식 경로 — 아무 일도 안 함).
    """
    if not any(d is None for d in dim):
        return
    changed = True
    while changed and any(d is None for d in dim):
        changed = False
        for c in cells:
            span, start, total = c[span_key], c[addr_key], c.get(size_key)
            if span <= 1 or not total:
                continue
            idxs = range(start, start + span)
            unknown = [i for i in idxs if dim[i] is None]
            if len(unknown) == 1:
                known = sum(dim[i] for i in idxs if dim[i] is not None)
                dim[unknown[0]] = max(total - known, 0)
                changed = True
    if any(d is None for d in dim):  # ponytail: 균등분배 폴백, 구조 못 풀 때만
        ref = next((c[size_key] for c in cells
                    if c[span_key] == len(dim) and c.get(size_key)), None)
        missing = [i for i, d in enumerate(dim) if d is None]
        known = sum(d for d in dim if d is not None)
        if ref:
            leftover = max(ref - known, 0)
        else:                          # 기준조차 없으면 알려진 칸 평균으로 추정
            avg = known / (len(dim) - len(missing)) if len(dim) > len(missing) else 1
            leftover = avg * len(missing)
        share = leftover / len(missing)
        for i in missing:
            dim[i] = share


def compute_table_geometry(cells: list) -> dict:
    """cells(row,col,colspan,rowspan,width,height) → 각 셀에 기하 bbox를 주입.

    검증된 알고리즘(templates/회의록_양식.hwpx 실측):
      colW[c]=colSpan==1 셀의 cellSz.width, rowH[r]=rowSpan==1 셀의 height.
      셀 bbox: x=ΣcolW[0..col-1], y=ΣrowH[0..row-1],
               w=ΣcolW[col..+colSpan-1], h=ΣrowH[row..+rowSpan-1].
      table_w=ΣcolW, table_h=ΣrowH. 정규화(nx,ny,nw,nh)=bbox/table 크기.
    각 cell dict에 x,y,w,h(HWPUNIT)·nx,ny,nw,nh(0..1)를 in-place 추가.
    반환: {col_w, row_h, table_w, table_h}. (width/height 누락 셀은 _resolve_dims 폴백)
    """
    if not cells:
        return {"col_w": [], "row_h": [], "table_w": 0, "table_h": 0}
    ncols = max(c["col"] + c["colspan"] for c in cells)
    nrows = max(c["row"] + c["rowspan"] for c in cells)
    col_w = [None] * ncols
    row_h = [None] * nrows
    for c in cells:
        if c["colspan"] == 1 and c.get("width"):
            col_w[c["col"]] = c["width"]
        if c["rowspan"] == 1 and c.get("height"):
            row_h[c["row"]] = c["height"]
    _resolve_dims(col_w, cells, "col", "colspan", "width")
    _resolve_dims(row_h, cells, "row", "rowspan", "height")

    col_x = [0]
    for w in col_w:
        col_x.append(col_x[-1] + w)
    row_y = [0]
    for h in row_h:
        row_y.append(row_y[-1] + h)
    table_w, table_h = col_x[-1], row_y[-1]

    for c in cells:
        x = col_x[c["col"]]
        y = row_y[c["row"]]
        w = col_x[c["col"] + c["colspan"]] - x
        h = row_y[c["row"] + c["rowspan"]] - y
        c["x"], c["y"], c["w"], c["h"] = x, y, w, h
        c["nx"] = x / table_w if table_w else 0
        c["ny"] = y / table_h if table_h else 0
        c["nw"] = w / table_w if table_w else 0
        c["nh"] = h / table_h if table_h else 0
    return {"col_w": col_w, "row_h": row_h, "table_w": table_w, "table_h": table_h}


def hit_test_cell(cells: list, nx: float, ny: float):
    """정규화 좌표 (nx,ny)∈[0,1]를 포함하는 셀의 (table,row,col) 반환 — 핀→셀 역추적.

    cells는 compute_table_geometry/scan_hwpx_grid로 nx,ny,nw,nh가 (전체 캔버스 기준)
    주입돼 있어야 한다. 다중 표에서는 같은 (row,col)이 여러 표에 있으므로 table로 구분.
    반열림 [x,x+w) 우선, 우/하단 엣지(nx==1,ny==1)는 닫힘 폴백으로 마지막 셀에 귀속.
    포함 셀이 없으면 None. (table 키 없는 단일 표 셀은 table=0)
    """
    for c in cells:
        if c["nx"] <= nx < c["nx"] + c["nw"] and c["ny"] <= ny < c["ny"] + c["nh"]:
            return (c.get("table", 0), c["row"], c["col"])
    for c in cells:  # 엣지 폴백 (닫힘 구간)
        if c["nx"] <= nx <= c["nx"] + c["nw"] and c["ny"] <= ny <= c["ny"] + c["nh"]:
            return (c.get("table", 0), c["row"], c["col"])
    return None


def _iter_top_tables(root) -> list:
    """문서 흐름의 최상위 표만 반환 (다른 tc 내부의 중첩표는 제외).

    ElementTree는 부모 포인터가 없어 부모맵을 1회 구성해 tbl 조상 유무로 판정.
    표가 1개 이하면 맵 구성 없이 즉시 반환(내장 단일 표 양식 경로).
    """
    all_tbl = root.findall(f".//{_HP}tbl")
    if len(all_tbl) <= 1:
        return all_tbl
    parent = {ch: pa for pa in root.iter() for ch in pa}

    def is_nested(t):
        cur = parent.get(t)
        while cur is not None:
            if cur.tag == f"{_HP}tbl":
                return True
            cur = parent.get(cur)
        return False

    return [t for t in all_tbl if not is_nested(t)]


def _extract_table_cells(tbl) -> list:
    """한 표의 직계 tr/tc만 순회해 셀 메타 추출 (중첩표 셀은 제외).

    반환 각 항목: {row, col, text, colspan, rowspan, width, height}.
    """
    cells = []
    for tr in tbl.findall(f"{_HP}tr"):
        for tc in tr.findall(f"{_HP}tc"):
            addr = tc.find(f"{_HP}cellAddr")
            if addr is None:
                continue
            try:
                r = int(addr.attrib.get("rowAddr", "0"))
                c = int(addr.attrib.get("colAddr", "0"))
            except ValueError:
                continue
            span = tc.find(f"{_HP}cellSpan")
            try:
                colspan = int(span.attrib.get("colSpan", "1")) if span is not None else 1
                rowspan = int(span.attrib.get("rowSpan", "1")) if span is not None else 1
            except ValueError:
                colspan = rowspan = 1
            sz = tc.find(f"{_HP}cellSz")
            width = height = None
            if sz is not None:
                try:
                    width = int(sz.attrib.get("width"))
                    height = int(sz.attrib.get("height"))
                except (TypeError, ValueError):
                    width = height = None
            sl = tc.find(f"{_HP}subList")
            txt = ""
            if sl is not None:
                lines = []
                for p in sl.findall(f"{_HP}p"):
                    line = "".join(t.text or "" for t in p.findall(f".//{_HP}t")).strip()
                    if line:
                        lines.append(line)
                txt = " ⏎ ".join(lines)
            cells.append({"row": r, "col": c, "text": txt[:120],
                          "colspan": colspan, "rowspan": rowspan,
                          "width": width, "height": height})
    return cells


def scan_hwpx_grid(path: str) -> dict:
    """회의록 HWPX 양식의 모든(최상위) 표를 기하 정밀 그리드로 추출.

    발주처 양식은 표가 여러 개라, 모든 최상위 표를 문서 순서대로 세로로 쌓아
    하나의 가상 캔버스로 만든다. 각 셀의 nx,ny,nw,nh는 '전체 캔버스' 기준이라
    프론트가 그대로 % 절대배치하면 폼 전체가 보인다.

    레이아웃:
      - 각 표는 compute_table_geometry로 자체 col_w/row_h/table_w/table_h·셀 bbox 산출.
      - 캔버스 기준폭 = 표들 중 최대 table_w (좁은 표는 좁게, 전폭 표는 넓게).
        (pagePr 폭 대신 최대표폭 — 단일 표일 때 캔버스폭=table_w라 기존 정규화와 동일,
         후방호환을 무료로 얻는다.)
      - 표는 좌측정렬(x_off=0)로 문서 순서대로 세로 스택, 표 사이 작은 간격(gap).

    반환: {ok,
      cells: [{table, row, col, text, colspan, rowspan, x, y, w, h, nx, ny, nw, nh}],
      tables: [{table, row_cnt, col_cnt, table_w, table_h, nx, ny, nw, nh}],
      canvas: {w, h, ratio},
      # 후방호환(단일 표 소비자) — 첫 표 기준
      row_cnt, col_cnt, table_w, table_h, col_w, row_h, error?}
    x,y,w,h는 표 내부 HWPUNIT 절대좌표, nx,ny,nw,nh는 전체 캔버스 0..1 정규화.
    중첩표(사진표) 셀은 부모 셀로 취급되어 별도 표로 수집되지 않는다.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open("Contents/section0.xml") as fp:
                root = ET.parse(fp).getroot()
    except (zipfile.BadZipFile, OSError, KeyError, ET.ParseError) as e:
        return {"ok": False, "error": f"HWPX 파싱 실패: {e}"}

    tbls = _iter_top_tables(root)
    if not tbls:
        return {"ok": False, "error": "양식에서 표를 찾을 수 없습니다."}

    # 1) 표별 셀·기하
    per_table = []  # [(cells, geo)]
    for ti, tbl in enumerate(tbls):
        cells = _extract_table_cells(tbl)
        geo = compute_table_geometry(cells)  # 셀에 x,y,w,h,nx,ny,nw,nh(표기준) 주입
        for c in cells:
            c["table"] = ti
        per_table.append((cells, geo))

    # 2) 캔버스 종합 — 최대 표폭 기준, 세로 스택
    canvas_w = max((geo["table_w"] for _, geo in per_table), default=0) or 1
    gap = round(canvas_w * 0.02)  # 표 사이 작은 간격
    total_h = sum(geo["table_h"] for _, geo in per_table)
    canvas_h = (total_h + gap * (len(per_table) - 1)) or 1

    all_cells, tables_meta = [], []
    y_off = 0
    for ti, (cells, geo) in enumerate(per_table):
        for c in cells:  # 표기준 bbox → 전체 캔버스 정규화 (좌측정렬: x_off=0)
            c["nx"] = c["x"] / canvas_w
            c["ny"] = (y_off + c["y"]) / canvas_h
            c["nw"] = c["w"] / canvas_w
            c["nh"] = c["h"] / canvas_h
        tables_meta.append({
            "table": ti,
            "row_cnt": max((c["row"] + c["rowspan"] for c in cells), default=0),
            "col_cnt": max((c["col"] + c["colspan"] for c in cells), default=0),
            "table_w": geo["table_w"], "table_h": geo["table_h"],
            "nx": 0.0, "ny": y_off / canvas_h,
            "nw": geo["table_w"] / canvas_w, "nh": geo["table_h"] / canvas_h,
        })
        all_cells.extend(cells)
        y_off += geo["table_h"] + gap

    g0 = per_table[0][1]
    t0 = tables_meta[0]
    return {"ok": True,
            "cells": all_cells, "tables": tables_meta,
            "canvas": {"w": canvas_w, "h": canvas_h, "ratio": canvas_h / canvas_w},
            # 후방호환 — 첫 표 기준 단일 표 키
            "row_cnt": t0["row_cnt"], "col_cnt": t0["col_cnt"],
            "table_w": g0["table_w"], "table_h": g0["table_h"],
            "col_w": g0["col_w"], "row_h": g0["row_h"]}


def scan_folder(folder: str) -> list:
    """폴더 내 .hwpx 전수 스캔 + .minutes.json 사이드카 연동."""
    results = []
    if not os.path.isdir(folder):
        return results
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".hwpx"):
            continue
        path = os.path.join(folder, name)
        meta = parse_minutes_hwpx(path)
        jpath = os.path.splitext(path)[0] + ".minutes.json"
        if os.path.exists(jpath):
            meta.editable = True
            meta.json_path = jpath
        results.append(meta)
    return results
