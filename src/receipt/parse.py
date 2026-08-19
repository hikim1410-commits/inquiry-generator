# -*- coding: utf-8 -*-
"""OCR 텍스트 줄 → 영수증 필드 (Windows OCR 주 경로의 구조화 단계).

Windows OCR은 글자만 주고 의미를 주지 않는다. 여기서 한국 영수증의 관용 표기를
정규식으로 집어낸다. 인식기가 놓친 글자·붙여 쓴 숫자까지 복원하지는 못하므로,
이 파서의 목표는 '완벽한 추출'이 아니라 **어디까지 확신할 수 있는지 정직하게 표시**하는
것이다. 확신이 서지 않으면 값을 만들어 채우지 않고 비운다 — 비운 자리는 상위
extract 계층이 LLM 비전 폴백을 걸지 판단하는 근거가 된다.

지어내기 방지가 이 모듈의 최우선 불변식이다. 애매하면 틀린 값을 내놓는 것보다
빈칸으로 두는 편이 낫다(빈칸은 폴백을 부르지만, 틀린 값은 그대로 정산에 실린다).

items[].price 는 **그 줄에 찍힌 금액**(수량이 이미 반영된 금액)이다. 단가가 아니다.
verify.items_sum 이 이 약속에 의존하므로 바꾸려면 양쪽을 함께 고쳐야 한다.
"""
import re

from src.receipt.verify import FLAG_REFUND, FLAG_VAT_SEPARATE

# 쉼표 천단위는 언제나 병합해도 안전하다: "9,000" → "9000"
_COMMA_SEP = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# 공백 천단위("24 000")는 '수량 금액'("커피 1 500")과 구분되지 않는다.
# 그래서 수량이 올 수 없는 **금액 레이블 줄에서만** 병합한다.
_SPACE_SEP = re.compile(r"(?<=\d) (?=\d{3}(?!\d))")
_NUM = re.compile(r"(?<![\d\-])(\d+)(?![\d])")
# 괄호·△·▲·선행 하이픈에 묶인 금액은 환불/취소 표기일 수 있다.
# 부호를 추정해 음수로 뒤집지 않고, 양수로 확정하지도 않는다.
# (닫는 괄호는 OCR이 흘리는 경우가 있어 선택으로 둔다.)
_AMOUNT_TOKEN = re.compile(
    r"[\(（]\s*(\d+)\s*[\)）]?|[△▲]\s*(\d+)|(?<![\d])-\s*(\d+)|(\d+)"
)
# "별도"는 금액이 아니라 조건 표기다. _VAT_KEYS에 넣으면 금액으로 읽으려 한다.
_VAT_SEPARATE_CTX = re.compile(r"(?:부가세|부가가치세|VAT|세액)\s*별도", re.I)

_BIZ_NO = re.compile(r"(\d{3})\s*-\s*(\d{2})\s*-\s*(\d{5})")
_DATE = re.compile(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})")
# 시각: 14:32 / 14 32 / 1432 — OCR이 콜론을 자주 흘린다(실측)
_TIME = re.compile(r"(?<!\d)([01]\d|2[0-3])\s*[:시]?\s*([0-5]\d)(?!\d)")
_FOUR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_APPROVAL = re.compile(r"(?:승인|approval)\D{0,6}(\d{6,12})")

# 합계/총액이 정산 총액이다. 결제·받을·판매금액은 봉사료·포인트 차감 뒤 액수일 수 있다.
_TOTAL_SUM_KEYS = ("합계", "합 계", "총액", "총 액")
_TOTAL_PAY_KEYS = ("결제금액", "받을금액", "판매금액")
_TOTAL_KEYS = _TOTAL_SUM_KEYS + _TOTAL_PAY_KEYS
_SUPPLY_KEYS = ("공급가액", "공급가", "과세물품가액")
_VAT_KEYS = ("부가세", "부가가치세", "세액")
_CARD_KEYS = ("카드", "신용", "체크", "CARD", "card")
# 카드 문맥처럼 보이지만 카드번호가 아닌 번호가 찍히는 줄
_CARD_EXCLUDE = ("가맹점", "단말기", "승인", "사업자", "등록번호", "전화", "TEL", "Tel")
# 상호로 오인하기 쉬운 머리글
_HEADER_KEYS = ("영수증", "고객용", "매출전표", "거래명세", "간이과세", "현금영수증",
                "신용카드", "COPY", "재발행")

# 품목으로 오인하기 쉬운 줄 — 금액 레이블·매장 정보·결제 정보
_NOISE_KEYS = _TOTAL_KEYS + _SUPPLY_KEYS + _VAT_KEYS + _CARD_KEYS + _HEADER_KEYS + (
    "사업자", "등록번호", "전화", "TEL", "Tel", "대표", "주소", "승인", "매장",
    "잔액", "포인트", "면세", "과세", "거스름", "받은금액", "할인", "봉사료",
    "가맹점", "단말기", "거래일",
)
# 주소 줄 — "서울시 강남구 테헤란로 123"의 번지가 금액으로 잡히는 것을 막는다
_ADDR = re.compile(r"(특별시|광역시|[가-힣]{1,4}시\s|[가-힣]{1,4}(?:구|군|읍|면)\s"
                   r"|[가-힣]+(?:로|길)\s*\d|\d+번지|\d+층)")

