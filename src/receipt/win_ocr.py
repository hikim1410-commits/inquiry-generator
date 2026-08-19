# -*- coding: utf-8 -*-
"""Windows 내장 OCR(Windows.Media.Ocr) — 무료·오프라인 한국어 텍스트 인식.

주 경로로 쓰는 이유: 한국어 인식기가 Windows에 이미 들어 있어 배포 부피 증가가 0이고
(로컬 OCR 엔진 동봉 배제 결론과 충돌하지 않는다), API 키·네트워크 없이 동작한다.

한계(2026-07-29 실측): 엔진은 줄 단위 텍스트만 주고 표의 열 구조를 보존하지 않는다.
품목명 열과 금액 열이 별개 줄로 나와 품목↔가격 짝을 잃는다. 그래서 줄이 아니라
**단어 좌표**를 받아 세로 위치로 행을 다시 묶는다(group_rows). 짧은 한글 단어를
통째로 놓치는 경우도 있어(실측 "생수" 누락), 이 모듈은 "인식 결과"만 책임지고
품질 판정과 LLM 폴백 전환은 extract 계층이 맡는다.
"""
import json
import os
import subprocess
import tempfile

from src.logutil import log as _log
from src.paths import resource_path

_SCRIPT = resource_path("tools", "win_ocr.ps1")
_TIMEOUT_BASE = 30      # PowerShell 기동·WinRT 초기화 몫
_TIMEOUT_PER_IMAGE = 5  # 건당 여유 (실측 0.1~0.2초, 스캔본·대형 이미지 감안)
_ROW_TOL_RATIO = 0.6    # 글자 높이의 몇 배까지를 같은 행으로 볼지


class OcrUnavailable(RuntimeError):
    """한국어 OCR 엔진이 없거나 실행기를 띄우지 못한 경우."""


def _run(args: list, count: int = 0) -> dict:
    if not os.path.isfile(_SCRIPT):
        raise OcrUnavailable(f"OCR 실행 스크립트가 없습니다: {_SCRIPT}")
    # 배치 전체에 고정 상한을 걸면 대량 처리 시 전건이 한꺼번에 실패해
    # 그대로 유료 폴백으로 새므로, 건수에 비례해 늘린다.
    timeout = _TIMEOUT_BASE + _TIMEOUT_PER_IMAGE * max(count, 1)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", _SCRIPT] + args
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise OcrUnavailable("PowerShell을 찾을 수 없습니다.")
    except subprocess.TimeoutExpired:
        raise OcrUnavailable(f"OCR 실행이 {timeout}초를 넘겼습니다.")

    out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
    if not out:
        err = (p.stderr or b"").decode("utf-8", errors="replace").strip()
        raise OcrUnavailable(f"OCR 실행기가 응답하지 않았습니다. {err[:200]}")
    try:
        data = json.loads(out)
    except ValueError:
        raise OcrUnavailable(f"OCR 응답을 해석할 수 없습니다: {out[:200]}")
    if not data.get("ok"):
        raise OcrUnavailable(data.get("error") or "OCR 실행 실패")
    # lang은 로그용이 아니라 실패 조건. 영어 엔진이 ok=true를 내는 경로를 막는다.
    tag = str(data.get("lang") or "").strip().lower()
    if tag != "ko" and not tag.startswith("ko-"):
        raise OcrUnavailable(
            f"한국어 OCR 언어팩 미설치(lang={data.get('lang') or '?'}) — "
            "Windows 설정에서 한국어 언어팩을 설치해야 합니다."
        )
    return data


def group_rows(words: list, tol_ratio: float = _ROW_TOL_RATIO) -> list:
    """단어들을 세로 위치로 묶어 행 목록으로 되돌린다.

    세로 중심이 지금까지 모인 행의 평균 글자 높이 × tol_ratio 안에 들면 같은 행.
    각 행은 가로 위치 순으로 정렬해 '품목명 … 수량 금액' 순서를 복원한다.
    """
    if not words:
        return []
    ws = sorted(words, key=lambda w: w["y"] + w["h"] / 2)
    rows = [[ws[0]]]
    for w in ws[1:]:
        cur = rows[-1]
        ref_c = sum(x["y"] + x["h"] / 2 for x in cur) / len(cur)
        ref_h = sum(x["h"] for x in cur) / len(cur)
        if abs((w["y"] + w["h"] / 2) - ref_c) <= ref_h * tol_ratio:
            cur.append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x"]) for r in rows]


def available() -> bool:
    """한국어 OCR 엔진을 쓸 수 있는지. 설정 화면·폴백 판단용."""
    try:
        _run(["-CheckOnly"])
        return True
    except OcrUnavailable:
        return False


def ocr_images(paths: list) -> list:
    """이미지 여러 장을 한 번에 인식한다 (PowerShell 기동 비용을 배치로 상쇄).

    반환: [{path, ok, lines: [str], rows: [[word]], error?}] — 입력 순서 유지.
    경로는 절대경로로 정규화한다. 상대경로를 넘기면 WinRT가 조용히 빈 결과를 내
        오인식이 아니라 '인식 성공, 내용 없음'으로 보여 정산 데이터가 비게 된다.
    """
    if not paths:
        return []
    abs_paths = [os.path.abspath(p) for p in paths]
    fd, listfile = tempfile.mkstemp(prefix="navion_ocr_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(abs_paths))
        data = _run(["-PathsFile", listfile], count=len(abs_paths))
    finally:
        try:
            os.unlink(listfile)
        except OSError:
            pass

    by_path = {}
    for r in data.get("results") or []:
        rows = group_rows(r.get("words") or []) if r.get("ok") else []
        by_path[os.path.normcase(r.get("path") or "")] = {
            "path": r.get("path"),
            "ok": bool(r.get("ok")) and bool(rows),
            "lines": [" ".join(w["text"] for w in row) for row in rows],
            "rows": rows,
            "error": r.get("error") or ("" if rows else "인식된 글자가 없습니다."),
        }

    out = []
    for p in abs_paths:
        out.append(by_path.get(os.path.normcase(p)) or {
            "path": p, "ok": False, "lines": [], "rows": [],
            "error": "OCR 결과가 반환되지 않았습니다.",
        })
    ok_n = sum(1 for r in out if r["ok"])
    _log(f"Windows OCR 완료 {ok_n}/{len(out)}건 (lang={data.get('lang')})")
    return out
