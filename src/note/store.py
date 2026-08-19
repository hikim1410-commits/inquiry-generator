# -*- coding: utf-8 -*-
"""회의 노트 사이드카(.note.json) 저장/로드 — `src/store/minutes_store.py` 동형.

파일이 진실의 원천이다(PRD_회의록노트 §6.1). 신규 DB를 두지 않고 회의록 폴더에
같은 basename의 형제 파일로 남긴다. 검색 인덱스는 파생물이라 언제든 재생성한다.

  <base>.hwpx / <base>.minutes.json / <base>.transcript.json / <base>.note.json

load는 어떤 상태의 파일이 와도 §6.2 전체 형을 채워 돌려준다 — 손으로 고쳐졌거나
구버전이거나 키가 빠져도 호출부가 .get 체인을 쓰지 않게 한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

SCHEMA_VERSION = 1

SOURCE_KINDS = ("audio", "text")


# 형제 파일로 인정하는 확장자. 이 밖의 "확장자"는 점이 든 베이스명의 일부다
# (예: '정례회의 2026.08' 의 '.08') — 잘라내면 전사본과 파일명 짝이 어긋난다.
_SIBLING_EXTS = (".hwpx", ".json", ".txt")


def sidecar_path(base_path: str) -> str:
    """어떤 형제 파일 경로든 → .note.json 경로.

    `.transcript.json` / `.minutes.json` 같은 이중 확장자도 베이스명으로 되돌린다.
    """
    s = str(base_path or "")
    root, ext = os.path.splitext(s)
    if ext.lower() not in _SIBLING_EXTS:
        return s + ".note.json"
    if ext.lower() in (".json", ".txt"):
        for tail in (".transcript", ".minutes", ".note"):
            if root.lower().endswith(tail):
                root = root[:-len(tail)]
                break
    return root + ".note.json"


def empty_note() -> dict:
    """§6.2 전체 형의 빈 노트."""
    return {
        "schema": SCHEMA_VERSION,
        "title": "",
        "created_at": "",
        "duration_sec": 0.0,
        "source": {"kind": "text", "audio_path": "", "audio_kept": False},
        "speakers": [],
        "summary": {},
        "summary_meta": {
            "provider": "", "model": "", "generated_at": "",
            "edited_by_user": False, "chunked": False,
        },
        "bookmarks": [],
        "tags": [],
        "links": {"transcript": "", "minutes_json": "", "hwpx": ""},
        "memo": "",
    }


def save_note(base_path: str, note: dict) -> str:
    """노트를 사이드카에 저장하고 경로를 반환. created_at은 기존 값을 지킨다."""
    path = sidecar_path(base_path)
    payload = normalize_note(note)
    if not payload["created_at"]:
        payload["created_at"] = datetime.now().isoformat(timespec="seconds")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                old = json.load(fp)
            created = (old or {}).get("created_at")
            if created:
                payload["created_at"] = str(created)
        except Exception:
            pass  # 깨진 기존 파일 때문에 새 저장을 막지 않는다
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return path


def load_note(path: str) -> dict:
    """사이드카를 읽어 §6.2 전체 형으로 정규화해 반환. 파일이 없으면 빈 노트."""
    p = sidecar_path(path) if not str(path or "").endswith(".note.json") else path
    if not os.path.exists(p):
        return empty_note()
    try:
        with open(p, "r", encoding="utf-8") as fp:
            return normalize_note(json.load(fp))
    except Exception:
        return empty_note()  # 깨진 파일도 계약(전체 형 반환)을 지킨다


def normalize_note(note) -> dict:
    """어떤 입력이 와도 §6.2 전체 형. 형이 틀린 항목은 버린다."""
    src = note if isinstance(note, dict) else {}
    out = empty_note()

    out["title"] = _line(src.get("title"), 200)
    out["created_at"] = _line(src.get("created_at"), 40)
    out["memo"] = str(src.get("memo") or "")
    try:
        out["duration_sec"] = max(0.0, float(src.get("duration_sec") or 0.0))
    except (ValueError, TypeError):
        out["duration_sec"] = 0.0

    s = src.get("source")
    if isinstance(s, dict):
        kind = str(s.get("kind") or "").strip().lower()
        out["source"] = {
            "kind": kind if kind in SOURCE_KINDS else "text",
            "audio_path": _line(s.get("audio_path"), 260),
            "audio_kept": bool(s.get("audio_kept")),
        }

    for sp in (src.get("speakers") or []):
        if not isinstance(sp, dict):
            continue
        key = _line(sp.get("key"), 40)
        if not key:
            continue
        out["speakers"].append({
            "key": key,
            "name": _line(sp.get("name"), 60),
            "org": _line(sp.get("org"), 60),
        })

    if isinstance(src.get("summary"), dict):
        out["summary"] = src["summary"]

    sm = src.get("summary_meta")
    if isinstance(sm, dict):
        out["summary_meta"] = {
            "provider": _line(sm.get("provider"), 40),
            "model": _line(sm.get("model"), 80),
            "generated_at": _line(sm.get("generated_at"), 40),
            "edited_by_user": bool(sm.get("edited_by_user")),
            "chunked": bool(sm.get("chunked")),
        }

    for i, b in enumerate(src.get("bookmarks") or []):
        if not isinstance(b, dict):
            continue
        out["bookmarks"].append({
            "id": _line(b.get("id"), 40) or "b%d" % (i + 1),
            "t_ms": _nonneg_int(b.get("t_ms")),
            "seg_id": _nonneg_int(b.get("seg_id")),
            "memo": _line(b.get("memo"), 300),
        })

    seen = set()
    for t in (src.get("tags") or []):
        s2 = _line(t, 40)
        if s2 and s2 not in seen:
            seen.add(s2)
            out["tags"].append(s2)

    links = src.get("links")
    if isinstance(links, dict):
        for k in out["links"]:
            out["links"][k] = _line(links.get(k), 260)

    return out


def _line(v, limit):
    return " ".join(str(v or "").split())[:limit]


def _nonneg_int(v):
    try:
        n = int(float(v))
    except (ValueError, TypeError):
        return 0
    return n if n >= 0 else 0