MIN_ITEM_PRICE = 100      # 이보다 작으면 품목 금액이 아니라 번지·수량 잡음으로 본다


def _norm(s: str, merge_spaces: bool = False) -> str:
    s = _COMMA_SEP.sub("", s)
    return _SPACE_SEP.sub("", s) if merge_spaces else s


def _numbers(s: str, merge_spaces: bool = False) -> list:
    return [int(n) for n in _NUM.findall(_norm(s, merge_spaces))]


def _labeled_amount(fragment: str):
    """레이블 뒤 첫 유효 금액. 환불 표기는 ('refund', n), 아니면 ('plain', n).

    MIN_ITEM_PRICE 하한은 품목 줄 전용이다. 여기서 쓰면 `합계 50`이 미인식된다.
    """
    s = _norm(fragment, merge_spaces=True)
    for m in _AMOUNT_TOKEN.finditer(s):
        n = int(m.group(1) or m.group(2) or m.group(3) or m.group(4))
        if m.lastindex == 4:
            return ("plain", n)
        return ("refund", n)
    return None


def _refund_on(lines, keys, rival_keys=()):
    """해당 레이블 뒤 첫 금액이 괄호·△·음수 표기인가."""
    for line in lines:
        idx = _first_key_pos(line, keys)
        if idx < 0:
            continue
        rival = _first_key_pos(line, rival_keys)
        if 0 <= rival < idx:
            continue
        end = idx + max(len(k) for k in keys if k in line and line.find(k) == idx)
        token = _labeled_amount(line[end:])
        if token and token[0] == "refund":
            return True
    return False


def _is_meta_line(line: str) -> bool:
    """날짜·사업자번호·주소처럼 '금액이 아닌 숫자'가 있는 줄인가."""
    return bool(_DATE.search(line) or _BIZ_NO.search(line) or _ADDR.search(line))


def _first_key_pos(line: str, keys) -> int:
    pos = [line.find(k) for k in keys if k in line]
    return min(pos) if pos else -1


def _find_labeled(lines, keys, rival_keys=()):
    """레이블이 붙은 줄의 금액. 합계는 보통 하단이라 뒤에서부터 찾는다.

    - 레이블 **뒤**의 첫 숫자를 쓴다. "합계 24,000 (부가세 2,182 포함)"에서
      마지막 숫자를 집으면 총액 자리에 부가세가 들어간다.
    - 다른 범주 레이블이 **앞**에 있으면 그 줄은 건너뛴다. "부가세합계 2,182"는
      '합계'를 부분문자열로 품고 있어 그냥 두면 총액으로 오인된다.
    """
    for line in reversed(lines):
        idx = _first_key_pos(line, keys)
        if idx < 0:
            continue
        rival = _first_key_pos(line, rival_keys)
        if 0 <= rival < idx:
            continue
        end = idx + max(len(k) for k in keys if k in line and line.find(k) == idx)
        # 환불 표기 금액은 양수로 확정하지 않는다 — 이 줄은 건너뛰고 다른 레이블을 본다.
        token = _labeled_amount(line[end:])
        if token and token[0] == "plain":
            return token[1]
    return None


def _pick_total(lines, rivals):
    """합계/총액 계열을 결제금액 계열보다 우선한다.

    두 계열이 서로 다른 값이면 어느 쪽도 확정하지 않는다 — 결제금액이 합계를
    덮어 정산 총액이 바뀌는 것을 막는다. 비운 총액은 no_total 폴백을 탄다.
    """
    sum_amt = _find_labeled(lines, _TOTAL_SUM_KEYS, rivals)
    pay_amt = _find_labeled(lines, _TOTAL_PAY_KEYS, rivals)
    if sum_amt is not None and pay_amt is not None and sum_amt != pay_amt:
        return None
    if sum_amt is not None:
        return sum_amt
    return pay_amt


def _last_amount_token(line: str):
    """품목 줄의 마지막 금액 토큰. 괄호·△·선행 하이픈이면 refund."""
    last = None
    for m in _AMOUNT_TOKEN.finditer(_norm(line)):
        n = int(m.group(1) or m.group(2) or m.group(3) or m.group(4))
        last = ("refund" if m.lastindex != 4 else "plain", n)
    return last


