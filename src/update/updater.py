# -*- coding: utf-8 -*-
"""GitHub Releases 기반 자동 업데이트 엔진.

흐름:
  1) check_latest()   — GitHub API로 최신 릴리스 확인
  2) start(url, size) — 백그라운드 스레드: 다운로드 → 압축해제 → phase=ready
  3) status()         — JS 폴링용 상태 스냅샷
  4) apply(pid, dir, exe) — updater 배치 작성·실행(detached), 호출측이 창 종료

개발 모드(is_frozen()==False)에서 apply()는 동작하지 않는다.
"""

import os
import sys
import tempfile
import threading
import zipfile

import requests

from src.version import GITHUB_REPO, __version__, is_newer

# ── 전역 상태 (스레드 세이프) ──────────────────────────────────────────────────
_lock = threading.Lock()
_state: dict = {"phase": "idle", "pct": 0, "msg": "", "error": ""}


def _set(**kw):
    with _lock:
        _state.update(kw)


def _reset():
    _set(phase="idle", pct=0, msg="", error="")


# ── 1) 최신 버전 확인 ─────────────────────────────────────────────────────────

def check_latest(timeout: int = 15) -> dict:
    """GitHub Releases latest 엔드포인트를 조회한다.

    반환 dict:
      ok, latest_tag, has_update, notes, asset_url, asset_name, asset_size
    """
    repo = GITHUB_REPO.strip()
    if not repo:
        return {"ok": False, "error": "GITHUB_REPO가 설정되지 않았습니다 (src/version.py 참조)."}

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        resp = requests.get(
            url,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"navion-inquiry-generator/{__version__}"},
            timeout=timeout,
        )
    except requests.Timeout:
        return {"ok": False, "error": f"응답 시간 초과({timeout}초). 네트워크를 확인하세요."}
    except requests.ConnectionError:
        return {"ok": False, "error": "네트워크에 연결할 수 없습니다."}
    except Exception as e:
        return {"ok": False, "error": f"네트워크 오류: {e}"}

    if resp.status_code == 404:
        return {"ok": False, "error": "GitHub 저장소 또는 릴리스를 찾을 수 없습니다."}
    if resp.status_code in (403, 429):
        return {"ok": False, "error": "GitHub API 요청 한도 초과. 잠시 후 다시 시도하세요."}
    if resp.status_code != 200:
        return {"ok": False, "error": f"GitHub API 오류 (HTTP {resp.status_code})."}

    data = resp.json()
    tag = data.get("tag_name", "")
    notes = data.get("body", "") or ""

    # .zip 에셋만 선택 (draft/prerelease는 latest 엔드포인트가 이미 제외)
    asset_url = asset_name = asset_size = None
    for asset in (data.get("assets") or []):
        if asset.get("name", "").lower().endswith(".zip"):
            asset_url = asset.get("browser_download_url", "")
            asset_name = asset.get("name", "")
            asset_size = asset.get("size", 0)
            break

    return {
        "ok": True,
        "latest_tag": tag,
        "has_update": bool(tag and is_newer(tag)),
        "notes": notes[:800],        # UI 표시용 — 너무 길면 자름
        "asset_url": asset_url or "",
        "asset_name": asset_name or "",
        "asset_size": asset_size or 0,
    }


# ── 2) 백그라운드 다운로드 ────────────────────────────────────────────────────

def start(asset_url: str, asset_size: int = 0) -> None:
    """다운로드·압축해제를 백그라운드 스레드에서 시작한다."""
    _set(phase="downloading", pct=0, msg="다운로드 준비 중…", error="")
    t = threading.Thread(target=_worker, args=(asset_url, asset_size), daemon=True)
    t.start()


def _work_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "navion_update")


