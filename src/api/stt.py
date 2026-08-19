# -*- coding: utf-8 -*-
"""STT 도메인 API — 전사 시작/진행/취소, 전사본 수정, 화자 이름, 사이드카 저장.

엔진(src.stt.*)은 메서드 안에서만 import 한다. Api 조립만으로 faster-whisper 를
끌어오면 미설치 PC 에서 부팅이 막힌다.

긴 전사는 백그라운드 스레드로 돌리고, UI 는 transcribe_status() 로 폴링한다.
실패는 파일 단위로 격리한다.
"""
import copy
import json
import os
import threading
import traceback
from datetime import datetime

from src.logutil import log as _log

from ._common import _err

# kordoc.AUDIO_EXTS 와 동일. 여기 복사하지 않고 호출 시점에 읽는다.
_PHASE_LABEL = {
    "idle": "대기",
    "convert": "변환",
    "transcribe": "전사",
    "diarize": "화자 분리",
    "install": "모델 설치",
    "done": "완료",
    "cancelled": "취소됨",
}
ERR_BUSY = "이미 전사가 진행 중입니다."
ERR_NO_FILES = "전사할 음성 파일이 없습니다."
ERR_NO_RESULT = "전사 결과가 없습니다."
ERR_NO_SEG = "세그먼트를 찾을 수 없습니다."
ERR_BAD_FOLDER = "저장할 폴더를 찾을 수 없습니다."
ERR_CANCEL = "사용자가 취소했습니다."
ERR_UNSUPPORTED = "지원하지 않는 형식입니다."
ERR_START = "음성 전사를 시작하지 못했습니다."
ERR_STATUS = "진행 상태를 확인하지 못했습니다."
ERR_CANCEL_FAIL = "전사를 취소하지 못했습니다."
ERR_READ = "전사본을 읽지 못했습니다."
ERR_EDIT = "세그먼트를 수정하지 못했습니다."
ERR_MAP = "화자 이름을 반영하지 못했습니다."
ERR_TO_MINUTES = "전사본을 회의록 입력으로 바꾸지 못했습니다."
ERR_SAVE = "전사본을 저장하지 못했습니다."
ERR_ENGINE = "엔진 상태를 확인하지 못했습니다."
ERR_INSTALL = "모델 설치에 실패했습니다."
ERR_THREAD = "음성 전사에 실패했습니다."
ERR_NO_AUDIO = "재생할 오디오가 없습니다."
ERR_AUDIO_URL = "오디오 주소를 만들지 못했습니다."
ERR_NOTE_META = "노트 정보를 저장하지 못했습니다."
ERR_NOTE_READ = "노트 정보를 읽지 못했습니다."
ERR_NOTE_BAD = "노트 정보가 올바르지 않습니다."


def _fail(msg, exc=None):
    """사용자에게는 한국어만. 예외 원문은 로그에만 남긴다."""
    if exc is not None:
        _log(f"STT {msg}: {exc}\n{traceback.format_exc()}")
    return _err(msg)


def _audio_exts():
    try:
        from src.convert.kordoc import AUDIO_EXTS
        return AUDIO_EXTS
    except Exception:
        return {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg",
                ".opus", ".mp4", ".mov"}


def _paths_from_payload(payload):
    if payload is None:
        return []
    if isinstance(payload, str):
        return [payload.strip()] if payload.strip() else []
    if isinstance(payload, (list, tuple)):
        return [str(p).strip() for p in payload if str(p).strip()]
    if not isinstance(payload, dict):
        return []
    raw = payload.get("paths")
    if raw is None:
        raw = payload.get("files")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [str(p).strip() for p in (raw or []) if str(p).strip()]


def _is_cancel_msg(error):
    return bool(error) and ("취소" in str(error))


def _seg_copy(seg):
    if not isinstance(seg, dict):
        return {"start": 0.0, "end": 0.0, "text": "", "speaker": ""}
    return {
        "start": float(seg.get("start") or 0.0),
        "end": float(seg.get("end") or 0.0),
        "text": "" if seg.get("text") is None else str(seg.get("text")),
        "speaker": "" if seg.get("speaker") is None else str(seg.get("speaker")),
    }


