# -*- coding: utf-8 -*-
"""HWP 생성기 — 템플릿 사본에 필드 기입 + 미사용 행 삭제 + HWP/PDF 저장.

COM 규칙:
  - 모든 한글 COM 호출은 전용 워커 스레드(HwpWorker) 안에서만 수행 (STA 직렬화)
  - 숨김 실행, 세션 재사용, 작업당 watchdog
  - 원본/템플릿은 읽기 전용 취급: 출력 경로에 사본을 만들고 그 사본을 연다
"""
import glob
import os
import queue
import shutil
import stat
import subprocess
import threading
import time
import traceback

from src.paths import resource_path, data_path
from src.logutil import log

# exe 옆 templates/ 폴더가 있으면 우선 사용 (재빌드 없이 템플릿 교체 가능)
# 없으면 번들 기본본(_MEIPASS) 사용
def _resolve_template_default():
    ext = data_path("templates", "견적서_템플릿.hwp")
    return ext if os.path.exists(ext) else resource_path("templates", "견적서_템플릿.hwp")

TEMPLATE_DEFAULT = _resolve_template_default()

MAX_LABOR = 4
MAX_EXP = 8

# COM 자동화 서버 실행 실패 계열 오류 (CO_E_SERVER_EXEC_FAILURE 등) —
# 새 사용자 프로필에서 한글이 한 번도 초기화되지 않았을 때 발생.
_SERVER_EXEC_HRESULTS = (-2146959355, -2147221021, -2147221164, -2147467259)