def _parse_items(lines):
    """'품목명 [수량] 금액' 꼴의 줄만 품목으로 인정한다.

    공백 천단위 병합을 하지 않는다 — "커피 1 500"을 1,500원으로 만들면
    금액이 조용히 날조된다(수량 1 · 500원이 맞다).
    수량이 없으면 1로 채우지 않는다(미기재 ≠ 1개). 괄호·△ 금액 품목은
    양수로 확정하지 않고 버려 호출 측이 FLAG_REFUND 를 붙이게 한다.
    """
    items = []
    refund = False
    for line in lines:
        if any(k in line for k in _NOISE_KEYS) or _is_meta_line(line):
            continue
        nums = _numbers(line)
        if not nums or nums[-1] < MIN_ITEM_PRICE:
            continue
        name = _NUM.sub("", _norm(line)).strip(" -·:,")
        # 이름에 글자가 없으면 OCR이 품목명을 통째로 놓친 줄 — 지어내지 않고 버린다
        if len(name) < 2 or not re.search(r"[가-힣A-Za-z]", name):
            continue
        token = _last_amount_token(line)
        if token and token[0] == "refund":
            refund = True
            continue
        qty = nums[-2] if len(nums) >= 2 and 0 < nums[-2] <= 999 else None
        items.append({"name": name, "qty": qty, "price": nums[-1]})
    return items, refund


def _parse_card(lines):
    """카드 문맥이 있는 줄에서만 마스킹 번호를 만든다.

    문맥 조건이 없으면 날짜·가맹점번호·단말기번호 같은 4자리 숫자쌍이 카드번호로
    둔갑한다(실측 확인). 카드 키워드가 있어도 가맹점·단말기 줄이면 배제한다.
    """
    for line in lines:
        if not any(k in line for k in _CARD_KEYS):
            continue
        if _is_meta_line(line) or any(k in line for k in _CARD_EXCLUDE):
            continue
        groups = _FOUR.findall(line)
        if len(groups) >= 2:
            # 원본 전체번호는 어떤 경우에도 만들지 않는다 (FR-09)
            return f"{groups[0]}-****-****-{groups[-1]}"
    return ""


def _parse_store(lines):
    """상호. 머리글·금액·주소 줄은 제외한다."""
    for line in lines[:5]:
        if len(line) < 2 or _NUM.search(line) or _is_meta_line(line):
            continue
        if any(k in line for k in _NOISE_KEYS):
            continue
        return line
    return ""


def _parse_datetime(lines):
    """거래일시. 시각은 **날짜와 같은 줄에서만** 찾는다.

    줄을 넘어가며 찾으면 다음 줄의 카드번호 앞 4자리(1234)가 12:34로 둔갑한다(실측).
    """
    for line in lines:
        md = _DATE.search(line)
        if not md:
            continue
        dt = f"{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}"
        mt = _TIME.search(line[md.end():])
        if mt:
            dt += f" {mt.group(1)}:{mt.group(2)}"
        return dt
    return ""


def parse_lines(lines: list) -> dict:
    """OCR 줄 목록 → 추출 스키마 dict. 못 읽은 필드는 빈값으로 남긴다."""
    lines = [l.strip() for l in (lines or []) if l and l.strip()]
    text = "\n".join(lines)

    out = {
        "store_name": "", "biz_no": "", "tx_datetime": "",
        "supply_amount": None, "vat": None, "total_amount": 0,
        "approval_no": "", "card_no_masked": "", "items": [], "_flags": [],
    }
    if not lines:
        return out

    out["store_name"] = _parse_store(lines)

    m = _BIZ_NO.search(text)
    if m:
        out["biz_no"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    out["tx_datetime"] = _parse_datetime(lines)
    out["card_no_masked"] = _parse_card(lines)

    ma = _APPROVAL.search(text)
    if ma:
        out["approval_no"] = ma.group(1)

    if _VAT_SEPARATE_CTX.search(text):
        out["_flags"].append(FLAG_VAT_SEPARATE)

    rivals_total = _VAT_KEYS + _SUPPLY_KEYS
    refund_total = _refund_on(lines, _TOTAL_KEYS, rivals_total)
    items, item_refund = _parse_items(lines)
    refund_any = (
        refund_total
        or _refund_on(lines, _SUPPLY_KEYS, _VAT_KEYS)
        or _refund_on(lines, _VAT_KEYS, _SUPPLY_KEYS)
        or item_refund
    )
    if refund_any:
        out["_flags"].append(FLAG_REFUND)

    # 총액이 환불 표기면 양수로 확정하지 않고 비워 폴백을 부른다. 부호는 추정하지 않는다.
    out["total_amount"] = 0 if refund_total else (
        _pick_total(lines, rivals_total) or 0)
    # 공급가·부가세를 못 읽으면 0이 아니라 None — 0은 면세/0원으로 읽힌다.
    out["supply_amount"] = _find_labeled(lines, _SUPPLY_KEYS, _VAT_KEYS)
    out["vat"] = _find_labeled(lines, _VAT_KEYS, _SUPPLY_KEYS)
    out["items"] = items
    return out
