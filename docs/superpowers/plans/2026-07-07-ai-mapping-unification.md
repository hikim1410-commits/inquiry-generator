# AI 매핑 통합 + 다중 표 완전 지원 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의록 매핑 편집기의 두 AI 기능을 버튼 1개·AI 호출 1회로 통합하고, 좌표를 [table,row,col]로 확장해 표1+ 핀이 실제 생성물에 반영되게 한다.

**Architecture:** 스캔의 검증된 `_iter_top_tables`를 생성 엔진으로 이동해 표 번호를 단일 소스화. fieldmap은 v3(항상 3요소 저장, 읽기는 길이 분기로 v1/v2 영구 호환). 새 `map_minutes_form()`이 표준 7슬롯+커스텀 핀을 한 프롬프트로 받고 코드 후검증. 추가→전환→제거 순서로 매 커밋마다 앱이 동작한다.

**Tech Stack:** Python 3.12(테스트)/3.13(빌드), ElementTree+zipfile, 바닐라 JS(pywebview), pytest.

**Spec:** `docs/superpowers/specs/2026-07-07-ai-mapping-unification-design.md` (승인본 — 상세 근거는 스펙 참조)

## Global Constraints

- 저장소 루트: `D:\Projects\codes\inquiry generator`
- 테스트 실행: `PYTHONDONTWRITEBYTECODE=1 "C:/Users/김형일/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/ -q -p no:cacheprovider` (전체 3~4초)
- **`git add -A`/`git add .` 절대 금지** — 워킹트리에 무관한 WIP(`src/hwp/hwp_writer.py` 미스테이지 수정, 언트래킹 `_diag_*` 파일들)가 있음. 각 Task에 명시된 파일만 스테이지. `src/hwp/hwp_writer.py`는 절대 스테이지/수정 금지.
- 커밋 메시지: 한국어, `feat(minutes): ...` 형식, 끝에 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- 내부 table 좌표는 항상 **0-based** (UI 표시만 "표 N" 1-based)
- 불변식: subList의 `<hp:p>`를 전부 remove하는 코드 경로는 반환 전 최소 1개 p 보장 (한글 크래시 방지 — 2026-07-07 실측)
- 네임스페이스 상수: `_HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'` (hwpx_minutes.py 정의, scan이 import)

---

### Task 1: 다중표 테스트 픽스처

**Files:**
- Modify: `tests/conftest.py` (없으면 생성)
- Test: `tests/test_hwpx_scan.py` (끝에 추가)

**Interfaces:**
- Produces: `make_multi_table_tpl(dst_dir) -> str` — 표준 양식(표0, 7×3)을 복제해 **표0의 완전한 사본을 표1로 추가**한 hwpx 경로 반환. 표1도 7×3, 셀 좌표·subList 구조 동일. 이후 Task 4·5·13이 이 픽스처를 사용.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_hwpx_scan.py` 끝에 추가:

```python
# ── 다중표 픽스처 계약 (T1) ─────────────────────────────────────────────────

def test_multi_table_fixture_two_tables(tmp_path, make_multi_table_tpl):
    tpl = make_multi_table_tpl(str(tmp_path))
    g = scan_hwpx_grid(tpl)
    assert g["ok"], g.get("error")
    assert len(g["tables"]) == 2            # 표0 + 표1 (중첩 사진표는 미포함)
    t1_cells = [c for c in g["cells"] if c["table"] == 1]
    assert t1_cells, "표1 셀이 스캔되지 않음"
    # 표1도 표0과 동일 구조(7×3) — (1,1) 셀 존재
    assert any(c["row"] == 1 and c["col"] == 1 for c in t1_cells)
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_hwpx_scan.py -q -k multi_table_fixture` → FAIL (fixture 'make_multi_table_tpl' not found)

- [ ] **Step 3: 픽스처 구현** — `tests/conftest.py`에 추가 (파일 없으면 생성; 있으면 기존 내용 유지하고 추가):

```python
# -*- coding: utf-8 -*-
import copy
import os
import shutil
import zipfile
import xml.etree.ElementTree as ET

import pytest

_HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
_HS = '{http://www.hancom.co.kr/hwpml/2011/section}'


@pytest.fixture
def make_multi_table_tpl():
    """표준 회의록 양식에 표0의 완전한 사본을 표1로 추가한 hwpx 생성기.

    deepcopy 방식이라 header.xml의 스타일 ID 카탈로그를 건드리지 않고도
    100% 유효한 두 번째 표를 얻는다(합성 최소 hwpx의 참조 깨짐 위험 회피).
    표0을 감싸는 <hp:p>(표 문단)를 통째로 복제해 <hs:sec> 끝에 형제로 추가.
    """
    def _make(dst_dir: str) -> str:
        # hwpx_minutes를 먼저 import — 모듈이 수행하는 ET.register_namespace 부수효과를
        # 그대로 공유해 재직렬화 시 hp/hs 접두어가 보존되게 한다(생성 엔진과 동일 조건).
        from src.minutes.hwpx_minutes import TEMPLATE_MINUTES  # noqa: F401
        dst = os.path.join(dst_dir, "multi_table.hwpx")
        shutil.copy2(TEMPLATE_MINUTES, dst)

        tmp = os.path.join(dst_dir, "_mt_extract")
        with zipfile.ZipFile(dst) as zf:
            names = zf.namelist()
            zf.extractall(tmp)

        xml_path = os.path.join(tmp, "Contents", "section0.xml")
        ET.register_namespace('hp', _HP.strip('{}'))
        ET.register_namespace('hs', _HS.strip('{}'))
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # 표0을 담은 최상위 <hp:p> 탐색(직계 p 중 하위에 tbl을 가진 첫 번째)
        parent = {ch: pa for pa in root.iter() for ch in pa}
        first_tbl = root.find(f'.//{_HP}tbl')
        p = first_tbl
        while p is not None and not (p.tag == f'{_HP}p' and parent.get(p) is root):
            p = parent.get(p)
        assert p is not None, "표0 문단을 찾지 못함"
        root.append(copy.deepcopy(p))
        tree.write(xml_path, encoding="UTF-8", xml_declaration=True)

        # 재압축 — mimetype 첫 엔트리·STORED 유지
        with zipfile.ZipFile(dst, 'w') as zf:
            mt = os.path.join(tmp, "mimetype")
            zf.write(mt, "mimetype", compress_type=zipfile.ZIP_STORED)
            for name in names:
                if name == "mimetype":
                    continue
                fp = os.path.join(tmp, *name.split("/"))
                if os.path.isfile(fp):
                    zf.write(fp, name, compress_type=zipfile.ZIP_DEFLATED)
        return dst
    return _make
