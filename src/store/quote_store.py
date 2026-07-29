# -*- coding: utf-8 -*-
"""견적서 JSON 저장/로드 + 파일 명명 규칙.

명명 규칙 (프로젝트 CLAUDE.md): 견적서_{용역명}_{YYMMDD}.hwpx/.pdf/.quote.json
(산출물은 v1.7부터 HWPX 강제 — zip/XML이라 스캔·검증·후처리가 쉬움. 키 이름 "hwp"는
호출부 호환을 위해 유지)
"""
import json
import os
import re
from datetime import datetime

SCHEMA_VERSION = 1
_INVALID = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_filename(name: str, max_len: int = 60) -> str:
    s = _INVALID.sub(" ", name or "").strip()
    s = re.sub(r"\s+", " ", s)
    return (s[:max_len].strip() or "무제")


def base_filename(service_name: str, iso_date: str) -> str:
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
    except Exception:
        d = datetime.now()
    return f"견적서_{sanitize_filename(service_name)}_{d.strftime('%y%m%d')}"


def quote_paths(folder: str, service_name: str, iso_date: str) -> dict:
    base = base_filename(service_name, iso_date)
    return {
        "base": base,
        "hwp": os.path.join(folder, base + ".hwpx"),
        "pdf": os.path.join(folder, base + ".pdf"),
        "json": os.path.join(folder, base + ".quote.json"),
    }


def save_quote(folder: str, quote: dict) -> str:
    """quote dict → .quote.json 저장, 경로 반환."""
    doc = quote.get("doc", {})
    paths = quote_paths(folder, doc.get("service_name", ""), doc.get("date", ""))
    quote = dict(quote)
    quote["schema_version"] = SCHEMA_VERSION
    meta = quote.setdefault("meta", {})
    now = datetime.now().isoformat(timespec="seconds")
    meta.setdefault("created", now)
    meta["modified"] = now
    os.makedirs(folder, exist_ok=True)
    with open(paths["json"], "w", encoding="utf-8") as fp:
        json.dump(quote, fp, ensure_ascii=False, indent=2)
    return paths["json"]


def load_quote(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)