def _hwp_exe_path():
    """레지스트리(LocalServer32) 또는 알려진 설치 경로에서 Hwp.exe 경로를 찾는다."""
    try:
        import winreg
    except Exception:
        winreg = None

    def _read(root, sub):
        try:
            with winreg.OpenKey(root, sub) as k:
                return winreg.QueryValueEx(k, None)[0]
        except OSError:
            return None

    if winreg is not None:
        clsid = None
        for progid in ("HWPFrame.HwpObject", "HWPFrame.HwpObject.1",
                       "HWPFrame.HwpObject.2"):
            clsid = _read(winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID")
            if clsid:
                break
        subs = []
        if clsid:
            # 64비트 파이썬 + 32비트 한글이면 LocalServer32가 WOW6432Node 아래에 있음
            subs = [rf"WOW6432Node\CLSID\{clsid}\LocalServer32",
                    rf"CLSID\{clsid}\LocalServer32"]
        for sub in subs:
            val = _read(winreg.HKEY_CLASSES_ROOT, sub)
            if not val:
                continue
            # 값 예: "C:\...\Hwp.exe" -Automation
            path = val.split('"')[1] if '"' in val else val.split(" -")[0].strip()
            if os.path.exists(path):
                return path

    # 폴백: 표준 설치 경로 패턴 탐색
    for pat in (r"C:\Program Files (x86)\HNC\*\HOffice*\Bin\Hwp.exe",
                r"C:\Program Files\HNC\*\HOffice*\Bin\Hwp.exe",
                r"C:\Program Files (x86)\Hnc\*\*\Bin\Hwp.exe",
                r"C:\Program Files\Hnc\*\*\Bin\Hwp.exe"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def _prelaunch_automation(wait: float = 14.0) -> bool:
    """한글 자동화 서버(Hwp.exe -Automation)를 직접 선기동하고 COM 등록을 기다린다.

    새 프로필 첫 실행 시 한글이 백그라운드에서 자기 자신을 띄우지 못해
    CO_E_SERVER_EXEC_FAILURE가 나는 문제를 우회한다(수동 1회 기동과 동일 효과)."""
    exe = _hwp_exe_path()
    if not exe:
        log("자가복구: Hwp.exe 경로를 찾지 못했습니다 (한글 미설치 가능).")
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([exe, "-Automation"], creationflags=flags)
    except Exception as e:
        log(f"자가복구: 자동화 서버 기동 실패: {e}")
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            import win32com.client
            win32com.client.Dispatch("HWPFrame.HwpObject")
            log("자가복구: 자동화 서버 준비 완료.")
            return True
        except Exception:
            continue
    log("자가복구: 자동화 서버 응답 대기 시간 초과 — 그래도 재시도합니다.")
    return True


def _clear_gen_py_cache():
    """손상되거나 파이썬 버전 불일치인 win32com gen_py 캐시를 제거한다."""
    try:
        import win32com
        gen = getattr(win32com, "__gen_path__", "")
        if not gen:
            gen = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp", "gen_py")
        if gen and os.path.isdir(gen):
            shutil.rmtree(gen, ignore_errors=True)
            log(f"자가복구: win32com gen_py 캐시 정리 ({gen}).")
    except Exception as e:
        log(f"자가복구: gen_py 캐시 정리 실패: {e}")


def _use_dynamic_dispatch():
    """pyhwpx가 쓰는 gencache.EnsureDispatch(조기바인딩/makepy)를 동적(지연바인딩)
    Dispatch로 교체한다.

    PyInstaller 동결 빌드에서 pywintypes313.dll이 두 경로(_internal 루트 +
    pywin32_system32)로 이중 로드되면, makepy 생성 클래스의 CLSID(PyIID)와
    pythoncom이 인식하는 PyIID가 서로 다른 인스턴스의 타입이 된다. 그 결과
    DispatchBaseClass.__init__의 QueryInterface(self.CLSID)가
    "Only strings and iids can be converted to a CLSID"(TypeError)로 죽는다
    (2026-07-07 실측, v1.5.0 3.13 빌드). 동적 Dispatch는 makepy 클래스/CLSID를
    아예 쓰지 않아 문제를 원천 회피한다. pyhwpx는 win32com.client.constants를
    사용하지 않으므로 지연바인딩 전환이 안전하다(실기 검증 완료).
    """
    import win32com.client.dynamic
    import win32com.client.gencache as _gc

    def _ensure_dynamic(prog_id, *args, **kwargs):
        return win32com.client.dynamic.Dispatch(prog_id)

    _gc.EnsureDispatch = _ensure_dynamic


def make_hwp():
    """한글 COM 인스턴스 생성. 첫 기동 실패 시 자가복구(서버 선기동/캐시 정리) 후 재시도."""
    _use_dynamic_dispatch()  # 동결 빌드 pywintypes 이중로드 → CLSID 변환오류 회피
    from pyhwpx import Hwp
    try:
        return Hwp(new=True, visible=False, register_module=True)
    except Exception as e:
        hres = getattr(e, "hresult", None) or (e.args[0] if getattr(e, "args", None) else None)
        log(f"HWP 1차 기동 실패(hresult={hres}) → 자가복구 시도: {e}")
        # 1단계: 자동화 서버 직접 선기동 후 재시도
        _prelaunch_automation()
        last = e
        for _ in range(3):
            try:
                return Hwp(new=True, visible=False, register_module=True)
            except Exception as e2:
                last = e2
                time.sleep(2.0)
        # 2단계: gen_py 캐시 손상 가능성 → 정리 후 마지막 재시도
        _clear_gen_py_cache()
        _prelaunch_automation()
        try:
            return Hwp(new=True, visible=False, register_module=True)
        except Exception as e3:
            last = e3
        log(f"HWP 자가복구 실패 — 한글 구동 불가: {last}")
        raise last


def _hwp_pids():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Hwp.exe", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10).stdout
        pids = set()
        for line in out.splitlines()[1:]:
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                pids.add(int(parts[1]))
        return pids
    except Exception:
        return set()


def _scan_fields_com(hwp, hwp_path: str) -> dict:
    """한글 COM으로 템플릿 필드 목록 + max_labor/max_exp 감지."""
    import re
    hwp.open(hwp_path, arg="forceopen:true")
    try:
        fields = set()
        for opt in (0, 1, 2, 3):
            try:
                fl = hwp.GetFieldList(0, opt)
                for f in fl.split("\x02"):
                    n = f.split("{{")[0].strip()
                    if n:
                        fields.add(n)
            except Exception:
                pass

        # 연번 최대값 감지
        max_labor = 0
        max_exp = 0
        for f in fields:
            m = re.match(r"labor(\d+)_", f)
            if m:
                max_labor = max(max_labor, int(m.group(1)))
            m = re.match(r"exp(\d+)_", f)
            if m:
                max_exp = max(max_exp, int(m.group(1)))

        # 표준 필드셋과 대조
        std = _standard_field_set(max(max_labor, MAX_LABOR), max(max_exp, MAX_EXP))
        unknown = sorted(fields - std)
        missing = sorted(std - fields)
        is_standard = not unknown and not missing
    finally:
        # 예외 시에도 문서를 닫아 스캔 대상 파일 잠금 잔존 방지
        try:
            hwp.Run("FileClose")
        except Exception:
            pass
    return {"fields": sorted(fields), "max_labor": max_labor or MAX_LABOR,
            "max_exp": max_exp or MAX_EXP, "is_standard": is_standard,
            "unknown": unknown, "missing": missing}


def _standard_field_set(max_labor=MAX_LABOR, max_exp=MAX_EXP) -> set:
    """표준 필드명 전체 집합.

    비연번 필드 목록의 단일 출처는 AI 매핑 카탈로그(STANDARD_CATALOG) —
    새 표준 필드 추가 시 카탈로그에만 넣으면 스캔 대조와 AI 매핑이 함께 갱신된다."""
    from src.ai.template_mapper import STANDARD_CATALOG
    s = set(STANDARD_CATALOG)
    for i in range(1, max_labor + 1):
        s |= {f"labor{i}_{x}" for x in
              ("grade", "cnt", "price", "months", "rate", "amt", "ratio")}
    for i in range(1, max_exp + 1):
        s |= {f"exp{i}_{x}" for x in
              ("name", "detail", "qty", "price", "amt", "ratio")}
    return s


def _load_fieldmap_for(template_path: str) -> dict:
    """템플릿 옆 .fieldmap.json 로드 (포맷 작성자: src/ai/template_mapper.py).
    없거나 손상이면 빈 dict → 표준 템플릿으로 동작."""
    import json
    path = os.path.splitext(template_path)[0] + ".fieldmap.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _clear_readonly(path: str):
    """파일의 읽기 전용 속성을 해제 (쓰기 가능하게).

    shutil.copy2가 템플릿의 읽기 전용 비트까지 복사하면 생성된 견적서가
    '읽기 전용'으로 열려 수동 편집이 막힌다. 복사 직후·저장 직후에 호출해
    항상 편집 가능한 산출물을 보장한다(없는 파일·권한 오류는 무시)."""
    try:
        if os.path.exists(path):
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _expand_expense_rows(hwp, start_row: int, end_row: int, tpl_name,
                         merge_label: bool = True):
    """경비 표의 마지막 행 아래로 행을 동적 추가하고 표준 슬롯 필드를 부여.

    템플릿 기본 경비 행수(보통 8)를 초과하는 견적을 위해, 마지막 경비 행
    바로 아래에 (end_row - start_row)개 행을 만들고 각 행에
    exp{i}_(name|detail|qty|price|amt|ratio) 셀필드를 부여한다.
    tools/make_template2.py의 행 추가 패턴(InsertLowerRow + LowerCell + RightCell)과 동일.

    새로 부여하는 필드명은 표준 슬롯명(exp{i}_*)이라 PutFieldText의
    표준→템플릿 번역과 자연히 일치한다(매핑에 없으면 그대로 사용).

    행 추가만 하면 새 행의 좌측(카테고리) 열에 '경비' 병합셀과 분리된
    빈 셀이 생긴다 → 마지막에 '경비' 라벨 셀을 새 행들까지 세로 병합해
    인쇄 시 '경비'가 전체 경비 행을 덮도록 한다(실측으로 확정한 시퀀스).
    """
    added = 0
    for i in range(start_row + 1, end_row + 1):
        # 직전 경비 행의 첫 열(name)로 이동 → 그 아래에 새 행 삽입
        if not hwp.MoveToField(tpl_name(f"exp{i-1}_name"), True, True, False):
            break
        hwp.Run("TableInsertLowerRow")
        hwp.Run("TableLowerCell")  # 새 행의 같은 열(첫 열)로 진입
        names = [f"exp{i}_name", f"exp{i}_detail", f"exp{i}_qty",
                 f"exp{i}_price", f"exp{i}_amt", f"exp{i}_ratio"]
        for j, nm in enumerate(names):
            if j > 0:
                hwp.Run("TableRightCell")
            hwp.SetCurFieldName(nm, option=1, direction="", memo="")
        added += 1

    # '경비' 카테고리 라벨 셀을 새로 추가된 행들까지 세로 병합
    # (exp_name 셀의 왼쪽 = '경비' 라벨 셀. TableCellBlock→Extend로 블록 잡고
    #  추가 행 수만큼 아래로 확장 후 TableMergeCell — exp12 PDF 렌더로 검증함)
    # merge_label=False(커스텀 fieldmap 템플릿)면 병합 생략 — "exp1_name 왼쪽 셀 =
    # 카테고리 라벨" 가정이 깨진 표에서 무관한 셀을 병합해 표를 훼손하지 않도록
    if merge_label and added and hwp.MoveToField(tpl_name("exp1_name"), True, True, False):
        hwp.Run("TableLeftCell")        # '경비' 라벨 셀로 이동
        hwp.Run("TableCellBlock")       # 셀 블록 선택 시작
        hwp.Run("TableCellBlockExtend")  # 확장 선택 모드 ON
        for _ in range(added):          # 추가된 행 수만큼 아래로 블록 확장
            hwp.Run("TableLowerCell")
        hwp.Run("TableMergeCell")       # 셀 합치기
        hwp.Run("Cancel")               # 선택 해제


def _fill_document(hwp, plan: dict, out_hwp: str, out_pdf: str = None,
                   template: str = TEMPLATE_DEFAULT,
                   fieldmap: dict = None) -> dict:
    """열린 한글 인스턴스로 1건 생성. plan: RenderPlan을 dict화한 것.

    fieldmap: template_mapper가 저장한 .fieldmap.json 전체 dict (없으면 표준 템플릿).
      - field_map: {템플릿 필드명 → 표준 슬롯명}. 여기서 역방향(표준→템플릿)으로
        뒤집어 필드 기입·행 삭제 시 템플릿 실제 필드명을 쓴다.
      - max_labor/max_exp: 템플릿의 행 수 (기본 4/8과 다를 수 있음).
    """
    report = {"hwp": None, "pdf": None, "pdf_error": "", "deleted_rows": []}

    fieldmap = fieldmap or {}
    max_labor_tpl = int(fieldmap.get("max_labor") or MAX_LABOR)
    max_exp_tpl = int(fieldmap.get("max_exp") or MAX_EXP)
    std_to_tpl = {std: tpl for tpl, std in (fieldmap.get("field_map") or {}).items()}

    def tpl_name(std_name):
        return std_to_tpl.get(std_name, std_name)

    os.makedirs(os.path.dirname(os.path.abspath(out_hwp)), exist_ok=True)
    # 저장 포맷은 출력 확장자로 결정 (.hwpx → HWPX 강제)
    out_fmt = "HWPX" if out_hwp.lower().endswith(".hwpx") else "HWP"
    tpl_ext = os.path.splitext(template)[1].lower()
    # 템플릿과 출력 확장자가 다르면(.hwp 템플릿 → .hwpx 출력) 내용/확장자 불일치
    # 파일을 만들지 않도록 임시 작업 사본을 템플릿 확장자로 만들고, SaveAs에서 변환한다
    work = out_hwp if out_hwp.lower().endswith(tpl_ext) else out_hwp + ".work" + tpl_ext
    # 기존 산출물이 읽기 전용이면 copy2가 PermissionError(WinError 5)를 낼 수 있으므로 먼저 해제
    _clear_readonly(work)
    shutil.copy2(template, work)
    # copy2가 템플릿의 읽기 전용 비트까지 복사 → 즉시 해제(편집 가능한 사본 보장)
    _clear_readonly(work)
    try:
        hwp.open(work, arg="forceopen:true")

        def delete_row_at_field(name):
            if hwp.MoveToField(tpl_name(name), True, True, False):
                hwp.Run("TableDeleteRow")
                report["deleted_rows"].append(name)

        labor_used = max(1, min(max_labor_tpl, int(plan["labor_used"])))
        exp_used = max(1, int(plan["exp_used"]))

        # 경비 항목이 템플릿 기본 행수보다 많으면 행을 동적 추가 (9개 이상 누락 방지)
        if exp_used > max_exp_tpl:
            _expand_expense_rows(hwp, max_exp_tpl, exp_used, tpl_name,
                                 merge_label=not std_to_tpl)
            max_exp_tpl = exp_used

        # 미사용 행 삭제 (아래쪽부터)
        for i in range(max_labor_tpl, labor_used, -1):
            delete_row_at_field(f"labor{i}_grade")
        for i in range(max_exp_tpl, exp_used, -1):
            delete_row_at_field(f"exp{i}_name")
        if not plan["show_trim"]:
            delete_row_at_field("trim_label")

        # 필드 기입 (표준 슬롯명 → 템플릿 필드명 번역)
        for name, value in plan["fields"].items():
            hwp.PutFieldText(tpl_name(name), value if value is not None else "")

        # 저장 (SaveAs가 HWP→HWPX 변환까지 수행)
        _clear_readonly(out_hwp)
        hwp.save_as(out_hwp, format=out_fmt)
        _clear_readonly(out_hwp)  # 한글이 읽기 전용 속성을 유지했을 경우 대비(편집 가능 보장)
        # 저장 실증 — save_as가 조용히 실패해도 "생성 완료"로 보고하지 않는다
        if not (os.path.exists(out_hwp) and os.path.getsize(out_hwp) > 0):
            raise RuntimeError(f"{out_fmt} 저장 실패: 산출물이 생성되지 않았습니다.")
        report["hwp"] = out_hwp
        if out_pdf:
            try:
                hwp.save_as(out_pdf, format="PDF")
                if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 0:
                    _clear_readonly(out_pdf)
                    report["pdf"] = out_pdf
                else:
                    report["pdf_error"] = "PDF 파일이 생성되지 않았습니다."
            except Exception as e:
                report["pdf_error"] = f"PDF 변환 실패: {e}"
    finally:
        # 열린 문서를 자동화(숨김) 한글 세션에서 닫아 파일 잠금을 해제한다.
        # 닫지 않으면 재사용 세션이 파일을 계속 잡고 있어, 사용자가 열 때
        # "다른 곳에서 사용 중" → '읽기 전용'으로 열린다 (OS 속성과 무관한 진짜 원인).
        # 예외 경로에서도 닫아야 임시 작업 사본 제거가 가능하다.
        try:
            hwp.Run("FileClose")
        except Exception:
            pass
        # 임시 작업 사본 제거 — 실패 경로에서도 .work.hwp가 작업 폴더에 남아
        # 스캐너가 유령 견적서 카드로 줍는 일이 없도록 finally에서 정리
        if work != out_hwp:
            try:
                os.remove(work)
            except OSError:
                pass
    return report


def generate_once(plan: dict, out_hwp: str, out_pdf: str = None,
                  template: str = None) -> dict:
    """단발 생성 (자체 한글 인스턴스 생성/종료). 테스트·CLI용."""
    template = template or _resolve_template_default()
    hwp = make_hwp()
    try:
        return _fill_document(hwp, plan, out_hwp, out_pdf, template,
                              fieldmap=_load_fieldmap_for(template))
    finally:
        try:
            hwp.quit()
        except Exception:
            pass


class HwpWorker:
    """한글 COM 전용 워커 스레드. submit()은 어느 스레드에서든 호출 가능."""

    def __init__(self, template: str = None):
        # import 시점 상수가 아니라 생성 시점에 재해석 — 사용자가 커스텀 템플릿을
        # data 폴더에 적용한 뒤 워커를 재생성하면 즉시 반영된다
        self.template = template or _resolve_template_default()
        self._jobs = queue.Queue()
        self._session_pids = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="hwp-worker")
        self._thread.start()

    # ---------- 외부 API ----------
    def generate(self, plan: dict, out_hwp: str, out_pdf: str = None,
                 timeout: int = 180) -> dict:
        return self._submit({"op": "generate", "plan": plan,
                             "out_hwp": out_hwp, "out_pdf": out_pdf}, timeout)

    def diagnose(self, timeout: int = 60) -> dict:
        return self._submit({"op": "diagnose"}, timeout)

    def scan_fields(self, hwp_path: str, timeout: int = 60) -> dict:
        """템플릿 HWP의 필드 목록·max_labor·max_exp 스캔 (COM STA 스레드에서 실행)."""
        return self._submit({"op": "scan_fields", "hwp_path": hwp_path}, timeout)

    def shutdown(self, timeout: int = 8):
        """quit 작업 완료까지 대기 — 데몬 스레드가 죽기 전에 hwp.quit() 보장."""
        try:
            rq = queue.Queue()
            self._jobs.put({"op": "quit", "_result": rq})
            rq.get(timeout=timeout)
        except queue.Empty:
            # 응답 지연 시 좀비 방지를 위해 세션 강제 종료
            self._kill_session()
        except Exception:
            pass

    # ---------- 내부 ----------
    def _submit(self, job: dict, timeout: int) -> dict:
        rq = queue.Queue()
        job["_result"] = rq
        self._jobs.put(job)
        try:
            return rq.get(timeout=timeout)
        except queue.Empty:
            # watchdog: 멈춘 한글 프로세스 강제 종료 → 워커는 예외로 탈출
            self._kill_session()
            return {"ok": False, "error": f"한글 작업이 {timeout}초 안에 끝나지 않아 중단했습니다. "
                                          f"한글 창이 열려 있다면 닫고 다시 시도하세요."}

    def _kill_session(self):
        with self._lock:
            pids = set(self._session_pids)
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            except Exception:
                pass

    def _loop(self):
        import pythoncom
        pythoncom.CoInitialize()
        hwp = None

        def ensure_hwp():
            nonlocal hwp
            if hwp is None:
                before = _hwp_pids()
                hwp = make_hwp()
                with self._lock:
                    self._session_pids = _hwp_pids() - before
            return hwp

        def drop_session():
            nonlocal hwp
            try:
                if hwp is not None:
                    hwp.quit()
            except Exception:
                pass
            hwp = None
            with self._lock:
                self._session_pids = set()

        while True:
            job = self._jobs.get()
            op = job.get("op")
            rq = job.get("_result")
            if op == "quit":
                drop_session()
                if rq:
                    rq.put({"ok": True})
                break
            try:
                if op == "generate":
                    h = ensure_hwp()
                    rep = _fill_document(h, job["plan"], job["out_hwp"],
                                         job.get("out_pdf"), self.template,
                                         fieldmap=_load_fieldmap_for(self.template))
                    rq.put({"ok": True, **rep})
                elif op == "diagnose":
                    info = {"template_exists": os.path.exists(self.template)}
                    try:
                        h = ensure_hwp()
                        info["com_ok"] = True
                        info["version"] = str(h.Version)
                    except Exception as e:
                        info["com_ok"] = False
                        info["error"] = str(e)
                    rq.put({"ok": info.get("com_ok", False), **info})
                elif op == "scan_fields":
                    h = ensure_hwp()
                    result = _scan_fields_com(h, job["hwp_path"])
                    rq.put({"ok": True, **result})
                else:
                    rq.put({"ok": False, "error": f"알 수 없는 작업: {op}"})
            except Exception as e:
                # 세션이 오염됐을 수 있으므로 재생성 대상으로 표시
                drop_session()
                tb = traceback.format_exc()
                log(f"HWP 작업 실패(op={op}): {e}\n{tb}")
                rq.put({"ok": False, "error": str(e), "traceback": tb})
