# -*- coding: utf-8 -*-
"""회의록 HWPX 템플릿 표 구조 → 데이터 슬롯별 셀좌표 AI 매핑.

견적서의 src/ai/template_mapper.py 와 같은 역할을 HWPX(셀 좌표) 방식으로 수행한다.
커스텀 회의록 양식의 표를 스캔(src/scan/hwpx_scan.scan_hwpx_grid)한 그리드를
AI에 주고, 각 표준 슬롯의 값이 들어갈 셀(row,col)을 받는다.
결과는 템플릿 옆 .minutes.fieldmap.json 으로 캐시되며 build_minutes(cell_map=)로 전달된다.

minutes.fieldmap.json 구조 (v3):
  {
    "version": 3,
    "template": "파일명.hwpx",
    "is_standard": false,         # DEFAULT_CELLS 와 동일하면 true
    "cell_map": {"business_name": [0,1,1], "meeting_date": [0,2,1], ...},  # [table,row,col]
    "unmapped": ["content"]       # 셀을 못 찾은 슬롯
  }
"""
import json
import os

from src.ai import llm
from src.minutes.hwpx_minutes import DEFAULT_CELLS

# 표준 데이터 슬롯 → 한국어 설명 (AI 매핑 대상)
MINUTES_SLOTS = {
    "business_name": "사업명 (값이 들어갈 셀)",
    "meeting_date":  "회의 일시 (값이 들어갈 셀)",
    "meeting_place": "회의 장소 (값이 들어갈 셀)",
    "meeting_topic": "회의 주제/안건 (값이 들어갈 셀)",
    "participants":  "참석자 명단 (다중 줄이 들어갈 셀)",
    "total_count":   "총 참석 인원 '(총 N명)' (값이 들어갈 셀)",
    "content":       "회의 내용 본문 (섹션·본문이 들어갈 셀)",
}


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

    레거시 개별 매핑 함수 2종(표준 슬롯 매핑·커스텀 라벨링)의 통합 대체.
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


def _fieldmap_path(template_path: str) -> str:
    base = os.path.splitext(template_path)[0]
    return base + ".minutes.fieldmap.json"


