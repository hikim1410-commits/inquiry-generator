# -*- coding: utf-8 -*-
"""견적 데이터 → HWP 템플릿 필드 값 매핑 (COM 무의존 순수 함수).

템플릿 필드 (templates/견적서_템플릿.hwp, 107개):
  누름틀: recv, quote_no, ref_name, ref_tel, quote_date
  셀필드: svc_name, svc_period, amount_kor,
          labor{1-4}_(grade|cnt|price|months|rate|amt|ratio),
          labor_sum_(amt|ratio), exp{1-8}_(name|detail|qty|price|amt|ratio),
          exp_sum_(amt|ratio), subtotal_(amt|ratio),
          mgmt_(basis|amt|ratio), profit_(basis|amt|ratio),
          supply_(amt|ratio), vat_(basis|amt|ratio),
          trim_(label|basis|amt|ratio), final_(amt|ratio)
"""
from dataclasses import dataclass, field

from src.engine.calc import (QuoteResult, fmt_won, fmt_pct, fmt_num, fmt_rate,
                             round_half_up)
from src.engine.money_kor import amount_kor

MAX_LABOR = 4
MAX_EXP = 8          # 템플릿 기본 경비 행수 (이보다 많으면 생성 시 행을 동적 추가)
EXP_HARD_LIMIT = 30  # 경비 행 동적 확장 안전 상한 (표 폭주·생성 지연 방지)


@dataclass
class RenderPlan:
    """HWP 생성 작업 명세 (COM 워커에 전달)."""
    fields: dict                 # 필드명 → 텍스트
    labor_used: int              # 사용 인건비 행 수 (1~4)
    exp_used: int                # 사용 경비 행 수 (1~8)
    show_trim: bool              # 절삭 행 유지 여부
    warnings: list = field(default_factory=list)


def _date_kor(iso_date: str) -> str:
    """'2026-06-10' → '2026년 6월 10일'"""
    try:
        y, m, d = iso_date.split("-")
        return f"{int(y)}년 {int(m)}월 {int(d)}일"
    except Exception:
        return iso_date


# 설정 공급자(company) 키 → 템플릿 셀필드명 (10개 항목 전부 설정값으로 동적 반영)
SUPPLIER_FIELD_MAP = {
    "name":     "sup_name",
    "reg_no":   "sup_reg_no",
    "ceo":      "sup_ceo",       # 형식: "{대표자명}   (인)"
    "address":  "sup_address",
    "biz_type": "sup_biz_type",
    "biz_item": "sup_biz_item",
    "manager":  "sup_manager",
    "tel":      "sup_tel",
    "email":    "sup_email",
    "fax":      "sup_fax",
}


