# -*- coding: utf-8 -*-
"""라운드트립 검증 (한글 COM 필요 — `pytest -m hwp`로 명시 실행).

22M 골든 케이스로 HWP/PDF를 실제 생성하고, 생성물을 바이너리 파서로
재파싱하여 표시값이 전부 들어갔는지 확인한다.
"""
import os

import pytest

from src.engine.calc import LaborRow, ExpenseRow, calculate
from src.hwp.field_map import build_render_plan
from src.scan.hwp_scan import parse_hwp, parse_hwpx_quote, _read_bodytext

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "output", "_test")

pytestmark = pytest.mark.hwp


def _golden_doc():
    return {
        "recipient": "한국과학기술연구원",
        "quote_no": "제 2026-001호",
        "ref_name": "", "ref_tel": "",
        "date": "2026-06-10",
        "service_name": "저가의 고효율 라이다 센서 사업 타당성 분석 용역(검증)",
        "service_period": "계약일로부터 3주일",
    }


def _golden_result():
    labor = [
        LaborRow("책임연구원", 6993408, count=1, rate=0.4, months=0.75),
        LaborRow("연구원", 5362452, count=2, rate=0.4, months=0.75),
        LaborRow("연구보조원", 3584618, count=4, rate=0.4, months=0.75),
    ]
    expenses = [
        ExpenseRow("전문가 활용비",
                   details=["- 시장참여자 검증/자문", "- 인허가 계획 자문", "- 해외진출 계획 자문"],
                   qty_text="5명", unit_price=600000, qty=5),
        ExpenseRow("문헌구입비", details=["- 산업동향 유료 보고서 구매"],
                   qty_text="1식", unit_price=4000000, qty=1),
        ExpenseRow("국내여비", details=["- 교통비, 식사비 등"],
                   qty_text="-", unit_price=308982),
        ExpenseRow("회의비", details=["- 3~4인 * 4회"],
                   qty_text="13명", unit_price=30000, qty=13),
    ]
    return calculate(labor, expenses, profit_on=True, trim=0.0)


def test_roundtrip_hwpx_output():
    """v1.7 기본 산출물(.hwpx) 라운드트립 — SaveAs HWPX 변환 + zip 재파싱."""
    import zipfile

    from src.hwp.hwp_writer import generate_once

    plan = build_render_plan(_golden_doc(), _golden_result())
    out_hwpx = os.path.join(OUT_DIR, "roundtrip_22m.hwpx")
    report = generate_once(
        {"fields": plan.fields, "labor_used": plan.labor_used,
         "exp_used": plan.exp_used, "show_trim": plan.show_trim},
        out_hwpx)

    assert report["hwp"] == out_hwpx and os.path.getsize(out_hwpx) > 10000
    # 진짜 HWPX(zip)인지 — 확장자만 hwpx인 HWP 바이너리면 여기서 실패
    with zipfile.ZipFile(out_hwpx) as zf:
        names = zf.namelist()
        assert "Contents/section0.xml" in names
    # 임시 작업 사본(.work.hwp)이 남지 않아야 함
    assert not os.path.exists(out_hwpx + ".work.hwp")

    meta = parse_hwpx_quote(out_hwpx)
    assert meta.error == ""
    assert meta.amount == 22000000
    assert "라이다" in meta.service_name
    assert meta.date == "2026-06-10"
    assert "한국과학기술연구원" in meta.recipient


