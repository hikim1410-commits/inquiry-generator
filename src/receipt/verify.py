# -*- coding: utf-8 -*-
"""영수증 금액 정합성 검증 (PRD FR-05).

값을 임의로 고치지 않는다 — 어긋난 사실만 플래그로 남기고 판단은 사람에게 넘긴다.
정부 사업비 정산에서 금액 자동 보정은 오기입을 조용히 확정시키는 행위라 금지한다.

이 플래그는 두 가지로 쓰인다:
  1) 검토 화면에서 사용자에게 어디를 봐야 하는지 알려주는 강조 표시
  2) Windows OCR 결과의 품질 판정 — 어긋나면 LLM 비전으로 폴백 (extract.py)
"""

FLAG_SUM = "sum_mismatch"      # 품목 합계 ≠ 총액
FLAG_VAT = "vat_mismatch"      # 공급가액 + 부가세 ≠ 총액
FLAG_NO_TOTAL = "no_total"     # 총액을 못 읽음 — 정산 자체가 불가
FLAG_NO_ITEMS = "no_items"     # 총액은 있는데 품목이 없음 — 합계 대조가 불가능
FLAG_REFUND = "refund_suspected"   # 괄호·△·음수 표기 — 환불/취소 의심, 부호 확정 불가
FLAG_VAT_SEPARATE = "vat_separate"  # "부가세 별도" — 총액이 결제액이 아닐 수 있음


def _int(v) -> int:
    """산술용. None(미확인)은 0으로 읽되 원본 필드를 0으로 바꿔 쓰지 않는다.

    공급가·부가세가 None이면 호출부가 (supply or vat)에서 대조를 건너뛴다 —
    미확인은 면세/0원이 아니므로 그 스킵이 맞다.
    """
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def items_sum(items) -> int:
    """품목 합계.

    price는 **그 줄에 찍힌 금액**(수량이 이미 반영된 값)이라 qty를 다시 곱하지 않는다.
    곱하면 수량 2 이상인 품목이 하나만 있어도 정상 영수증이 전부 불일치로 잡혀,
    무료 주 경로를 버리고 유료 폴백으로 새는 오탐이 된다(2026-07-29 검수에서 확인).
    parse._parse_items 와 ai/receipt.RECEIPT_DIRECTIVE 가 같은 약속을 따른다.
    qty가 None(수량 미기재)이어도 곱하지 않으므로 예외가 나지 않는다.
    """
    total = 0
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        total += _int(it.get("price"))
    return total


def verify(data: dict) -> list:
    """검증 플래그 목록을 돌려준다. data는 6절 추출 스키마 형태."""
    # parse가 붙인 환불·부가세별도를 새 리스트로 이어받는다.
    # 복사하지 않으면 extract가 _flags를 덮어써서 폴백 근거가 사라진다.
    flags = [f for f in (data.get("_flags") or []) if f]
    total = _int(data.get("total_amount"))
    supply = _int(data.get("supply_amount"))
    vat = _int(data.get("vat"))
    items = data.get("items") or []

    if total <= 0:
        if FLAG_NO_TOTAL not in flags:
            flags.append(FLAG_NO_TOTAL)
        return flags        # 총액이 없으면 나머지 대조는 의미가 없다

    if not items:
        # 품목이 비면 합계 대조가 불가능하다 — 통과로 두면 OCR 누락이 확정 후보가 된다
        if FLAG_NO_ITEMS not in flags:
            flags.append(FLAG_NO_ITEMS)
    elif items_sum(items) != total:
        if FLAG_SUM not in flags:
            flags.append(FLAG_SUM)
    if (supply or vat) and supply + vat != total:
        if FLAG_VAT not in flags:
            flags.append(FLAG_VAT)
    return flags