def _worker(asset_url: str, asset_size: int):
    work = _work_dir()
    zip_path = os.path.join(work, "download.zip")
    staging = os.path.join(work, "staging")

    try:
        os.makedirs(work, exist_ok=True)

        # ── 다운로드 ──────────────────────────────────────────────────────────
        _set(phase="downloading", pct=0, msg="다운로드 시작…")
        resp = requests.get(asset_url, stream=True, timeout=120,
                            headers={"User-Agent": f"navion-inquiry-generator/{__version__}"})
        resp.raise_for_status()

        total = asset_size or int(resp.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024  # 1 MB

        with open(zip_path, "wb") as fp:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    fp.write(chunk)
                    downloaded += len(chunk)
                    pct = int(downloaded * 100 / total) if total else 0
                    mb = downloaded / 1024 / 1024
                    _set(pct=pct, msg=f"다운로드 중… {mb:.1f} MB")

        _set(phase="extracting", pct=0, msg="압축 해제 중…")

        # ── 압축 해제 ─────────────────────────────────────────────────────────
        if os.path.isdir(staging):
            import shutil
            shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            for i, member in enumerate(members):
                zf.extract(member, staging)
                _set(pct=int((i + 1) * 100 / len(members)))

        # ── 최상위 폴더 탐지 (한글 폴더명 하드코딩 회피) ─────────────────────
        top_dirs = [d for d in os.listdir(staging)
                    if os.path.isdir(os.path.join(staging, d))]
        if len(top_dirs) == 1:
            staging_app = os.path.join(staging, top_dirs[0])
        else:
            # 단일 폴더가 없으면 staging 자체를 앱 루트로 간주
            staging_app = staging

        with _lock:
            _state["staging_app"] = staging_app
        _set(phase="ready", pct=100, msg="준비 완료", error="")

    except Exception as e:
        _set(phase="error", error=str(e), msg="")


# ── 3) 상태 조회 ──────────────────────────────────────────────────────────────

def status() -> dict:
    with _lock:
        return dict(_state)


# ── 4) 업데이트 적용 (배치 작성 + detached 실행) ───────────────────────────────

def _ps_quote(s: str) -> str:
    """PowerShell 단일따옴표 문자열용 이스케이프 (' → '')."""
    return str(s).replace("'", "''")


def apply(pid: int, install_dir: str, app_exe_name: str) -> dict:
    """updater PowerShell 스크립트를 %TEMP%\\navion_update 에 쓰고 detached 실행.

    배치(chcp 65001) 대신 PowerShell을 쓰는 이유: 한글 경로를 유니코드로 안전하게
    다루고, Wait-Process + 강제 종료 폴백, 교체 로그를 남기기 위함.
    호출 측은 이 함수 반환 직후 창을 종료해야 한다(파일 잠금 해제).
    frozen 아닐 때는 오류를 반환한다.
    """
    from src.paths import is_frozen
    if not is_frozen():
        return {"ok": False, "error": "개발 모드에서는 업데이트 적용이 지원되지 않습니다."}

    with _lock:
        if _state.get("phase") != "ready":
            return {"ok": False, "error": "아직 다운로드가 완료되지 않았습니다."}
        staging_app = _state.get("staging_app", "")

    if not staging_app or not os.path.isdir(staging_app):
        return {"ok": False, "error": "압축 해제 폴더를 찾을 수 없습니다."}

    work = _work_dir()
    ps_path = os.path.join(work, "apply_update.ps1")
    log_path = os.path.join(work, "update-log.txt")
    app_exe = os.path.join(install_dir, app_exe_name)

    ps = f"""# 내비온 자동 업데이트 적용 (PowerShell, UTF-8 BOM)
$ErrorActionPreference = 'Continue'
$log = '{_ps_quote(log_path)}'
function Log($m) {{ "$([DateTime]::Now.ToString('HH:mm:ss')) $m" | Out-File -FilePath $log -Append -Encoding utf8 }}
$targetPid = {int(pid)}
$staging = '{_ps_quote(staging_app)}'
$install = '{_ps_quote(install_dir)}'
$exe     = '{_ps_quote(app_exe)}'
$work    = '{_ps_quote(work)}'
Log "updater 시작 pid=$targetPid (self=$PID)"
# 0) 동시 업데이터 상호 배제 — AV가 숨김 PowerShell 기동을 수 분 지연시키면 사용자가
#    재시도를 반복해 업데이터가 여러 개 쌓인다(2026-06-12 실측: 4개 동시, 12분 지연).
#    같은 스크립트를 돌리는 다른 PS 인스턴스를 종료해 단일 실행을 보장한다.
try {{
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object {{ $_.ProcessId -ne $PID -and $_.CommandLine -like '*apply_update.ps1*' }} |
        ForEach-Object {{ Log "동시 업데이터 종료 pid=$($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
}} catch {{ Log "동시 실행 정리 예외: $_" }}
# 0b) staging이 없으면 이미 다른 업데이터가 적용·정리를 끝낸 것 — 이중 재실행 금지.
if (-not (Test-Path -LiteralPath $staging)) {{ Log '적용할 staging 없음(이미 완료됨) - 종료'; exit }}
# 1) 대상 앱 종료 대기 (최대 30초) → 안 죽으면 강제 종료
$deadline = [DateTime]::Now.AddSeconds(30)
while (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {{
    if ([DateTime]::Now -gt $deadline) {{ Log '타임아웃 - 강제 종료'; Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1; break }}
    Start-Sleep -Milliseconds 500
}}
Log '대상 프로세스 종료 확인'
# 1b) install 폴더에서 실행 중인 모든 프로세스 종료 — 재실행된 앱 인스턴스(업데이터가
#     침묵하는 동안 사용자가 다시 연 앱)와 kordoc node.exe가 파일을 잠가 robocopy를
#     실패시킨다. 경로로 한정하므로 무관한 프로세스는 건드리지 않는다.
try {{
    $prefix = $install.TrimEnd('\\') + '\\'
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {{ $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) }} |
        ForEach-Object {{ Log "install 내 프로세스 종료 pid=$($_.ProcessId) ($($_.Name))"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
}} catch {{ Log "install 프로세스 정리 예외: $_" }}
Start-Sleep -Seconds 1
# 2) 파일 교체 (덮어쓰기, 삭제 없음 → kordoc-runtime·config·token 보존)
#    /IS /IT 제거: 동일 파일은 건너뛰어 잠금 충돌과 복사 시간을 줄인다
#    (변경된 파일은 크기·수정시각이 달라 robocopy 기본 동작으로 정상 복사됨).
#    잠금 등으로 실패하면(8+) 잠시 후 1회 재시도 — onedir 앱은 부분 복사 시
#    exe와 _internal/ DLL 불일치로 부팅 불능(Themida 보호 exe는 무결성 검사 실패)이 된다.
$rc = 0
for ($attempt = 1; $attempt -le 2; $attempt++) {{
    robocopy $staging $install /E /R:5 /W:2 | Out-Null
    $rc = $LASTEXITCODE
    Log "robocopy 시도 $attempt 종료코드 $rc"
    if ($rc -lt 8) {{ break }}          # 0~7 = 성공(비트 플래그), 8+ = 하나 이상 복사 실패
    Start-Sleep -Seconds 3
}}
# 3) 새 버전 재실행 — 교체 실패(8+)거나 exe 누락이면 손상된 앱 실행을 막는다
if ($rc -ge 8) {{
    Log "치명적: 파일 교체 실패(코드 $rc). 손상 방지를 위해 재실행을 건너뜁니다."
    try {{ (New-Object -ComObject WScript.Shell).Popup(
        "업데이트 중 파일 교체에 실패했습니다.`n다른 프로그램이 파일을 사용 중일 수 있습니다.`n프로그램을 다시 실행하거나 재설치해 주세요.`n`n로그: $log",
        0, "내비온 업데이트 실패", 0x10) | Out-Null }} catch {{ }}
}} elseif (-not (Test-Path -LiteralPath $exe)) {{
    Log "치명적: 실행 파일을 찾을 수 없습니다($exe). 재실행 중단."
}} else {{
    Start-Sleep -Milliseconds 800      # 파일 핸들 flush·AV 스캔이 끝날 여유
    Start-Process -FilePath $exe -WorkingDirectory $install
    Log '재실행 완료'
}}
# 4) 정리 (스크립트·로그는 다음 실행에서 덮임)
Start-Sleep -Seconds 2
Remove-Item -LiteralPath (Join-Path $work 'staging') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $work 'download.zip') -Force -ErrorAction SilentlyContinue
Log '정리 완료'
"""

    try:
        # PowerShell 5.1은 BOM 없는 .ps1을 ANSI로 읽어 한글 경로가 깨진다 → BOM 필요.
        # encoding="utf-8-sig"는 순수파이썬 encodings.utf_8_sig 모듈을 첫 사용 시 동적
        # import하는데, PyInstaller 동결 빌드에서 pywebview JS브릿지 스레드(비-메인)로
        # 이 코드가 처음 실행되면 "unknown encoding: utf-8-sig"로 실패하는 사례가
        # 실측됨(2026-07-02). 항상 쓸 수 있는 내장 utf-8 코덱으로 BOM 바이트를 직접
        # 붙여 동일한 바이트 결과를 우회 생성한다.
        with open(ps_path, "wb") as fp:
            fp.write(b"\xef\xbb\xbf")
            fp.write(ps.replace("\n", "\r\n").encode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"업데이트 스크립트 작성 실패: {e}"}

    try:
        import subprocess
        # 창 없는(windowed) 앱에서 자식을 띄울 때 주의점:
        #  - DETACHED_PROCESS 와 CREATE_NO_WINDOW 동시 지정은 상호배타라 자식이 즉사한다.
        #    → CREATE_NO_WINDOW 만 사용(콘솔 창 없음 + 부모 종료 후에도 생존).
        #  - 부모에 유효한 std 핸들이 없으므로 stdin/out/err 를 DEVNULL 로 명시해야
        #    PowerShell 이 정상 기동한다(누락 시 핸들 무효로 즉시 종료).
        CREATE_NO_WINDOW = 0x08000000
        # PATH 미해석 대비 powershell 절대경로
        ps_exe = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.isfile(ps_exe):
            ps_exe = "powershell"
        subprocess.Popen(
            [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", ps_path],
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as e:
        return {"ok": False, "error": f"업데이트 프로세스 실행 실패: {e}"}

    _set(phase="applying")
    return {"ok": True}
