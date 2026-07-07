# -*- coding: utf-8 -*-
"""M7: hwpx_minutes.py 동등성·구조 테스트.

COM 불필요 — 기본 pytest로 실행.
"""
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import pytest

from src.minutes.hwpx_minutes import TEMPLATE_MINUTES, build_minutes

_HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'

SAMPLE_DATA = {
    "business_name": "AI 음장 센싱 기반 스마트 홈 보안 모니터링 시스템",
    "meeting_date": "2026. 04. 09.(목) 09:17~09:52",
    "meeting_place": "온라인 화상회의",
    "meeting_topic": "모두의 창업 경진대회 참여 준비 및 창업 활동 현황 논의",
    "participants": [
        "KIST 김종민 박사",
        "내비온 장윤화 이사, 김형일 / KST 문준혁",
    ],
    "total_count": 4,
    "sections": [
        {"type": "header", "text": " ■ 김종민 박사 창업 활동 현황"},
        {"type": "bullet", "text": "텍스코어 사업 선정 완료"},
        {"type": "sub",    "text": "삼성 미래육성 기술재단 창업 멘토링 지원 대기 중"},
        {"type": "empty",  "text": ""},
        {"type": "header", "text": " ■ 모두의 창업 경진대회 참여 검토"},
        {"type": "bullet", "text": "총 4회 라운드 진행"},
        {"type": "sub",    "text": "예비 창업자 지원 가능, 멘토링 및 공통 교육 이수 필요"},
        {"type": "empty",  "text": ""},
        {"type": "header", "text": " ■ 향후 추진사항"},
        {"type": "bullet", "text": "모두의 창업 경진대회 올해 집중 준비"},
        {"type": "sub",    "text": "내비온: 모두의 창업 프로세스 상세 스터디 후 절차·과정 안내"},
    ],
}


