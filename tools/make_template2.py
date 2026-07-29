# -*- coding: utf-8 -*-
"""1회성 템플릿화 v2: 국가수리과학연구소 수정4차 견적서 → templates/견적서_템플릿.hwp

원본(다운로드 폴더)은 절대 수정하지 않고 사본에 작업한다.
v1(make_template.py, 라이다 원본)과의 구조 차이:
  - 인건비 4행이 원본에 이미 존재 → 행 추가 불필요
  - 경비 6행 존재 → exp7·exp8 2행만 추가 (v1은 4행→8행)
  - 절삭 행 없음 → 부가세 아래 삽입 (v1과 동일)
  - 참조·전화번호 누름틀 자리에 기존 값(이선화/042-…)이 차 있음 → 삭제 후 누름틀
  - 원본에 계산식(%fmu) 필드 없음 (GetFieldList 0개 확인) → 합계 셀 정리 불필요
"""
import os
import shutil
import sys
import traceback

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORIGINAL = r"C:\Users\eicic.AIDEN-DESKTOP\Downloads\[내비온] 국가수리과학연구소 용역견적서_예산 증액안_20260610(수정 4차) .hwp"
TEMPLATE = os.path.join(BASE, "templates", "견적서_템플릿.hwp")
BACKUP = os.path.join(BASE, "templates", "백업_견적서_템플릿_v1_라이다.hwp")
CHECK_PDF = os.path.join(BASE, "templates", "_template_check.pdf")

LOG = []


def log(msg):
    LOG.append(str(msg))
    print(f"[tpl2] {msg}", flush=True)


def find_text(hwp, text):
    pset = hwp.HParameterSet.HFindReplace
    hwp.HAction.GetDefault("RepeatFind", pset.HSet)
    pset.FindString = text
    pset.IgnoreMessage = 1
    pset.FindType = 1
    try:
        pset.Direction = hwp.FindDir("Forward")
    except Exception:
        pset.Direction = 0
    ok = bool(hwp.HAction.Execute("RepeatFind", pset.HSet))
    if not ok:
        raise RuntimeError(f"찾기 실패: {text!r}")
    return ok


def set_cell_field(hwp, name):
    """현재 캐럿 셀에 셀 필드 부여."""
    hwp.SetCurFieldName(name, option=1, direction="", memo="")


def step(hwp, action, n=1):
    for _ in range(n):
        hwp.Run(action)


def addr(hwp):
    try:
        ki = hwp.KeyIndicator()
        return ki[-1] if ki else "?"
    except Exception:
        return "?"


def field_row(hwp, names, first_at_caret=True):
    """캐럿 셀부터 오른쪽으로 이동하며 필드 연속 부여."""
    for i, name in enumerate(names):
        if i > 0 or not first_at_caret:
            step(hwp, "TableRightCell")
        set_cell_field(hwp, name)
        log(f"  field {name} @ {addr(hwp)}")


def insert_row_below(hwp):
    """현재 행 아래에 행 삽입 후 캐럿을 새 행(같은 열)으로 이동."""
    a0 = addr(hwp)
    step(hwp, "TableInsertLowerRow")
    step(hwp, "TableLowerCell")
    a1 = addr(hwp)
    log(f"  row insert+enter: {a0} → {a1}")
    if a0 == a1:
        raise RuntimeError(f"새 행 진입 실패: {a0}")


def replace_with_field(hwp, anchor, name, label):
    """앵커 텍스트 찾기 → 선택 삭제 → 그 자리에 누름틀 생성."""
    find_text(hwp, anchor)
    hwp.Run("Delete")
    r = hwp.CreateField(name, label, "")
    log(f"누름틀 {name}: create={r}, exist={hwp.FieldExist(name)}")


