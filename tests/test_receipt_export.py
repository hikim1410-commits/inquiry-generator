# -*- coding: utf-8 -*-
"""영수증 Excel 정산 세부내역 내보내기.

(1) None 필드는 빈 셀 (0으로 쓰지 않음)
(2) 합계 행은 숫자 셀만 더함 (빈 셀 무시, 실제 0은 포함)
(3) flag_texts 가 검증 열에 들어감 (없으면 '정상')
"""
from openpyxl import load_workbook

from src.receipt.export_xlsx import export_receipts

SHEET = "영수증정산"
COL_SUPPLY = 6
COL_VAT = 7
COL_TOTAL = 8
COL_ITEMS = 11
COL_FLAGS = 12


def _card(name="a.jpg", flag_texts=None, items=None, **data_kw):
    data = {
        "store_name": "내비온마트",
        "biz_no": "123-45-67890",
        "tx_datetime": "2026-06-11 14:32",
        "total_amount": 0,
        "supply_amount": None,
        "vat": None,
        "card_no_masked": "1234-****-****-5678",
        "approval_no": "30251234",
        "items": items if items is not None else [],
    }
    data.update(data_kw)
    return {"name": name, "flag_texts": flag_texts if flag_texts is not None else [],
            "data": data}


def _export(tmp_path, cards, filename="out.xlsx"):
    path = str(tmp_path / filename)
    res = export_receipts(cards, path)
    assert res["ok"], res.get("error")
    wb = load_workbook(path)
    assert SHEET in wb.sheetnames
    return res, wb[SHEET]


def test_none_amount_fields_stay_empty_cells(tmp_path):
    """supply/vat 가 None 이면 빈 셀. 실제 0원인 합계는 0으로 남긴다."""
    card = _card(
        total_amount=0,
        supply_amount=None,
        vat=None,
        items=[{"name": "커피", "qty": None, "price": 0}],
    )
    res, ws = _export(tmp_path, [card])
    assert res["count"] == 1
    assert ws.cell(2, COL_SUPPLY).value is None
    assert ws.cell(2, COL_VAT).value is None
    assert ws.cell(2, COL_TOTAL).value == 0
    # 수량 None → 'x수량' 표기 없음. 가격 0은 실제 0원이라 적힌다.
    assert ws.cell(2, COL_ITEMS).value == "커피 0"
    assert ws.cell(2, COL_SUPPLY).number_format == "#,##0"
    assert ws.merged_cells.ranges == set()


def test_total_row_sums_numeric_cells_only(tmp_path):
    """빈 셀(None)은 합에 넣지 않고, 숫자 0은 더한다."""
    cards = [
        _card("a.jpg", supply_amount=10000, vat=1000, total_amount=11000),
        _card("b.jpg", supply_amount=None, vat=None, total_amount=5000),
        _card("c.jpg", supply_amount=0, vat=0, total_amount=0),
    ]
    res, ws = _export(tmp_path, cards)
    assert res["count"] == 3
    # 데이터 행: None 은 빈 셀, 0 은 0
    assert ws.cell(2, COL_SUPPLY).value == 10000
    assert ws.cell(3, COL_SUPPLY).value is None
    assert ws.cell(4, COL_SUPPLY).value == 0
    total_row = 5
    assert ws.cell(total_row, 1).value == "합계"
    # 10000 + (빈 셀 무시) + 0 = 10000. None을 0으로 치면 10000이 아님을 확인.
    assert ws.cell(total_row, COL_SUPPLY).value == 10000
    assert ws.cell(total_row, COL_VAT).value == 1000
    assert ws.cell(total_row, COL_TOTAL).value == 16000  # 11000+5000+0


def test_flag_texts_in_verify_column(tmp_path):
    cards = [
        _card("flagged.jpg",
              flag_texts=["항목 합계가 총액과 다릅니다", "품목을 읽지 못했습니다"],
              total_amount=24000, supply_amount=21818, vat=2182,
              items=[{"name": "아메리카노", "qty": 2, "price": 9000}]),
        _card("clean.jpg", flag_texts=[], total_amount=1000,
              supply_amount=1000, vat=0),
    ]
    res, ws = _export(tmp_path, cards)
    assert res["count"] == 2
    assert ws.cell(2, COL_FLAGS).value == "항목 합계가 총액과 다릅니다, 품목을 읽지 못했습니다"
    assert ws.cell(3, COL_FLAGS).value == "정상"
    assert ws.cell(2, COL_ITEMS).value == "아메리카노 x2 9000"
    assert [ws.cell(1, c).value for c in range(1, 13)] == [
        "번호", "파일명", "상호", "사업자번호", "거래일시",
        "공급가액", "부가세", "합계", "카드번호", "승인번호", "품목", "검증",
    ]
    assert ws.freeze_panes == "A2"
    assert ws.cell(2, 9).value == "1234-****-****-5678"


def test_total_row_stays_empty_when_no_number(tmp_path):
    """전부 미확인인 열의 합계는 0이 아니라 빈 셀 — 없는 사실을 정산서에 만들지 않는다."""
    cards = [_card("a.jpg", total_amount=24000, supply_amount=None, vat=None)]
    res, ws = _export(tmp_path, cards, "empty_sum.xlsx")
    total_row = res["count"] + 2
    assert ws.cell(total_row, COL_SUPPLY).value is None
    assert ws.cell(total_row, COL_VAT).value is None
    assert ws.cell(total_row, COL_TOTAL).value == 24000


def test_total_row_keeps_real_zero(tmp_path):
    """실제 0원(면세)은 합계에 0으로 남는다 — 미확인과 구분."""
    cards = [_card("a.jpg", total_amount=1000, supply_amount=1000, vat=0)]
    res, ws = _export(tmp_path, cards, "zero_sum.xlsx")
    assert ws.cell(res["count"] + 2, COL_VAT).value == 0