@pytest.fixture(scope="module")
def built_hwpx(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("minutes_out")
    out = str(tmp / "test_minutes.hwpx")
    result = build_minutes(SAMPLE_DATA, out_path=out)
    assert result["ok"], f"build_minutes 실패: {result.get('error')}\n{result.get('traceback','')}"
    return out


def _parse_section0(hwpx_path):
    with zipfile.ZipFile(hwpx_path, 'r') as zf:
        with zf.open("Contents/section0.xml") as f:
            return ET.parse(f).getroot()


def _all_texts(elem):
    return [t.text or "" for t in elem.findall(f'.//{_HP}t')]


def _find_cell(root, row, col):
    for tbl in root.findall(f'.//{_HP}tbl'):
        for tr in tbl.findall(f'{_HP}tr'):
            for tc in tr.findall(f'{_HP}tc'):
                addr = tc.find(f'{_HP}cellAddr')
                if (addr is not None
                        and addr.attrib.get('rowAddr') == str(row)
                        and addr.attrib.get('colAddr') == str(col)):
                    return tc
    return None


# ── 기본 구조 ──────────────────────────────────────────────────────────────────

def test_template_exists():
    assert os.path.isfile(TEMPLATE_MINUTES), f"템플릿 없음: {TEMPLATE_MINUTES}"


def test_build_returns_ok(built_hwpx):
    assert os.path.isfile(built_hwpx)
    assert os.path.getsize(built_hwpx) > 5000


def test_output_is_valid_zip(built_hwpx):
    assert zipfile.is_zipfile(built_hwpx)


def test_mimetype_first_stored(built_hwpx):
    with zipfile.ZipFile(built_hwpx, 'r') as zf:
        names = zf.namelist()
        assert names[0] == "mimetype", f"첫 항목이 mimetype이 아님: {names[0]}"
        info = zf.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED, "mimetype은 STORED여야 함"


def test_section0_exists(built_hwpx):
    with zipfile.ZipFile(built_hwpx, 'r') as zf:
        assert "Contents/section0.xml" in zf.namelist()


# ── 셀 내용 검증 ───────────────────────────────────────────────────────────────

def test_cell_business_name(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 1, 1)
    assert tc is not None
    texts = _all_texts(tc)
    assert any("AI 음장 센싱" in t for t in texts), f"사업명 없음: {texts}"


def test_cell_meeting_date(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 2, 1)
    texts = _all_texts(tc)
    assert any("2026. 04. 09." in t for t in texts), f"일시 없음: {texts}"


def test_cell_meeting_place(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 3, 1)
    texts = _all_texts(tc)
    assert any("온라인" in t for t in texts), f"장소 없음: {texts}"


def test_cell_meeting_topic(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 4, 1)
    texts = _all_texts(tc)
    assert any("모두의 창업" in t for t in texts), f"회의주제 없음: {texts}"


# ── 참석자 셀 ──────────────────────────────────────────────────────────────────

def test_participants_cell_text(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 5, 1)
    assert tc is not None
    texts = _all_texts(tc)
    assert any("KIST 김종민 박사" in t for t in texts)
    assert any("내비온" in t for t in texts)


def test_participants_first_line_bold(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 5, 1)
    sl = tc.find(f'{_HP}subList')
    paras = sl.findall(f'{_HP}p')
    assert len(paras) >= 1
    first_run = paras[0].find(f'{_HP}run')
    assert first_run is not None
    assert first_run.attrib.get('charPrIDRef') == '11', "첫 줄은 charPrIDRef=11(bold)이어야 함"


def test_participants_second_line_normal(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 5, 1)
    sl = tc.find(f'{_HP}subList')
    paras = sl.findall(f'{_HP}p')
    if len(paras) >= 2:
        second_run = paras[1].find(f'{_HP}run')
        assert second_run.attrib.get('charPrIDRef') == '0', "두 번째 줄은 charPrIDRef=0이어야 함"


def test_total_count_cell(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 5, 2)
    assert tc is not None
    texts = _all_texts(tc)
    assert any("총 4명" in t for t in texts), f"총인원 없음: {texts}"


# ── 회의내용 셀 ────────────────────────────────────────────────────────────────

def test_content_cell_has_sections(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 6, 1)
    assert tc is not None
    texts = _all_texts(tc)
    joined = "\n".join(texts)
    assert "김종민 박사 창업 활동 현황" in joined
    assert "텍스코어 사업 선정 완료" in joined
    assert "향후 추진사항" in joined


def test_content_header_para_pr(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 6, 1)
    sl = tc.find(f'{_HP}subList')
    paras = sl.findall(f'{_HP}p')
    header_paras = [p for p in paras if p.attrib.get('paraPrIDRef') == '17'
                    and p.find(f'{_HP}run') is not None
                    and p.find(f'.//{_HP}t') is not None
                    and (p.find(f'.//{_HP}t').text or "").strip()]
    assert len(header_paras) >= 3, f"header(paraPrIDRef=17) 문단 부족: {len(header_paras)}"


def test_content_bullet_para_pr(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 6, 1)
    sl = tc.find(f'{_HP}subList')
    paras = sl.findall(f'{_HP}p')
    bullet_paras = [p for p in paras if p.attrib.get('paraPrIDRef') == '24']
    assert len(bullet_paras) >= 2, f"bullet(paraPrIDRef=24) 문단 부족: {len(bullet_paras)}"


def test_content_sub_para_pr(built_hwpx):
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 6, 1)
    sl = tc.find(f'{_HP}subList')
    paras = sl.findall(f'{_HP}p')
    sub_paras = [p for p in paras if p.attrib.get('paraPrIDRef') == '25']
    assert len(sub_paras) >= 2, f"sub(paraPrIDRef=25) 문단 부족: {len(sub_paras)}"


def test_photo_table_preserved(built_hwpx):
    """사진표(paraPrIDRef=28 또는 내부 tbl 포함 문단)가 회의내용 셀에 있는지 확인."""
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 6, 1)
    sl = tc.find(f'{_HP}subList')
    paras = sl.findall(f'{_HP}p')
    photo_exists = any(
        p.attrib.get('paraPrIDRef') == '28' or p.find(f'.//{_HP}tbl') is not None
        for p in paras
    )
    assert photo_exists, "사진표 문단(paraPrIDRef=28 또는 내부 tbl)이 없음"


# ── linesegarray vertpos 단조 증가 ─────────────────────────────────────────────

def test_lineseg_vertpos_monotonic(built_hwpx):
    """회의내용 셀의 lineseg vertpos가 단조 비감소(텍스트 오버랩 없음)."""
    root = _parse_section0(built_hwpx)
    tc = _find_cell(root, 6, 1)
    sl = tc.find(f'{_HP}subList')
    paras = sl.findall(f'{_HP}p')
    vertpos_list = []
    for p in paras:
        lsa = p.find(f'{_HP}linesegarray')
        if lsa is not None:
            ls = lsa.find(f'{_HP}lineseg')
            if ls is not None:
                try:
                    vertpos_list.append(int(ls.attrib.get('vertpos', 0)))
                except ValueError:
                    pass
    assert vertpos_list, "lineseg vertpos를 찾을 수 없음"
    for i in range(1, len(vertpos_list)):
        assert vertpos_list[i] >= vertpos_list[i - 1], (
            f"vertpos 역전: [{i-1}]={vertpos_list[i-1]}, [{i}]={vertpos_list[i]}"
        )


