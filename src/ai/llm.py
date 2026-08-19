# -*- coding: utf-8 -*-
"""멀티 LLM 프로바이더 추상화 — Gemini / OpenAI / Anthropic 공통 JSON 호출.

기존 코드가 requests 기반(SDK 미사용)이고 PyInstaller로 패키징되므로,
세 프로바이더 모두 raw HTTP(requests)로 통일한다. 각 프로바이더의 구조화 출력
방식만 다르게 처리하고, 결과는 항상 파싱된 dict로 돌려준다.

complete_json(provider, api_key, model, prompt, schema?) -> {ok, data?/error?, model_error?}
  - schema: Gemini 방언 스키마(대문자 타입). None이면 JSON 모드만(스키마 강제 없음).
list_models(provider, api_key) -> {ok, models?/error?}
validate_key(provider, api_key) -> {ok, models?/error?}
"""
import json
import time

import requests

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_BASE = "https://api.openai.com/v1"
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

# API 미조회 시 폴백 기본 모델 목록 (사용자는 '직접 입력'으로 임의 모델 사용 가능)
DEFAULT_MODELS = {
    "openai": ["gpt-5.1", "gpt-5", "gpt-4.1", "gpt-4o"],
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
}

PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "openai": "OpenAI (GPT)",
    "anthropic": "Anthropic (Claude)",
}


# ---------- 공통 유틸 ----------