def load_minutes_fieldmap(template_path: str) -> dict:
    """템플릿 옆 .minutes.fieldmap.json 로드. 없으면 빈 dict."""
    path = _fieldmap_path(template_path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_minutes_fieldmap(template_path: str, map_result: dict) -> str:
    """AI 매핑 결과를 .minutes.fieldmap.json 으로 저장 (v3, 좌표 [table,row,col]).

    map_result["cell_map"]은 구버전 저장 포맷([행,열] 2요소)과
    map_minutes_form([표,행,열] 3요소, 다중 표) 양쪽에서 올 수 있어
    _norm_cell_map으로 항상 3요소로 정규화한 뒤 저장한다.
    """
    path = _fieldmap_path(template_path)
    cell_map = _norm_cell_map(map_result.get("cell_map", {}))
    data = {
        "version": 3,
        "template": os.path.basename(template_path),
        "is_standard": is_standard_map(cell_map),
        "cell_map": cell_map,
        "unmapped": map_result.get("unmapped", []),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ── fieldmap v3: 사용자 편집본 저장 (cell_map + custom_slots + annotations, 좌표 항상 [table,row,col]) ────

def _norm_cell_map(cell_map: dict) -> dict:
    """cell_map(JSON 유래)에서 표준 7슬롯만 수용, 항상 [table,row,col] 3요소로 정규화.

    2요소([행,열]) 입력은 table=0으로 승격(단일 표 전제의 구버전 호환), 3요소는
    그대로 정수화. 그 외 형태(길이 0/1 등)는 무시.
    """
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


def _validate_custom_slots(custom_slots) -> tuple:
    """custom_slots [{id, label, cell:[table,row,col] 또는 [row,col]}] 검증.

    (정규화 리스트, 경고) 반환. cell은 항상 [table,row,col] 3요소로 정규화해
    담는다(2요소 입력은 table=0 승격).
    잘못된 항목(누락 id·라벨 비문자열·좌표 형식 오류)은 무시하고 경고에 담는다.
    id 중복도 거부(첫 항목만 유지) — 생성 시 custom_fields[id] 충돌 방지.
    """
    out, warnings, seen_ids = [], [], set()
    for s in (custom_slots or []):
        if not isinstance(s, dict):
            warnings.append("custom_slot 항목이 객체가 아님 — 무시")
            continue
        sid = s.get("id")
        label = s.get("label")
        cell = s.get("cell")
        if not isinstance(sid, str) or not sid.strip():
            warnings.append("custom_slot id 누락/비문자열 — 무시")
            continue
        if not isinstance(label, str):
            warnings.append(f"custom_slot '{sid}' 라벨 타입 오류 — 무시")
            continue
        try:
            if len(cell) >= 3:
                t, r, c = int(cell[0]), int(cell[1]), int(cell[2])
            else:
                t, r, c = 0, int(cell[0]), int(cell[1])
        except (TypeError, ValueError, IndexError, KeyError):
            warnings.append(f"custom_slot '{sid}' 셀 좌표 오류 — 무시")
            continue
        if sid in seen_ids:
            warnings.append(f"custom_slot id '{sid}' 중복 — 무시")
            continue
        seen_ids.add(sid)
        out.append({"id": sid, "label": label, "cell": [t, r, c]})
    return out, warnings


def _validate_annotations(annotations) -> tuple:
    """annotations [{table, row, col, label, comment, slot?}] 검증. (정규화 리스트, 경고).

    9-e 1셀=1핀: 동일 (table,row,col)에 두 번째 핀은 거부(첫 핀만 유지).
    table 기본 0(구버전 annotation 후방호환 — 단일 표 = table 0).
    정수 좌표·라벨 문자열 검증. 잘못된 항목은 무시·경고.
    """
    out, warnings, seen = [], [], set()
    for a in (annotations or []):
        if not isinstance(a, dict):
            warnings.append("annotation 항목이 객체가 아님 — 무시")
            continue
        try:
            r, c = int(a["row"]), int(a["col"])
        except (TypeError, ValueError, KeyError):
            warnings.append("annotation 좌표 오류 — 무시")
            continue
        try:
            tbl = int(a.get("table", 0))
        except (TypeError, ValueError):
            tbl = 0
        label = a.get("label", "")
        if not isinstance(label, str):
            warnings.append(f"annotation ({tbl},{r},{c}) 라벨 타입 오류 — 무시")
            continue
        if (tbl, r, c) in seen:
            warnings.append(f"annotation ({tbl},{r},{c}) 중복 핀 거부 (1셀=1핀)")
            continue
        seen.add((tbl, r, c))
        item = {"table": tbl, "row": r, "col": c, "label": label,
                "comment": str(a.get("comment", "") or "")}
        slot = a.get("slot")
        if isinstance(slot, str) and slot.strip():
            item["slot"] = slot
        # 핀 픽셀 위치(자유 위치) 보존 — 재오픈 시 같은 자리에 복원 (이미지-핀 UI).
        # 값이 없거나 범위 밖이면 생략(좌표만 있는 기존 핀과 후방호환).
        try:
            nx, ny = float(a["nx"]), float(a["ny"])
            if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0:
                item["nx"], item["ny"] = nx, ny
        except (TypeError, ValueError, KeyError):
            pass
        out.append(item)
    return out, warnings


def save_minutes_cellmap(template_path: str, cell_map: dict,
                         custom_slots=None, annotations=None) -> dict:
    """사용자 편집본을 .minutes.fieldmap.json version 3로 저장.

    구조: {version:3, template, is_standard, cell_map, unmapped,
           custom_slots, annotations}
    cell_map 값·custom_slots[].cell 은 항상 [table,row,col] 3요소로 정규화되어
    저장된다(2요소 입력은 table=0 승격). is_standard 는 cell_map 으로 재계산.
    잘못된 custom_slots/annotations 항목은 무시하고 warnings 로 보고(저장은 진행).

    반환: 저장한 fieldmap dict + {"path", "warnings"}.
    """
    cells = _norm_cell_map(cell_map)
    slots, slot_warn = _validate_custom_slots(custom_slots)
    anns, ann_warn = _validate_annotations(annotations)

    data = {
        "version": 3,
        "template": os.path.basename(template_path),
        "is_standard": is_standard_map(cells),
        "cell_map": cells,
        "unmapped": [s for s in MINUTES_SLOTS if s not in cells],
        "custom_slots": slots,
        "annotations": anns,
    }
    path = _fieldmap_path(template_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    result = dict(data)
    result["path"] = path
    result["warnings"] = slot_warn + ann_warn
    return result
