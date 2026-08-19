# -*- coding: utf-8 -*-
"""Api 코어 — 수명주기(__init__/shutdown)·창 연결·드롭존·HWP 워커·작업 폴더."""
import traceback

from src.store import config_store as cs
from src.logutil import log as _log

from ._common import _DEPRECATED_MODELS


class ApiCore:
    def __init__(self):
        self._window = None
        self._worker = None
        self.cfg = cs.load_config()
        self._migrate_model()
        # 구버전 last_folder → doc_types.quote.folder 1회 이관
        try:
            if cs.migrate_doc_type_folders(self.cfg):
                cs.save_config(self.cfg)
        except Exception:
            pass
        # 회의록 preset 시딩·정규화 (적대리뷰 #4: _merge 비의존 전용 마이그레이션)
        try:
            if cs.migrate_minutes_presets(self.cfg):
                cs.save_config(self.cfg)
        except Exception:
            pass

    def _migrate_model(self):
        """구형/종료 모델이 저장돼 있으면 최신 별칭으로 교체 (AI 호출 404 예방).
        손상된 config(gemini가 dict 아님)에서도 기동이 막히지 않도록 방어."""
        g = self.cfg.get("gemini")
        if not isinstance(g, dict):
            return
        if g.get("model", "") in _DEPRECATED_MODELS:
            g["model"] = "gemini-flash-latest"
            try:
                cs.save_config(self.cfg)
            except Exception:
                pass

    def attach_window(self, window):
        self._window = window
        # 페이지 로드 후 드롭존에 네이티브 DnD 핸들러 등록 (실패해도 파일선택 버튼으로 동작)
        window.events.loaded += self._wire_dropzones

    # 드롭존 셀렉터 → JS 통지 시 zone 식별자
    _DROPZONES = (("#ai-dropzone", "ai"), ("#minutes-dropzone", "minutes"),
                  ("#receipt-dropzone", "receipt"))

    def _wire_dropzones(self):
        """pywebview DOM 이벤트로 drop을 받아야 파일 전체 경로(pywebviewFullPath)가
        주입된다 (edgechromium은 Python측 리스너가 있어야 경로를 축적함)."""
        import functools
        try:
            from webview.dom import DOMEventHandler
        except Exception as e:
            _log(f"드롭존 등록 불가(webview.dom 없음): {e}")
            return
        for selector, zone in self._DROPZONES:
            try:
                el = self._window.dom.get_element(selector)
                if el is None:
                    continue
                el.on("drop", DOMEventHandler(
                    functools.partial(self._on_drop, zone),
                    prevent_default=True, stop_propagation=True))
                _log(f"드롭존 핸들러 등록: {selector}")
            except Exception as e:
                _log(f"드롭존 등록 실패({selector}): {e}")

    def _on_drop(self, zone, event):
        """pywebview가 별도 스레드에서 호출. 경로 추출·통지만 하고 즉시 반환
        (변환은 JS가 call('convert_files')로 표준 경로 재요청)."""
        try:
            import json as _json
            files = (event.get("dataTransfer") or {}).get("files") or []
            paths, unmatched = [], []
            for f in files:
                p = f.get("pywebviewFullPath")
                if p:
                    paths.append(p)
                else:
                    unmatched.append(f.get("name", "?"))
            payload = _json.dumps(
                {"zone": zone, "paths": paths, "unmatched": unmatched},
                ensure_ascii=False)
            self._window.evaluate_js(
                f"window.onNativeFilesDropped && window.onNativeFilesDropped({payload})")
        except Exception as e:
            _log(f"드롭 처리 예외: {e}\n{traceback.format_exc()}")

    def _get_worker(self):
        if self._worker is None:
            from src.hwp.hwp_writer import HwpWorker
            self._worker = HwpWorker()
        return self._worker

    def shutdown(self):
        if self._worker:
            self._worker.shutdown()
    def _doc_folder(self, doc_type="quote"):
        """문서 유형별 작업 폴더. doc_types[t].folder가 비면 last_folder 폴백
        (minutes도 폴백 — 기존 회의록이 견적 폴더에 생성돼 온 연속성)."""
        f = (self.cfg.get("doc_types", {}).get(doc_type, {}) or {}).get("folder", "")
        return f or self.cfg.get("last_folder", "")
