# -*- coding: utf-8 -*-
"""설정 도메인 — config 조회/저장, AI 프로바이더·키·모델·프롬프트, 견적 옵션."""
from datetime import date as _date

from src.store import config_store as cs
from src.ai import engine as ai_engine
from src.ai import llm as ai_llm

from ._common import _err, _AI_PROMPT_MAX


class SettingsApi:
    # ================= 설정 =================
    def get_config(self):
        cfg = self.cfg
        ai_keys = {p: bool(cs.get_ai_key(cfg, p)) for p in cs.AI_PROVIDERS}
        ai_models = {p: cs.get_ai_model(cfg, p) for p in cs.AI_PROVIDERS}
        return {"ok": True, "config": {
            "company": cfg["company"],
            "unit_prices": cfg["unit_prices"],
            "default_price_year": cfg["default_price_year"],
            # AI (멀티 프로바이더)
            "ai_provider": cs.get_provider(cfg),
            "ai_providers": list(cs.AI_PROVIDERS),
            "ai_provider_labels": ai_llm.PROVIDER_LABELS,
            "ai_keys_set": ai_keys,
            "ai_models": ai_models,
            "ai_default_models": ai_llm.DEFAULT_MODELS,
            "gemini_models": cs.GEMINI_MODELS,    # gemini 큐레이트 드롭다운용
            # AI 초안 기초 지침 (빈 문자열 = 기본 지침 사용) + 기본값(복원·비교용)
            "ai_prompts": {t: cs.get_ai_prompt(cfg, t)
                           for t in cs.AI_PROMPT_DOC_TYPES},
            "ai_prompt_defaults": dict(ai_engine.DIRECTIVE_DEFAULTS),
            "max_counts": cfg.get("labor", {}).get("max_counts", {}),
            "labor_ratio": cs.get_labor_ratio(cfg),
            "last_folder": cfg.get("last_folder", ""),
            # 문서 유형별 작업 폴더 (UI 시딩용 — 폴백 적용된 실효값)
            "doc_folders": {
                "quote": self._doc_folder("quote"),
                "minutes": self._doc_folder("minutes"),
            },
            "keep_il": cfg.get("money", {}).get("keep_il", True),
            "tutorial_seen": bool(cfg.get("tutorial", {}).get("seen", False)),
        }}

    # ---- 멀티 프로바이더 AI ----
    def set_ai_provider(self, provider):
        try:
            cs.set_provider(self.cfg, provider)
            return {"ok": True, "provider": cs.get_provider(self.cfg)}
        except Exception as e:
            return _err(e)

    def set_ai_key(self, provider, key):
        try:
            cs.set_ai_key(self.cfg, provider, key or "")
            return {"ok": True, "key_set": bool(key)}
        except Exception as e:
            return _err(e)

    def set_ai_model(self, provider, model):
        try:
            cs.set_ai_model(self.cfg, provider, model)
            return {"ok": True, "model": cs.get_ai_model(self.cfg, provider)}
        except Exception as e:
            return _err(e)

    def set_ai_prompt(self, doc_type, text):
        """AI 초안 기초 지침 저장. 빈 값·기본값과 동일하면 오버라이드 해제("")
        — 사용자가 그대로 저장해도 향후 기본 지침 개선이 계속 반영되게."""
        try:
            if doc_type not in cs.AI_PROMPT_DOC_TYPES:
                return _err(f"알 수 없는 문서 유형: {doc_type}")
            norm = str(text or "").replace("\r\n", "\n").strip()
            if len(norm) > _AI_PROMPT_MAX:
                return _err(f"프롬프트가 너무 깁니다 ({_AI_PROMPT_MAX:,}자 이내로 입력하세요).")
            if norm == ai_engine.DIRECTIVE_DEFAULTS.get(doc_type, "").strip():
                norm = ""
            cs.set_ai_prompt(self.cfg, doc_type, norm)
            return {"ok": True, "doc_type": doc_type,
                    "custom": bool(norm), "text": norm}
        except Exception as e:
            return _err(e)

    def validate_ai_key(self, provider):
        key = cs.get_ai_key(self.cfg, provider)
        if not key:
            return _err("저장된 API 키가 없습니다.")
        return ai_engine.validate_key(provider, key)

    def list_ai_models(self, provider):
        key = cs.get_ai_key(self.cfg, provider)
        if not key:
            return _err("먼저 API 키를 저장하세요.")
        return ai_engine.list_models(provider, key)

    def set_config(self, partial):
        try:
            allowed = {"company", "unit_prices", "default_price_year", "last_folder"}
            for k, v in (partial or {}).items():
                if k in allowed:
                    self.cfg[k] = v
            cs.save_config(self.cfg)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def suggest_quote_no(self, year=None):
        y = str(year or _date.today().year)
        return {"ok": True, "quote_no": cs.next_quote_no(self.cfg, y, peek=False)}
    def set_max_counts(self, counts):
        """직급별 최대 인원(전역 기본값) 저장."""
        try:
            cur = self.cfg.setdefault("labor", {}).setdefault("max_counts", {})
            for g, v in (counts or {}).items():
                try:
                    cur[g] = max(1, int(v))
                except (TypeError, ValueError):
                    pass
            cs.save_config(self.cfg)
            return {"ok": True, "max_counts": cur}
        except Exception as e:
            return _err(e)

    def set_labor_ratio(self, ratio):
        """인건비 목표 비율 저장 (목표금액 대비, 0.1~0.9)."""
        try:
            r = max(0.1, min(0.9, float(ratio)))
            cs.set_labor_ratio(self.cfg, r)
            return {"ok": True, "labor_ratio": r}
        except (TypeError, ValueError):
            return _err("유효한 비율값이 아닙니다")
        except Exception as e:
            return _err(e)

    def set_tutorial_seen(self, seen=True):
        """튜토리얼 1회 노출 플래그 저장 (완료·건너뛰기·ESC 공통)."""
        try:
            self.cfg.setdefault("tutorial", {})["seen"] = bool(seen)
            cs.save_config(self.cfg)
            return {"ok": True}
        except Exception as e:
            return _err(e)