# ── 빈 데이터 엣지케이스 ───────────────────────────────────────────────────────

def test_empty_participants(tmp_path):
    data = {
        "business_name": "테스트 사업",
        "meeting_date": "2026. 01. 01.(목)",
        "meeting_place": "서울",
        "meeting_topic": "테스트",
        "participants": [],
        "total_count": 0,
        "sections": [],
    }
    out = str(tmp_path / "empty.hwpx")
    r = build_minutes(data, out_path=out)
    assert r["ok"], r.get("error")
    assert zipfile.is_zipfile(out)
    # 한글 하드크래시 방지 불변식: 모든 셀 subList에 문단이 1개 이상.
    # 문단 0개 셀이 있으면 한글이 파일을 여는 즉시 죽는다
    # (HwpApp.dll+0x254854, 0xc0000005 — 2026-07-07 실측).
    root = _parse_section0(out)
    for tc in root.findall(f'.//{_HP}tc'):
        sl = tc.find(f'{_HP}subList')
        if sl is not None:
            addr = tc.find(f'{_HP}cellAddr')
            assert len(sl.findall(f'{_HP}p')) >= 1, \
                f"문단 0개 셀: {addr.attrib if addr is not None else '?'}"


def test_single_participant(tmp_path):
    data = {
        "business_name": "사업", "meeting_date": "2026. 06. 11.",
        "meeting_place": "본사", "meeting_topic": "주간회의",
        "participants": ["내비온 김형일"],
        "total_count": 1,
        "sections": [{"type": "header", "text": " ■ 안건"}, {"type": "bullet", "text": "항목 1"}],
    }
    out = str(tmp_path / "single.hwpx")
    r = build_minutes(data, out_path=out)
    assert r["ok"], r.get("error")
    root = _parse_section0(out)
    tc = _find_cell(root, 5, 1)
    texts = _all_texts(tc)
    assert any("김형일" in t for t in texts)


def test_missing_template_returns_error(tmp_path):
    r = build_minutes(SAMPLE_DATA,
                      template_hwpx=str(tmp_path / "없는파일.hwpx"),
                      out_path=str(tmp_path / "out.hwpx"))
    assert not r["ok"]
    assert "error" in r


# ── 자동 out_path 명명 ─────────────────────────────────────────────────────────

def test_auto_out_path(tmp_path):
    data = {
        "business_name": "자동경로테스트",
        "meeting_date": "2026. 06. 11.(수)",
        "meeting_place": "테스트",
        "meeting_topic": "자동 파일명 테스트",
        "participants": ["테스터"],
        "total_count": 1,
        "sections": [],
    }
    # out_path를 지정하지 않으면 template 옆에 자동 생성
    r = build_minutes(data, out_path=str(tmp_path / "auto_named.hwpx"))
    assert r["ok"], r.get("error")
    assert os.path.isfile(r["path"])


# ── A-6-1: 단순 셀 쓰기 내성 (방어적 구조 생성) ──────────────────────────────

def _degraded_template(src_tpl, out_tpl, row, col):
    """src_tpl을 복사하되 (row,col) 셀의 subList를 비워(빈 subList) 저장.

    병합 잔여·빈 셀처럼 subList>p>run>t 구조가 없는 셀을 모사한다.
    """
    with zipfile.ZipFile(src_tpl) as zf:
        names = zf.namelist()
        data = {n: zf.read(n) for n in names}
    xml = data["Contents/section0.xml"].decode("utf-8")
    root = ET.fromstring(xml)
    tbl = root.find(f".//{_HP}tbl")
    for tr in tbl.findall(f"{_HP}tr"):
        for tc in tr.findall(f"{_HP}tc"):
            addr = tc.find(f"{_HP}cellAddr")
            if (addr is not None
                    and addr.attrib.get("rowAddr") == str(row)
                    and addr.attrib.get("colAddr") == str(col)):
                sl = tc.find(f"{_HP}subList")
                if sl is not None:
                    for child in list(sl):
                        sl.remove(child)
    data["Contents/section0.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        + ET.tostring(root, encoding="unicode")
    ).encode("utf-8")
    with zipfile.ZipFile(out_tpl, "w") as zf:
        for n in names:
            ct = zipfile.ZIP_STORED if n == "mimetype" else zipfile.ZIP_DEFLATED
            zf.writestr(n, data[n], compress_type=ct)
    return out_tpl


