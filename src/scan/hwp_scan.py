# -*- coding: utf-8 -*-
"""기존 HWP/HWPX 견적서 스캐너 — 한글 실행 없이 메타데이터 추출.

.hwp: olefile로 PrvText 스트림(UTF-16LE, 최대 1023자)에서 수신처/용역명/견적금액/
견적일자를 정규식으로 추출. 부족하면 BodyText/Section0(zlib) 텍스트 폴백.
.hwpx(v1.7부터 기본 산출물): zipfile로 Preview/PrvText.txt + Contents/section0.xml
텍스트에 같은 정규식 적용.
"""
import os
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, asdict
from typing import Optional

import olefile

_RE_AMOUNT = re.compile(r"₩\s*([\d,]+)")
_RE_DATE = re.compile(r"견적일자\s*[:：]?\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_RE_QUOTE_NO = re.compile(r"견적번호\s*[:：]?\s*(제?\s*[\w\-]+호?)")
_RE_RECV = re.compile(r"<([^<>]{2,40})\s*귀하")
_RE_RECV_TXT = re.compile(r"^\s*(.{2,40}?)\s*귀하", re.M)
_RE_SVC = re.compile(r"<용\s*역\s*명><([^<>]+)>")
_RE_SVC_TXT = re.compile(r"용\s*역\s*명\s*\n(.+)")


@dataclass
class QuoteMeta:
    path: str
    filename: str
    service_name: str = ""
    recipient: str = ""
    amount: Optional[int] = None
    date: str = ""           # YYYY-MM-DD
    quote_no: str = ""
    source: str = "hwp"      # hwp | json
    editable: bool = False   # 같은 베이스명 .quote.json 존재 여부
    json_path: str = ""
    mtime: float = 0.0
    error: str = ""

    def to_dict(self):
        return asdict(self)


def _read_prvtext(ole) -> str:
    if not ole.exists("PrvText"):
        return ""
    data = ole.openstream("PrvText").read()
    return data.decode("utf-16-le", errors="replace")


def _read_bodytext(ole) -> str:
    """BodyText/Section0 레코드에서 텍스트 추출 (압축 해제)."""
    try:
        if not ole.exists("BodyText/Section0"):
            return ""
        raw = ole.openstream("BodyText/Section0").read()
        # FileHeader 압축 플래그 확인
        hdr = ole.openstream("FileHeader").read()
        compressed = bool(hdr[36] & 0x01) if len(hdr) > 36 else True
        data = zlib.decompress(raw, -15) if compressed else raw
    except Exception:
        return ""
    out = []
    i = 0
    n = len(data)
    while i + 4 <= n:
        h = struct.unpack("<I", data[i:i + 4])[0]
        tag = h & 0x3FF
        size = (h >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:
            if i + 4 > n:
                break
            size = struct.unpack("<I", data[i:i + 4])[0]
            i += 4
        payload = data[i:i + size]
        i += size
        if tag == 67:  # HWPTAG_PARA_TEXT
            chars = []
            j = 0
            while j + 1 < len(payload):
                ch = struct.unpack("<H", payload[j:j + 2])[0]
                if ch >= 32:
                    chars.append(chr(ch))
                    j += 2
                elif ch in (10, 13):
                    chars.append("\n")
                    j += 2
                elif ch in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12,
                            14, 15, 16, 17, 18, 19, 20, 21, 22, 23):
                    j += 16  # 확장(1~3,11,12,14~18,21~23)·inline(4~9,19,20) 컨트롤 모두 8 WCHAR
                else:
                    j += 2
            t = "".join(chars).strip()
            if t:
                out.append(t)
    return "\n".join(out)


def _extract_fields(meta: QuoteMeta, prv: str, body_fn):
    """PrvText 우선 → 부족 시 body_fn() 텍스트 폴백으로 정규식 추출.
    body_fn은 최초 1회만 호출(지연 평가)."""
    body_cache = []

    def body():
        if not body_cache:
            body_cache.append(body_fn() or "")
        return body_cache[0]

    # 용역명
    m = _RE_SVC.search(prv) or _RE_SVC_TXT.search(body())
    if m:
        meta.service_name = m.group(1).strip()

    # 수신처
    m = _RE_RECV.search(prv) or _RE_RECV_TXT.search(body())
    if m:
        recv = m.group(1).strip()
        meta.recipient = recv.split("><")[-1].strip()

    # 금액 (₩ 표기 첫 매치)
    m = _RE_AMOUNT.search(prv) or _RE_AMOUNT.search(body())
    if m:
        meta.amount = int(m.group(1).replace(",", ""))

    # 견적일자
    m = _RE_DATE.search(prv) or _RE_DATE.search(body())
    if m:
        y, mo, d = m.groups()
        meta.date = f"{y}-{int(mo):02d}-{int(d):02d}"

    # 견적번호
    m = _RE_QUOTE_NO.search(prv) or _RE_QUOTE_NO.search(body())
    if m:
        meta.quote_no = m.group(1).strip()


def _fallback_service_name(meta: QuoteMeta):
    if not meta.service_name:
        # 파일명 기반 best-effort: "내비온_견적서_XXX.hwp" → XXX
        stem = os.path.splitext(meta.filename)[0]
        parts = stem.split("_")
        meta.service_name = parts[-1] if len(parts) > 1 else stem


def parse_hwp(path: str) -> QuoteMeta:
    meta = QuoteMeta(path=path, filename=os.path.basename(path))
    try:
        meta.mtime = os.path.getmtime(path)
        ole = olefile.OleFileIO(path)
    except Exception as e:
        meta.error = f"파일 열기 실패: {e}"
        return meta
    try:
        _extract_fields(meta, _read_prvtext(ole), lambda: _read_bodytext(ole))
    except Exception as e:
        meta.error = f"파싱 오류: {e}"
    finally:
        ole.close()
    _fallback_service_name(meta)
    return meta


# HWPX 문단 네임스페이스 (Contents/section0.xml)
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _hwpx_prvtext(zf: zipfile.ZipFile) -> str:
    """Preview/PrvText.txt — 한글 저장본은 UTF-16LE(BOM), 자체 생성본은 UTF-8."""
    try:
        data = zf.read("Preview/PrvText.txt")
    except KeyError:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-16-le", errors="replace")


def _hwpx_bodytext(zf: zipfile.ZipFile) -> str:
    """section0.xml의 모든 <hp:t> 텍스트를 줄 단위로 이어붙인 폴백 텍스트."""
    try:
        with zf.open("Contents/section0.xml") as fp:
            root = ET.parse(fp).getroot()
    except (KeyError, ET.ParseError):
        return ""
    lines = []
    for t in root.iter(f"{_HP}t"):
        s = (t.text or "").strip()
        if s:
            lines.append(s)
    return "\n".join(lines)


def parse_hwpx_quote(path: str) -> QuoteMeta:
    """HWPX 견적서 메타 추출 — .hwp와 동일한 정규식을 zip 텍스트에 적용."""
    meta = QuoteMeta(path=path, filename=os.path.basename(path), source="hwpx")
    try:
        meta.mtime = os.path.getmtime(path)
        with zipfile.ZipFile(path) as zf:
            _extract_fields(meta, _hwpx_prvtext(zf), lambda: _hwpx_bodytext(zf))
    except Exception as e:
        meta.error = f"파일 열기 실패: {e}"
    _fallback_service_name(meta)
    return meta


def scan_folder(folder: str) -> list:
    """폴더 내 .hwp/.hwpx 전수 스캔 + .quote.json 연동.
    (.hwpx가 기본 산출물, .hwp는 레거시 호환)"""
    results = []
    if not os.path.isdir(folder):
        return results
    for name in sorted(os.listdir(folder)):
        low = name.lower()
        if not (low.endswith(".hwp") or low.endswith(".hwpx")):
            continue
        path = os.path.join(folder, name)
        meta = parse_hwpx_quote(path) if low.endswith(".hwpx") else parse_hwp(path)
        jpath = os.path.splitext(path)[0] + ".quote.json"
        if os.path.exists(jpath):
            meta.editable = True
            meta.json_path = jpath
        results.append(meta)
    return results
