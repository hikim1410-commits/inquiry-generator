# -*- coding: utf-8 -*-
"""HWPX 회의록 생성 엔진 — COM 불필요, ElementTree + zipfile 직접 편집.

양식: templates/회의록_양식.hwpx  (7행×3열 표, [내비온] 회의록 양식)
  row 1 col 1: 사업명
  row 2 col 1: 일시
  row 3 col 1: 장소
  row 4 col 1: 회의주제
  row 5 col 1: 참석자 (다중줄), col 2: 총인원
  row 6 col 1: 회의내용 (sections + 사진표 deepcopy 보존)

charPr ID 매핑 (원본 양식 기준):
  0  = 11pt 일반
  11 = 11pt bold (참석자 첫줄)
  12 = 11pt bold (■ 섹션 헤더)
  13 = 총인원 셀 전용
  14 = empty_small 전용

paraPr ID 매핑:
  11 = 참석자 줄
  17 = header / empty_small
  20 = empty (일반 빈줄)
  24 = bullet (1차 항목)
  25 = sub (2차 항목)
  26 = 총인원 셀 전용
  27 = 회의내용 trailing
  28 = 사진표 포함 문단 (deepcopy 보존)
"""
import copy
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from src.paths import resource_path

TEMPLATE_MINUTES = resource_path("templates", "회의록_양식.hwpx")

# 표준 양식의 데이터 슬롯 → 셀 좌표 (tableAddr, rowAddr, colAddr).
# 커스텀 양식은 AI 분석 결과(cell_map)로 이 좌표를 덮어쓴다.
# (table 0 = 문서의 첫 표; rowAddr 0 = 제목행, 1=사업명 … 6=회의내용; col 0=라벨, col 1/2=값)
DEFAULT_CELLS = {
    "business_name": (0, 1, 1),
    "meeting_date":  (0, 2, 1),
    "meeting_place": (0, 3, 1),
    "meeting_topic": (0, 4, 1),
    "participants":  (0, 5, 1),
    "total_count":   (0, 5, 2),
    "content":       (0, 6, 1),
}

# HWP/HWPML 네임스페이스
_NS = {
    'ha':        'http://www.hancom.co.kr/hwpml/2011/app',
    'hp':        'http://www.hancom.co.kr/hwpml/2011/paragraph',
    'hp10':      'http://www.hancom.co.kr/hwpml/2016/paragraph',
    'hs':        'http://www.hancom.co.kr/hwpml/2011/section',
    'hc':        'http://www.hancom.co.kr/hwpml/2011/core',
    'hh':        'http://www.hancom.co.kr/hwpml/2011/head',
    'hhs':       'http://www.hancom.co.kr/hwpml/2011/history',
    'hm':        'http://www.hancom.co.kr/hwpml/2011/master-page',
    'hpf':       'http://www.hancom.co.kr/schema/2011/hpf',
    'dc':        'http://purl.org/dc/elements/1.1/',
    'opf':       'http://www.idpf.org/2007/opf/',
    'ooxmlchart':'http://www.hancom.co.kr/hwpml/2016/ooxmlchart',
    'hwpunitchar':'http://www.hancom.co.kr/hwpml/2016/HwpUnitChar',
    'epub':      'http://www.idpf.org/2007/ops',
    'config':    'urn:oasis:names:tc:opendocument:xmlns:config:1.0',
}
_HP = '{' + _NS['hp'] + '}'

for _prefix, _uri in _NS.items():
    ET.register_namespace(_prefix, _uri)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _find_cell(tbl, row, col):
    for tr in tbl.findall(f'{_HP}tr'):
        for tc in tr.findall(f'{_HP}tc'):
            addr = tc.find(f'{_HP}cellAddr')
            if (addr is not None
                    and addr.attrib.get('rowAddr') == str(row)
                    and addr.attrib.get('colAddr') == str(col)):
                return tc
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