def build_render_plan(doc: dict, result: QuoteResult, company: dict = None,
                      max_labor: int = MAX_LABOR) -> RenderPlan:
    """doc: 문서 정보 dict, result: 계산 결과, company: 설정 공급자 정보 → 필드 값 일체.

    doc 키: recipient, quote_no, ref_name, ref_tel, date(ISO),
            service_name, service_period
    company 키: SUPPLIER_FIELD_MAP의 10개 키 → sup_* 셀필드 (빈 값은 공백 1칸)
    max_labor: 템플릿 인건비 행 수 (커스텀 템플릿 fieldmap 값 주입 — 기본 4)
    """
    warnings = []
    max_labor = max(1, int(max_labor or MAX_LABOR))
    labor_rows = [r for r in result.labor_rows if r.count > 0]
    exp_rows = [e for e in result.expense_rows if e.name.strip()]

    if len(labor_rows) > max_labor:
        warnings.append(f"인건비 직급이 {max_labor}개를 초과해 앞 {max_labor}개만 출력합니다.")
        labor_rows = labor_rows[:max_labor]
    # 경비는 8개 초과 시 생성 단계에서 표 행을 동적 추가하므로 자르지 않는다.
    # 단, 비정상 입력으로 인한 표 폭주·생성 지연 방지를 위해 안전 상한만 둔다.
    if len(exp_rows) > EXP_HARD_LIMIT:
        warnings.append(f"경비 항목이 {EXP_HARD_LIMIT}개를 초과해 앞 {EXP_HARD_LIMIT}개만 출력합니다.")
        exp_rows = exp_rows[:EXP_HARD_LIMIT]
    if not labor_rows:
        warnings.append("인건비 행이 없습니다. 최소 1개 직급이 필요합니다.")

    f = {}
    # ---- 누름틀 (빈 값은 안내문 노출 방지 위해 공백 1칸) ----
    f["recv"] = doc.get("recipient") or " "
    f["quote_no"] = doc.get("quote_no") or " "
    f["ref_name"] = (" " + doc["ref_name"]) if doc.get("ref_name") else " "
    f["ref_tel"] = (" " + doc["ref_tel"]) if doc.get("ref_tel") else " "
    f["quote_date"] = _date_kor(doc.get("date", "")) or " "  # 빈 값 안내문 노출 방지

    # ---- 공급자(설정→문서 동적 반영): 담당자·전화·이메일·팩스 ----
    # 템플릿에 sup_* 셀필드가 없으면 PutFieldText는 무시되어 무해(하위호환).
    company = company or {}
    for ck, fld in SUPPLIER_FIELD_MAP.items():
        val = str(company.get(ck, "") or "").strip()
        if not val:
            f[fld] = " "
        elif ck == "ceo":
            f[fld] = f"{val}   (인)"   # 대표자명 + 실물 직인란 고정 문자
        else:
            f[fld] = val

    # ---- 기본 정보 ----
    f["svc_name"] = doc.get("service_name", "")
    f["svc_period"] = doc.get("service_period", "")
    final_won = round_half_up(result.final)
    f["amount_kor"] = amount_kor(final_won)

    # ---- 인건비 행 ----
    for i, r in enumerate(labor_rows, start=1):
        f[f"labor{i}_grade"] = r.grade
        f[f"labor{i}_cnt"] = f"{fmt_num(r.count)}명"
        f[f"labor{i}_price"] = fmt_won(r.unit_price)
        f[f"labor{i}_months"] = f"{fmt_num(r.months)}개월"
        f[f"labor{i}_rate"] = fmt_rate(r.rate)
        f[f"labor{i}_amt"] = fmt_won(r.amount)
        f[f"labor{i}_ratio"] = fmt_pct(result.ratio(r.amount))
    f["labor_sum_amt"] = fmt_won(result.labor_total)
    f["labor_sum_ratio"] = fmt_pct(result.ratio(result.labor_total))

    # ---- 경비 행 ----
    for i, e in enumerate(exp_rows, start=1):
        f[f"exp{i}_name"] = e.name
        f[f"exp{i}_detail"] = "\r\n".join(e.details) if e.details else " "
        f[f"exp{i}_qty"] = e.qty_text or (fmt_num(e.qty) if e.qty else "-")
        f[f"exp{i}_price"] = fmt_won(e.unit_price) if e.unit_price is not None else "-"
        f[f"exp{i}_amt"] = fmt_won(e.amount)
        f[f"exp{i}_ratio"] = fmt_pct(result.ratio(e.amount))
    f["exp_sum_amt"] = fmt_won(result.expense_total)
    f["exp_sum_ratio"] = fmt_pct(result.ratio(result.expense_total))

    # ---- 합계 블록 ----
    f["subtotal_amt"] = fmt_won(result.direct)
    f["subtotal_ratio"] = fmt_pct(result.ratio(result.direct))
    f["mgmt_basis"] = "인건비+경비의 5%"
    f["mgmt_amt"] = fmt_won(result.mgmt)
    f["mgmt_ratio"] = fmt_pct(result.ratio(result.mgmt))
    if result.profit_on:
        f["profit_basis"] = "인건비+경비+일반관리비의 10%"
        f["profit_amt"] = fmt_won(result.profit)
        f["profit_ratio"] = fmt_pct(result.ratio(result.profit))
    else:
        f["profit_basis"] = "이윤 미계상"
        f["profit_amt"] = "-"
        f["profit_ratio"] = "-"
    f["supply_amt"] = fmt_won(result.supply)
    f["supply_ratio"] = fmt_pct(result.ratio(result.supply))
    f["vat_basis"] = "공급가액의 10%"
    f["vat_amt"] = fmt_won(result.vat)
    f["vat_ratio"] = fmt_pct(result.ratio(result.vat))

    show_trim = round_half_up(result.trim) > 0
    if show_trim:
        f["trim_label"] = "만원미만 절삭"
        f["trim_basis"] = "만원 미만 절삭"
        f["trim_amt"] = f"-{fmt_won(result.trim)}"
        f["trim_ratio"] = f"-{fmt_pct(result.ratio(result.trim))}"

    f["final_amt"] = fmt_won(result.final)
    f["final_ratio"] = fmt_pct(result.ratio(result.final))

    return RenderPlan(fields=f, labor_used=max(1, len(labor_rows)),
                      exp_used=max(1, len(exp_rows)),
                      show_trim=show_trim, warnings=warnings)