def test_roundtrip_22m_golden():
    from src.hwp.hwp_writer import generate_once

    plan = build_render_plan(_golden_doc(), _golden_result())
    assert not plan.warnings

    out_hwp = os.path.join(OUT_DIR, "roundtrip_22m.hwp")
    out_pdf = os.path.join(OUT_DIR, "roundtrip_22m.pdf")
    report = generate_once(
        {"fields": plan.fields, "labor_used": plan.labor_used,
         "exp_used": plan.exp_used, "show_trim": plan.show_trim},
        out_hwp, out_pdf)

    # 산출물 존재
    assert report["hwp"] and os.path.getsize(out_hwp) > 10000
    assert report["pdf"] and os.path.getsize(out_pdf) > 10000, report.get("pdf_error")
    # 미사용 행 삭제: labor4, exp5~8 (절삭 행 포함 6개)
    assert "labor4_grade" in report["deleted_rows"]
    assert "exp8_name" in report["deleted_rows"]
    assert "trim_label" in report["deleted_rows"]

    # 메타 재파싱
    meta = parse_hwp(out_hwp)
    assert meta.amount == 22000000
    assert "라이다" in meta.service_name
    assert meta.date == "2026-06-10"
    assert "한국과학기술연구원" in meta.recipient

    # 본문 표시값 전수 확인
    import olefile
    ole = olefile.OleFileIO(out_hwp)
    body = _read_bodytext(ole)
    ole.close()
    expected_texts = [
        "제 2026-001호", "2026년 6월 10일",
        "금이천이백만원정 (₩ 22,000,000), 부가세 포함",
        "책임연구원", "1명", "6,993,408", "0.75개월", "40%", "2,098,022", "9.5%",
        "연구원", "2명", "5,362,452", "3,217,471", "14.6%",
        "연구보조원", "4명", "3,584,618", "4,301,542", "19.6%",
        "9,617,035", "43.7%",
        "전문가 활용비", "- 시장참여자 검증/자문", "- 해외진출 계획 자문",
        "3,000,000", "13.6%",
        "문헌구입비", "4,000,000", "18.2%",
        "국내여비", "308,982", "1.4%",
        "회의비", "390,000", "1.8%",
        "7,698,982", "35.0%",
        "17,316,017", "78.7%",
        "인건비+경비의 5%", "865,801", "3.9%",
        "인건비+경비+일반관리비의 10%", "1,818,182", "8.3%",
        "20,000,000", "90.9%",
        "공급가액의 10%", "2,000,000", "9.1%",
        "22,000,000", "100.0%",
    ]
    missing = [t for t in expected_texts if t not in body]
    assert not missing, f"본문 누락: {missing}"

    # 삭제 검증: 보조원 행/빈 경비 행의 잔재 없음 + 절삭 행 없음
    assert "만원미만 절삭" not in body
    # 2023년 잔재(누름틀 치환 확인)
    assert "2023-152" not in body
    assert "2023년 11월 23일" not in body


def test_expense_overflow_adds_rows():
    """경비 12개(템플릿 8행 초과) → 행을 동적 추가, 9~12번째도 본문에 반영."""
    from src.hwp.hwp_writer import generate_once

    labor = [LaborRow("책임연구원", 6993408, count=1, rate=0.4, months=0.75)]
    expenses = [
        ExpenseRow(f"추가경비{i:02d}", details=[f"- 내역{i}"],
                   qty_text="1식", unit_price=100000, qty=1)
        for i in range(1, 13)
    ]
    result = calculate(labor, expenses, profit_on=True, trim=0.0)
    plan = build_render_plan(_golden_doc(), result)
    assert plan.exp_used == 12  # 8개로 잘리지 않음

    out_hwp = os.path.join(OUT_DIR, "roundtrip_exp12.hwp")
    report = generate_once(
        {"fields": plan.fields, "labor_used": plan.labor_used,
         "exp_used": plan.exp_used, "show_trim": plan.show_trim},
        out_hwp, None)
    assert report["hwp"] and os.path.getsize(out_hwp) > 10000

    import olefile
    ole = olefile.OleFileIO(out_hwp)
    body = _read_bodytext(ole)
    ole.close()
    # 동적 추가된 9~12번째 경비 항목명이 실제 본문에 들어가야 한다
    for i in (1, 8, 9, 12):
        assert f"추가경비{i:02d}" in body, f"경비{i:02d} 본문 누락(행 추가 실패)"


def test_output_writable_when_template_readonly(tmp_path):
    """템플릿이 읽기 전용이어도 생성된 견적서는 편집 가능(쓰기 가능)해야 한다."""
    import shutil
    import stat as _stat
    from src.hwp.hwp_writer import generate_once, TEMPLATE_DEFAULT

    ro_tpl = str(tmp_path / "ro_template.hwp")
    shutil.copy2(TEMPLATE_DEFAULT, ro_tpl)
    os.chmod(ro_tpl, _stat.S_IREAD)  # 템플릿을 읽기 전용으로 강제
    try:
        plan = build_render_plan(_golden_doc(), _golden_result())
        out_hwp = os.path.join(OUT_DIR, "roundtrip_ro.hwp")
        report = generate_once(
            {"fields": plan.fields, "labor_used": plan.labor_used,
             "exp_used": plan.exp_used, "show_trim": plan.show_trim},
            out_hwp, None, template=ro_tpl)
        assert report["hwp"]
        assert os.access(out_hwp, os.W_OK), "생성된 견적서가 읽기 전용입니다(편집 불가)"
    finally:
        os.chmod(ro_tpl, _stat.S_IWRITE)  # tmp 정리 가능하도록 해제


