# -*- coding: utf-8 -*-
"""시스템·연동 도메인 — 파일/폴더 다이얼로그, Drive, 문서 변환, 진단, 업데이트."""
import os
import traceback

from src.store import config_store as cs
from src.ai import llm as ai_llm
from src.drive import gdrive
from src.logutil import log as _log

from ._common import _err, _file_dialog


class SystemApi:
    def pick_doc_folder(self, doc_type="quote"):
        """폴더 선택 다이얼로그 → doc_types[t].folder 저장.
        quote는 last_folder도 동기 기록(하위호환)."""
        try:
            import webview
            res = self._window.create_file_dialog(_file_dialog(webview, "FOLDER"))
            if not res:
                return {"ok": False, "cancelled": True}
            folder = res[0] if isinstance(res, (list, tuple)) else str(res)
            self.cfg.setdefault("doc_types", {}).setdefault(doc_type, {})["folder"] = folder
            if doc_type == "quote":
                self.cfg["last_folder"] = folder
            cs.save_config(self.cfg)
            return {"ok": True, "folder": folder, "doc_type": doc_type}
        except Exception as e:
            return _err(e)

    def pick_folder(self):
        return self.pick_doc_folder("quote")
    def open_file(self, path):
        try:
            if not os.path.exists(path):
                return _err(f"파일이 없습니다: {path}")
            os.startfile(path)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def open_sibling_pdf(self, hwp_path):
        pdf = os.path.splitext(hwp_path)[0] + ".pdf"
        if os.path.exists(pdf):
            os.startfile(pdf)
            return {"ok": True}
        return _err("같은 이름의 PDF가 없습니다.")

    def open_external(self, url):
        """외부 URL을 기본 브라우저로 연다 (웹뷰 이탈 방지)."""
        try:
            if not str(url).startswith(("http://", "https://")):
                return _err("허용되지 않은 URL입니다.")
            import webbrowser
            webbrowser.open(url)
            return {"ok": True}
        except Exception as e:
            return _err(e)
    # ================= Google Drive =================
    def drive_status(self):
        st = gdrive.status()
        d = self.cfg.get("drive", {})
        st["ok"] = True
        st["folder"] = d.get("folder", "")
        st["auto"] = bool(d.get("auto", False))
        return st

    def drive_connect(self):
        return gdrive.connect()

    def drive_disconnect(self):
        return gdrive.disconnect()

    def set_drive_options(self, payload):
        try:
            d = self.cfg.setdefault("drive", {})
            if "folder" in payload:
                d["folder"] = str(payload.get("folder") or "").strip()
            if "auto" in payload:
                d["auto"] = bool(payload.get("auto"))
            cs.save_config(self.cfg)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def drive_upload(self, paths):
        folder = self.cfg.get("drive", {}).get("folder", "")
        return gdrive.upload_files(paths, folder)
    # ================= 문서 변환 (kordoc) =================
    def convert_status(self):
        """변환 엔진 상태 — 드롭존 활성/비활성 판단용.
        {ok, state, ready, node:{found,version,ok}, kordoc:{installed,version}}"""
        try:
            from src import convert
            return convert.status()
        except Exception as e:
            return _err(e)

    def _notify_convert_progress(self, info: dict):
        """설치/변환 진행 상황을 UI overlay로 통지 (실패해도 변환은 계속)."""
        try:
            import json as _json
            payload = _json.dumps(info, ensure_ascii=False)
            self._window.evaluate_js(
                f"window.__convertProgress && window.__convertProgress({payload})")
        except Exception:
            pass

    def convert_files(self, paths):
        """파일 경로 목록 → Markdown 일괄 변환 (kordoc 미설치 시 1회 자동 설치).

        반환 {ok: True, results: [...], installed_now: bool}
        — 개별 파일 실패는 results 안의 ok=False로 표현.
        전체 ok=False는 한 건도 진행 불가한 상황(Node 없음 등)에만."""
        try:
            from src import convert
            paths = [str(p) for p in (paths or []) if p]
            if not paths:
                return _err("변환할 파일이 없습니다.")

            installed_now = False
            # kordoc 필요 여부: 패스스루(txt/md) 외 형식이 하나라도 있으면 부트스트랩
            needs_kordoc = any(
                os.path.splitext(p)[1].lower() not in convert.PASSTHROUGH_EXTS
                for p in paths)
            if needs_kordoc:
                ens = convert.ensure_kordoc(progress_cb=self._notify_convert_progress)
                if not ens.get("ok"):
                    return _err(ens.get("error", "변환 도구 준비 실패"),
                                error_code=ens.get("error_code", ""))
                installed_now = bool(ens.get("installed_now"))

            results = convert.convert_many(
                paths, progress_cb=self._notify_convert_progress)
            self._notify_convert_progress({"phase": "done"})
            return {"ok": True, "results": results, "installed_now": installed_now}
        except Exception as e:
            _log(f"convert_files 예외: {e}\n{traceback.format_exc()}")
            return _err(e, traceback=traceback.format_exc())

    def pick_convert_files(self):
        """변환할 문서 파일 다중 선택 대화상자 (드래그앤드롭 폴백 겸 1급 경로)."""
        try:
            import webview
            result = self._window.create_file_dialog(
                _file_dialog(webview, "OPEN"),
                allow_multiple=True,
                file_types=(
                    "문서 파일 (*.hwp;*.hwpx;*.hml;*.pdf;*.docx;*.xlsx;*.xls;*.txt;*.md)",
                    "모든 파일 (*.*)",
                ),
            )
            if result:
                return {"ok": True, "paths": list(result)}
            return {"ok": False, "cancelled": True}
        except Exception as e:
            return _err(e)
    # ================= 진단 =================
    def diagnose(self):
        info = {"ok": True}
        try:
            import winreg
            try:
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"SOFTWARE\Classes\HWPFrame.HwpObject")
                info["hwp_com"] = True
            except OSError:
                info["hwp_com"] = False
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                   r"Software\HNC\HwpAutomation\Modules")
                info["security_module"] = bool(winreg.QueryValueEx(
                    k, "FilePathCheckerModule")[0])
            except OSError:
                info["security_module"] = False
        except Exception:
            info["hwp_com"] = None
        from src.hwp.hwp_writer import TEMPLATE_DEFAULT
        info["template"] = os.path.exists(TEMPLATE_DEFAULT)
        _prov = cs.get_provider(self.cfg)
        info["ai_provider"] = _prov
        info["ai_provider_label"] = ai_llm.PROVIDER_LABELS.get(_prov, _prov)
        info["ai_key"] = bool(cs.get_ai_key(self.cfg, _prov))
        info["folder"] = self._doc_folder("quote")
        info["folder_ok"] = os.path.isdir(info["folder"]) if info["folder"] else False
        try:
            info["drive_connected"] = gdrive.status().get("connected", False)
        except Exception:
            info["drive_connected"] = False
        try:
            from src import convert
            ni = convert.node_info()
            info["node"] = ni["version"] if ni["found"] else False
            info["node_bundled"] = ni.get("bundled", False)
            ki = convert.kordoc_installed()
            info["kordoc"] = ki["version"] if ki["installed"] else False
        except Exception:
            info["node"] = None
            info["kordoc"] = None
        return info

    def diagnose_hwp_session(self):
        """실제 한글 구동 테스트 (느림 — 버튼으로만)."""
        try:
            return self._get_worker().diagnose()
        except Exception as e:
            return _err(e)
    # ================= 버전 / 자동 업데이트 =================

    def get_app_version(self) -> dict:
        from src.version import __version__, GITHUB_REPO
        return {"ok": True, "version": __version__, "repo": GITHUB_REPO}

    def check_update(self) -> dict:
        try:
            from src.update import updater
            return updater.check_latest()
        except Exception as e:
            return _err(e)

    def start_update(self, asset_url: str = "", asset_size: int = 0) -> dict:
        try:
            from src.update import updater
            from src.paths import is_frozen
            if not is_frozen():
                return {"ok": False, "error": "개발 모드에서는 업데이트 적용이 지원되지 않습니다."}
            # 안전 가드: 정말로 새 버전인지 재확인 (동일/구버전 업데이트 방지)
            chk = updater.check_latest()
            if not chk.get("ok"):
                return {"ok": False, "error": chk.get("error", "업데이트 확인 실패")}
            if not chk.get("has_update"):
                return {"ok": False, "error": "이미 최신 버전입니다."}
            url = chk.get("asset_url") or asset_url
            if not url:
                return {"ok": False, "error": "다운로드할 파일을 찾을 수 없습니다."}
            updater.start(url, int(chk.get("asset_size") or asset_size or 0))
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def update_status(self) -> dict:
        try:
            from src.update import updater
            s = updater.status()
            return {"ok": True, **s}
        except Exception as e:
            return _err(e)

    def apply_update(self) -> dict:
        try:
            from src.update import updater
            import sys as _sys
            exe = _sys.executable
            install_dir = os.path.dirname(os.path.abspath(exe))
            app_exe_name = os.path.basename(exe)
            result = updater.apply(os.getpid(), install_dir, app_exe_name)
            if result.get("ok") and self._window:
                self._window.destroy()
            return result
        except Exception as e:
            return _err(e)