```

- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS. 전체 스위트도 그린 확인.
- [ ] **Step 5: 커밋** — `git add tests/conftest.py tests/test_hwpx_scan.py` → `feat(tests): 다중표 hwpx 픽스처 + 스캔 계약 테스트`

---

### Task 2: `_iter_top_tables`를 생성 엔진으로 이동 (단일 소스화)

**Files:**
- Modify: `src/minutes/hwpx_minutes.py` (`_find_cell` 정의 근처에 함수 추가)
- Modify: `src/scan/hwpx_scan.py:245-264` (로컬 정의 삭제 → import)

**Interfaces:**
- Produces: `hwpx_minutes._iter_top_tables(root) -> list[Element]` — 문서 순서의 최상위 표 목록(중첩 제외). Task 4가 사용. 코드는 hwpx_scan.py 245-264행의 것을 **한 글자도 바꾸지 말고** 이동.

- [ ] **Step 1:** `src/scan/hwpx_scan.py`의 `_iter_top_tables`(245-264행, docstring 포함)를 잘라내 `src/minutes/hwpx_minutes.py`의 `_find_cell` 정의 바로 뒤에 붙여넣기.
- [ ] **Step 2:** `src/scan/hwpx_scan.py` 상단의 기존 `from src.minutes.hwpx_minutes import ...` 줄에 `_iter_top_tables` 추가 (기존 import 항목 유지).
- [ ] **Step 3:** 전체 테스트 → 296+ 전부 PASS (기존 스캔 테스트가 이동을 검증).
- [ ] **Step 4: 커밋** — `git add src/minutes/hwpx_minutes.py src/scan/hwpx_scan.py` → `refactor(minutes): _iter_top_tables를 생성 엔진으로 이동 — 스캔·생성 표 인덱싱 단일 소스화`

---

### Task 3: DEFAULT_CELLS 3-tuple + `_norm_cells` + `is_standard_map`

**Files:**
- Modify: `src/minutes/hwpx_minutes.py` (DEFAULT_CELLS, `_norm_cells`:251-265)
- Modify: `src/ai/minutes_template_mapper.py:170-178` (`is_standard_map`)
- Test: `tests/test_minutes.py`, `tests/test_minutes_template_mapper.py`

**Interfaces:**
- Produces: `DEFAULT_CELLS = {slot: (table,row,col)}` 3-tuple. `_norm_cells(cell_map) -> {slot: (t,r,c)}` — 값 길이 분기(len>=3 그대로, len 2 → t=0). `is_standard_map`은 2/3요소 cell_map 모두 판정.

- [ ] **Step 1: 실패 테스트** — `tests/test_minutes.py` 끝에:

```python
def test_norm_cells_accepts_2_and_3_elem():
    from src.minutes.hwpx_minutes import _norm_cells
    cells = _norm_cells({"business_name": [4, 2],          # 2요소 = 표0
                         "meeting_date": [1, 3, 1]})        # 3요소
    assert cells["business_name"] == (0, 4, 2)
    assert cells["meeting_date"] == (1, 3, 1)
    assert cells["content"] == (0, 6, 1)                    # 기본값도 3-tuple
```

`tests/test_minutes_template_mapper.py` 끝에:

```python
def test_is_standard_map_2elem_and_3elem():
    from src.ai.minutes_template_mapper import is_standard_map
    std2 = {"business_name": [1, 1], "meeting_date": [2, 1], "meeting_place": [3, 1],
            "meeting_topic": [4, 1], "participants": [5, 1], "total_count": [5, 2],
            "content": [6, 1]}
    std3 = {k: [0] + v for k, v in std2.items()}
    assert is_standard_map(std2) is True     # 기존 v1/v2 파일 형태
    assert is_standard_map(std3) is True     # v3 형태
    assert is_standard_map({**std3, "content": [1, 6, 1]}) is False  # 표1이면 비표준
