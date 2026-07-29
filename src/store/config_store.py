# -*- coding: utf-8 -*-
"""config.json 관리 + Gemini API 키 DPAPI 암호화 저장."""
import base64
import copy
import json
import os
import tempfile
import threading
import time

from src.paths import data_path
from src.logutil import log as _log

_config_lock = threading.RLock()

# config.json은 쓰기 가능한 데이터 → EXE에서는 exe 옆 폴더 (번들 임시폴더 아님)
CONFIG_PATH = data_path("config.json")

# 견적 구성 제안에 적합한 Gemini 텍스트 Flash 모델 (2026-06 기준).
# id는 generativelanguage API의 모델명. label은 설정 드롭다운 표기.
# gemini-flash-latest는 항상 최신 Flash로 자동 갱신되는 별칭(구형 종료에 안전).
# 드롭다운 선택지는 api._DEPRECATED_MODELS와 절대 겹치면 안 됨
# (겹치면 사용자가 고른 모델이 재시작 시 자동 치환돼 되돌아감). test_ai에서 불변식 검증.
GEMINI_MODELS = [
    {"id": "gemini-flash-latest", "label": "Gemini Flash (항상 최신 · 권장)"},
    {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash (최고 성능)"},
    {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash"},
    {"id": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash-Lite (최저가)"},
]

DEFAULT_CONFIG = {
    "company": {
        "name": "주식회사 내비온",
        "reg_no": "319-86-00553",
        "ceo": "조 성 한",
        "address": "(06175) 서울시 강남구 테헤란로 108길 11, 삼호빌딩 4층",
        "biz_type": "전문, 과학 및 기술서비스업",
        "biz_item": "학술연구용역/ 기술 거래 중개 및 알선업",
        "manager": "",
        "tel": "",
        "email": "",
        "fax": "02-6407-7739",
    },
    # 학술연구용역 인건비 기준단가 (월). 연도별 편집 가능.
    "unit_prices": {
        "2026": {
            "책임연구원": 7567456,
            "연구원": 5802624,
            "연구보조원": 3878858,
            "보조원": 2909242,
        }
    },
    "default_price_year": "2026",
    # 인건비 자동조정 — 직급별 최대 인원(전역 기본값, 견적별 조정 가능).
    # 책임연구원은 규칙상 항상 1명이라 조정 대상 아님.
    "labor": {
        "max_counts": {"연구원": 5, "연구보조원": 5, "보조원": 10},
        # 인건비 목표 비율 (목표금액 대비, 0.1~0.9). 경비 목표 = 직접비 − 인건비 목표.
        "labor_ratio": 0.5,
    },
    "gemini": {"api_key_enc": "", "model": "gemini-flash-latest"},
    # 멀티 AI 프로바이더: gemini 키는 위 "gemini"에 유지(하위호환),
    # openai·anthropic는 아래에 저장. provider가 현재 선택된 프로바이더.
    "ai": {
        "provider": "gemini",
        "openai": {"api_key_enc": "", "model": "gpt-5.1"},
        "anthropic": {"api_key_enc": "", "model": "claude-opus-4-8"},
    },
    # AI 초안 기초 지침 오버라이드 — 빈 문자열이면 내장 기본 지침(engine.DIRECTIVE_DEFAULTS) 사용.
    # 과업 내용·단가표·경비 가이드 등 데이터 블록은 지침과 무관하게 항상 시스템이 자동 첨부.
    "ai_prompts": {"quote": "", "minutes": ""},
    "quote_no_seq": {},          # {"2026": 2} → 다음 번호 제 2026-002호
    "last_folder": "",           # (하위호환) 견적서 폴더 구버전 키 — doc_types.quote.folder가 우선
    # 문서 유형별 네임스페이스 — 새 유형 추가 시 여기에 키 추가 (ui DOC_TYPES와 짝)
    "doc_types": {
        "quote":   {"folder": ""},
        "minutes": {"folder": "", "template_path": ""},
    },
    "hwp": {"visible_debug": False},
    "money": {"keep_il": True},
    "drive": {"folder": "내비온 견적서", "auto": False},
    # 최초 실행 튜토리얼 — 완료/건너뛰기 시 True (설정에서 다시 보기 가능)
    "tutorial": {"seen": False},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    # 읽기도 같은 락으로 보호 — Windows에서 os.replace와 읽기 핸들 경합 방지
    with _config_lock:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
                    return _merge(DEFAULT_CONFIG, json.load(fp))
            except Exception as e:
                # 손상 config를 조용히 버리면 직후 save_config가 기본값으로
                # 덮어써 API 키·회사정보가 복구 불가능하게 소실된다 — 백업 보존
                try:
                    from datetime import datetime
                    bad = CONFIG_PATH + ".bad-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                    os.replace(CONFIG_PATH, bad)
                    _log(f"config.json 손상({e}) — {os.path.basename(bad)}로 보존 후 기본값 사용")
                except OSError:
                    _log(f"config.json 손상({e}) — 백업 실패, 기본값 사용")
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg: dict):
    """동시 저장 보호 + 원자적 쓰기 (임시파일 → os.replace)로 손상 방지."""
    with _config_lock:
        dir_path = os.path.dirname(CONFIG_PATH) or "."
        fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(cfg, fp, ensure_ascii=False, indent=2)
            # 외부 프로세스(백신·탐색기 등)의 일시적 잠금에 대비한 짧은 재시도
            for attempt in range(5):
                try:
                    os.replace(tmp, CONFIG_PATH)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ---- DPAPI 키 암호화 (현재 Windows 사용자 계정에 바인딩) ----

def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    try:
        import win32crypt
        blob = win32crypt.CryptProtectData(
            plain.encode("utf-8"), None, None, None, None, 0)
        return base64.b64encode(blob).decode("ascii")
    except Exception as e:
        raise ValueError(f"DPAPI 암호화 실패(Windows 계정 권한 확인 필요): {e}") from e


def decrypt_secret(enc: str) -> str:
    if not enc:
        return ""
    try:
        import win32crypt
        raw = base64.b64decode(enc.encode("ascii"))
        out = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
        return out[1].decode("utf-8")
    except Exception:
        return ""


def set_gemini_key(cfg: dict, plain_key: str) -> dict:
    cfg.setdefault("gemini", {})["api_key_enc"] = encrypt_secret(plain_key.strip())
    save_config(cfg)
    return cfg


def get_gemini_key(cfg: dict) -> str:
    return decrypt_secret(cfg.get("gemini", {}).get("api_key_enc", ""))


# ---- 멀티 프로바이더 (gemini / openai / anthropic) ----

AI_PROVIDERS = ("gemini", "openai", "anthropic")
_AI_DEFAULT_MODELS = {
    "gemini": "gemini-flash-latest",
    "openai": "gpt-5.1",
    "anthropic": "claude-opus-4-8",
}


def _provider_store(cfg: dict, provider: str) -> dict:
    """프로바이더별 {api_key_enc, model} 저장 dict 반환.
    gemini는 하위호환을 위해 최상위 cfg['gemini']에 유지."""
    if provider == "gemini":
        return cfg.setdefault("gemini", {"api_key_enc": "",
                                         "model": _AI_DEFAULT_MODELS["gemini"]})
    ai = cfg.setdefault("ai", {})
    return ai.setdefault(provider, {"api_key_enc": "",
                                    "model": _AI_DEFAULT_MODELS.get(provider, "")})


def get_provider(cfg: dict) -> str:
    p = cfg.get("ai", {}).get("provider", "gemini")
    return p if p in AI_PROVIDERS else "gemini"


def set_provider(cfg: dict, provider: str) -> dict:
    if provider not in AI_PROVIDERS:
        raise ValueError(f"알 수 없는 AI 프로바이더: {provider}")
    cfg.setdefault("ai", {})["provider"] = provider
    save_config(cfg)
    return cfg


def get_ai_key(cfg: dict, provider: str) -> str:
    return decrypt_secret(_provider_store(cfg, provider).get("api_key_enc", ""))


def set_ai_key(cfg: dict, provider: str, plain_key: str) -> dict:
    _provider_store(cfg, provider)["api_key_enc"] = encrypt_secret((plain_key or "").strip())
    save_config(cfg)
    return cfg


def get_ai_model(cfg: dict, provider: str) -> str:
    return (_provider_store(cfg, provider).get("model")
            or _AI_DEFAULT_MODELS.get(provider, ""))


def set_ai_model(cfg: dict, provider: str, model: str) -> dict:
    _provider_store(cfg, provider)["model"] = str(model or "").strip()
    save_config(cfg)
    return cfg


# ---- AI 초안 기초 지침 (문서 유형별 오버라이드) ----

AI_PROMPT_DOC_TYPES = ("quote", "minutes")


def get_ai_prompt(cfg: dict, doc_type: str) -> str:
    """저장된 기초 지침 오버라이드. 빈 문자열 = 내장 기본 지침 사용."""
    v = (cfg.get("ai_prompts") or {}).get(doc_type, "")
    return v if isinstance(v, str) else ""


def set_ai_prompt(cfg: dict, doc_type: str, text: str) -> dict:
    if doc_type not in AI_PROMPT_DOC_TYPES:
        raise ValueError(f"알 수 없는 문서 유형: {doc_type}")
    cfg.setdefault("ai_prompts", {})[doc_type] = str(text or "")
    save_config(cfg)
    return cfg


def get_labor_ratio(cfg: dict) -> float:
    """인건비 목표 비율 (목표금액 대비). 기본 0.5 (50%)."""
    v = (cfg.get("labor") or {}).get("labor_ratio", 0.5)
    try:
        return max(0.1, min(0.9, float(v)))
    except (TypeError, ValueError):
        return 0.5


def set_labor_ratio(cfg: dict, ratio: float) -> None:
    cfg.setdefault("labor", {})["labor_ratio"] = max(0.1, min(0.9, float(ratio)))
    save_config(cfg)


def get_minutes_tpl(cfg: dict) -> str:
    """저장된 회의록 커스텀 템플릿 경로. 빈 문자열 = 내장 기본 사용."""
    v = (cfg.get("doc_types") or {}).get("minutes", {}).get("template_path", "")
    return v if isinstance(v, str) else ""


def set_minutes_tpl(cfg: dict, path: str) -> None:
    cfg.setdefault("doc_types", {}).setdefault("minutes", {})["template_path"] = str(path or "")
    save_config(cfg)


def migrate_doc_type_folders(cfg: dict) -> bool:
    """구버전 last_folder → doc_types.quote.folder 1회 복사.
    이미 quote.folder가 설정돼 있으면 덮어쓰지 않는다. 변경 시 True 반환
    (저장은 호출자 책임 — Api.__init__의 기존 마이그레이션 패턴과 동일)."""
    dt = cfg.setdefault("doc_types", {}).setdefault("quote", {})
    if not dt.get("folder") and cfg.get("last_folder"):
        dt["folder"] = cfg["last_folder"]
        return True
    return False


# ---- 회의록 Preset 저장소 (Wave 2 B-1/B-2/B-3) ----
#
# 저장 위치: doc_types.minutes.presets[]  (보관 목록)
# 활성 양식의 단일 출처는 기존 doc_types.minutes.template_path (후방호환).
#   - 빈 문자열 = 내장 양식 사용 (get_minutes_template / generate_minutes 규약)
#   - select_minutes_preset 한 곳에서만 presets ↔ template_path 동기화를 강제해
#     "이중 진실 원천"(보관 목록 vs 활성)을 방지한다. 활성 여부는 template_path로 파생.

BUILTIN_PRESET_ID = "builtin"


def _builtin_minutes_preset() -> dict:
    """내장 양식(TEMPLATE_MINUTES) preset — 항상 첫 항목·삭제/이름변경 불가."""
    from src.minutes.hwpx_minutes import TEMPLATE_MINUTES
    return {
        "id": BUILTIN_PRESET_ID,
        "name": "기본 회의록 양식",
        "template_path": TEMPLATE_MINUTES,
        "is_builtin": True,
        "created": "",
    }


def migrate_minutes_presets(cfg: dict) -> bool:
    """presets[] 시딩·정규화 (적대리뷰 #4: _merge가 리스트를 통째로 덮으므로 전용 처리).

    (a) presets 부재/비배열 → 내장 preset 1개로 초기화,
    (b) 내장 preset을 항상 canonical 값으로 첫 항목 보장(구 내장 항목은 교체),
    (c) 타입 검증(비 dict·id 누락 항목 폐기).
    변경 시 True (저장은 호출자 책임 — migrate_doc_type_folders 패턴)."""
    m = cfg.setdefault("doc_types", {}).setdefault("minutes", {})
    raw = m.get("presets")
    user = []
    if isinstance(raw, list):
        for p in raw:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if not isinstance(pid, str) or not pid.strip():
                continue
            if p.get("is_builtin") or pid == BUILTIN_PRESET_ID:
                continue  # 내장은 canonical로 재생성
            user.append(p)
    new_list = [_builtin_minutes_preset()] + user
    if raw == new_list:
        return False
    m["presets"] = new_list
    return True


def _presets_dir() -> str:
    """사용자 추가 양식 사본 보관 폴더 (9-c: 앱 데이터 폴더 복사)."""
    d = data_path("minutes_templates")
    os.makedirs(d, exist_ok=True)
    return d


def copy_minutes_template(src_path: str) -> str:
    """양식 파일을 앱 데이터 폴더로 복사하고 사본 경로 반환.
    원본 이동·삭제에 견고(9-c). 같은 이름 충돌 시 접미사로 회피."""
    import shutil
    dst_dir = _presets_dir()
    stem, ext = os.path.splitext(os.path.basename(src_path))
    dst = os.path.join(dst_dir, stem + ext)
    n = 1
    while os.path.exists(dst):
        dst = os.path.join(dst_dir, f"{stem}({n}){ext}")
        n += 1
    shutil.copy2(src_path, dst)
    return dst


def get_minutes_presets(cfg: dict) -> list:
    """presets[] 반환 (시딩·정규화 보장)."""
    migrate_minutes_presets(cfg)
    return cfg["doc_types"]["minutes"]["presets"]


def _find_preset(cfg: dict, preset_id: str) -> dict:
    for p in get_minutes_presets(cfg):
        if p.get("id") == preset_id:
            return p
    raise ValueError(f"존재하지 않는 preset입니다: {preset_id}")


def add_minutes_preset(cfg: dict, template_path: str, name: str = None) -> dict:
    """사용자 양식을 presets[]에 등록. template_path는 (보통 복사된) 양식 경로."""
    import uuid
    presets = get_minutes_presets(cfg)
    existing = {p.get("id") for p in presets}
    pid = uuid.uuid4().hex[:12]
    while pid in existing:
        pid = uuid.uuid4().hex[:12]
    preset = {
        "id": pid,
        "name": (name or "").strip()
                or os.path.splitext(os.path.basename(template_path))[0],
        "template_path": template_path,
        "is_builtin": False,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    presets.append(preset)
    save_config(cfg)
    return preset


def select_minutes_preset(cfg: dict, preset_id: str) -> dict:
    """활성 preset 지정 — 동기화 단일 지점. template_path를 활성 출처로 반영.
    내장 preset이면 template_path=''(빈 값 = 내장 사용, 후방호환)."""
    target = _find_preset(cfg, preset_id)
    m = cfg["doc_types"]["minutes"]
    m["template_path"] = "" if target.get("is_builtin") else target.get("template_path", "")
    save_config(cfg)
    return target


def delete_minutes_preset(cfg: dict, preset_id: str) -> dict:
    """preset 등록 해제. 내장 거부. 활성 preset였으면 template_path 내장 폴백."""
    target = _find_preset(cfg, preset_id)
    if target.get("is_builtin"):
        raise ValueError("내장 양식은 삭제할 수 없습니다.")
    m = cfg["doc_types"]["minutes"]
    m["presets"].remove(target)
    if m.get("template_path") and m.get("template_path") == target.get("template_path"):
        m["template_path"] = ""  # 활성 삭제 → 내장 폴백
    save_config(cfg)
    return target


def rename_minutes_preset(cfg: dict, preset_id: str, name: str) -> dict:
    """preset 이름 변경. 내장 거부."""
    target = _find_preset(cfg, preset_id)
    if target.get("is_builtin"):
        raise ValueError("내장 양식은 이름을 바꿀 수 없습니다.")
    new = str(name or "").strip()
    if new:
        target["name"] = new
    save_config(cfg)
    return target


def get_minutes_gallery_autoshow(cfg: dict) -> bool:
    """양식 갤러리 자동 표시 여부 (기본 true, 9-d — tutorial.seen 패턴 미러)."""
    v = (cfg.get("doc_types") or {}).get("minutes", {}).get("gallery_autoshow", True)
    return bool(v) if isinstance(v, bool) else True


def set_minutes_gallery_autoshow(cfg: dict, on: bool) -> None:
    cfg.setdefault("doc_types", {}).setdefault("minutes", {})["gallery_autoshow"] = bool(on)
    save_config(cfg)


def next_quote_no(cfg: dict, year: str, peek: bool = False) -> str:
    """'제 2026-001호' 형식 자동 번호. peek=False면 시퀀스 증가 후 저장."""
    seqs = cfg.setdefault("quote_no_seq", {})
    seq = int(seqs.get(year, 0)) + 1
    if not peek:
        seqs[year] = seq
        save_config(cfg)
    return f"제 {year}-{seq:03d}호"