def _shift_segments(segments, offset_sec):
    off = float(offset_sec or 0.0)
    out = []
    for seg in segments or []:
        item = _seg_copy(seg)
        item["start"] = item["start"] + off
        item["end"] = item["end"] + off
        out.append(item)
    return out


def _safe_remove(path, protected):
    if not path or path in protected:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _keywords(raw):
    """사용자 키워드 목록. 빈 값은 버리고 순서는 유지한다."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    seen = set()
    for item in raw:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _build_note_meta(files, language, duration_sec):
    """전사 세션 메타에 노트 필드(title/memo/keywords/created_at)를 채운다."""
    names = [f.get("name") or "" for f in (files or []) if f.get("ok")]
    if not names:
        names = [f.get("name") or "" for f in (files or []) if f.get("name")]
    source = ", ".join(n for n in names if n)
    title = ""
    if source:
        title = os.path.splitext(os.path.basename(source.split(",")[0].strip()))[0]
    return {
        "language": language or "",
        "duration_sec": float(duration_sec or 0.0),
        "source": source,
        "title": title,
        "memo": "",
        "keywords": [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


class SttApi:
    """세션 메모리에 전사 상태·세그먼트를 둔다. 엔진은 호출만 한다."""

    def _stt_state(self):
        if getattr(self, "_stt_lock", None) is None:
            self._stt_lock = threading.Lock()
        if not hasattr(self, "_stt_cancel"):
            self._stt_cancel = threading.Event()
        if not hasattr(self, "_stt_thread"):
            self._stt_thread = None
        if not hasattr(self, "_stt_running"):
            self._stt_running = False
        if not hasattr(self, "_stt_phase"):
            self._stt_phase = "idle"
        if not hasattr(self, "_stt_pct"):
            self._stt_pct = 0.0
        if not hasattr(self, "_stt_done_sec"):
            self._stt_done_sec = 0.0
        if not hasattr(self, "_stt_total_sec"):
            self._stt_total_sec = 0.0
        if not hasattr(self, "_stt_current"):
            self._stt_current = ""
        if not hasattr(self, "_stt_file_index"):
            self._stt_file_index = 0
        if not hasattr(self, "_stt_file_total"):
            self._stt_file_total = 0
        if not hasattr(self, "_stt_error"):
            self._stt_error = ""
        if not hasattr(self, "_stt_segments"):
            self._stt_segments = []
        if not hasattr(self, "_stt_files"):
            self._stt_files = []
        if not hasattr(self, "_stt_names"):
            self._stt_names = {}
        if not hasattr(self, "_stt_meta"):
            self._stt_meta = {}
        if not hasattr(self, "_stt_opts"):
            self._stt_opts = {}
        if not hasattr(self, "_window"):
            self._window = None
        return self

    def _stt_snapshot(self):
        self._stt_state()
        from src.stt.serialize import format_ts
        with self._stt_lock:
            phase = self._stt_phase
            done_sec = float(self._stt_done_sec or 0.0)
            total_sec = float(self._stt_total_sec or 0.0)
            return {
                "ok": True,
                "running": bool(self._stt_running),
                "phase": phase,
                "label": _PHASE_LABEL.get(phase, phase),
                "pct": float(self._stt_pct or 0.0),
                "done_sec": done_sec,
                "total_sec": total_sec,
                "done_ts": format_ts(done_sec),
                "total_ts": format_ts(total_sec),
                "current": self._stt_current or "",
                "file_index": int(self._stt_file_index or 0),
                "file_total": int(self._stt_file_total or 0),
                "error": self._stt_error or "",
                "cancelled": phase == "cancelled" or _is_cancel_msg(self._stt_error),
            }

    def _stt_set(self, **kw):
        self._stt_state()
        with self._stt_lock:
            if "phase" in kw:
                self._stt_phase = kw["phase"]
            if "pct" in kw:
                self._stt_pct = float(kw["pct"] or 0.0)
            if "done_sec" in kw:
                self._stt_done_sec = float(kw["done_sec"] or 0.0)
            if "total_sec" in kw:
                self._stt_total_sec = float(kw["total_sec"] or 0.0)
            if "current" in kw:
                self._stt_current = kw["current"] or ""
            if "file_index" in kw:
                self._stt_file_index = int(kw["file_index"] or 0)
            if "file_total" in kw:
                self._stt_file_total = int(kw["file_total"] or 0)
            if "error" in kw:
                self._stt_error = kw["error"] or ""
            if "running" in kw:
                self._stt_running = bool(kw["running"])
        self._stt_notify()  # lock 밖에서 스냅샷 — 비재진입 Lock 교착 방지

    def _stt_notify(self):
        window = getattr(self, "_window", None)
        if window is None:
            return
        try:
            payload = json.dumps(self._stt_snapshot(), ensure_ascii=False)
            window.evaluate_js(
                f"window.onSttProgress && window.onSttProgress({payload})")
        except Exception:
            pass

    def _stt_cancelled(self):
        self._stt_state()
        return self._stt_cancel.is_set()

    def _stt_file_pct(self, within, index=None, total=None):
        """파일 i/n 안에서 within(0~1) 을 전체 퍼센트로."""
        n = max(int(self._stt_file_total if total is None else total) or 0, 1)
        idx = int(self._stt_file_index if index is None else index) or 1
        i = max(idx - 1, 0)
        within = min(max(float(within or 0.0), 0.0), 1.0)
        return round((i + within) / n * 100.0, 1)

    def _stt_copy_session(self):
        """세그먼트·매핑·파일 목록을 잠금 안에서 복사한다."""
        self._stt_state()
        with self._stt_lock:
            return (
                [_seg_copy(s) for s in self._stt_segments],
                dict(self._stt_names),
                copy.deepcopy(self._stt_files),
                copy.deepcopy(self._stt_meta),
            )

    def _stt_put_session(self, segments=None, files=None, meta=None, names=None):
        self._stt_state()
        with self._stt_lock:
            if segments is not None:
                self._stt_segments = segments
            if files is not None:
                self._stt_files = files
            if meta is not None:
                self._stt_meta = meta
            if names is not None:
                self._stt_names = names

    # ================= 전사 시작/진행/취소 =================
    def start_transcribe(self, payload=None):
        """음성 파일 목록을 받아 백그라운드 전사를 시작한다."""
        try:
            self._stt_state()
            paths = _paths_from_payload(payload)
            if not paths:
                return _err(ERR_NO_FILES)
            opts = {}
            if isinstance(payload, dict) and isinstance(payload.get("opts"), dict):
                opts = dict(payload.get("opts") or {})
            with self._stt_lock:
                if self._stt_running:
                    return _err(ERR_BUSY)
                self._stt_cancel.clear()
                self._stt_running = True
                self._stt_phase = "convert"
                self._stt_pct = 0.0
                self._stt_error = ""
                self._stt_segments = []
                self._stt_files = []
                self._stt_names = {}
                self._stt_meta = {}
                # 노트 요약도 세션 재료 — 새 전사가 시작되면 이전 회의 것을 버린다.
                # 세대 카운터는 진행 중인 요약 LLM 응답이 옛 세션 것임을 판별한다.
                self._note_summary = None
                self._note_summary_meta = {}
                self._stt_gen = getattr(self, "_stt_gen", 0) + 1
                self._stt_opts = opts
                self._stt_file_total = len(paths)
                self._stt_file_index = 0
                self._stt_current = os.path.basename(paths[0])
            t = threading.Thread(
                target=self._stt_run, args=(list(paths),),
                name="stt-transcribe", daemon=True)
            self._stt_thread = t
            t.start()
            return {"ok": True}
        except Exception as e:
            self._stt_set(running=False, phase="idle", error=ERR_START)
            return _fail(ERR_START, e)

    def transcribe_status(self):
        try:
            return self._stt_snapshot()
        except Exception as e:
            return _fail(ERR_STATUS, e)

    def cancel_transcribe(self):
        try:
            self._stt_state()
            self._stt_cancel.set()
            if not self._stt_running:
                self._stt_set(phase="cancelled", error=ERR_CANCEL, running=False)
            return {"ok": True}
        except Exception as e:
            return _fail(ERR_CANCEL_FAIL, e)

    def _stt_run(self, paths):
        from src.stt import audio, diarize, engine

        collected = []
        files = []
        cancelled = False
        last_lang = ""
        total_dur = 0.0
        try:
            n = len(paths)
            for i, path in enumerate(paths, start=1):
                if self._stt_cancelled():
                    cancelled = True
                    break
                name = os.path.basename(path)
                self._stt_set(
                    phase="convert", current=name, file_index=i, file_total=n,
                    pct=self._stt_file_pct(0.0, index=i, total=n), error="")
                one = self._stt_one(path, audio, engine, diarize)
                files.append(one)
                if one.get("cancelled"):
                    cancelled = True
                    break
                if one.get("ok"):
                    # 파일 경계 오프셋 — 세션 타임스탬프를 단조화해야
                    # 검색·근거 점프의 이분탐색이 성립한다
                    segs_i = one.get("segments") or []
                    if total_dur > 0:
                        segs_i = _shift_segments(segs_i, total_dur)
                    collected.extend(segs_i)
                    last_lang = one.get("language") or last_lang
                    total_dur += float(one.get("duration_sec") or 0.0)
            if cancelled:
                self._stt_put_session(
                    segments=collected, files=files,
                    meta=_build_note_meta(files, last_lang, total_dur))
                self._stt_set(running=False, phase="cancelled", error=ERR_CANCEL,
                              pct=self._stt_pct, current="")
                return
            self._stt_put_session(
                segments=collected, files=files,
                meta=_build_note_meta(files, last_lang, total_dur))
            self._stt_set(running=False, phase="done", pct=100.0, current="",
                          error="")
        except Exception as e:
            _log(f"STT 전사 스레드 예외: {e}\n{traceback.format_exc()}")
            self._stt_put_session(segments=collected, files=files)
            self._stt_set(running=False, phase="idle", error=ERR_THREAD)

    def _stt_one(self, path, audio, engine, diarize):
        name = os.path.basename(path)
        protected = {os.path.abspath(path) if path else ""}
        temps = []
        base = {
            "ok": False, "name": name, "path": path, "segments": [],
            "language": "", "duration_sec": 0.0, "error": "", "cancelled": False,
        }
        try:
            if not path or not os.path.isfile(path):
                base["error"] = "파일을 찾을 수 없습니다."
                return base
            ext = os.path.splitext(path)[1].lower()
            if ext not in _audio_exts():
                base["error"] = ERR_UNSUPPORTED
                return base

            if self._stt_cancelled():
                base["cancelled"] = True
                base["error"] = ERR_CANCEL
                return base

            self._stt_set(phase="convert", current=name,
                          pct=self._stt_file_pct(0.05))
            norm = audio.normalize(path)
            if not norm.get("ok"):
                base["error"] = norm.get("error") or "오디오 변환에 실패했습니다."
                return base
            wav = norm.get("wav_path") or ""
            duration = float(norm.get("duration_sec") or 0.0)
            base["duration_sec"] = duration
            if wav and os.path.abspath(wav) not in protected:
                temps.append(wav)

            pieces = audio.chunk(wav)
            if not pieces:
                pieces = [{"path": wav, "offset_sec": 0.0}]
            for piece in pieces:
                pth = piece.get("path") or wav
                if pth and os.path.abspath(pth) not in protected:
                    if pth not in temps:
                        temps.append(pth)

            if self._stt_cancelled():
                base["cancelled"] = True
                base["error"] = ERR_CANCEL
                return base

            segs = []
            language = ""
            self._stt_set(phase="transcribe", current=name, total_sec=duration,
                          done_sec=0.0, pct=self._stt_file_pct(0.1))

            def on_progress(done_sec, total_sec):
                tot = float(total_sec or duration or 0.0)
                done = float(done_sec or 0.0)
                frac = 0.1 + 0.7 * (done / tot if tot else 0.0)
                self._stt_set(
                    phase="transcribe", current=name,
                    done_sec=done, total_sec=tot or duration,
                    pct=self._stt_file_pct(frac))

            for piece in pieces:
                if self._stt_cancelled():
                    base["cancelled"] = True
                    base["error"] = ERR_CANCEL
                    return base
                tr = engine.transcribe(
                    piece.get("path") or wav,
                    opts=self._stt_opts or None,
                    on_progress=on_progress,
                    should_cancel=self._stt_cancelled,
                )
                if not tr.get("ok"):
                    err = tr.get("error") or "음성 전사에 실패했습니다."
                    if _is_cancel_msg(err) or self._stt_cancelled():
                        base["cancelled"] = True
                        base["error"] = ERR_CANCEL
                    else:
                        base["error"] = err
                    return base
                language = tr.get("language") or language
                segs.extend(_shift_segments(
                    tr.get("segments") or [], piece.get("offset_sec") or 0.0))

            if self._stt_cancelled():
                base["cancelled"] = True
                base["error"] = ERR_CANCEL
                return base

            self._stt_set(phase="diarize", current=name,
                          pct=self._stt_file_pct(0.85))
            n_spk = (self._stt_opts or {}).get("num_speakers", -1)
            try:
                n_spk = int(n_spk)
            except (TypeError, ValueError):
                n_spk = -1
            dia = diarize.diarize(wav, num_speakers=n_spk)
            if dia.get("ok"):
                segs = diarize.assign(segs, dia.get("turns") or [])
                segs = diarize.merge(segs)
            else:
                # 전사는 살리고 화자만 비운다 — 파일 전체를 버리지 않는다.
                warn = dia.get("error") or ""
                if warn:
                    base["error"] = ""
                    base["warning"] = warn
                    _log(f"STT 화자 분리 실패 [{name}]: {warn}")

            base["ok"] = True
            base["segments"] = [_seg_copy(s) for s in segs]
            base["language"] = language or ""
            self._stt_set(pct=self._stt_file_pct(1.0), current=name)
            return base
        except Exception as e:
            _log(f"STT 파일 처리 예외 [{name}]: {e}")
            base["error"] = "음성 전사에 실패했습니다."
            return base
        finally:
            for tmp in temps:
                _safe_remove(tmp, protected)

    # ================= 전사본 조회/수정 =================
    def get_transcript(self):
        try:
            from src.stt import serialize
            raws, names, files_in, meta = self._stt_copy_session()
            segs = serialize.apply_speaker_names(raws, names)
            speakers = []
            for raw in raws:
                sp = str((raw or {}).get("speaker") or "").strip()
                if sp and sp not in speakers:
                    speakers.append(sp)
            out_segs = []
            for i, s in enumerate(segs):
                item = dict(s)
                item["start"] = float(item.get("start") or 0.0)
                item["end"] = float(item.get("end") or 0.0)
                item["ts"] = serialize.format_ts(item.get("start"))
                orig = raws[i] if i < len(raws) else {}
                item["speaker_orig"] = str((orig or {}).get("speaker") or "")
                out_segs.append(item)
            files = []
            for f in files_in:
                item = dict(f)
                mapped = serialize.apply_speaker_names(
                    f.get("segments") or [], names)
                item["segments"] = []
                for s in mapped:
                    row = dict(s)
                    row["start"] = float(row.get("start") or 0.0)
                    row["end"] = float(row.get("end") or 0.0)
                    row["ts"] = serialize.format_ts(row.get("start"))
                    item["segments"].append(row)
                files.append(item)
            return {
                "ok": True,
                "segments": out_segs,
                "speakers": speakers,
                "files": files,
                "mapping": names,
                "meta": meta,
            }
        except Exception as e:
            return _fail(ERR_READ, e)

    def update_transcript(self, payload=None):
        try:
            self._stt_state()
            payload = payload or {}
            if not isinstance(payload, dict):
                return _err("수정할 세그먼트를 지정하세요.")
            if isinstance(payload.get("segments"), list):
                self._stt_put_session(
                    segments=[_seg_copy(s) for s in payload["segments"]])
                return self.get_transcript()
            if "index" not in payload:
                return _err("수정할 세그먼트를 지정하세요.")
            try:
                idx = int(payload.get("index"))
            except (TypeError, ValueError):
                return _err(ERR_NO_SEG)
            with self._stt_lock:
                segs = self._stt_segments
                if idx < 0 or idx >= len(segs):
                    return _err(ERR_NO_SEG)
                row = dict(segs[idx])
                if "text" in payload:
                    row["text"] = "" if payload.get("text") is None else str(
                        payload.get("text"))
                if "speaker" in payload:
                    row["speaker"] = "" if payload.get("speaker") is None else str(
                        payload.get("speaker"))
                segs[idx] = row
            return self.get_transcript()
        except Exception as e:
            return _fail(ERR_EDIT, e)

    def set_speaker_names(self, payload=None):
        try:
            self._stt_state()
            mapping = payload
            if isinstance(payload, dict) and "mapping" in payload:
                mapping = payload.get("mapping")
            if mapping is None:
                mapping = {}
            if not isinstance(mapping, dict):
                return _err("화자 이름 매핑이 올바르지 않습니다.")
            clean = {}
            for k, v in mapping.items():
                key = str(k)
                if not key:
                    continue
                clean[key] = "" if v is None else str(v)
            self._stt_put_session(names=clean)
            return self.get_transcript()
        except Exception as e:
            return _fail(ERR_MAP, e)

    # ================= 회의록 연결/보존 =================
    def transcript_to_minutes(self):
        """serialize.to_minutes_input 결과를 반환. minutes_draft 는 건드리지 않는다."""
        try:
            segs, names, _files, _meta = self._stt_copy_session()
            if not segs:
                return _err(ERR_NO_RESULT)
            from src.stt import serialize
            text = serialize.to_minutes_input(segs, names)
            return {"ok": True, "text": text, "description": text, "chars": len(text)}
        except Exception as e:
            return _fail(ERR_TO_MINUTES, e)

    def get_audio_url(self):
        """현재 전사 세션의 원본 오디오를 재생할 수 있는 로컬 HTTP URL."""
        try:
            path, duration = self._stt_audio_source()
            if not path:
                return _err(ERR_NO_AUDIO, url="", duration_sec=duration)
            from src.note import serve
            info = serve.ensure_server()
            if not info.get("ok"):
                return _err(
                    info.get("error") or ERR_AUDIO_URL,
                    url="", duration_sec=duration)
            token = serve.register(path)
            url = serve.audio_url(token)
            return {
                "ok": True,
                "url": url,
                "duration_sec": duration,
                "error": "",
            }
        except Exception as e:
            return _fail(ERR_AUDIO_URL, e)

    def update_note_meta(self, payload=None):
        """사용자가 고친 제목·메모·키워드를 세션 메타에 보관한다."""
        try:
            self._stt_state()
            payload = payload or {}
            if not isinstance(payload, dict):
                return _err(ERR_NOTE_BAD)
            with self._stt_lock:
                meta = dict(self._stt_meta or {})
                if "title" in payload:
                    meta["title"] = "" if payload.get("title") is None else str(
                        payload.get("title"))
                if "memo" in payload:
                    meta["memo"] = "" if payload.get("memo") is None else str(
                        payload.get("memo"))
                if "keywords" in payload:
                    meta["keywords"] = _keywords(payload.get("keywords"))
                if not meta.get("created_at"):
                    meta["created_at"] = datetime.now().isoformat(
                        timespec="seconds")
                self._stt_meta = meta
            return {"ok": True, "error": ""}
        except Exception as e:
            return _fail(ERR_NOTE_META, e)

    def get_note_meta(self):
        """세션에 보관된 노트 제목·메모·키워드·생성시각·길이를 반환한다."""
        try:
            _segs, _names, _files, meta = self._stt_copy_session()
            meta = meta or {}
            keywords = meta.get("keywords")
            if not isinstance(keywords, list):
                keywords = []
            else:
                keywords = [str(x) for x in keywords]
            return {
                "ok": True,
                "title": str(meta.get("title") or ""),
                "memo": str(meta.get("memo") or ""),
                "keywords": keywords,
                "created_at": str(meta.get("created_at") or ""),
                "duration_sec": float(meta.get("duration_sec") or 0.0),
            }
        except Exception as e:
            return _fail(ERR_NOTE_READ, e)

    def _stt_audio_source(self):
        """재생할 원본 오디오 경로와 세션 길이. 없으면 경로가 빈 문자열."""
        _segs, _names, files, meta = self._stt_copy_session()
        duration = float((meta or {}).get("duration_sec") or 0.0)
        for row in files or []:
            path = (row or {}).get("path") or ""
            if row.get("ok") and path and os.path.isfile(path):
                return path, duration
        for row in files or []:
            path = (row or {}).get("path") or ""
            if path and os.path.isfile(path):
                return path, duration
        return "", duration

    def save_transcript(self, payload=None):
        """`.transcript.json` + `.transcript.txt` 사이드카를 저장한다."""
        try:
            raws, names, _files, meta = self._stt_copy_session()
            if not raws:
                return _err(ERR_NO_RESULT)
            payload = payload or {}
            if not isinstance(payload, dict):
                payload = {}
            from src.stt import serialize
            segs = serialize.apply_speaker_names(raws, names)
            meta = dict(meta or {})
            meta.setdefault("title", "")
            meta.setdefault("memo", "")
            if not isinstance(meta.get("keywords"), list):
                meta["keywords"] = []
            else:
                meta["keywords"] = _keywords(meta.get("keywords"))
            base = _sidecar_base(payload, meta)
            if not base:
                return _err(ERR_BAD_FOLDER)
            folder = os.path.dirname(base)
            if folder and not os.path.isdir(folder):
                return _err(ERR_BAD_FOLDER)
            json_path = base + ".transcript.json"
            txt_path = base + ".transcript.txt"
            body = serialize.to_transcript_json(segs, meta)
            text = serialize.to_plain_text(segs)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
            _log(f"STT 전사본 저장 [{os.path.basename(json_path)}]")
            return {"ok": True, "json_path": json_path, "txt_path": txt_path}
        except Exception as e:
            return _fail(ERR_SAVE, e)

    # ================= 엔진 상태 =================
    def stt_status(self):
        """runtime.status() 를 UI 배너용으로 감싼다."""
        try:
            from src.stt import runtime
            r = runtime.status()
            if not isinstance(r, dict):
                return _err(ERR_ENGINE)
            out = dict(r)
            out.setdefault("ok", True)
            return out
        except Exception as e:
            return _fail(ERR_ENGINE, e)

    def install_stt_models(self):
        """runtime.ensure_models() 를 진행률과 함께 실행한다."""
        try:
            from src.stt import runtime
            self._stt_state()
            if self._stt_running:
                return _err(ERR_BUSY)

            def on_progress(got, total):
                tot = float(total or 0.0)
                done = float(got or 0.0)
                pct = (done / tot * 100.0) if tot else 0.0
                self._stt_set(phase="install", pct=round(pct, 1),
                              done_sec=done, total_sec=tot, current="모델")

            r = runtime.ensure_models(on_progress=on_progress)
            if not isinstance(r, dict):
                return _err(ERR_INSTALL)
            if r.get("ok"):
                self._stt_set(phase="idle", pct=100.0, current="")
            else:
                self._stt_set(phase="idle", error=r.get("error") or "", current="")
            out = dict(r)
            out.setdefault("ok", False)
            return out
        except Exception as e:
            return _fail(ERR_INSTALL, e)


def _sidecar_base(payload, meta):
    """저장 경로의 확장자 없는 접두어를 고른다."""
    out_path = (payload.get("out_path") or payload.get("path") or "").strip()
    folder = (payload.get("folder") or payload.get("out_dir") or "").strip()
    stem = (payload.get("stem") or payload.get("name") or "").strip()
    if out_path:
        if os.path.isdir(out_path):
            folder = out_path
        else:
            root, ext = os.path.splitext(out_path)
            low = ext.lower()
            if low in (".json", ".txt"):
                if root.lower().endswith(".transcript"):
                    return root[:-len(".transcript")]
                return root
            if low:
                return root
            return out_path
    if not folder:
        return ""
    if not stem:
        src = str((meta or {}).get("source") or "").split(",")[0].strip()
        stem = os.path.splitext(os.path.basename(src))[0] if src else "transcript"
    stem = os.path.splitext(os.path.basename(stem))[0] or "transcript"
    return os.path.join(folder, stem)