def main():
    assert os.path.exists(ORIGINAL), f"원본 없음: {ORIGINAL}"
    orig_size = os.path.getsize(ORIGINAL)
    os.makedirs(os.path.dirname(TEMPLATE), exist_ok=True)

    # 기존 템플릿(v1) 백업 — 이미 백업이 있으면 덮지 않음
    if os.path.exists(TEMPLATE) and not os.path.exists(BACKUP):
        shutil.copy2(TEMPLATE, BACKUP)
        log(f"기존 템플릿 백업 → {BACKUP}")

    work = TEMPLATE
    shutil.copy2(ORIGINAL, work)
    log(f"copy → {work}")

    from src.hwp.hwp_writer import make_hwp
    hwp = make_hwp()
    try:
        hwp.open(work, arg="forceopen:true")
        log("open ok")

        # ---- 1. 수신처 셀 누름틀 ----
        hwp.Run("MoveDocBegin")
        replace_with_field(hwp, "국가수리과학연구소", "recv", "수신기관명")
        replace_with_field(hwp, "제 2026-62호", "quote_no", "견적번호")
        replace_with_field(hwp, "이선화", "ref_name", "참조자")
        replace_with_field(hwp, "042-717-5798", "ref_tel", "연락처")
        replace_with_field(hwp, "2026년 6월 10일", "quote_date", "견적일자")

        # ---- 2. 용역명/기간/금액 ----
        find_text(hwp, "국가수리과학연구소 사업화 유망기술 기술소개서(SMK) 제작 및 기술마케팅 용역")
        set_cell_field(hwp, "svc_name")
        log(f"svc_name @ {addr(hwp)}")
        find_text(hwp, "계약일 ~ 2026년 11월 30일")
        set_cell_field(hwp, "svc_period")
        log(f"svc_period @ {addr(hwp)}")
        find_text(hwp, "금오천만원정")
        set_cell_field(hwp, "amount_kor")
        log(f"amount_kor @ {addr(hwp)}")

        # ---- 3. 인건비 4행 (원본에 이미 4행 존재) ----
        labor_anchor = {1: "7,567,456", 2: "5,802,624", 3: "3,878,858", 4: "2,909,242"}
        for i in (1, 2, 3, 4):
            find_text(hwp, labor_anchor[i])     # 단가 셀 (금액 셀보다 앞)
            set_cell_field(hwp, f"labor{i}_price")
            log(f"labor{i}_price @ {addr(hwp)}")
            step(hwp, "TableLeftCell")
            set_cell_field(hwp, f"labor{i}_cnt")
            step(hwp, "TableLeftCell")
            set_cell_field(hwp, f"labor{i}_grade")
            step(hwp, "TableRightCell", 3)      # cnt, price 지나 참여기간으로
            field_row(hwp, [f"labor{i}_months", f"labor{i}_rate",
                            f"labor{i}_amt", f"labor{i}_ratio"])

        # ---- 4. 인건비 계 ----
        find_text(hwp, "26,354,585")
        set_cell_field(hwp, "labor_sum_amt")
        hwp.PutFieldText("labor_sum_amt", "999")            # 왕복 검증
        got = hwp.GetFieldText("labor_sum_amt")
        log(f"labor_sum_amt 왕복: {got!r} @ {addr(hwp)}")
        hwp.PutFieldText("labor_sum_amt", "26,354,585 ")
        step(hwp, "TableRightCell")
        set_cell_field(hwp, "labor_sum_ratio")
        log(f"labor_sum_ratio @ {addr(hwp)}")

        # ---- 5. 경비 6행 ----
        exp_anchor = {1: "여비", 2: "SMK 제작", 3: "전시회 운영비",
                      4: "회의비", 5: "SW 활용비", 6: "인쇄비"}
        for i in (1, 2, 3, 4, 5, 6):
            find_text(hwp, exp_anchor[i])
            set_cell_field(hwp, f"exp{i}_name")
            log(f"exp{i}_name @ {addr(hwp)}")
            field_row(hwp, [f"exp{i}_detail", f"exp{i}_qty", f"exp{i}_price",
                            f"exp{i}_amt", f"exp{i}_ratio"], first_at_caret=False)

        # ---- 경비 2행 추가 (exp7~exp8) ----
        for i in (7, 8):
            hwp.MoveToField(f"exp{i-1}_name", True, True, False)
            insert_row_below(hwp)
            field_row(hwp, [f"exp{i}_name", f"exp{i}_detail", f"exp{i}_qty",
                            f"exp{i}_price", f"exp{i}_amt", f"exp{i}_ratio"])

        # ---- 6. 경비 계 / 소계 ----
        find_text(hwp, "13,000,000")
        set_cell_field(hwp, "exp_sum_amt")
        step(hwp, "TableRightCell")
        set_cell_field(hwp, "exp_sum_ratio")
        log(f"exp_sum @ {addr(hwp)}")

        find_text(hwp, "39,354,585")
        set_cell_field(hwp, "subtotal_amt")
        step(hwp, "TableRightCell")
        set_cell_field(hwp, "subtotal_ratio")
        log(f"subtotal @ {addr(hwp)}")

        # ---- 일반관리비 / 이윤 ----
        find_text(hwp, "인건비+경비의 6% 이내")
        set_cell_field(hwp, "mgmt_basis")
        field_row(hwp, ["mgmt_amt", "mgmt_ratio"], first_at_caret=False)

        find_text(hwp, "인건비+경비+일반관리비의 10% 이내")
        set_cell_field(hwp, "profit_basis")
        field_row(hwp, ["profit_amt", "profit_ratio"], first_at_caret=False)

        # ---- 총계(공급가액) ----
        find_text(hwp, "45,454,545")
        set_cell_field(hwp, "supply_amt")
        step(hwp, "TableRightCell")
        set_cell_field(hwp, "supply_ratio")
        log(f"supply @ {addr(hwp)}")

        # ---- 부가세 ----
        find_text(hwp, "공급가액의 10%")
        set_cell_field(hwp, "vat_basis")
        field_row(hwp, ["vat_amt", "vat_ratio"], first_at_caret=False)

        # ---- 7. 절삭 행 (부가세 행 아래 삽입) ----
        insert_row_below(hwp)                   # vat_ratio 셀에서 아래로
        set_cell_field(hwp, "trim_ratio")       # 새 행 같은 열(구성비)부터 왼쪽으로
        step(hwp, "TableLeftCell")
        set_cell_field(hwp, "trim_amt")
        step(hwp, "TableLeftCell")
        set_cell_field(hwp, "trim_basis")
        step(hwp, "TableLeftCell")
        set_cell_field(hwp, "trim_label")
        log(f"trim_label @ {addr(hwp)}")

        # ---- 8. 최종견적 ----
        find_text(hwp, "50,000,000")            # 캐럿이 표 하단 → 최종견적 행이 첫 매치
        set_cell_field(hwp, "final_amt")
        step(hwp, "TableRightCell")
        set_cell_field(hwp, "final_ratio")
        log(f"final @ {addr(hwp)}")

        hwp.PutFieldText("trim_label", "절    삭")
        hwp.PutFieldText("trim_basis", "총계+부가세-최종견적")

        # ---- 9. 검증 ----
        fields = set()
        for opt in (0, 1, 2, 3):
            try:
                fl = hwp.GetFieldList(0, opt)
                names = [f.split("{{")[0] for f in fl.split("\x02") if f]
                log(f"GetFieldList(opt={opt}): {len(names)}개")
                fields |= set(names)
            except Exception as e:
                log(f"GetFieldList(opt={opt}) err: {e}")
        log(f"최종 필드 수(전체 옵션 합집합): {len(fields)}")

        expected = {"recv", "quote_no", "ref_name", "ref_tel", "quote_date",
                    "svc_name", "svc_period", "amount_kor",
                    "labor_sum_amt", "labor_sum_ratio",
                    "exp_sum_amt", "exp_sum_ratio",
                    "subtotal_amt", "subtotal_ratio",
                    "mgmt_basis", "mgmt_amt", "mgmt_ratio",
                    "profit_basis", "profit_amt", "profit_ratio",
                    "supply_amt", "supply_ratio",
                    "vat_basis", "vat_amt", "vat_ratio",
                    "trim_label", "trim_basis", "trim_amt", "trim_ratio",
                    "final_amt", "final_ratio"}
        for i in (1, 2, 3, 4):
            expected |= {f"labor{i}_{s}" for s in
                         ("grade", "cnt", "price", "months", "rate", "amt", "ratio")}
        for i in range(1, 9):
            expected |= {f"exp{i}_{s}" for s in
                         ("name", "detail", "qty", "price", "amt", "ratio")}
        missing = expected - fields
        extra = fields - expected
        log(f"누락 필드: {sorted(missing) if missing else '없음'}")
        log(f"추가 필드: {sorted(extra) if extra else '없음'}")

        hwp.save_as(work, format="HWP")
        log(f"템플릿 저장: {work} ({os.path.getsize(work):,} bytes)")
        hwp.save_as(CHECK_PDF, format="PDF")
        log(f"검증 PDF: {CHECK_PDF}")

        ok = not missing
        log(f"RESULT: {'OK' if ok else 'MISSING_FIELDS'}")
        if not ok:
            sys.exit(2)
    finally:
        try:
            hwp.quit()
        except Exception:
            pass

    assert os.path.getsize(ORIGINAL) == orig_size, "원본 크기 변동!"
    log("원본 무결성 확인")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        print("===LOG===")
        print("\n".join(LOG))
        sys.exit(1)