def _set_simple_cell_text(tbl, row, col, text):
    """단순 슬롯 텍스트를 셀에 쓴다. 구조가 없으면 방어적으로 생성(A-6-1).

    tc가 None(좌표가 실셀 아님)이면 조용히 건너뛴다(미매핑=빈칸 정책).
    빈 셀·병합 잔여 셀처럼 subList/p/run/t가 없어도 AttributeError 없이 보정.
    기존 스타일 run이 있으면 보존, 없으면 charPrIDRef 기본값(0)으로 새 run 생성.
    """
    tc = _find_cell(tbl, row, col)
    if tc is None:
        return
    sublist = tc.find(f'{_HP}subList')
    if sublist is None:
        sublist = ET.SubElement(tc, f'{_HP}subList')
    p = sublist.find(f'{_HP}p')
    if p is None:
        p = ET.SubElement(sublist, f'{_HP}p', {
            'id': '0', 'paraPrIDRef': '0', 'styleIDRef': '0',
            'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
        })
    run = p.find(f'{_HP}run')
    if run is None:
        run = ET.SubElement(p, f'{_HP}run', {'charPrIDRef': '0'})
    t = run.find(f'{_HP}t')
    if t is None:
        t = ET.SubElement(run, f'{_HP}t')
    t.text = text
    for extra_run in p.findall(f'{_HP}run')[1:]:
        p.remove(extra_run)


def _make_para(ptype, text, vertpos):
    """paragraph 엘리먼트 생성. (element, next_vertpos) 반환."""
    if ptype == "empty_small":
        p = ET.Element(f'{_HP}p', {
            'id': '0', 'paraPrIDRef': '17', 'styleIDRef': '0',
            'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
        })
        ET.SubElement(p, f'{_HP}run', {'charPrIDRef': '14'})
        lsa = ET.SubElement(p, f'{_HP}linesegarray')
        ET.SubElement(lsa, f'{_HP}lineseg', {
            'textpos': '0', 'vertpos': str(vertpos), 'vertsize': '500',
            'textheight': '500', 'baseline': '425', 'spacing': '300',
            'horzpos': '100', 'horzsize': '41204', 'flags': '393216',
        })
        return p, vertpos + 800

    elif ptype == "empty":
        p = ET.Element(f'{_HP}p', {
            'id': '0', 'paraPrIDRef': '20', 'styleIDRef': '0',
            'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
        })
        ET.SubElement(p, f'{_HP}run', {'charPrIDRef': '0'})
        lsa = ET.SubElement(p, f'{_HP}linesegarray')
        ET.SubElement(lsa, f'{_HP}lineseg', {
            'textpos': '0', 'vertpos': str(vertpos), 'vertsize': '1100',
            'textheight': '1100', 'baseline': '935', 'spacing': '660',
            'horzpos': '100', 'horzsize': '41204', 'flags': '393216',
        })
        return p, vertpos + 1760

    elif ptype == "header":
        p = ET.Element(f'{_HP}p', {
            'id': '0', 'paraPrIDRef': '17', 'styleIDRef': '0',
            'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
        })
        run = ET.SubElement(p, f'{_HP}run', {'charPrIDRef': '12'})
        ET.SubElement(run, f'{_HP}t').text = text
        lsa = ET.SubElement(p, f'{_HP}linesegarray')
        ET.SubElement(lsa, f'{_HP}lineseg', {
            'textpos': '0', 'vertpos': str(vertpos), 'vertsize': '1100',
            'textheight': '1100', 'baseline': '935', 'spacing': '660',
            'horzpos': '100', 'horzsize': '41204', 'flags': '393216',
        })
        return p, vertpos + 1760

    elif ptype == "bullet":
        p = ET.Element(f'{_HP}p', {
            'id': '0', 'paraPrIDRef': '24', 'styleIDRef': '0',
            'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
        })
        run = ET.SubElement(p, f'{_HP}run', {'charPrIDRef': '0'})
        ET.SubElement(run, f'{_HP}t').text = text
        lsa = ET.SubElement(p, f'{_HP}linesegarray')
        ET.SubElement(lsa, f'{_HP}lineseg', {
            'textpos': '0', 'vertpos': str(vertpos), 'vertsize': '1100',
            'textheight': '1100', 'baseline': '935', 'spacing': '660',
            'horzpos': '500', 'horzsize': '40804', 'flags': '2490368',
        })
        return p, vertpos + 1760

    elif ptype == "sub":
        p = ET.Element(f'{_HP}p', {
            'id': '0', 'paraPrIDRef': '25', 'styleIDRef': '0',
            'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
        })
        run = ET.SubElement(p, f'{_HP}run', {'charPrIDRef': '0'})
        ET.SubElement(run, f'{_HP}t').text = text
        lsa = ET.SubElement(p, f'{_HP}linesegarray')
        ET.SubElement(lsa, f'{_HP}lineseg', {
            'textpos': '0', 'vertpos': str(vertpos), 'vertsize': '1100',
            'textheight': '1100', 'baseline': '935', 'spacing': '660',
            'horzpos': '1000', 'horzsize': '40304', 'flags': '2490368',
        })
        return p, vertpos + 1760

    return None, vertpos