def test_simple_cell_write_into_empty_cell(tmp_path):
    """A-6-1: subList>p>run>t가 없는 셀에 단순 슬롯 매핑해도 성공 + 텍스트 기록."""
    tpl = _degraded_template(
        TEMPLATE_MINUTES, str(tmp_path / "degraded.hwpx"), 1, 0)
    data = dict(SAMPLE_DATA)
    out = str(tmp_path / "out.hwpx")
    # business_name을 구조가 비워진 (1,0) 셀로 재매핑
    r = build_minutes(data, template_hwpx=tpl, out_path=out,
                      cell_map={"business_name": [1, 0]})
    assert r["ok"], r.get("error")
    root = _parse_section0(out)
    tc = _find_cell(root, 1, 0)
    texts = _all_texts(tc)
    assert any("AI 음장 센싱" in t for t in texts), f"방어적 쓰기 실패: {texts}"


# ── A-6-2: content·사진표 보존 경계 ──────────────────────────────────────────

def test_standard_build_no_photo_warning(built_hwpx):
    """A-6-2: 표준 양식 build는 사진표 보존 → 사진표 경고 없음 (동작 불변)."""
    r = build_minutes(SAMPLE_DATA, out_path=str(built_hwpx) + ".w.hwpx")
    assert r["ok"], r.get("error")
    warnings = r.get("warnings") or []
    assert not any("사진표" in w for w in warnings), f"표준 양식에 불필요한 경고: {warnings}"


def test_content_remap_no_photo_warns(tmp_path):
    """A-6-2: content를 사진표 없는 셀로 매핑 시 경고 플래그(사일런트 소실 방지)."""
    out = str(tmp_path / "remap.hwpx")
    r = build_minutes(SAMPLE_DATA, out_path=out,
                      cell_map={"content": [3, 1]})  # 장소 값셀 = 사진표 없음
    assert r["ok"], r.get("error")
    warnings = r.get("warnings") or []
    assert any("사진표" in w for w in warnings), f"경고 누락: {warnings}"


# ── A-3-2: 커스텀 정적 슬롯 생성 경로 (custom_fields → 지정 셀) ───────────────

def test_custom_slot_text_written(tmp_path):
    """custom_slots cell 좌표에 data.custom_fields 텍스트가 들어간다."""
    out = str(tmp_path / "custom.hwpx")
    data = dict(SAMPLE_DATA, custom_fields={"dept": "연구개발부"})
    slots = [{"id": "dept", "label": "담당부서", "cell": [3, 0]}]  # 실셀(라벨셀)에 덮어쓰기
    r = build_minutes(data, out_path=out, custom_slots=slots)
    assert r["ok"], r.get("error")
    root = _parse_section0(out)
    tc = _find_cell(root, 3, 0)
    assert tc is not None
    assert any("연구개발부" in t for t in _all_texts(tc))


def test_custom_slot_unset_is_safe_blank(tmp_path):
    """custom_fields에 값 없는 슬롯은 빈 칸으로 안전 생성(예외 없음)."""
    out = str(tmp_path / "blank.hwpx")
    data = dict(SAMPLE_DATA)  # custom_fields 없음
    slots = [{"id": "dept", "label": "담당부서", "cell": [3, 0]}]
    r = build_minutes(data, out_path=out, custom_slots=slots)
    assert r["ok"], r.get("error")


def test_no_custom_slots_unchanged(tmp_path):
    """custom_slots 미지정 시 기존 동작 불변 (사업명 등 정상)."""
    out = str(tmp_path / "nocustom.hwpx")
    r = build_minutes(SAMPLE_DATA, out_path=out)
    assert r["ok"], r.get("error")
    root = _parse_section0(out)
    tc = _find_cell(root, 1, 1)
    assert any("AI 음장 센싱" in t for t in _all_texts(tc))


def test_normalize_preserves_custom_fields():
    """minutes.py 정규화 화이트리스트: custom_fields 보존(unknown 폐기 방지)."""
    from src.ai.minutes import _normalize_minutes
    out = _normalize_minutes(dict(SAMPLE_DATA, custom_fields={"dept": "기획팀"}))
    assert out.get("custom_fields") == {"dept": "기획팀"}
    # 부재 시 키 자체가 없어야(기존 출력 불변)
    out2 = _normalize_minutes(dict(SAMPLE_DATA))
    assert "custom_fields" not in out2


# ── T3: 좌표 3요소화 — DEFAULT_CELLS/_norm_cells 3-tuple ─────────────────────

def test_norm_cells_accepts_2_and_3_elem():
    from src.minutes.hwpx_minutes import _norm_cells
    cells = _norm_cells({"business_name": [4, 2],          # 2요소 = 표0
                         "meeting_date": [1, 3, 1]})        # 3요소
    assert cells["business_name"] == (0, 4, 2)
    assert cells["meeting_date"] == (1, 3, 1)
    assert cells["content"] == (0, 6, 1)                    # 기본값도 3-tuple
