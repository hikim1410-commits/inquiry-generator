# -*- coding: utf-8 -*-
"""pywebview js_api — UI와 엔진/저장소/HWP워커/AI를 잇는 단일 계약 지점.

모든 메서드는 JSON 직렬화 가능한 dict를 주고받는다.
계산은 전부 Python 엔진에서 수행 (JS 산수 금지 원칙).

도메인별 모듈(core/settings/quote/minutes/receipt/system)의 믹스인을 Api 하나로
조립한다 — pywebview에 노출되는 공개 표면은 분할 전과 동일.
"""
from src.store import config_store as cs
from src.ai import engine as ai_engine
from src.ai import llm as ai_llm

from ._common import (_AI_PROMPT_MAX, _DEPRECATED_MODELS, _display, _err,
                      _file_dialog, _num_or_none, _parse_expenses,
                      _parse_labor, _parse_quote)
from .core import ApiCore
from .settings import SettingsApi
from .quote import QuoteApi
from .minutes import MinutesApi
from .receipt import ReceiptApi
from .system import SystemApi


class Api(SettingsApi, QuoteApi, MinutesApi, ReceiptApi, SystemApi, ApiCore):
    """도메인 믹스인 조립체 — 메서드 구현은 각 도메인 모듈 참조."""