def _capture_content_styles(old_paras):
    """기존 회의내용 셀 문단에서 header/bullet/sub/empty 스타일 원본(deepcopy)을 추출.

    양식마다 문단 스타일 ID(paraPrIDRef)의 정렬·들여쓰기가 다르다(표준=JUSTIFY,
    어떤 커스텀 양식은 동일 ID가 CENTER). 하드코딩 ID 대신 **그 양식의 본문 문단
    스타일을 그대로 물려받아** 텍스트만 갈아끼우면 어떤 양식이든 정렬이 맞는다.
    못 찾은 레벨은 None → 호출부가 _make_para 로 폴백.
    """
    header = bullet = sub = empty = None
    for pp in old_paras:
        if pp.find(f'.//{_HP}tbl') is not None:
            continue  # 사진표 문단 제외
        ppr = pp.attrib.get('paraPrIDRef')
        txt = ''.join((t.text or '') for t in pp.findall(f'.//{_HP}t')).strip()
        if not txt:
            if empty is None:
                empty = copy.deepcopy(pp)
        elif txt.startswith('■'):
            if header is None:
                header = copy.deepcopy(pp)
        elif bullet is None:
            bullet = copy.deepcopy(pp)
        elif sub is None and ppr != bullet.attrib.get('paraPrIDRef'):
            sub = copy.deepcopy(pp)   # bullet 보다 깊은(다른) 들여쓰기 문단
    return {"header": header, "bullet": bullet, "sub": sub, "empty": empty}


def _fill_para_clone(tmpl, text, vertpos):
    """원본 문단(tmpl)을 deepcopy 해 첫 <t>에 text 를 채우고 vertpos 갱신.

    paraPrIDRef·charPrIDRef·linesegarray(정렬·들여쓰기·글꼴) 전부 보존된다.
    """
    p = copy.deepcopy(tmpl)
    ts = p.findall(f'.//{_HP}t')
    if ts:
        ts[0].text = text
        for t in ts[1:]:
            t.text = ''
    else:
        run = p.find(f'{_HP}run')
        if run is None:
            run = ET.SubElement(p, f'{_HP}run', {'charPrIDRef': '0'})
        ET.SubElement(run, f'{_HP}t').text = text
    lsa = p.find(f'{_HP}linesegarray')
    if lsa is not None:
        ls = lsa.find(f'{_HP}lineseg')
        if ls is not None:
            ls.attrib['vertpos'] = str(vertpos)
    return p, vertpos + 1760


# ── 공개 API ─────────────────────────────────────────────────────────────────

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


