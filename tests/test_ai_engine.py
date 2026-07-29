# -*- coding: utf-8 -*-
"""ai.engine 디스패처 — 프로바이더 라우팅·키 부재 처리 (G004 테스트 공백 보강).

실 API 호출 없음 — gemini/llm 경계는 전부 mock.
"""
from unittest import mock

from src.ai import engine
from src.ai import gemini
from src.ai import llm


def _kw(**over):
    kw = dict(description="설명", target=10000000, profit_on=True,
              expense_budget=1000000, price_table={}, year="2026",
              api_key="k", model="m")
    kw.update(over)
    return kw


def test_directive_defaults_cover_both_doc_types():
    assert set(engine.DIRECTIVE_DEFAULTS) == {"quote", "minutes"}
    assert all(isinstance(v, str) and v.strip()
               for v in engine.DIRECTIVE_DEFAULTS.values())


def test_draft_quote_without_key_errors_with_label():
    r = engine.draft_quote("openai", **_kw(api_key=""))
    assert r["ok"] is False
    assert llm.PROVIDER_LABELS["openai"] in r["error"]


def test_draft_quote_gemini_routes_to_gemini_module():
    with mock.patch.object(gemini, "draft_quote",
                           return_value={"ok": True, "draft": {"x": 1}}) as g:
        r = engine.draft_quote("gemini", **_kw())
    assert r == {"ok": True, "draft": {"x": 1}}
    assert g.call_count == 1


def test_draft_quote_other_provider_uses_llm_and_normalize():
    with mock.patch.object(llm, "complete_json",
                           return_value={"ok": True, "data": {"raw": 1}}) as cj, \
         mock.patch.object(gemini, "_normalize",
                           side_effect=lambda d: {"normalized": d}) as nz:
        r = engine.draft_quote("openai", **_kw())
    assert r == {"ok": True, "draft": {"normalized": {"raw": 1}}}
    assert cj.call_args.args[0] == "openai"
    assert nz.call_count == 1


def test_draft_quote_llm_failure_passthrough():
    err = {"ok": False, "error": "quota", "model_error": True}
    with mock.patch.object(llm, "complete_json", return_value=err):
        r = engine.draft_quote("anthropic", **_kw())
    assert r is err


def test_list_models_routing():
    with mock.patch.object(gemini, "list_text_flash_models",
                           return_value={"ok": True, "models": []}) as gm:
        assert engine.list_models("gemini", "k")["ok"]
    assert gm.call_count == 1
    with mock.patch.object(llm, "list_models",
                           return_value={"ok": True, "models": []}) as lm:
        assert engine.list_models("openai", "k")["ok"]
    assert lm.call_args.args == ("openai", "k")


def test_validate_key_routing():
    with mock.patch.object(gemini, "validate_key",
                           return_value={"ok": True}) as gv:
        assert engine.validate_key("gemini", "k")["ok"]
    assert gv.call_count == 1
    with mock.patch.object(llm, "validate_key",
                           return_value={"ok": False, "error": "bad"}) as lv:
        assert engine.validate_key("openai", "k")["ok"] is False
    assert lv.call_args.args == ("openai", "k")
