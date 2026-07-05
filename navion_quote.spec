# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 명세 — 내비온 견적서 생성기.

빌드:  py -3.12 -m PyInstaller navion_quote.spec --noconfirm
산출:  dist/내비온 견적서 생성기/내비온 견적서 생성기.exe  (onedir)

- ui/, templates/ 는 데이터로 번들(읽기 전용). 코드에서 src.paths.resource_path로 접근.
- config.json/token.json/app-log.txt 는 EXE 옆(쓰기 가능 위치)에 생성됨.
- pywebview(EdgeChromium)는 자체 PyInstaller 훅이 WebView2 DLL을 자동 수집.
"""
import sys as _sys_ver_check
if _sys_ver_check.version_info[:2] != (3, 12):
    raise SystemExit(
        "[spec] 빌드 인터프리터 버전 불일치: 현재 Python "
        f"{_sys_ver_check.version_info[0]}.{_sys_ver_check.version_info[1]}. "
        "'py -3.12 -m PyInstaller navion_quote.spec --noconfirm' 로 3.12에서 빌드하세요. "
        "(과거 3.13으로 빌드한 배포본이 사용자 PC와 드리프트해 장애 발생 — tasks/lessons.md 2026-06-11 참고)"
    )

from PyInstaller.utils.hooks import collect_all, collect_data_files

# ── EXE 버전 리소스 생성 (파일 속성 → 자세히 탭에 표기) ──────────────────────
# src/version.py 의 __version__ 을 단일 출처로 사용해 드리프트 방지.
import sys as _sys_pre, os as _os_pre
_sys_pre.path.insert(0, _os_pre.path.dirname(SPECPATH))
from src.version import __version__ as _APP_VER, version_tuple4 as _vt4
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

def _make_version_resource():
    vt = _vt4()
    ver_str = _APP_VER
    info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=vt, prodvers=vt,
            mask=0x3f, flags=0x0,
            OS=0x40004, fileType=0x1, subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                # 키 = 언어(0x0412 한국어) + 코드페이지(0x04b0=1200 Unicode).
                # Translation 의 [lang, codepage] 와 반드시 일치해야 문자열이 읽힌다.
                StringTable("041204b0", [
                    StringStruct("CompanyName",      "내비온"),
                    StringStruct("FileDescription",  "내비온 견적서·회의록 생성기"),
                    StringStruct("FileVersion",      ver_str),
                    StringStruct("InternalName",     "navion_inquiry_generator"),
                    StringStruct("ProductName",      "내비온 견적서 생성기"),
                    StringStruct("ProductVersion",   ver_str),
                ])
            ]),
            VarFileInfo([VarStruct("Translation", [0x0412, 0x04b0])]),
        ],
    )
    ver_file = _os_pre.path.join(SPECPATH, "build", "version_info.txt")
    _os_pre.makedirs(_os_pre.path.dirname(ver_file), exist_ok=True)
    with open(ver_file, "w", encoding="utf-8") as _fp:
        _fp.write(str(info))
    return ver_file

_VERSION_FILE = _make_version_resource()


# templates 폴더 전체가 아니라 실제 사용하는 템플릿 1개만 번들.
# (직인 포함 백업본 '견적서_템플릿_직인포함.hwp'가 배포물에 들어가지 않도록)
import os as _os_pre
# fieldmap.json: 빌드 전 tools/rescan_template.py 실행으로 생성 (없으면 경고)
_fieldmap = "templates/견적서_템플릿.fieldmap.json"
if not _os_pre.path.isfile(_fieldmap):
    print(f"[spec] 경고: {_fieldmap} 없음. "
          "빌드 전 'python tools/rescan_template.py'를 실행하세요.")

datas = [
    ("ui", "ui"),
    ("templates/견적서_템플릿.hwp", "templates"),
    ("templates/회의록_양식.hwpx", "templates"),
    *collect_data_files('certifi'),   # cacert.pem — HTTPS 요청용 TLS 인증서 번들
]
# fieldmap.json이 있으면 번들에 포함 (없어도 빌드는 계속)
if _os_pre.path.isfile(_fieldmap):
    datas.append((_fieldmap, "templates"))
binaries = []
hiddenimports = [
    # pywin32 (pyhwpx COM + DPAPI 키 암호화)
    "win32com", "win32com.client", "win32timezone", "win32crypt",
    "pythoncom", "pywintypes",
    # pythonnet (pywebview EdgeChromium 백엔드)
    "clr",
]

# 데이터/바이너리/서브모듈을 통째로 수집해야 안전한 패키지들
for pkg in ("webview", "clr_loader", "pythonnet", "pyhwpx",
            "googleapiclient", "google_auth_oauthlib",
            "google.auth", "google.oauth2", "google_auth_httplib2"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="내비온 견적서 생성기",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 앱: 콘솔 없음 (오류는 app-log.txt에 기록됨)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",   # 견적서 모티프 아이콘 (tools/make_icon.py로 재생성)
    version=_VERSION_FILE,    # 파일 속성 → 자세히 탭에 버전 표기
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="내비온 견적서 생성기",
)

# MOTW(다운로드 차단) 대응 — .exe.config는 EXE와 같은 최상위 폴더에 있어야
# .NET Framework가 인식한다. PyInstaller 6.x는 datas를 전부 _internal/ 아래로
# 넣으므로 datas로는 불가능 → COLLECT 후 직접 복사한다.
# (다운로드 ZIP 해제 시 Zone.Identifier가 붙은 Python.Runtime.dll 로드 거부 방지)
import shutil as _shutil
import os as _os
_dist_dir = _os.path.join(DISTPATH, "내비온 견적서 생성기")
_shutil.copy(
    _os.path.join(SPECPATH, "내비온 견적서 생성기.exe.config"),
    _os.path.join(_dist_dir, "내비온 견적서 생성기.exe.config"),
)

# kordoc 비내장(최적화) — 무거운 node_modules(수백 MB)는 빌드에 넣지 않는다.
# 첫 변환 때 ensure_kordoc()가 npm으로 검증된 메이저(kordoc@3) 내 최신본을 받아
# kordoc-runtime/node_modules 에 설치한다(사용자 PC, 1회). 빌드에는 사용자가
# Node.js를 따로 설치하지 않아도 되도록 node.exe + npm 도구만 _nodejs/에 동봉한다.
# 결과: 배포본이 수백 MB 줄어든다(예: 754MB node_modules 제외).
_kordoc_dst = _os.path.join(_dist_dir, "kordoc-runtime")
_nodejs_dst = _os.path.join(_kordoc_dst, "_nodejs")

# npm이 깐 파일/이전 빌드 잔재엔 읽기 전용 비트가 섞여 있어 기본 rmtree가
# WinError 5(액세스 거부)로 죽는다. 비트를 풀고 재시도하는 핸들러를 단다.
import stat as _stat
def _rm_readonly(_func, _path, _exc):
    try:
        _os.chmod(_path, _stat.S_IWRITE)
        _func(_path)
    except Exception:
        pass
def _rmtree_safe(_p):
    # py3.12+ 는 onexc, 이전은 onerror
    try:
        _shutil.rmtree(_p, onexc=lambda f, p, e: _rm_readonly(f, p, e))
    except TypeError:
        _shutil.rmtree(_p, onerror=lambda f, p, e: _rm_readonly(f, p, e))

# 이전 빌드의 kordoc-runtime 잔재 정리 후, node 도구만 새로 동봉
if _os.path.isdir(_kordoc_dst):
    _rmtree_safe(_kordoc_dst)
_os.makedirs(_nodejs_dst, exist_ok=True)

_node_src = _shutil.which("node")
if _node_src and _os.path.isfile(_node_src):
    _node_home = _os.path.dirname(_node_src)
    # node.exe
    _shutil.copy2(_node_src, _os.path.join(_nodejs_dst, "node.exe"))
    # npm 패키지(node_modules/npm) — npm-cli.js를 bundled node로 직접 실행하는 데 필요
    _npm_pkg_src = _os.path.join(_node_home, "node_modules", "npm")
    if _os.path.isdir(_npm_pkg_src):
        _shutil.copytree(_npm_pkg_src,
                         _os.path.join(_nodejs_dst, "node_modules", "npm"))
    # 셸 심(npm.cmd 등)도 함께 — npm.cmd는 같은 폴더의 node.exe·npm-cli.js를 찾는다
    for _shim in ("npm", "npm.cmd", "npm.ps1", "npx", "npx.cmd"):
        _s = _os.path.join(_node_home, _shim)
        if _os.path.isfile(_s):
            _shutil.copy2(_s, _os.path.join(_nodejs_dst, _shim))
    # 동봉 파일 읽기 전용 비트 해제 — 다음 빌드 rmtree·사용자 업데이트 robocopy 보호
    for _r, _ds, _fs in _os.walk(_kordoc_dst):
        for _n in _fs:
            try:
                _os.chmod(_os.path.join(_r, _n), _stat.S_IWRITE)
            except Exception:
                pass
    print(f"[spec] Node 도구 동봉 완료(node.exe+npm): {_node_home} -> {_nodejs_dst}")
else:
    print("[spec] 경고: 빌드 머신에 node.exe를 찾지 못했습니다. "
          "Node.js를 설치한 뒤 다시 빌드하세요.")