def build_minutes(data: dict, template_hwpx: str = None, out_path: str = None,
                  cell_map: dict = None, custom_slots: list = None) -> dict:
    """MINUTES_SCHEMA 데이터 → HWPX 파일 생성.

    data keys:
      business_name, meeting_date, meeting_place, meeting_topic,
      participants: [str, ...]  (첫 줄 bold, 나머지 일반),
      total_count: int,
      sections: [{"type": "header|bullet|sub|empty", "text": str}, ...]

    cell_map: 커스텀 양식의 슬롯→[row,col] 매핑 (AI 분석 결과). None이면 표준 좌표.

    반환: {ok: bool, path: str, error?: str}
    """
    if template_hwpx is None:
        template_hwpx = TEMPLATE_MINUTES
    if not os.path.exists(template_hwpx):
        return {"ok": False, "error": f"템플릿 없음: {template_hwpx}"}

    cells = _norm_cells(cell_map)
    warnings = []

    tmp = tempfile.mkdtemp(prefix="minutes_")
    try:
        # 1) 압축 해제
        with zipfile.ZipFile(template_hwpx, 'r') as zf:
            zf.extractall(tmp)

        xml_path = os.path.join(tmp, "Contents", "section0.xml")
        tree = ET.parse(xml_path)
        root = tree.getroot()

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

        # 2) 단순 셀 (사업명/일시/장소/주제) — 기존 run 스타일 보존
        for slot in ("business_name", "meeting_date", "meeting_place", "meeting_topic"):
            tb, r, c = _resolve(cells[slot])
            if tb is not None:
                _set_simple_cell_text(tb, r, c, data.get(slot, ""))

        # 2b) 커스텀 슬롯 (정적 텍스트) — custom_slots cell 좌표에 data.custom_fields 기록.
        # 미지정 슬롯은 빈 텍스트로 안전 생성, custom_slots 없으면 동작 불변(9-a (ii)).
        custom_fields = data.get("custom_fields") or {}
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

        # 3) 참석자 셀
        participants = data.get("participants") or []
        tb5, r5, c5 = _resolve(cells["participants"])
        tc5_1 = _find_cell(tb5, r5, c5) if tb5 is not None else None
        if tc5_1 is not None:
            sl5 = tc5_1.find(f'{_HP}subList')
            for old in sl5.findall(f'{_HP}p'):
                sl5.remove(old)
            # 참석자가 비어도 빈 문단 1개는 반드시 남긴다. 한글은 문단 0개인
            # subList 셀을 여는 즉시 하드 크래시한다(HwpApp.dll+0x254854,
            # 0xc0000005 — 2026-07-07 실측, 셀당 최소 1문단 불변식).
            if not participants:
                participants = [""]
            for idx, line in enumerate(participants):
                p = ET.SubElement(sl5, f'{_HP}p', {
                    'id': '2147483648' if idx == 0 else '0',
                    'paraPrIDRef': '11', 'styleIDRef': '0',
                    'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
                })
                char_id = '11' if idx == 0 else '0'
                run = ET.SubElement(p, f'{_HP}run', {'charPrIDRef': char_id})
                ET.SubElement(run, f'{_HP}t').text = line
                lsa = ET.SubElement(p, f'{_HP}linesegarray')
                ET.SubElement(lsa, f'{_HP}lineseg', {
                    'textpos': '0', 'vertpos': str(idx * 1760),
                    'vertsize': '1100', 'textheight': '1100',
                    'baseline': '935', 'spacing': '660',
                    'horzpos': '1000', 'horzsize': '31016', 'flags': '2490368',
                })

        # 4) 총인원 셀
        tb52, r52, c52 = _resolve(cells["total_count"])
        tc5_2 = _find_cell(tb52, r52, c52) if tb52 is not None else None
        if tc5_2 is not None:
            sl52 = tc5_2.find(f'{_HP}subList')
            for old in sl52.findall(f'{_HP}p'):
                sl52.remove(old)
            total = data.get("total_count", 0)
            count_text = f"(총 {total}명)" if total else ""
            p_cnt = ET.SubElement(sl52, f'{_HP}p', {
                'id': '0', 'paraPrIDRef': '26', 'styleIDRef': '0',
                'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
            })
            run_cnt = ET.SubElement(p_cnt, f'{_HP}run', {'charPrIDRef': '13'})
            ET.SubElement(run_cnt, f'{_HP}t').text = count_text
            lsa_cnt = ET.SubElement(p_cnt, f'{_HP}linesegarray')
            ET.SubElement(lsa_cnt, f'{_HP}lineseg', {
                'textpos': '0', 'vertpos': '0', 'vertsize': '1100',
                'textheight': '1100', 'baseline': '935', 'spacing': '660',
                'horzpos': '1000', 'horzsize': '6112', 'flags': '393216',
            })

        # 5) 회의내용 셀 — 사진표 deepcopy 보존
        tb6, r6, c6 = _resolve(cells["content"])
        tc6_1 = _find_cell(tb6, r6, c6) if tb6 is not None else None
        if tc6_1 is not None:
            sl6 = tc6_1.find(f'{_HP}subList')
            old_paras = sl6.findall(f'{_HP}p')

            # 사진표 = 내부에 실제 표(tbl)를 가진 문단만. (paraPrIDRef=='28'은 표준 전용
            # 휴리스틱이라 커스텀 양식의 일반 본문 문단을 오인해 끝에 끼워넣는 버그가 있었음.)
            photo_para = None
            for pp in old_paras:
                if pp.find(f'.//{_HP}tbl') is not None:
                    photo_para = copy.deepcopy(pp)
                    break

            # 양식 고유 본문 문단 스타일(정렬·들여쓰기) 캡처 — 비우기 전에.
            content_styles = _capture_content_styles(old_paras)

            # A-6-2: 표준 양식의 content 셀(6,1)은 사진표를 가지므로 photo_para!=None →
            # 경고 없음(동작 불변). content를 사진표 없는 셀로 재매핑하면 photo_para가
            # None이라 사진표가 재구성에 포함되지 않으므로(사일런트 소실) 경고로 가시화.
            if photo_para is None:
                warnings.append("사진표 없는 셀로 content 매핑됨")

            for old in list(sl6.findall(f'{_HP}p')):
                sl6.remove(old)

            vpos = 0
            p0, vpos = _make_para("empty_small", "", vpos)
            sl6.append(p0)

            sections = data.get("sections") or []
            for sec in sections:
                ptype = sec.get("type", "empty")
                text = sec.get("text", "")
                # 양식의 본문 스타일을 물려받아 씀(정렬 일치). 없으면 _make_para 폴백.
                tmpl = content_styles.get(ptype)
                if ptype == "sub" and tmpl is None:
                    tmpl = content_styles.get("bullet")   # sub 원본 없으면 bullet+들여쓰기
                    if tmpl is not None and text:
                        text = "  " + text
                if tmpl is not None:
                    para, vpos = _fill_para_clone(tmpl, text, vpos)
                else:
                    para, vpos = _make_para(ptype, text, vpos)
                if para is not None:
                    sl6.append(para)

            p_pre, vpos = _make_para("empty", "", vpos)
            sl6.append(p_pre)

            if photo_para is not None:
                photo_lsa = photo_para.find(f'{_HP}linesegarray')
                if photo_lsa is not None:
                    ls = photo_lsa.find(f'{_HP}lineseg')
                    if ls is not None:
                        ls.attrib['vertpos'] = str(vpos)
                sl6.append(photo_para)
                vpos += 2608

            p_trail = ET.Element(f'{_HP}p', {
                'id': '0', 'paraPrIDRef': '27', 'styleIDRef': '0',
                'pageBreak': '0', 'columnBreak': '0', 'merged': '0',
            })
            ET.SubElement(p_trail, f'{_HP}run', {'charPrIDRef': '0'})
            lsa_t = ET.SubElement(p_trail, f'{_HP}linesegarray')
            ET.SubElement(lsa_t, f'{_HP}lineseg', {
                'textpos': '0', 'vertpos': str(vpos), 'vertsize': '1100',
                'textheight': '1100', 'baseline': '935', 'spacing': '660',
                'horzpos': '1000', 'horzsize': '40304', 'flags': '393216',
            })
            sl6.append(p_trail)

        # 6) XML 기록
        xml_decl = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        xml_str = ET.tostring(root, encoding='unicode', xml_declaration=False)
        with open(xml_path, 'w', encoding='utf-8') as f:
            f.write(xml_decl + xml_str)

        # 7) PrvText.txt 업데이트
        prvtext_path = os.path.join(tmp, "Preview", "PrvText.txt")
        if os.path.exists(prvtext_path):
            lines = [
                "<회 의 록>",
                f"<사업명><{data.get('business_name', '')}>",
                f"<일  시><{data.get('meeting_date', '')}>",
                f"<장  소><{data.get('meeting_place', '')}>",
                f"<회의주제><{data.get('meeting_topic', '')}>",
                "<참석자><" + (participants[0] if participants else ""),
            ]
            if len(participants) > 1:
                lines.append("\n".join(participants[1:]) + f"><(총 {data.get('total_count', 0)}명)>")
            else:
                lines.append(f"><(총 {data.get('total_count', 0)}명)>")
            lines.append("<회의내용>")
            for sec in (data.get("sections") or []):
                lines.append(sec.get("text", "") if sec.get("type") != "empty" else "")
            with open(prvtext_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines) + "\n")

        # 8) HWPX 재패키징 (mimetype STORED 첫번째)
        if out_path is None:
            topic = data.get("meeting_topic", "회의록")[:20].replace(" ", "_")
            date_tag = (data.get("meeting_date") or "")[:10].replace(". ", "").replace(".", "")
            out_path = os.path.join(
                os.path.dirname(template_hwpx),
                f"회의록_{topic}_{date_tag}.hwpx",
            )

        if os.path.exists(out_path):
            os.remove(out_path)

        with zipfile.ZipFile(out_path, 'w') as zf:
            mime_src = os.path.join(tmp, "mimetype")
            if os.path.exists(mime_src):
                zf.write(mime_src, "mimetype", compress_type=zipfile.ZIP_STORED)
            for dirpath, _, filenames in os.walk(tmp):
                for fn in filenames:
                    if fn == "mimetype":
                        continue
                    full = os.path.join(dirpath, fn)
                    arc = os.path.relpath(full, tmp).replace("\\", "/")
                    zf.write(full, arc, compress_type=zipfile.ZIP_DEFLATED)

        return {"ok": True, "path": out_path, "warnings": warnings}

    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
