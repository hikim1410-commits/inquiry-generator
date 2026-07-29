# -*- coding: utf-8 -*-
"""api 공용 헬퍼 — 페이로드 파싱·표시 모델·오류 응답 (분할 전 api.py 모듈 헬퍼)."""
from src.engine.calc import (LaborRow, ExpenseRow, fmt_won, fmt_pct,
                             fmt_num, fmt_rate, round_half_up,
                             parse_leading_num)
from src.engine.money_kor import amount_kor


def _err(msg, **kw):
    return {"ok": False, "error": str(msg), **kw}


def _file_dialog(webview_mod, kind):
    """pywebview 6.x: webview.FileDialog.{OPEN,SAVE,FOLDER} enum.
    requirements.txt는 >=5.0(느슨)이라 구버전 상수(OPEN_DIALOG 등)로 폴백."""
    fd = getattr(webview_mod, "FileDialog", None)
    return getattr(fd, kind) if fd else getattr(webview_mod, f"{kind}_DIALOG")


# AI 초안 기초 지침 최대 길이 (config 비대화·실수 붙여넣기 방지)
_AI_PROMPT_MAX = 8000


def _parse_labor(items):
    rows = []
    for it in items or []:
        rows.append(LaborRow(
            grade=str(it.get("grade", "")),
            unit_price=float(it.get("unit_price") or 0),
            count=float(it.get("count") or 0),
            rate=float(it.get("rate") or 0),
            months=float(it.get("months") or 0),
        ))
    return rows


def _num_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_expenses(items):
    rows = []
    for it in items or []:
        raw_details = it.get("details")
        if isinstance(raw_details, str):
            raw_details = raw_details.split("\n")
        details = [str(d).strip() for d in (raw_details or []) if str(d).strip()]
        qty = _num_or_none(it.get("qty"))
        # 폴백: qty 없이 수량 표기만 있으면 표기에서 숫자 추출 (구버전/AI 초안 호환)
        if qty is None:
            qty = parse_leading_num(it.get("qty_text"))
        rows.append(ExpenseRow(
            name=str(it.get("name", "")).strip(),
            details=details,
            qty_text=str(it.get("qty_text", "")).strip(),
            unit_price=_num_or_none(it.get("unit_price")),
            qty=qty,
            extra1=_num_or_none(it.get("extra1")),
            extra2=_num_or_none(it.get("extra2")),
        ))
    return rows


def _display(result):
    """QuoteResult → UI 표시 모델."""
    r = result
    def block(x):
        return {"won": fmt_won(x), "pct": fmt_pct(r.ratio(x)), "raw": x}
    labor = []
    for row in r.labor_rows:
        labor.append({
            "grade": row.grade,
            "cnt": f"{fmt_num(row.count)}명" if row.count else "-",
            "price": fmt_won(row.unit_price),
            "months": f"{fmt_num(row.months)}개월" if row.months else "-",
            "rate": fmt_rate(row.rate) if row.rate else "-",
            "amt": fmt_won(row.amount),
            "pct": fmt_pct(r.ratio(row.amount)),
            "active": row.count > 0,
        })
    expenses = []
    for e in r.expense_rows:
        expenses.append({
            "name": e.name, "details": e.details, "qty_text": e.qty_text or "-",
            "price": fmt_won(e.unit_price) if e.unit_price is not None else "-",
            "amt": fmt_won(e.amount),
            "pct": fmt_pct(r.ratio(e.amount)),
            "active": bool(e.name.strip()),
        })
    return {
        "labor": labor, "expenses": expenses,
        "labor_sum": block(r.labor_total),
        "exp_sum": block(r.expense_total),
        "subtotal": block(r.direct),
        "mgmt": block(r.mgmt),
        "profit": block(r.profit),
        "supply": block(r.supply),
        "vat": block(r.vat),
        "total": block(r.total),
        "trim": block(r.trim),
        "final": block(r.final),
        "final_won_int": round_half_up(r.final),
        "amount_kor": amount_kor(round_half_up(r.final)),
        "profit_on": r.profit_on,
    }


def _parse_quote(payload):
    labor = _parse_labor(payload.get("labor"))
    expenses = _parse_expenses(payload.get("expenses"))
    profit_on = bool(payload.get("options", {}).get("profit", True))
    trim = float(payload.get("trim") or 0)
    return labor, expenses, profit_on, trim


# 종료/구형 Gemini 모델 — 자동으로 최신 별칭으로 치환 (저장된 config 치유)
_DEPRECATED_MODELS = {
    "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-flash-8b",
    "gemini-1.5-flash-002", "gemini-pro", "gemini-1.0-pro",
}
