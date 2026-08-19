# -*- coding: utf-8 -*-
"""영수증 OCR — 파서·검증·폴백 게이트.

Windows OCR 실행은 환경(언어팩) 의존이라 여기서는 호출하지 않는다. 대신 실측으로
얻은 OCR 출력 줄을 고정 입력으로 삼아, 그 뒤 단계(파싱·검증·폴백 판정)를 검증한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ai.receipt import mask_card, _normalize_receipt, build_receipt_prompt
from src.receipt import extract, parse, verify

# 2026-07-29 실측: 3배 해상도 합성 영수증의 Windows OCR 출력 (행 재조립 후)
OCR_LINES_3X = [
    "(주)내비온마트",
    "사업자번호 123-45-67890",
    "서울시 강남구 테헤란로 123",
    "2026-06-11 1432",
    "아메리카노 2 9,000",
    "샌드위치 1 12,000",
    "3 3,000",                 # 품목명 "생수"를 OCR이 통째로 놓친 줄
    "공급가액 21,818",
    "부가세 2,182",
    "합계 24,000",
    "카드 1234- -5678",        # 마스킹 별표를 OCR이 흘린 줄
    "승인 번호 30251234",
]


# ====== 파싱 ======

def test_parse_extracts_core_fields():
    d = parse.parse_lines(OCR_LINES_3X)
    assert d["store_name"] == "(주)내비온마트"
    assert d["biz_no"] == "123-45-67890"
    assert d["tx_datetime"] == "2026-06-11 14:32"   # 콜론 소실을 복원
    assert d["total_amount"] == 24000
    assert d["supply_amount"] == 21818
    assert d["vat"] == 2182
    assert d["approval_no"] == "30251234"


def test_parse_reads_quantity_and_thousands_without_comma():
    d = parse.parse_lines(["합계 24 000", "아메리카노 2 9,000"])
    assert d["total_amount"] == 24000              # 쉼표 대신 공백도 천단위로
    assert d["items"][0]["qty"] == 2


def test_parse_never_invents_card_number_from_date():
    """카드 문맥이 있어도 날짜 줄이면 카드번호를 만들지 않는다.

    입력에 카드 키워드가 없으면 _is_meta_line 가드를 타지 않아 테스트가
    아무것도 검증하지 못한다(검수 지적) — 그래서 '카드승인'을 같은 줄에 둔다.
    """
    d = parse.parse_lines(["카드승인 2026-06-11 1432"])
    assert d["card_no_masked"] == ""


def test_parse_never_invents_card_number_from_merchant_no():
    """가맹점·단말기 번호가 카드번호로 둔갑하면 안 된다."""
    assert parse.parse_lines(["신용카드 가맹점번호 1234 5678"])["card_no_masked"] == ""
    assert parse.parse_lines(["카드 단말기 8012 3456"])["card_no_masked"] == ""


def test_parse_does_not_invent_time_from_next_line():
    """다음 줄의 카드번호 앞 4자리가 거래시각으로 둔갑하면 안 된다."""
    d = parse.parse_lines(["2026-06-11", "1234-****-****-5678"])
    assert d["tx_datetime"] == "2026-06-11"


def test_parse_does_not_inflate_price_from_quantity_space():
    """'커피 1 500'을 1,500원으로 만들면 금액이 조용히 날조된다."""
    d = parse.parse_lines(["커피 1 500", "생수 3 300"])
    prices = {it["name"]: (it["qty"], it["price"]) for it in d["items"]}
    assert prices["커피"] == (1, 500)
    assert prices["생수"] == (3, 300)


def test_parse_total_with_trailing_vat_in_same_line():
    d = parse.parse_lines(["합계 24,000 (부가세 2,182 포함)"])
    assert d["total_amount"] == 24000


def test_parse_vat_total_label_not_read_as_total():
    """'부가세합계'가 '합계'를 품고 있어 총액으로 오인되면 안 된다."""
    d = parse.parse_lines(["합계 24,000", "부가세합계 2,182"])
    assert d["total_amount"] == 24000
    assert d["vat"] == 2182


def test_parse_store_skips_header_lines():
    d = parse.parse_lines(["[고객용]", "신용카드매출전표", "(주)내비온마트",
                           "합계 24,000", "2026-06-11"])
    assert d["store_name"] == "(주)내비온마트"


def test_parse_masks_card_from_card_line():
    d = parse.parse_lines(OCR_LINES_3X)
    assert d["card_no_masked"] == "1234-****-****-5678"
    assert "****" in d["card_no_masked"]


def test_parse_skips_address_and_date_as_items():
    d = parse.parse_lines(OCR_LINES_3X)
    names = [it["name"] for it in d["items"]]
    assert "아메리카노" in names and "샌드위치" in names
    assert not any("테헤란로" in n for n in names)   # 주소 번지 → 품목 오인 금지
    assert not any(n.strip() == "06" for n in names)  # 날짜 조각 → 품목 오인 금지


def test_parse_drops_item_without_name():
    """품목명을 놓친 줄은 지어내지 않고 버린다."""
    d = parse.parse_lines(OCR_LINES_3X)
    assert all(it["price"] != 3000 for it in d["items"])


def test_parse_empty_input():
    d = parse.parse_lines([])
    assert d["total_amount"] == 0 and d["items"] == []


# ====== 검증 ======

def test_verify_flags_sum_mismatch():
    d = {"total_amount": 24000, "items": [{"name": "a", "qty": 1, "price": 9000}]}
    assert verify.FLAG_SUM in verify.verify(d)


def test_verify_flags_vat_mismatch():
    d = {"total_amount": 24000, "supply_amount": 20000, "vat": 2000, "items": []}
    assert verify.FLAG_VAT in verify.verify(d)


def test_verify_clean_receipt_has_no_flags():
    """price는 줄 금액이므로 qty를 다시 곱하지 않는다 — 9000+15000=24000."""
    d = {"total_amount": 24000, "supply_amount": 21818, "vat": 2182,
         "items": [{"name": "a", "qty": 2, "price": 9000},
                   {"name": "b", "qty": 1, "price": 15000}]}
    assert verify.verify(d) == []


def test_verify_does_not_multiply_quantity():
    """수량 2 이상 품목이 있는 정상 영수증을 불일치로 오탐하면 안 된다.

    오탐하면 무료 주 경로를 버리고 유료 LLM 폴백으로 새므로 설계가 무너진다.
    """
    d = {"total_amount": 24000,
         "items": [{"name": "아메리카노", "qty": 2, "price": 9000},
                   {"name": "샌드위치", "qty": 1, "price": 12000},
                   {"name": "생수", "qty": 3, "price": 3000}]}
    assert verify.items_sum(d["items"]) == 24000
    assert verify.verify(d) == []


def test_verify_no_total_short_circuits():
    assert verify.verify({"total_amount": 0, "items": []}) == [verify.FLAG_NO_TOTAL]


def test_verify_does_not_mutate_values():
    """검증은 값을 고치지 않는다 — 정산 오기입을 조용히 확정시키면 안 된다."""
    d = {"total_amount": 24000, "supply_amount": 20000, "vat": 2000, "items": []}
    verify.verify(d)
    assert d["supply_amount"] == 20000 and d["total_amount"] == 24000


# ====== 폴백 게이트 ======

def test_fallback_triggers_on_flags():
    assert extract.needs_fallback({"total_amount": 1}, [verify.FLAG_SUM])


def test_fallback_triggers_on_missing_required_fields():
    base = {"store_name": "가", "tx_datetime": "2026-06-11", "total_amount": 1000}
    assert not extract.needs_fallback(base, [])
    assert extract.needs_fallback(dict(base, total_amount=0), [])
    assert extract.needs_fallback(dict(base, store_name=""), [])
    assert extract.needs_fallback(dict(base, tx_datetime=""), [])


def test_real_ocr_output_triggers_fallback():
    """실측 OCR 결과는 품목 누락으로 합계가 어긋나므로 폴백 대상이어야 한다."""
    d = parse.parse_lines(OCR_LINES_3X)
    assert extract.needs_fallback(d, verify.verify(d))


def test_extract_one_without_key_keeps_ocr_result():
    """API 키가 없으면 폴백 대신 OCR 결과를 사유와 함께 넘긴다(작업 중단 금지)."""
    r = extract.extract_one("x.png", ocr_result={"ok": True, "lines": OCR_LINES_3X},
                            allow_fallback=False)
    assert r["ok"] and r["source"] == extract.SOURCE_OCR
    assert r["fallback_reason"]
    assert r["data"]["total_amount"] == 24000


def test_extract_one_reports_ocr_failure():
    r = extract.extract_one("x.png", ocr_result={"ok": False, "lines": [],
                                                 "error": "파일 없음"},
                            allow_fallback=False)
    assert not r["ok"] and r["error"]


def test_extract_one_failure_without_error_text_is_still_failure():
    """사유가 비어 있어도 OCR 실패는 실패다 — 조용한 성공을 만들지 않는다."""
    r = extract.extract_one("x.png", ocr_result={"ok": False, "lines": [], "error": ""},
                            allow_fallback=False)
    assert not r["ok"] and r["error"]


# ====== LLM 폴백 모듈 ======

def test_mask_card_blocks_full_number():
    assert mask_card("1234567812345678") == "1234-****-****-5678"
    assert mask_card("1234-5678-9012-3456") == "1234-****-****-3456"


def test_normalize_receipt_masks_and_coerces():
    d = _normalize_receipt({"store_name": " 가게 ", "total_amount": "24,000",
                            "card_no_masked": "1234567812345678",
                            "items": [{"name": "커피", "price": "9,000"},
                                      {"name": "", "price": 100}]})
    assert d["store_name"] == "가게"
    assert d["total_amount"] == 24000
    assert d["card_no_masked"] == "1234-****-****-5678"
    # 수량 미기재는 1로 날조하지 않는다 — 미확인(None)으로 남긴다 (2026-08-19 값 표현 계약)
    assert len(d["items"]) == 1 and d["items"][0]["qty"] is None


def test_normalize_receipt_handles_garbage():
    d = _normalize_receipt(None)
    assert d["total_amount"] == 0 and d["items"] == []


def test_prompt_does_not_format_ocr_text():
    """OCR 텍스트의 중괄호가 format 치환으로 새지 않아야 한다."""
    p = build_receipt_prompt("합계 {total} {0} {")
    assert "합계 {total} {0} {" in p


# ====== 번들 ======

def test_ocr_script_is_bundled():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isfile(os.path.join(base, "tools", "win_ocr.ps1"))
    spec = open(os.path.join(base, "navion_quote.spec"), encoding="utf-8").read()
    assert "tools/win_ocr.ps1" in spec, "win_ocr.ps1이 spec datas에 없음 — EXE에서 OCR 불가"


def test_ocr_script_has_utf8_bom():
    """PowerShell 5.1은 BOM 없는 .ps1을 ANSI로 읽어 한글 오류 메시지가 깨진다."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, "tools", "win_ocr.ps1"), "rb") as f:
        assert f.read(3) == b"\xef\xbb\xbf"