def test_output_not_locked_under_persistent_session():
    """재사용(숨김) 한글 세션에서 생성한 직후에도 산출물이 잠겨있지 않아야 한다.

    진짜 '읽기 전용' 원인: 생성 후 문서를 닫지 않으면 백그라운드 한글이 파일을
    계속 열어둔 채 잡고 있어, 사용자가 그 파일을 열면 '다른 곳에서 사용 중' →
    읽기 전용으로 열린다(OS 속성 해제로는 못 푼다). 생성 끝에 FileClose로
    잠금을 풀었는지, 세션을 종료하지 않은 채 파일 rename으로 확인한다.
    """
    from src.hwp.hwp_writer import HwpWorker

    plan = build_render_plan(_golden_doc(), _golden_result())
    out_hwp = os.path.join(OUT_DIR, "roundtrip_lock.hwp")
    w = HwpWorker()
    try:
        rep = w.generate(
            {"fields": plan.fields, "labor_used": plan.labor_used,
             "exp_used": plan.exp_used, "show_trim": plan.show_trim},
            out_hwp, None)
        assert rep.get("ok"), rep.get("error")
        # 세션을 닫기 전에(잠금이 남아있다면 여기서 실패) rename 왕복으로 잠금 확인
        moved = out_hwp + ".movetest"
        try:
            os.replace(out_hwp, moved)
            os.replace(moved, out_hwp)
        except PermissionError:
            raise AssertionError("생성물이 한글 세션에 잠겨 있습니다(읽기 전용 유발)")
    finally:
        w.shutdown()


def test_no_profit_and_trim_variant():
    """무이윤 + 절삭 행 케이스: 목표 30,000,000."""
    from src.engine.goalseek import goal_seek
    from src.hwp.hwp_writer import generate_once

    labor = [
        LaborRow("책임연구원", 3783728, count=1, rate=0.0, months=6),
        LaborRow("연구원", 2901312, count=2, rate=0.0, months=6),
    ]
    expenses = [
        ExpenseRow("문헌구입비", details=["- 보고서 구매"], qty_text="1식",
                   unit_price=1000000, qty=1),
    ]
    gs = goal_seek(30000000, labor, expenses, profit_on=False,
                   mode="uniform")
    assert gs.ok, gs.error
    result = calculate(labor, expenses, profit_on=False, trim=gs.trim)

    from src.engine.calc import round_half_up
    assert round_half_up(result.final) == 30000000

    plan = build_render_plan(_golden_doc(), result)
    out_hwp = os.path.join(OUT_DIR, "roundtrip_noprofit.hwp")
    report = generate_once(
        {"fields": plan.fields, "labor_used": plan.labor_used,
         "exp_used": plan.exp_used, "show_trim": plan.show_trim},
        out_hwp, None)
    assert report["hwp"]

    import olefile
    ole = olefile.OleFileIO(out_hwp)
    body = _read_bodytext(ole)
    ole.close()
    assert "이윤 미계상" in body
    assert "30,000,000" in body
    if plan.show_trim:
        assert "만원미만 절삭" in body


def test_template_expense_category_merged():
    """템플릿 불변식: 경비 카테고리('경비') 열이 exp1~8 전 행 단일 병합 셀.

    2026-07-22 회귀 가드 — 템플릿이 미병합(개별 셀)으로 깨지면 산출물에서
    '경비' 라벨이 일부 행만 덮는 육안 결함이 재발한다(진단: 고유 셀 주소 수).
    """
    from src.hwp.hwp_writer import make_hwp, TEMPLATE_DEFAULT

    hwp = make_hwp()
    try:
        hwp.open(os.path.abspath(TEMPLATE_DEFAULT), arg="forceopen:true")
        addrs = []
        for i in range(1, 9):
            assert hwp.MoveToField(f"exp{i}_name", True, True, False), f"exp{i}_name 없음"
            hwp.Run("TableLeftCell")
            ki = hwp.KeyIndicator()
            addrs.append(ki[-1] if ki else "?")
        hwp.Run("FileClose")
        uniq = set(addrs)
        assert len(uniq) == 1, f"경비 카테고리 열이 미병합(고유 셀 {len(uniq)}개): {sorted(uniq)}"
    finally:
        try:
            hwp.quit()
        except Exception:
            pass
