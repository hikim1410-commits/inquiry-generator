# -*- coding: utf-8 -*-
"""kordoc 연동 — 문서 파일을 Markdown으로 변환 (Node.js CLI subprocess).

kordoc(https://github.com/chrisryugj/kordoc)은 HWP/HWPX/PDF/DOCX/XLSX를
Markdown으로 변환하는 Node.js 도구다. Python에 import할 수 없으므로
런타임에 exe 옆 kordoc-runtime/ 폴더에 npm으로 1회 설치 후 subprocess로 호출한다.

=== M0 스파이크에서 실측 확정한 CLI 명세 (kordoc@3.0.0, 2026-06-11) ===
  설치:  npm install --prefix <RUNTIME_DIR> kordoc@3 pdfjs-dist@4
         ※ pdfjs-dist는 optional peerDependency라 자동 설치되지 않지만
           없으면 PDF 변환이 "doc.destroy is not a function"으로 실패한다 — 반드시 동반 설치.
  실행:  node <RUNTIME_DIR>/node_modules/kordoc/dist/cli.js <파일> -o <출력.md> --silent
         (bin 엔트리는 package.json "bin"."kordoc" = ./dist/cli.js)
  성공:  exit 0, -o 경로에 UTF-8(BOM 없음) md 생성, 이미지는 출력 폴더 옆 images/에 추출
  실패:  exit 1, stderr에 한국어 메시지 (예: "지원하지 않는 파일 형식입니다.")
  경로:  한글·공백 포함 경로 정상 (인자 리스트 + shell=False)
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

from src.paths import data_path
from src.logutil import log as _log

# ---- 상수 ----
KORDOC_SPEC = "kordoc@3"          # 검증된 메이저(3) 내 최신본을 매번 받음
                                  # (CLI 명세가 v3 기준이라 메이저는 고정; 메이저를
                                  #  올리려면 CLI 호출 명세 재검증 후 변경할 것)
PDFJS_SPEC = "pdfjs-dist@4"       # kordoc PDF 변환 필수 peer dep
NODE_MIN_MAJOR = 18
INSTALL_TIMEOUT = 300             # npm install (초)
CONVERT_TIMEOUT = 120             # 파일당 변환 (초)

SUPPORTED_EXTS = {".hwp", ".hwpx", ".hml", ".pdf", ".docx", ".xlsx", ".xls"}
PASSTHROUGH_EXTS = {".txt", ".md", ".markdown"}

# 엔진 상태 (status()의 state)
STATE_READY = "ready"
STATE_NODE_MISSING = "node_missing"
STATE_NODE_TOO_OLD = "node_too_old"
STATE_KORDOC_MISSING = "kordoc_missing"

_install_lock = threading.Lock()

# Windows GUI 앱에서 콘솔창 깜빡임 방지
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _runtime_dir() -> str:
    """kordoc 설치 위치 (exe 옆 쓰기 가능 폴더). 테스트에서 monkeypatch."""
    return data_path("kordoc-runtime")


def _node_tools_dir() -> str:
    """동봉된 Node.js 도구(node.exe + npm) 폴더. 빌드 spec이 여기에만 넣는다.

    무거운 kordoc node_modules(수백 MB)는 더 이상 빌드에 넣지 않는다.
    사용자가 Node.js를 따로 설치하지 않아도 되도록 node.exe와 npm 도구만
    이 폴더에 동봉하고, kordoc 본체는 첫 변환 때 npm으로 받아 설치한다."""
    return os.path.join(_runtime_dir(), "_nodejs")


def _bundled_node() -> str:
    """배포 번들에 포함된 node.exe 경로. 없으면 빈 문자열 (개발 환경 폴백용).

    신규 레이아웃(_nodejs/node.exe) 우선, 구버전 호환으로 runtime 루트의
    node.exe도 인정한다."""
    for p in (os.path.join(_node_tools_dir(), "node.exe"),
              os.path.join(_runtime_dir(), "node.exe")):
        if os.path.isfile(p):
            return p
    return ""


def _bundled_npm_cli() -> str:
    """동봉된 npm의 npm-cli.js 경로 (bundled node로 직접 실행용). 없으면 빈 문자열.

    .cmd 셸 의존 없이 `node npm-cli.js install ...`로 호출하기 위함."""
    p = os.path.join(_node_tools_dir(), "node_modules", "npm", "bin", "npm-cli.js")
    return p if os.path.isfile(p) else ""


def _run(cmd, timeout, cwd=None):
    """subprocess 공통 옵션 — 한글 경로/출력 안전."""
    return subprocess.run(
        cmd, capture_output=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=cwd, shell=False, creationflags=_NO_WINDOW)


# ================= 환경 감지 =================

def node_info() -> dict:
    """{found, path, version, major, ok, bundled}
    번들 node.exe를 우선 사용하고, 없으면 시스템 PATH를 탐색한다."""
    bundled = _bundled_node()
    path = bundled or shutil.which("node")
    if not path:
        return {"found": False, "path": "", "version": "", "major": 0, "ok": False,
                "bundled": False}
    try:
        r = _run([path, "--version"], timeout=10)
        ver = (r.stdout or "").strip()          # 예: "v24.14.1"
    except Exception:
        ver = ""
    m = re.match(r"v(\d+)", ver)
    major = int(m.group(1)) if m else 0
    return {"found": True, "path": path, "version": ver,
            "major": major, "ok": major >= NODE_MIN_MAJOR, "bundled": bool(bundled)}


def npm_path():
    """npm 실행 파일 경로 (Windows에서는 npm.cmd). 없으면 None.
    동봉 npm.cmd(_nodejs/) 우선, 없으면 시스템 PATH의 npm을 찾는다."""
    bundled = os.path.join(_node_tools_dir(), "npm.cmd")
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("npm")


def _npm_base_cmd():
    """npm 호출 커맨드 프리픽스(리스트).

    동봉 node+npm이 있으면 [node.exe, npm-cli.js]로 .cmd 셸 의존 없이 직접
    실행하고(가장 견고), 없으면 [npm 경로]로 폴백한다. None이면 npm 없음."""
    node = _bundled_node()
    cli = _bundled_npm_cli()
    if node and cli:
        return [node, cli]
    npm = npm_path()
    return [npm] if npm else None


def _kordoc_pkg_dir() -> str:
    return os.path.join(_runtime_dir(), "node_modules", "kordoc")


def kordoc_installed() -> dict:
    """{installed, version} — subprocess 없이 package.json 존재로 판정.
    pdfjs-dist는 PDF 변환의 필수 peer dep이라 함께 있어야 설치 완료로 본다."""
    pkg_json = os.path.join(_kordoc_pkg_dir(), "package.json")
    pdfjs_dir = os.path.join(_runtime_dir(), "node_modules", "pdfjs-dist")
    if not os.path.isfile(pkg_json) or not os.path.isdir(pdfjs_dir):
        return {"installed": False, "version": ""}
    try:
        with open(pkg_json, encoding="utf-8") as f:
            ver = json.load(f).get("version", "")
    except Exception:
        return {"installed": False, "version": ""}
    return {"installed": True, "version": ver}


def status() -> dict:
    """드롭존 활성/비활성 판단용 종합 상태."""
    ni = node_info()
    if not ni["found"]:
        state = STATE_NODE_MISSING
    elif not ni["ok"]:
        state = STATE_NODE_TOO_OLD
    else:
        state = STATE_READY if kordoc_installed()["installed"] else STATE_KORDOC_MISSING
    return {"ok": True, "state": state, "ready": state == STATE_READY,
            "node": ni, "kordoc": kordoc_installed()}


# ================= 부트스트랩 =================

def ensure_kordoc(progress_cb=None) -> dict:
    """kordoc 미설치 시 1회 설치. 동시 호출은 Lock으로 직렬화."""
    with _install_lock:
        if kordoc_installed()["installed"]:
            return {"ok": True, "version": kordoc_installed()["version"],
                    "installed_now": False}
        ni = node_info()
        if not ni["found"]:
            return {"ok": False, "error": "Node.js가 설치되어 있지 않습니다.",
                    "error_code": STATE_NODE_MISSING}
        if not ni["ok"]:
            return {"ok": False,
                    "error": f"Node.js 버전이 낮습니다 (v{NODE_MIN_MAJOR} 이상 필요, 현재 {ni['version']}).",
                    "error_code": STATE_NODE_TOO_OLD}
        npm = npm_path()
        if not npm:
            return {"ok": False, "error": "npm을 찾을 수 없습니다 (Node.js 재설치 필요).",
                    "error_code": "install_failed"}

        if progress_cb:
            progress_cb({"phase": "install", "msg": "kordoc 설치 중..."})
        rt = _runtime_dir()
        os.makedirs(rt, exist_ok=True)
        cmd = _npm_base_cmd() + ["install", "--prefix", rt, KORDOC_SPEC,
               PDFJS_SPEC, "--no-audit", "--no-fund", "--loglevel=error"]
        _log(f"kordoc 설치 시작: {' '.join(cmd)}")
        t0 = time.time()
        try:
            r = _run(cmd, timeout=INSTALL_TIMEOUT, cwd=rt)
        except subprocess.TimeoutExpired:
            _log("kordoc 설치 타임아웃")
            return {"ok": False, "error": "변환 도구 설치 시간이 초과되었습니다.",
                    "error_code": "install_failed"}
        except Exception as e:
            _log(f"kordoc 설치 예외: {e}")
            return {"ok": False, "error": f"변환 도구 설치에 실패했습니다: {e}",
                    "error_code": "install_failed"}

        if r.returncode != 0 or not kordoc_installed()["installed"]:
            err = (r.stderr or r.stdout or "").strip()[-500:]
            _log(f"kordoc 설치 실패 (exit {r.returncode}): {err}")
            offline_marks = ("ENOTFOUND", "ETIMEDOUT", "EAI_AGAIN", "ECONNREFUSED",
                             "network", "offline")
            if any(mk in err for mk in offline_marks):
                return {"ok": False,
                        "error": "인터넷 연결을 확인하세요. 변환 도구 첫 설치에는 네트워크가 필요합니다.",
                        "error_code": "install_offline"}
            return {"ok": False, "error": f"변환 도구 설치에 실패했습니다: {err}",
                    "error_code": "install_failed"}

        ver = kordoc_installed()["version"]
        _log(f"kordoc {ver} 설치 완료 ({time.time() - t0:.0f}초)")
        return {"ok": True, "version": ver, "installed_now": True}


def _kordoc_cli() -> list:
    """kordoc CLI 실행 커맨드 [node경로, cli.js경로].
    bin 엔트리는 package.json에서 해석 (기본 dist/cli.js)."""
    ni = node_info()
    pkg_dir = _kordoc_pkg_dir()
    cli = os.path.join(pkg_dir, "dist", "cli.js")
    try:
        with open(os.path.join(pkg_dir, "package.json"), encoding="utf-8") as f:
            bin_entry = json.load(f).get("bin", {}).get("kordoc")
        if bin_entry:
            cli = os.path.normpath(os.path.join(pkg_dir, bin_entry))
    except Exception:
        pass
    return [ni["path"], cli]


# ================= 변환 =================

_RE_IMG_LINK = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_RE_MANY_BLANKS = re.compile(r"\n{3,}")


def _clean_markdown(md: str) -> str:
    """AI 입력용 정리 — 이미지 링크 제거(파일이 첨부되지 않으므로 무의미), 빈 줄 압축."""
    md = _RE_IMG_LINK.sub("", md)
    md = "\n".join(line.rstrip() for line in md.splitlines())
    md = _RE_MANY_BLANKS.sub("\n\n", md)
    return md.strip()


def _read_text_passthrough(path: str) -> str:
    """txt/md는 kordoc 없이 직접 읽기 (utf-8 → cp949 폴백).

    "utf-8-sig" 대신 BOM을 직접 벗겨 "utf-8"로 읽는다 — 동결 빌드에서 순수파이썬
    encodings.utf_8_sig 모듈의 지연 import가 실패하는 사례가 있었다(updater.py 참고).
    """
    raw = open(path, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _file_result(path: str, **kw) -> dict:
    base = {"ok": False, "name": os.path.basename(path), "path": path,
            "markdown": "", "chars": 0}
    base.update(kw)
    return base


def convert_file(path: str) -> dict:
    """단일 파일 → {ok, name, path, markdown, chars, error?, error_code?}
    전제: 호출 측에서 ensure_kordoc 완료 (PASSTHROUGH 제외)."""
    if not path or not os.path.isfile(path):
        return _file_result(path or "", error="파일을 찾을 수 없습니다.",
                            error_code="file_missing")
    ext = os.path.splitext(path)[1].lower()

    if ext in PASSTHROUGH_EXTS:
        try:
            text = _read_text_passthrough(path).strip()
        except Exception as e:
            return _file_result(path, error=f"파일을 읽지 못했습니다: {e}",
                                error_code="convert_failed")
        if not text:
            return _file_result(path, error="파일이 비어 있습니다.",
                                error_code="empty_output")
        return _file_result(path, ok=True, markdown=text, chars=len(text))

    if ext not in SUPPORTED_EXTS:
        return _file_result(
            path, error="지원하지 않는 형식입니다 (지원: HWP·HWPX·PDF·DOCX·XLSX·TXT·MD)",
            error_code="unsupported")

    tmp = tempfile.mkdtemp(prefix="kordoc-")
    out_md = os.path.join(tmp, "out.md")
    try:
        cmd = _kordoc_cli() + [path, "-o", out_md, "--silent"]
        t0 = time.time()
        try:
            r = _run(cmd, timeout=CONVERT_TIMEOUT)
        except subprocess.TimeoutExpired:
            _log(f"kordoc 변환 타임아웃 [{os.path.basename(path)}] ({CONVERT_TIMEOUT}초)")
            return _file_result(
                path, error="변환 시간이 초과되었습니다 (파일이 너무 크거나 손상됨).",
                error_code="timeout")
        if r.returncode != 0 or not os.path.isfile(out_md):
            err = (r.stderr or r.stdout or "").strip()
            # kordoc 출력에서 마지막 의미 있는 줄만 (장식 문자 제거)
            tail = [ln.strip(" →") for ln in err.splitlines() if ln.strip()][-1:] or ["원인 불명"]
            _log(f"kordoc 변환 실패 [{os.path.basename(path)}] exit={r.returncode}: {err[-300:]}")
            return _file_result(path, error=f"변환에 실패했습니다: {tail[0]}",
                                error_code="convert_failed")
        with open(out_md, encoding="utf-8") as f:
            md = _clean_markdown(f.read())
        if not md:
            _log(f"kordoc 변환 결과 0자 [{os.path.basename(path)}] — 스캔 문서 가능성")
            return _file_result(
                path, error="문서에서 텍스트를 추출하지 못했습니다 (스캔 이미지일 수 있음).",
                error_code="empty_output")
        _log(f"kordoc 변환 완료 [{os.path.basename(path)}] {len(md)}자 ({time.time() - t0:.1f}초)")
        return _file_result(path, ok=True, markdown=md, chars=len(md))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def convert_many(paths, progress_cb=None) -> list:
    """순차 변환. progress_cb({"phase":"convert","i","total","name"})로 진행 통지."""
    results = []
    total = len(paths)
    for i, p in enumerate(paths, start=1):
        if progress_cb:
            progress_cb({"phase": "convert", "i": i, "total": total,
                         "name": os.path.basename(p or "")})
        results.append(convert_file(p))
    return results