```

- [ ] **Step 2: 실패 확인** — 두 테스트 FAIL (현재 `_norm_cells`는 2-tuple 반환, DEFAULT_CELLS 2-tuple).
- [ ] **Step 3: 구현** — `hwpx_minutes.py`의 DEFAULT_CELLS를 3-tuple로:

```python
DEFAULT_CELLS = {
    "business_name": (0, 1, 1),
    "meeting_date":  (0, 2, 1),
    "meeting_place": (0, 3, 1),
    "meeting_topic": (0, 4, 1),
    "participants":  (0, 5, 1),
    "total_count":   (0, 5, 2),
    "content":       (0, 6, 1),
}
```

`_norm_cells`(251-265행) 교체:

```python
def _norm_cells(cell_map):
    """cell_map(JSON 유래)을 DEFAULT_CELLS 위에 병합, (table,row,col) 3-tuple로 정규화.

    값이 [r,c] 2요소면 표0으로 해석(v1/v2 fieldmap 하위호환), [t,r,c] 3요소는 그대로.
    잘못된 항목은 무시하고 기본값 유지.
    """
    cells = dict(DEFAULT_CELLS)
    for slot, rc in (cell_map or {}).items():
        if slot not in DEFAULT_CELLS:
            continue
        try:
            if len(rc) >= 3:
                cells[slot] = (int(rc[0]), int(rc[1]), int(rc[2]))
            else:
                cells[slot] = (0, int(rc[0]), int(rc[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return cells
```

`minutes_template_mapper.py`의 `is_standard_map`(170-178행) 교체:

```python
def is_standard_map(cell_map: dict) -> bool:
    """cell_map이 표준 양식 좌표(DEFAULT_CELLS, 전부 표0)와 완전히 동일하면 True.

    좌표는 2요소([r,c]=표0, v1/v2 호환)든 3요소([t,r,c], v3)든 모두 판정.
    """
    if set(cell_map.keys()) != set(DEFAULT_CELLS.keys()):
        return False
    for slot, (t, r, c) in DEFAULT_CELLS.items():
        rc = cell_map.get(slot)
        try:
            got = ((int(rc[0]), int(rc[1]), int(rc[2])) if len(rc) >= 3
                   else (0, int(rc[0]), int(rc[1])))
        except (TypeError, ValueError, IndexError):
            return False
        if got != (t, r, c):
            return False
    return True
```

- [ ] **Step 4:** 전체 테스트 → PASS. **주의**: build_minutes의 `_set_simple_cell_text(tbl, *cells[...])` 호출부가 3-tuple 언패킹으로 인자 4개가 되어 깨질 수 있음 — 이 시점에는 build_minutes가 아직 2-인자 호출이므로, 기존 테스트가 깨지면 Task 4를 선반영하지 말고 **이 Task에서는 호출부만 임시 어댑터로 유지**: `_set_simple_cell_text(tbl, cells["business_name"][1], cells["business_name"][2], ...)` 식으로 (t,r,c)의 r,c만 사용(표0 전제 유지 — Task 4에서 정식 교체). `_find_cell(tbl, *cells[...])` 3곳도 동일하게 `cells[...][1], cells[...][2]`로.
- [ ] **Step 5: 커밋** — `git add src/minutes/hwpx_minutes.py src/ai/minutes_template_mapper.py tests/test_minutes.py tests/test_minutes_template_mapper.py` → `feat(minutes): 좌표 3요소화 1단계 — DEFAULT_CELLS·_norm_cells·is_standard_map`

---

### Task 4: build_minutes 다중 표 지원

**Files:**
- Modify: `src/minutes/hwpx_minutes.py` (`build_minutes` 300-320행 부근 + 참석자/총인원/내용 3곳)
- Test: `tests/test_minutes.py`

**Interfaces:**
- Consumes: `_iter_top_tables`(Task 2), `_norm_cells` 3-tuple(Task 3), `make_multi_table_tpl`(Task 1)
- Produces: `build_minutes(..., cell_map={slot:[t,r,c]}, custom_slots=[{id,label,cell:[t,r,c]}])` — 표1+ 좌표가 실제 반영됨. custom_slots cell도 2/3요소 분기.

- [ ] **Step 1: 실패 테스트** — `tests/test_minutes.py` 끝에:

```python
# ── 다중표 생성 (T4) ─────────────────────────────────────────────────────────

def _mt_data():
    return {"business_name": "표1 테스트", "meeting_date": "2026. 07. 07.",
            "meeting_place": "본사", "meeting_topic": "다중표",
            "participants": ["내비온 김형일"], "total_count": 1,
            "sections": [{"type": "header", "text": " ■ 안건"}]}

def test_build_writes_to_table1(tmp_path, make_multi_table_tpl):
    tpl = make_multi_table_tpl(str(tmp_path))
    out = str(tmp_path / "mt_out.hwpx")
    cm = {"business_name": [1, 1, 1]}       # 표1의 (1,1)
    r = build_minutes(_mt_data(), template_hwpx=tpl, out_path=out, cell_map=cm)
    assert r["ok"], r.get("error")
    root = _parse_section0(out)
    from src.minutes.hwpx_minutes import _iter_top_tables, _find_cell
    tables = _iter_top_tables(root)
    assert len(tables) == 2
    tc = _find_cell(tables[1], 1, 1)         # 표1 물리 엘리먼트에서 직접 확인
    assert any("표1 테스트" in (t.text or "") for t in tc.findall(f'.//{_HP}t'))
    tc0 = _find_cell(tables[0], 1, 1)        # 표0 같은 좌표는 미기록(빈값/원본 유지)
    assert not any("표1 테스트" in (t.text or "") for t in tc0.findall(f'.//{_HP}t'))

def test_build_table1_and_photo_preserved(tmp_path, make_multi_table_tpl):
    """사진표(표0 content 내 중첩)를 보존하면서 표1에도 쓰기 — findall[N] 오인 회귀 방지."""
    tpl = make_multi_table_tpl(str(tmp_path))
    out = str(tmp_path / "mt_photo.hwpx")
    r = build_minutes(_mt_data(), template_hwpx=tpl, out_path=out,
                      cell_map={"business_name": [1, 1, 1]})
    assert r["ok"]
    root = _parse_section0(out)
    from src.minutes.hwpx_minutes import _iter_top_tables, _find_cell
    tables = _iter_top_tables(root)
    tc_content = _find_cell(tables[0], 6, 1)  # 표0 content(기본 좌표)에 사진표 잔존
    assert tc_content.find(f'.//{_HP}tbl') is not None

def test_build_table_out_of_range_skips_with_warning(tmp_path, make_multi_table_tpl):
    tpl = make_multi_table_tpl(str(tmp_path))
    out = str(tmp_path / "mt_oob.hwpx")
    r = build_minutes(_mt_data(), template_hwpx=tpl, out_path=out,
                      cell_map={"business_name": [5, 1, 1]})   # 표5 없음
    assert r["ok"]                                             # 크래시 없이 생성
    assert any("표 6" in w for w in r.get("warnings", []))     # 1-based 표기 경고

def test_empty_participants_on_table1_keeps_paragraph(tmp_path, make_multi_table_tpl):
    """한글 크래시 방지 불변식이 표1에서도 유지되는지."""
    tpl = make_multi_table_tpl(str(tmp_path))
    out = str(tmp_path / "mt_empty_p.hwpx")
    d = _mt_data(); d["participants"] = []
    r = build_minutes(d, template_hwpx=tpl, out_path=out,
                      cell_map={"participants": [1, 5, 1]})
    assert r["ok"]
    root = _parse_section0(out)
    from src.minutes.hwpx_minutes import _iter_top_tables, _find_cell
    tc = _find_cell(_iter_top_tables(root)[1], 5, 1)
    sl = tc.find(f'{_HP}subList')
    assert len(sl.findall(f'{_HP}p')) >= 1

def test_custom_slot_on_table1(tmp_path, make_multi_table_tpl):
    tpl = make_multi_table_tpl(str(tmp_path))
    out = str(tmp_path / "mt_custom.hwpx")
    d = _mt_data(); d["custom_fields"] = {"c1_3_1": "홍길동"}
    r = build_minutes(d, template_hwpx=tpl, out_path=out,
                      custom_slots=[{"id": "c1_3_1", "label": "작성자",
                                     "cell": [1, 3, 1]}])
    assert r["ok"]
    root = _parse_section0(out)
    from src.minutes.hwpx_minutes import _iter_top_tables, _find_cell
    tc = _find_cell(_iter_top_tables(root)[1], 3, 1)
    assert any("홍길동" in (t.text or "") for t in tc.findall(f'.//{_HP}t'))
```

- [ ] **Step 2: 실패 확인** — 신규 5건 FAIL(표1에 안 써짐), 기존 테스트는 그린 유지 확인.
- [ ] **Step 3: 구현** — `build_minutes` 내부(300-302행) 교체:

```python
        tables = _iter_top_tables(root)
        if not tables:
            return {"ok": False, "error": "양식 표를 찾을 수 없습니다."}

        def _resolve(slot_cells):
            """(table,row,col) → (표 엘리먼트|None, row, col). 범위 밖 표는 경고+건너뜀."""
            t, r, c = slot_cells
            if 0 <= t < len(tables):
                return tables[t], r, c
            warnings.append(f"표 {t + 1} 없음(양식에 표 {len(tables)}개) — 해당 항목 건너뜀")
            return None, r, c
```

단순 4슬롯(305-308행, Task 3의 임시 어댑터 제거):

```python
        for slot in ("business_name", "meeting_date", "meeting_place", "meeting_topic"):
            tb, r, c = _resolve(cells[slot])
            if tb is not None:
                _set_simple_cell_text(tb, r, c, data.get(slot, ""))
```

커스텀 슬롯 루프(313-319행) — cell 좌표 분기 교체:

```python
        for slot in (custom_slots or []):
            sid = slot.get("id")
            try:
                cc = slot["cell"]
                if len(cc) >= 3:
                    t, r, c = int(cc[0]), int(cc[1]), int(cc[2])
                else:                                  # 2요소 = 기존 v2 = 표0
                    t, r, c = 0, int(cc[0]), int(cc[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            tb, r, c = _resolve((t, r, c))
            if tb is not None:
                _set_simple_cell_text(tb, r, c, str(custom_fields.get(sid, "") or ""))
```

참석자(323행), 총인원(346행 부근), 내용(372행) — `_find_cell(tbl, ...)`을 `_resolve` 경유로:

```python
        # 3) 참석자 셀
        tb5, r5, c5 = _resolve(cells["participants"])
        tc5_1 = _find_cell(tb5, r5, c5) if tb5 is not None else None
        # (이하 기존 if tc5_1 is not None: 블록 그대로 — 빈 참석자 최소 1문단 로직 유지)

        # 4) 총인원 셀
        tb52, r52, c52 = _resolve(cells["total_count"])
        tc5_2 = _find_cell(tb52, r52, c52) if tb52 is not None else None

        # 5) 회의내용 셀
        tb6, r6, c6 = _resolve(cells["content"])
        tc6_1 = _find_cell(tb6, r6, c6) if tb6 is not None else None
```

(기존 `tc5_1 = _find_cell(tbl, *cells["participants"])` 형태 3줄을 위로 대체. 각 블록 내부 로직은 무변경. `tbl` 변수를 더 이상 만들지 않으므로 잔여 참조가 없는지 `tbl` 검색으로 확인.)

- [ ] **Step 4:** 전체 테스트 → PASS (신규 5 + 기존 전부. 기존 2요소 cell_map 테스트가 무수정 그린 = 하위호환 증명).
- [ ] **Step 5: 커밋** — `git add src/minutes/hwpx_minutes.py tests/test_minutes.py` → `feat(minutes): build_minutes 다중 표 지원 — 표1+ 좌표 실반영, 범위 밖 경고`

---

### Task 5: fieldmap v3 저장 경로

**Files:**
- Modify: `src/ai/minutes_template_mapper.py` (`_norm_cell_map`:216-226, `_validate_custom_slots`:249-258, `save_minutes_cellmap`:324, `save_minutes_fieldmap`:203)
- Test: `tests/test_minutes_template_mapper.py`, `tests/test_minutes_api.py`

**Interfaces:**
- Produces: 저장되는 fieldmap의 `version: 3`, `cell_map` 값·`custom_slots[].cell` 항상 `[t,r,c]` 3요소(2요소 입력은 t=0 승격). annotations 스키마 무변경.

- [ ] **Step 1: 실패 테스트** — `tests/test_minutes_template_mapper.py`에:

```python
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
```

- [ ] **Step 2: 실패 확인** — FAIL (version 2, 2요소 저장).
- [ ] **Step 3: 구현** — `_norm_cell_map` 교체:

```python
def _norm_cell_map(cell_map: dict) -> dict:
    """cell_map(JSON 유래)에서 표준 7슬롯만 수용, 항상 [table,row,col] 3요소로 정규화."""
    out = {}
    for slot, rc in (cell_map or {}).items():
        if slot not in MINUTES_SLOTS:
            continue
        try:
            if len(rc) >= 3:
                out[slot] = [int(rc[0]), int(rc[1]), int(rc[2])]
            else:
                out[slot] = [0, int(rc[0]), int(rc[1])]
        except (TypeError, ValueError, IndexError):
            continue
    return out
```

`_validate_custom_slots`의 좌표 파싱(249-253행) 교체:

```python
        try:
            if len(cell) >= 3:
                t, r, c = int(cell[0]), int(cell[1]), int(cell[2])
            else:
                t, r, c = 0, int(cell[0]), int(cell[1])
        except (TypeError, ValueError, IndexError, KeyError):
            warnings.append(f"custom_slot '{sid}' 셀 좌표 오류 — 무시")
            continue
```

및 출력 `out.append({"id": sid, "label": label, "cell": [t, r, c]})`.
`save_minutes_cellmap`의 `"version": 2` → `"version": 3`. `save_minutes_fieldmap`의 `"version": 1` → `"version": 3` (cell_map은 이미 3요소로 들어옴 — Task 7의 map_minutes_form 출력).

- [ ] **Step 4:** 전체 테스트 → PASS (기존 라운드트립 테스트 중 version==2 를 단언하는 게 있으면 3으로 갱신 — `grep -n '"version"' tests/` 로 확인).
- [ ] **Step 5: 커밋** — `git add src/ai/minutes_template_mapper.py tests/test_minutes_template_mapper.py tests/test_minutes_api.py` → `feat(minutes): fieldmap v3 — 좌표 항상 [table,row,col] 저장, 읽기 하위호환`

---

### Task 6: 그리드 직렬화 + 병합 인접성 순수 함수

**Files:**
- Modify: `src/ai/minutes_template_mapper.py` (신규 함수 3개)
- Test: `tests/test_minutes_template_mapper.py`

**Interfaces:**
- Produces:
  - `_serialize_grid(grid_cells) -> str` — `[표N] R행×C열` 헤더 + `(r,c)[+csN][+rsN]: 텍스트` 줄
  - `_neighbor_left(cells_in_table, cell) -> dict|None`, `_neighbor_above(cells_in_table, cell) -> dict|None` — 병합 폭 기반 인접 셀. Task 7이 사용.

- [ ] **Step 1: 실패 테스트**:

```python
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
```

- [ ] **Step 2: 실패 확인** — ImportError.
- [ ] **Step 3: 구현** (`minutes_template_mapper.py`, `MINUTES_SLOTS` 아래):

```python
def _serialize_grid(grid_cells: list) -> str:
    """표별 헤더 + `(r,c)[+csN][+rsN]: 텍스트` 직렬화 (AI 프롬프트용).

    span 1은 태그 생략(토큰 절약). 병합으로 덮인 좌표는 목록에 아예 없음을
    프롬프트가 함께 설명한다. 텍스트 120자 절단·⏎ 병합은 scan 단계에서 이미 처리됨.
    """
    by_table = {}
    for c in grid_cells:
        by_table.setdefault(int(c.get("table", 0)), []).append(c)
    lines = []
    for t in sorted(by_table):
        cells = sorted(by_table[t], key=lambda c: (c["row"], c["col"]))
        rows = max((c["row"] + c.get("rowspan", 1) for c in cells), default=0)
        cols = max((c["col"] + c.get("colspan", 1) for c in cells), default=0)
        lines.append(f"[표{t}] {rows}행×{cols}열")
        for c in cells:
            tag = ""
            if c.get("colspan", 1) > 1:
                tag += f"+cs{c['colspan']}"
            if c.get("rowspan", 1) > 1:
                tag += f"+rs{c['rowspan']}"
            lines.append(f"  ({c['row']},{c['col']}){tag}: {c.get('text') or '(빈 셀)'}")
    return "\n".join(lines)


def _neighbor_left(cells_in_table: list, cell: dict) -> dict:
    """cell 왼쪽에 맞닿은 셀(병합 폭 기준, col+colspan == cell.col).

    rowspan 겹침 범위 안에서 탐색. 후보가 정확히 1개일 때만 반환(모호하면 None).
    같은 table 부분집합을 넘겨야 한다(표 간 좌표 중복 흔함).
    """
    r0, r1 = cell["row"], cell["row"] + cell.get("rowspan", 1)
    cands = [c for c in cells_in_table
             if c["col"] + c.get("colspan", 1) == cell["col"]
             and c["row"] < r1 and r0 < c["row"] + c.get("rowspan", 1)]
    return cands[0] if len(cands) == 1 else None


def _neighbor_above(cells_in_table: list, cell: dict) -> dict:
    """cell 위에 맞닿은 셀(row+rowspan == cell.row). 규칙은 _neighbor_left와 대칭."""
    c0, c1 = cell["col"], cell["col"] + cell.get("colspan", 1)
    cands = [c for c in cells_in_table
             if c["row"] + c.get("rowspan", 1) == cell["row"]
             and c["col"] < c1 and c0 < c["col"] + c.get("colspan", 1)]
    return cands[0] if len(cands) == 1 else None
```

- [ ] **Step 4:** 테스트 PASS 확인 후 **커밋** — `git add src/ai/minutes_template_mapper.py tests/test_minutes_template_mapper.py` → `feat(ai): 그리드 직렬화(병합 표기) + 병합 폭 기반 인접성 순수 함수`

---

### Task 7: `map_minutes_form()` — 통합 AI 호출 + 후검증

**Files:**
- Modify: `src/ai/minutes_template_mapper.py` (신규 프롬프트 + 함수; 기존 두 함수는 아직 유지)
- Test: `tests/test_minutes_template_mapper.py`

**Interfaces:**
- Consumes: `_serialize_grid`, `_neighbor_left/above`(Task 6), `llm.complete_json(provider, api_key, model, prompt, schema, timeout)`
- Produces: `map_minutes_form(grid_cells, provider, api_key, model, timeout=45) -> {"ok", "cell_map": {slot:[t,r,c]}, "unmapped": [slot], "pins": [{table,row,col,label}], "warnings": [str], "error"?}` — Task 8이 사용.

- [ ] **Step 1: 실패 테스트** (기존 `monkeypatch`로 `mtm.llm.complete_json` 모킹하는 파일 관례를 따름 — 파일 상단 import 방식 확인 후 동일하게):

```python
def _grid_std():
    """참석자류 최소 그리드: (1,0)라벨 → (1,1)빈칸, (2,0)커스텀 라벨 → (2,1)빈칸."""
    return [_cell(0, 0, 0, "회 의 록", cs=2),
            _cell(0, 1, 0, "사업명"), _cell(0, 1, 1, ""),
            _cell(0, 2, 0, "작성자"), _cell(0, 2, 1, ""),
            _cell(0, 3, 0, "일 시"), _cell(0, 3, 1, "")]

def _mock_llm(monkeypatch, payload):
    from src.ai import minutes_template_mapper as mtm
    monkeypatch.setattr(mtm.llm, "complete_json",
                        lambda *a, **k: {"ok": True, "data": payload})

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

def test_map_form_no_key():
    from src.ai.minutes_template_mapper import map_minutes_form
    r = map_minutes_form(_grid_std(), "gemini", "", "m")
    assert not r["ok"] and r["cell_map"] == {} and r["pins"] == []

def test_map_form_adjacency_warning_not_removal(monkeypatch):
    """인접 라벨 없는 핀은 제거 대신 경고 유지(사용자가 지울 수 있게)."""
    from src.ai.minutes_template_mapper import map_minutes_form
    grid = _grid_std() + [_cell(0, 9, 5, "")]     # 고립된 빈 셀
    _mock_llm(monkeypatch, {
        "slots": [], "pins": [{"table": 0, "row": 9, "col": 5, "label": "고아"}]})
    r = map_minutes_form(grid, "gemini", "KEY", "m")
    assert [p["label"] for p in r["pins"]] == ["고아"]
    assert any("인접" in w for w in r["warnings"])
```

- [ ] **Step 2: 실패 확인** — ImportError.
- [ ] **Step 3: 구현** — 프롬프트 상수 + 함수 (`_AUTO_PROMPT_TMPL` 아래에 추가):

```python
_FORM_PROMPT_TMPL = """당신은 한글(HWPX) 양식(회의록·상담일지 등) 표 구조 분석가입니다.

아래는 양식의 모든 최상위 표의 셀입니다. 각 셀은
`(행,열)[+cs가로병합][+rs세로병합]: 텍스트` 형식이며, 텍스트가 없으면 (빈 셀)입니다.
+cs2는 오른쪽으로 2칸이 하나로 합쳐진 셀, +rs2는 아래로 2칸이 합쳐진 셀입니다(표시가
없으면 병합 없음). **병합으로 덮인 좌표는 목록에 아예 나타나지 않습니다** — 예를 들어
(1,1)이 +cs2라면 (1,2)라는 좌표는 존재하지 않으니 절대 고르지 마세요.

## 개념 정의
- '라벨 칸' = 항목 이름이 적힌 셀(예: "사업명", "일 시", "참석자", "성명", "연락처").
- '표준 슬롯' = 아래 목록의 7개 공통 항목. 대부분의 회의록류 양식에 존재합니다.
- '커스텀 항목' = 표준 슬롯이 아닌, 이 양식에만 있는 라벨(예: "작성자", "부서").
- '입력 칸' = 라벨의 값이 실제로 들어갈 셀. 표준 슬롯의 입력 칸은 "(총 N명)"처럼 형식
  힌트가 적힌 견본 텍스트 셀도 허용됩니다. 커스텀 항목의 입력 칸은 **반드시 빈 셀**이어야
  합니다(이미 다른 텍스트가 있으면 그 항목은 건너뜁니다).
  입력 칸은 항상 **라벨과 같은 표 안**의 인접 셀입니다. 우선순위: ① 라벨의 바로 오른쪽
  → ② 라벨의 바로 아래. "오른쪽"은 열+1이 아니라 라벨이 가로 병합돼 있으면 그 폭만큼
  건너뛴 다음 칸이고, "아래"도 세로 병합 폭만큼 건너뜁니다. 반드시 목록에 실제로 있는
  좌표만 쓰세요.

## 표준 슬롯 (7개 — 이 양식에 있는 것만)
{slots}

## 표 셀 목록
{grid}

## 작업 지시 (순서대로)
1. 표준 슬롯 7개 각각에 대해 그 의미에 맞는 라벨을 표 전체에서 찾아
   (예: "일 시"/"일시"/"회의일시" → meeting_date), 입력 칸의 표·행·열을 slots에
   넣으세요. 찾지 못한 슬롯은 slots에 넣지 마세요(슬롯명 임의 생성 금지).
2. 1번에서 쓴 라벨·입력 칸을 **제외한** 나머지 라벨 칸들을 훑어 pins에 추가하세요.
   - 표준 슬롯에 쓴 칸을 pins에 다시 넣지 마세요(표준 우선, 중복 금지).
   - 표 전체 폭만큼 가로 병합된 셀은 보통 제목/구분선이니 라벨 후보에서 제외하세요.
   - 인접한 빈 칸이 없거나 라벨이 불분명하면 건너뜁니다(억지로 만들지 않음).
   - 한 입력 칸에 항목 하나만. 라벨 칸 자신을 입력 칸으로 반환하지 마세요.

## 까다로운 패턴
- 한 행에 라벨 2쌍: (2,0)"성명" (2,1)빈칸 (2,2)"연락처" (2,3)빈칸이면
  "성명"→(2,1), "연락처"→(2,3)입니다. (2,1)을 연락처에 쓰지 마세요.
- 라벨 칸에 "작성자:"처럼 콜론이 있고 오른쪽/아래에 별도 빈 칸이 없으면 그 항목은
  건너뛰세요(같은 셀 이어쓰기는 지원하지 않습니다).

JSON으로만 답하세요:
{{"slots": [{{"slot":"business_name","table":0,"row":1,"col":1}}, ...],
 "pins": [{{"table":0,"row":0,"col":0,"label":"라벨 텍스트"}}, ...]}}
"""


def map_minutes_form(grid_cells: list, provider: str = "gemini",
                     api_key: str = "", model: str = "gemini-flash-latest",
                     timeout: int = 45) -> dict:
    """표준 7슬롯 매핑 + 커스텀 라벨 핀을 AI 1회 호출로 동시 산출.

    map_minutes_cells(표준)와 auto_label_cells(커스텀)의 통합 대체.
    후검증(코드가 최종 방어선): 좌표 실존성, 표준↔커스텀 교차 중복(표준 우선),
    커스텀 핀은 빈 셀만/표준은 견본 텍스트 허용(비대칭), 병합 폭 기반 인접성은
    경고만(오탐 시 사용자가 지울 수 있게 제거하지 않음).

    반환: {"ok", "cell_map": {slot: [t,r,c]}, "unmapped": [slot],
           "pins": [{table,row,col,label}], "warnings": [str], "error"?}
    """
    if not api_key:
        return {"ok": False, "error": "AI API 키가 없어 자동 분석을 건너뜁니다.",
                "cell_map": {}, "unmapped": list(MINUTES_SLOTS.keys()),
                "pins": [], "warnings": []}

    slot_lines = "\n".join(f"  {k}: {v}" for k, v in MINUTES_SLOTS.items())
    prompt = _FORM_PROMPT_TMPL.format(slots=slot_lines,
                                      grid=_serialize_grid(grid_cells))
    r = llm.complete_json(provider, api_key, model, prompt, schema=None,
                          timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "AI 호출 실패"),
                "cell_map": {}, "unmapped": list(MINUTES_SLOTS.keys()),
                "pins": [], "warnings": []}

    data = r.get("data") or {}
    by_coord = {(int(c.get("table", 0)), c["row"], c["col"]): c for c in grid_cells}
    by_table = {}
    for c in grid_cells:
        by_table.setdefault(int(c.get("table", 0)), []).append(c)
    warnings = []

    def _adjacent_label_ok(cell):
        cells_t = by_table.get(int(cell.get("table", 0)), [])
        for nb in (_neighbor_left(cells_t, cell), _neighbor_above(cells_t, cell)):
            if nb is not None and (nb.get("text") or "").strip():
                return True
        return False

    # 1) 표준 슬롯 — 실존성 + 슬롯명 검증(견본 텍스트 허용)
    cell_map, used = {}, set()
    for s in (data.get("slots") or []):
        if not isinstance(s, dict):
            continue
        slot = s.get("slot")
        if slot not in MINUTES_SLOTS or slot in cell_map:
            continue
        try:
            key = (int(s["table"]), int(s["row"]), int(s["col"]))
        except (TypeError, ValueError, KeyError):
            continue
        if key not in by_coord or key in used:
            continue
        cell_map[slot] = [key[0], key[1], key[2]]
        used.add(key)
        if not _adjacent_label_ok(by_coord[key]):
            warnings.append(f"{slot}: 인접 라벨을 찾지 못함 — 위치 확인 권장")

    # 2) 커스텀 핀 — 실존성 + 빈 셀 + 교차/내부 중복 제거
    pins = []
    for p in (data.get("pins") or []):
        if not isinstance(p, dict):
            continue
        label = p.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        try:
            key = (int(p["table"]), int(p["row"]), int(p["col"]))
        except (TypeError, ValueError, KeyError):
            continue
        if key not in by_coord or key in used:
            continue
        if (by_coord[key].get("text") or "").strip():
            continue                              # 커스텀은 빈 셀만
        used.add(key)
        pins.append({"table": key[0], "row": key[1], "col": key[2],
                     "label": label.strip()})
        if not _adjacent_label_ok(by_coord[key]):
            warnings.append(f"커스텀 '{label.strip()}': 인접 라벨 없음 — 확인 권장")

    unmapped = [s for s in MINUTES_SLOTS if s not in cell_map]
    return {"ok": True, "cell_map": cell_map, "unmapped": unmapped,
            "pins": pins, "warnings": warnings}
```

- [ ] **Step 4:** 테스트 9건 + 전체 스위트 PASS.
- [ ] **Step 5: 커밋** — `git add src/ai/minutes_template_mapper.py tests/test_minutes_template_mapper.py` → `feat(ai): map_minutes_form — 표준+커스텀 통합 AI 호출·코드 후검증`

---

### Task 8: 백엔드 통합 엔드포인트 `Api.map_minutes_form` (추가만 — 기존 유지)

**Files:**
- Modify: `src/api.py` (`scan_minutes_grid` 아래에 신규 메서드 추가; `scan_minutes_template`/`auto_label_minutes_form`는 이 Task에서 삭제하지 않음 — Task 10에서 UI 전환 후 Task 11이 제거)
- Test: `tests/test_minutes_api.py`

**Interfaces:**
- Consumes: `mtm.map_minutes_form`(Task 7), `scan_hwpx_grid`, `save_minutes_fieldmap`, `load_minutes_fieldmap`, `is_standard_map`
- Produces: `Api.map_minutes_form(template_path) -> {ok, ai_used, cell_map, unmapped, pins(+nx,ny), warnings, slot_labels, is_standard, grid, fieldmap_path?, ai_error?}` — Task 10의 UI가 호출.

- [ ] **Step 1: 실패 테스트** — `tests/test_minutes_api.py`의 기존 scan_minutes_template 테스트 구성 방식(파일을 먼저 읽고 Api 인스턴스 생성·모킹 패턴을 그대로 복제)을 따라:

```python
def test_map_minutes_form_endpoint(tmp_path, monkeypatch, ...기존 픽스처):
    # 기존 scan_minutes_template 테스트와 동일한 준비(실제 표준 양식 복사 + AI 모킹) 후:
    from src.ai import minutes_template_mapper as mtm
    monkeypatch.setattr(mtm, "map_minutes_form", lambda cells, *a, **k: {
        "ok": True, "cell_map": {"business_name": [0, 1, 1]},
        "unmapped": [], "pins": [{"table": 0, "row": 2, "col": 1, "label": "작성자"}],
        "warnings": []})
    r = api.map_minutes_form(tpl_path)
    assert r["ok"] and r["ai_used"]
    assert r["cell_map"] == {"business_name": [0, 1, 1]}
    p = r["pins"][0]
    assert 0.0 <= p["nx"] <= 1.0 and 0.0 <= p["ny"] <= 1.0   # 셀 중심 좌표 주입
    assert "grid" in r and r["grid"]["ok"]
    assert os.path.exists(r["fieldmap_path"])                 # 캐시 저장 승계
```

(모킹 대상은 `src.ai.minutes_template_mapper.map_minutes_form` — api.py가 함수 참조를 지역 import하므로 모듈 속성을 패치. 기존 파일의 동일 패턴 확인 후 맞출 것.)

- [ ] **Step 2: 실패 확인** — AttributeError (엔드포인트 없음).
- [ ] **Step 3: 구현** — `src/api.py`의 `scan_minutes_grid` 메서드 바로 아래에:

```python
    def map_minutes_form(self, template_path: str) -> dict:
        """양식 AI 통합 분석 — 표준 7슬롯 + 커스텀 라벨 핀을 1회 호출로.

        scan_minutes_template(표준)·auto_label_minutes_form(커스텀)의 통합 대체.
        fieldmap 캐시 저장·AI 실패 시 기존 정상 캐시 보호 규칙은 종전과 동일.
        반환: {ok, ai_used, cell_map, unmapped, pins(+nx,ny), warnings,
               slot_labels, is_standard, grid, fieldmap_path?, ai_error?}
        """
        try:
            from src.scan.hwpx_scan import scan_hwpx_grid
            from src.ai.minutes_template_mapper import (
                map_minutes_form as _map_form, save_minutes_fieldmap,
                load_minutes_fieldmap, is_standard_map, MINUTES_SLOTS)
            if not os.path.isfile(template_path):
                return _err(f"파일을 찾을 수 없습니다: {template_path}")

            grid = scan_hwpx_grid(template_path)
            if not grid.get("ok"):
                return grid

            provider = cs.get_provider(self.cfg)
            api_key = cs.get_ai_key(self.cfg, provider)
            map_r = _map_form(grid["cells"], provider, api_key,
                              cs.get_ai_model(self.cfg, provider))

            by_cell = {(c.get("table", 0), c["row"], c["col"]): c
                       for c in grid["cells"]}
            pins = []
            for p in map_r.get("pins", []):
                c = by_cell.get((p["table"], p["row"], p["col"]))
                pin = dict(p)
                if c:                                  # 셀 중심 — 프론트 핀 배치용
                    pin["nx"] = c["nx"] + c["nw"] / 2
                    pin["ny"] = c["ny"] + c["nh"] / 2
                pins.append(pin)

            result = {
                "ok": True, "ai_used": True,
                "cell_map": map_r.get("cell_map", {}),
                "unmapped": map_r.get("unmapped", []),
                "pins": pins,
                "warnings": map_r.get("warnings", []),
                "slot_labels": MINUTES_SLOTS,
                "is_standard": is_standard_map(map_r.get("cell_map", {})),
                "grid": grid,
            }
            if not map_r.get("ok"):
                result["ai_error"] = map_r.get("error", "")
            if map_r.get("ok") or not load_minutes_fieldmap(template_path):
                result["fieldmap_path"] = save_minutes_fieldmap(template_path, map_r)
            return result
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())
```

- [ ] **Step 4:** 전체 테스트 PASS. **커밋** — `git add src/api.py tests/test_minutes_api.py` → `feat(api): map_minutes_form 통합 엔드포인트 추가 (기존 2개는 Task 11에서 제거)`

---

### Task 9: UI 좌표 3요소화 + 표0 제한 잔재 정리

**Files:**
- Modify: `ui/app.js` — `normCellMap`(1950-1957), `mnDeriveCellMap`(2116-2122), 주석(2123-2124), `mnPinsOffTable0`(2125-2129), `mnDeriveCustomSlots`(2134-2138), `mnCellMapToAnns`(~2198-2205), `savePinPop` 내 표0 경고(~2337), `saveMinutesMapping` 내 경고(2459-2460)
- Test: `tests/test_ui_parser.py`

**Interfaces:**
- Produces: `mnDeriveCellMap() -> {slot:[t,r,c]}`, `mnDeriveCustomSlots(anns)`가 전 표 대상 + id 규칙(표0=`c{r}_{c}` 유지, 표1+=`c{t}_{r}_{c}`), `normCellMap` 3요소 보존, `mnCellMapToAnns` 3요소 소비. Task 10이 사용.

- [ ] **Step 1: test_ui_parser 갱신 (실패 선행)** — 기존 `test_derive_custom_slots_table0_only_no_slot`(99-113행)을 다중표 검증으로 교체, id 안정성 테스트(116-126행)에 표1 케이스 추가:

```python
def test_derive_custom_slots_multi_table_ids(...기존 헬퍼 사용):
    anns = [{"table": 0, "row": 3, "col": 0, "label": "부서"},
            {"table": 1, "row": 3, "col": 0, "label": "작성자"},
            {"table": 1, "row": 2, "col": 1, "label": ""},        # 빈 라벨 제외
            {"table": 0, "row": 4, "col": 1, "slot": "content"}]  # 슬롯 핀 제외
    out = run_derive_custom_slots(anns)     # 파일의 기존 Node 실행 헬퍼
    assert out == [
        {"id": "c3_0",   "label": "부서",   "cell": [0, 3, 0]},   # 표0 = 레거시 id 유지
        {"id": "c1_3_0", "label": "작성자", "cell": [1, 3, 0]},   # 표1+ = 표 포함 id
    ]
```

- [ ] **Step 2: 실패 확인** — 신규/갱신 테스트 FAIL.
- [ ] **Step 3: 구현** — `normCellMap` 교체:

```javascript
function normCellMap(m) {
  const o = {};
  for (const k in (m || {})) {
    const v = m[k];
    if (!Array.isArray(v) || v.length < 2) continue;
    // 3요소 [table,row,col] 보존, 2요소(구버전 fieldmap)는 표0으로 승격
    o[k] = v.length >= 3 ? [+v[0], +v[1], +v[2]] : [0, +v[0], +v[1]];
  }
  return o;
}
```

`mnDeriveCellMap` + `mnDeriveCustomSlots` 교체(2116-2138행 — ponytail 주석·`mnPinsOffTable0` 함수 삭제 포함):

```javascript
function mnDeriveCellMap() {   // 표준 슬롯 핀(모든 표) → cell_map [table,row,col]
  const m = {};
  mnEditor.annotations.forEach(a => {
    if (a.slot && MN_SLOT_LABELS[a.slot]) m[a.slot] = [mnT(a), a.row, a.col];
  });
  return m;
}

/* 커스텀 라벨 핀(슬롯 없음, 라벨 있음) → custom_slots [{id,label,cell:[t,r,c]}].
   id는 좌표 기반이라 라벨을 바꿔도 안정적 — 재편집 시 custom_fields 값이 유지된다.
   표0은 기존 "c행_열" 형식 유지(저장된 재편집 데이터와의 연결 보존),
   표1+만 "c표_행_열" — 두 형식은 충돌하지 않는다. */
function mnDeriveCustomSlots(annotations) {
  return (annotations || [])
    .filter(a => !a.slot && a.label && a.label.trim())
    .map(a => {
      const t = (a.table || 0);
      const id = t === 0 ? `c${a.row}_${a.col}` : `c${t}_${a.row}_${a.col}`;
      return { id, label: a.label.trim(), cell: [t, a.row, a.col] };
    });
}
```

`mnCellMapToAnns`(2198-2205행 부근) — 3요소 소비로 교체:

```javascript
function mnCellMapToAnns(cellMap) {
  const out = [];
  for (const slot in (cellMap || {})) {
    const rc = cellMap[slot];                 // normCellMap 통과 후 항상 [t,r,c]
    if (!MN_SLOT_LABELS[slot] || !Array.isArray(rc) || rc.length < 3) continue;
    out.push({ table: rc[0], row: rc[1], col: rc[2],
               slot, label: MN_SLOT_LABELS[slot], comment: '' });
  }
  return out;
}
```

(기존 함수 본문이 이 형태와 다르면 실제 본문을 기준으로 좌표 부분만 위 규칙대로 수정.)
`savePinPop` 내 `if (t !== 0) toast('...첫 번째 표...')` 줄 삭제(~2337행). `saveMinutesMapping`의 `mnPinsOffTable0()` 호출과 경고 토스트 2줄(2459-2460행) 삭제.

- [ ] **Step 4:** 전체 테스트 PASS (test_ui_parser 갱신분 포함). `grep -n "mnPinsOffTable0" ui/app.js` → 0건 확인.
- [ ] **Step 5: 커밋** — `git add ui/app.js tests/test_ui_parser.py` → `feat(ui): 매핑 좌표 3요소화 — 표0 제한 제거, normCellMap 절단 버그 수정`

---

### Task 10: UI 버튼 통합 (엔드포인트 전환)

**Files:**
- Modify: `ui/index.html:727`(버튼 텍스트/툴팁), `ui/index.html:735-736`(푸터 버튼 삭제)
- Modify: `ui/app.js` — `runMinutesAiAutoMap`(2420-2449) 개편, `autoLabelMinutesForm`(2477-2506) 삭제, 리스너 바인딩(`#mn-map-autolabel` grep) 삭제, `openMinutesMapEditor` 내 키-없음 disabled 처리(2187-2192 부근)를 통합 버튼 대상으로 전환, 죽은 코드 `scanMinutesTemplate`(1461-1496)+관련 마크업(`#mn-tpl-scan-result` 등 index.html) 삭제
- Modify: `ui/app.css` — `.modal-actions .mn-map-autolabel` 규칙(338-339행 부근) 삭제

**Interfaces:**
- Consumes: `call('map_minutes_form', tpl)`(Task 8), `mnCellMapToAnns`·`mnDeriveCustomSlots`(Task 9)

- [ ] **Step 1: index.html** — 727행 버튼을:

```html
<button id="mn-map-ai" class="btn btn-mini btn-violet" type="button"
  title="양식의 표준 7항목(사업명·일시 등)과 그 외 라벨 칸을 AI로 한 번에 찾아 핀을 배치합니다">✨ AI 자동 매핑</button>
```

735-736행의 `#mn-map-autolabel` 버튼 삭제. `#mn-tpl-scan-result`/`#mn-tpl-map-body`/`#mn-tpl-unmapped` 마크업 블록 삭제(존재 위치 grep).

- [ ] **Step 2: app.js 통합 핸들러** — `runMinutesAiAutoMap` 전체 교체:

```javascript
/* [AI 자동 매핑] — 표준 7슬롯 + 커스텀 라벨을 1회 호출로 배치.
   병합 정책: AI가 이번에 제안한 정확한 (table,row,col)만 교체, 그 외 기존 핀
   (수동 다듬은 커스텀 포함)은 전부 보존. */
async function runMinutesAiAutoMap() {
  if (!aiKeySet()) { toast('AI 자동 매핑을 쓰려면 설정에서 AI API 키를 먼저 등록하세요', 'warn', 5000); return; }
  const tpl = mnEditor.templatePath;
  if (!tpl) return;
  overlay(true, 'AI가 양식을 분석하는 중...');
  const r = await call('map_minutes_form', tpl);
  overlay(false);
  if (!r.ok) { toast(r.error || 'AI 자동 매핑 실패', 'err', 5000); return; }

  const stdAnns = mnCellMapToAnns(normCellMap(r.cell_map));
  const pinAnns = (r.pins || []).map(p => ({
    table: p.table || 0, row: p.row, col: p.col,
    label: p.label, comment: '', nx: p.nx, ny: p.ny,
  }));
  const aiAnns = stdAnns.concat(pinAnns)
    .sort((a, b) => mnT(a) - mnT(b) || a.row - b.row || a.col - b.col);
  const aiCells = new Set(aiAnns.map(a => mnT(a) + ',' + a.row + ',' + a.col));
  // AI가 이번에 지정한 셀의 기존 핀만 제거 — 나머지는 전부 보존
  mnEditor.annotations = mnEditor.annotations.filter(a => !aiCells.has(mnT(a) + ',' + a.row + ',' + a.col));
  aiAnns.forEach(a => {
    const [nx, ny] = mnPinPos(a);
    mnEditor.annotations.push({ ...a, nx, ny });
  });
  renderMapCanvas(); renderSlotStatus();

  const unmapped = (r.unmapped || []).map(s => MN_SLOT_LABELS[s] || s);
  const warns = r.warnings || [];
  let msg = `AI 매핑 완료 — 표준 ${stdAnns.length}개, 커스텀 ${pinAnns.length}개`;
  if (unmapped.length) msg += ` · 미매핑 ${unmapped.length}개 (${unmapped.join(', ')})`;
  if (warns.length) msg += '\n' + warns.join('\n');
  if (r.ai_error) msg += `\n${r.ai_error}`;
  const box = $('#mn-map-warn');
  box.className = 'ai-status show' + (unmapped.length || warns.length || r.ai_error ? ' warn' : '');
  box.textContent = msg;
  toast('AI 자동 매핑 완료 — 결과를 확인하고 필요하면 직접 수정하세요', 'ok');
}
```

- [ ] **Step 3: 삭제** — `autoLabelMinutesForm` 함수 전체, `#mn-map-autolabel` 리스너 바인딩(grep으로 위치 확인), `openMinutesMapEditor`의 autolabel disabled 블록(2187-2192 부근)을 `#mn-map-ai` 대상 disabled+title 처리로 전환, `scanMinutesTemplate`(1461-1496) 삭제, app.css 규칙 삭제.
- [ ] **Step 4:** `grep -n "autoLabelMinutesForm\|mn-map-autolabel\|scanMinutesTemplate\|auto_label_minutes_form\|scan_minutes_template" ui/` → 0건. 전체 테스트 PASS.
- [ ] **Step 5:** 실기 스모크 — 앱 실행(`python app.py` 또는 빌드본) 후 매핑 편집기 열어 버튼 1개 표시·클릭 시 오버레이/핀 배치 육안 확인 (AI 키 설정된 환경에서. 키 없으면 disabled 확인만).
- [ ] **Step 6: 커밋** — `git add ui/app.js ui/index.html ui/app.css` → `feat(ui): AI 자동 매핑 버튼 통합 — 표준+커스텀 1클릭·1호출`

---

### Task 11: 레거시 제거

**Files:**
- Modify: `src/api.py` — `scan_minutes_template`(1207-1245), `auto_label_minutes_form`(1262-1295) 삭제
- Modify: `src/ai/minutes_template_mapper.py` — `map_minutes_cells`+`_PROMPT_TMPL`, `auto_label_cells`+`_AUTO_PROMPT_TMPL` 삭제
- Test: `tests/test_minutes_api.py`, `tests/test_minutes_template_mapper.py` — 구 함수/엔드포인트 대상 테스트 삭제·이관

- [ ] **Step 1:** 구 테스트 정리 — 두 테스트 파일에서 `map_minutes_cells`/`auto_label_cells`/`scan_minutes_template`/`auto_label_minutes_form` 대상 테스트를 확인: Task 7·8 신규 테스트가 같은 검증을 커버하는 항목은 삭제, 커버 안 되는 검증(예: AI 실패 시 기존 캐시 보호)은 `map_minutes_form` 대상으로 이관.
- [ ] **Step 2:** 함수/엔드포인트 4개 삭제. `grep -rn "map_minutes_cells\|auto_label_cells\|scan_minutes_template\|auto_label_minutes_form" src/ ui/ tests/` → 0건.
- [ ] **Step 3:** 전체 테스트 PASS.
- [ ] **Step 4: 커밋** — `git add src/api.py src/ai/minutes_template_mapper.py tests/test_minutes_api.py tests/test_minutes_template_mapper.py` → `refactor(minutes): 레거시 AI 매핑 2계열 제거 — map_minutes_form로 일원화`

---

### Task 12: 버전 1.6.0 + 문서 정합

**Files:**
- Modify: `src/version.py` (`__version__ = "1.5.1"` → `"1.6.0"`)
- Modify: `docs/DECISIONS_minutes_parity.md` E-1(70-76행) — "트리거 대기(P2)" → 구현됨(v1.6.0) 갱신

- [ ] **Step 1:** 버전·문서 수정. 전체 테스트 PASS.
- [ ] **Step 2: 커밋** — `git add src/version.py docs/DECISIONS_minutes_parity.md` → `chore(version): 1.5.1 → 1.6.0 (AI 매핑 통합 + 다중 표 완전 지원)`

---

### Task 13: 실기 검증 (한글 열기까지)

**Files:** 없음 (스크래치에서 검증만)

- [ ] **Step 1:** 스크래치에 다중표 픽스처 생성(tests의 `make_multi_table_tpl` 로직 재사용) 후 `build_minutes`로 표1 좌표(cell_map 3요소 + custom_slots 표1)를 채운 hwpx 생성.
- [ ] **Step 2:** 한글 생존 프로토콜로 열기 검증 (2026-07-07 확립):

```powershell
Get-Process -Name Hwp -EA SilentlyContinue | Stop-Process -Force   # 창 없는 잔여만 확인 후
Start-Process -FilePath <생성물.hwpx>; Start-Sleep -Seconds 12
@(Get-Process -Name Hwp -EA SilentlyContinue).Count    # 1이어야 함(0=크래시)
```

이벤트 로그 교차 확인: `Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000} -MaxEvents 3` 에 hwp.exe 신규 크래시 없음.
- [ ] **Step 3:** 열린 문서에서 표1 셀 값 반영 육안 확인(가능하면 스크린샷). 확인 후 테스트 인스턴스 종료.
- [ ] **Step 4:** 사용자 안내 — 실제 커스텀 다중표 양식(상담일지류)으로 앱에서 매핑→생성 1회 수행 요청 (AI 실호출 포함 최종 확인). 릴리스(v1.6.0 빌드·배포)는 사용자 확인 후 별도 진행.