def _extract_json(text: str):
    """코드펜스/잡텍스트가 섞여도 첫 JSON 오브젝트를 추출해 파싱."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        t = t[start:end + 1]
    return json.loads(t)


_JSON_TYPE = {"OBJECT": "object", "STRING": "string", "ARRAY": "array",
              "INTEGER": "integer", "NUMBER": "number", "BOOLEAN": "boolean"}


def gemini_to_jsonschema(s):
    """Gemini 방언(대문자 타입) → 표준 JSON Schema (OpenAI/Anthropic strict 용).

    객체에는 additionalProperties:false + required 전체를 강제(Anthropic 요건).
    동적 키 오브젝트(additionalProperties가 스키마)는 표현 불가 → None 반환.
    """
    if not isinstance(s, dict):
        return s
    t = s.get("type")
    # 동적 키 맵은 strict json_schema로 표현 불가
    if isinstance(s.get("additionalProperties"), dict):
        return None
    out = {}
    if t:
        out["type"] = _JSON_TYPE.get(t, str(t).lower())
    if s.get("description"):
        out["description"] = s["description"]
    if "enum" in s:
        out["enum"] = s["enum"]
    if out.get("type") == "object":
        props = s.get("properties", {}) or {}
        conv = {k: gemini_to_jsonschema(v) for k, v in props.items()}
        if any(v is None for v in conv.values()):
            return None
        out["properties"] = conv
        out["required"] = s.get("required", list(props.keys()))
        out["additionalProperties"] = False
    if out.get("type") == "array" and "items" in s:
        item = gemini_to_jsonschema(s["items"])
        if item is None:
            return None
        out["items"] = item
    return out


def _err(msg, **kw):
    return {"ok": False, "error": str(msg), **kw}


# ---------- Gemini ----------

def _gemini_json(api_key, model, prompt, schema, timeout, temperature=0.2, images=None):
    gen = {"temperature": temperature, "responseMimeType": "application/json"}
    if schema:
        gen["responseSchema"] = schema
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": "image/png", "data": b64}} for b64 in (images or [])]
    payload = {"contents": [{"parts": parts}], "generationConfig": gen}
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    r = requests.post(url, json=payload, timeout=timeout,
                      headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
    if r.status_code == 200:
        try:
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return {"ok": True, "data": _extract_json(text)}
        except Exception as e:
            # 모델이 잘린/비정형 응답을 준 경우 — 일시 현상일 수 있어 재시도 대상
            return _err(f"응답 파싱 실패: {e}", retryable=True)
    if r.status_code == 404 or (r.status_code == 400 and "not found" in r.text.lower()):
        return _err(_model_msg("Gemini", model), model_error=True, status=r.status_code)
    if r.status_code in (400, 401, 403):
        return _err(_key_msg(r.status_code, r.text), status=r.status_code)
    if r.status_code == 429:
        # 응답의 RetryInfo가 권하는 대기 시간을 재시도 힌트로 전달
        retry_after = 0
        try:
            for d in r.json().get("error", {}).get("details", []):
                if "RetryInfo" in d.get("@type", ""):
                    retry_after = int(float(d.get("retryDelay", "0s").rstrip("s")))
        except Exception:
            pass
        return _err("무료 사용량 한도 초과(429). 잠시 후 다시 시도하세요.",
                    status=429, retry_after=retry_after)
    if r.status_code == 503:
        return _err("Gemini 서버가 일시적으로 과부하 상태입니다(503). 잠시 후 다시 시도하세요.",
                    status=503)
    return _err(f"Gemini 오류 (HTTP {r.status_code}): {r.text[:160]}", status=r.status_code)


# ---------- OpenAI ----------

def _openai_json(api_key, model, prompt, schema, timeout, images=None):
    url = f"{OPENAI_BASE}/chat/completions"
    user_content = prompt
    if images:
        user_content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}} for b64 in images]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "너는 오직 JSON 객체만 출력한다. 설명·코드펜스 금지."},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }
    # schema=None·변환 불가(동적 키 등)는 기존 json_object. 텍스트 호출이 깨지지 않게.
    std = gemini_to_jsonschema(schema) if schema else None
    if std:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": std, "strict": False},
        }
    r = requests.post(url, json=payload, timeout=timeout,
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"})
    if r.status_code == 200:
        content = r.json()["choices"][0]["message"]["content"]
        return {"ok": True, "data": _extract_json(content)}
    if r.status_code == 404 or (r.status_code == 400 and "model" in r.text.lower()
                                and ("not" in r.text.lower() or "exist" in r.text.lower())):
        return _err(_model_msg("OpenAI", model), model_error=True, status=r.status_code)
    if r.status_code in (401, 403):
        return _err(_key_msg(r.status_code, r.text), status=r.status_code)
    return _err(f"OpenAI 오류 (HTTP {r.status_code}): {r.text[:160]}", status=r.status_code)


# ---------- Anthropic ----------

def _anthropic_json(api_key, model, prompt, schema, timeout, images=None):
    url = f"{ANTHROPIC_BASE}/messages"
    user_content = prompt
    if images:
        user_content = [{"type": "image",
                         "source": {"type": "base64", "media_type": "image/png",
                                    "data": b64}} for b64 in images]
        user_content.append({"type": "text", "text": prompt})
    payload = {
        "model": model,
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": user_content}],
    }
    std = gemini_to_jsonschema(schema) if schema else None
    if std:
        # 구조화 출력 강제 (output_config.format — 유효 JSON 보장)
        payload["output_config"] = {"format": {"type": "json_schema", "schema": std}}
    else:
        # 스키마 표현 불가(동적 키 등) → 프롬프트로 JSON 전용 출력 지시
        payload["system"] = "오직 유효한 JSON 객체 하나만 출력한다. 설명·코드펜스·여는말 금지."
    r = requests.post(url, json=payload, timeout=timeout,
                      headers={"x-api-key": api_key,
                               "anthropic-version": ANTHROPIC_VERSION,
                               "content-type": "application/json"})
    if r.status_code == 200:
        data = r.json()
        text = next((b.get("text", "") for b in data.get("content", [])
                     if b.get("type") == "text"), "")
        return {"ok": True, "data": _extract_json(text)}
    if r.status_code == 404:
        return _err(_model_msg("Anthropic", model), model_error=True, status=r.status_code)
    if r.status_code in (401, 403):
        return _err(_key_msg(r.status_code, r.text), status=r.status_code)
    return _err(f"Anthropic 오류 (HTTP {r.status_code}): {r.text[:160]}", status=r.status_code)


def _model_msg(label, model):
    return (f"선택한 {label} 모델 '{model}'을(를) 사용할 수 없습니다(종료·오타·미지원 모델).\n"
            "설정 화면에서 모델을 변경하거나 목록을 새로고침하세요.")


def _key_msg(code, text):
    return (f"API 키 또는 요청 오류 (HTTP {code}). API 키가 올바른지 확인하세요.\n{text[:160]}")


# ---------- 비전(이미지→텍스트) ----------

def complete_text(provider, api_key, model, prompt, images=None, timeout=120):
    """이미지(base64 PNG 목록) + 프롬프트 → 일반 텍스트 응답 {ok, text}.

    스캔 PDF 전사(OCR 폴백)용 — JSON 강제 없음. 세 프로바이더 모두
    base64 이미지 입력을 지원한다(선행 조사 F007 계열 확인 사실)."""
    if not api_key:
        return _err(f"{PROVIDER_LABELS.get(provider, provider)} API 키가 없습니다. 설정에서 입력하세요.")
    images = images or []
    try:
        if provider == "gemini":
            parts = [{"text": prompt}] + [
                {"inline_data": {"mime_type": "image/png", "data": b64}} for b64 in images]
            payload = {"contents": [{"parts": parts}],
                       "generationConfig": {"temperature": 0.0}}
            r = requests.post(f"{GEMINI_BASE}/models/{model}:generateContent",
                              json=payload, timeout=timeout,
                              headers={"x-goog-api-key": api_key,
                                       "Content-Type": "application/json"})
            if r.status_code == 200:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                return {"ok": True, "text": text}
        elif provider == "openai":
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}} for b64 in images]
            payload = {"model": model,
                       "messages": [{"role": "user", "content": content}]}
            r = requests.post(f"{OPENAI_BASE}/chat/completions", json=payload,
                              timeout=timeout,
                              headers={"Authorization": f"Bearer {api_key}",
                                       "Content-Type": "application/json"})
            if r.status_code == 200:
                return {"ok": True, "text": r.json()["choices"][0]["message"]["content"]}
        elif provider == "anthropic":
            content = [{"type": "image",
                        "source": {"type": "base64", "media_type": "image/png",
                                   "data": b64}} for b64 in images]
            content.append({"type": "text", "text": prompt})
            payload = {"model": model, "max_tokens": 8000,
                       "messages": [{"role": "user", "content": content}]}
            r = requests.post(f"{ANTHROPIC_BASE}/messages", json=payload,
                              timeout=timeout,
                              headers={"x-api-key": api_key,
                                       "anthropic-version": ANTHROPIC_VERSION,
                                       "content-type": "application/json"})
            if r.status_code == 200:
                text = next((b.get("text", "") for b in r.json().get("content", [])
                             if b.get("type") == "text"), "")
                return {"ok": True, "text": text}
        else:
            return _err(f"알 수 없는 AI 프로바이더: {provider}")
    except requests.Timeout:
        return _err(f"응답 시간 초과({timeout}초).")
    except Exception as e:
        return _err(f"네트워크 오류: {e}")
    return _err(f"{PROVIDER_LABELS.get(provider, provider)} 오류 (HTTP {r.status_code}): "
                f"{r.text[:160]}", status=r.status_code)


# ---------- 공개 API ----------

_RETRY_STATUS = {429, 500, 502, 503, 529}


def complete_json(provider, api_key, model, prompt, schema=None, timeout=60,
                  temperature=0.2, images=None):
    """프로바이더 공통 JSON 응답. 일시 오류(429/5xx·파싱 실패)는 대기 후 재시도.

    images=None(텍스트 전용 — 견적서·회의록): 최대 3회 시도. 기존과 동일.
    images가 있으면 비전 입력을 넣고 재시도 1회(총 2회)로 제한한다.
    OpenAI는 schema가 변환되면 response_format json_schema로 강제하고,
    schema=None·변환 불가면 기존 json_object를 유지한다.

    오류 dict에는 status(HTTP 코드)가 실리며, 재시도 판단은 status/retryable로만 한다.
    429 응답이 retry_after(초)를 주면 그 시간(최대 30초)만큼 대기한다."""
    if not api_key:
        return _err(f"{PROVIDER_LABELS.get(provider, provider)} API 키가 없습니다. 설정에서 입력하세요.")
    last = None
    # 비전은 대형 PNG 재전송을 1회로 제한. 텍스트(images=None)는 3회 유지.
    attempts = 2 if images else 3
    for attempt in range(attempts):
        try:
            if provider == "gemini":
                r = _gemini_json(api_key, model, prompt, schema, timeout, temperature, images)
            elif provider == "openai":
                r = _openai_json(api_key, model, prompt, schema, timeout, images)
            elif provider == "anthropic":
                r = _anthropic_json(api_key, model, prompt, schema, timeout, images)
            else:
                return _err(f"알 수 없는 AI 프로바이더: {provider}")
        except requests.Timeout:
            return _err(f"응답 시간 초과({timeout}초).")
        except Exception as e:
            return _err(f"네트워크 오류: {e}")

        if r.get("ok"):
            return r
        last = r
        if (r.get("status") in _RETRY_STATUS or r.get("retryable")) and attempt < attempts - 1:
            if r.get("status") in _RETRY_STATUS:        # 파싱 실패는 즉시 재시도
                delay = max(8 * (attempt + 1), int(r.get("retry_after") or 0))
                time.sleep(min(delay, 30))
            continue
        return r          # 모델/키/요청 오류 — 재시도 무의미
    return last or _err("AI 호출 실패")


def list_models(provider, api_key, timeout=20):
    """프로바이더별 사용 가능 모델 목록 (드롭다운 새로고침용)."""
    if not api_key:
        return _err("먼저 API 키를 저장하세요.")
    try:
        if provider == "openai":
            r = requests.get(f"{OPENAI_BASE}/models", timeout=timeout,
                             headers={"Authorization": f"Bearer {api_key}"})
            if r.status_code != 200:
                return _err(f"HTTP {r.status_code}: {r.text[:160]}")
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            ids = [i for i in ids if i.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))]
            ids.sort(reverse=True)
            return {"ok": True, "models": ids or DEFAULT_MODELS["openai"]}
        if provider == "anthropic":
            r = requests.get(f"{ANTHROPIC_BASE}/models", timeout=timeout,
                             headers={"x-api-key": api_key,
                                      "anthropic-version": ANTHROPIC_VERSION})
            if r.status_code != 200:
                return _err(f"HTTP {r.status_code}: {r.text[:160]}")
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            ids = [i for i in ids if i]
            return {"ok": True, "models": ids or DEFAULT_MODELS["anthropic"]}
        return _err(f"모델 목록 미지원 프로바이더: {provider}")
    except Exception as e:
        return _err(str(e))


def validate_key(provider, api_key, timeout=20):
    res = list_models(provider, api_key, timeout)
    if res.get("ok"):
        return {"ok": True, "models": res["models"][:12]}
    return res
