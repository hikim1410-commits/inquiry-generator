# 영수증 OCR 기능 — GitHub 유사 프로젝트 조사 요약 (2026-07-22)

> 조사 방법: market-deep-research 스킬 축소 적용(내부 PRD용). 조사원 3축(sonnet) 병렬 →
> 팀리드가 PRD 인용 fact 전건 원문 재열람(WebFetch). F-ID는 facts.jsonl 참조.
> confirmed가 아닌 사실은 본문에서 그렇게 표시한다.

## 1. 결론 (Executive)

**권장 아키텍처: "이미지/PDF → (전처리) → 멀티프로바이더 LLM 비전 직접 추출 → 스키마 강제
JSON → 도메인 규칙 재검증(합계=항목합) → 사용자 검토·수정 UI → 정산 산출물(Excel/HWP) 반영".**

근거:
1. 실서비스 자가호스팅 경비앱 3곳(Receipt Wrangler, TaxHacker, firefly-receipt-scanner)이
   모두 "멀티프로바이더 LLM + 구조화 출력 + 사용자 검토" 구조를 프로덕션에서 사용 중 (F005, F006).
   이는 대상 앱이 이미 보유한 `src/ai/llm.py`(gemini/openai/anthropic JSON completion) 구조와 동형.
2. "이미지→LLM 비전 직접"과 "로컬 OCR 텍스트→LLM 후처리"는 양자택일이 아니라 같은 코드베이스의
   토글 옵션으로 공존하는 패턴이 확인됨 (F007). MVP는 비전 직접, 필요 시 텍스트 모드 추가 여지.
3. 전용 딥러닝 모델 자체 학습 경로는 비추천: Donut은 2024-07 이후 정체(F011)이고, 그 영수증
   데이터셋 CORD는 **인도네시아어**라 한국어에 못 쓴다(F002). 한국어 영수증용 공개 가중치는
   사실상 부재(워커 B 보고, F013).
4. 로컬 OCR 엔진(Tesseract/EasyOCR/PaddleOCR) 동봉은 PyInstaller 데스크톱 배포 부피·복잡도를
   키우는 반면, LLM 비전은 API 키만으로 동작(F007 비교표). 단점은 **오프라인 불가**(F007 명시).

## 2. OSS 지형 3갈래 (축 A)

| 갈래 | 대표 | 상태 | 시사점 |
|---|---|---|---|
| 템플릿·정규식 파서 | invoice2data (MIT, 활발) | OCR 백엔드 교체형 + YAML 템플릿 정규식 + AI fallback (F001) | 벤더별 템플릿 유지보수 부담 — 영수증(비정형)에 부적합 |
| 전용 딥러닝 모델 | Donut, InvoiceNet | 2024년 이후 정체 (F011, F013) | 자체 학습 경로 사장 추세 |
| OCR/LLM 하이브리드 | Sparrow(GPL 주의), Receipt Wrangler, TaxHacker | 2026년 현재 활발 | 현행 주류. LLM 구조화가 표준 |

## 3. 한국어 특화 수단 (축 B)

- **CLOVA OCR**: Document 타입에 **Receipt(KR) 특화 도메인 공식 존재** — 매장 정보·결제 내역·
  지불 방식·금액 추출 (F009, 공식 가이드 확인). 요금은 종량제라는 사실만 공식 확인, 구체 금액은
  2차 출처 3건 불일치로 **[확인 필요]** (F010).
- **Upstage**: 영수증 prebuilt 추출기 보유. 공식 가격 Information Extract $0.04~0.06/페이지,
  Document Parse $0.01~0.03, Document OCR $0.0015 (VAT 10% 별도) (F003).
- **Google Cloud Vision**: 범용 텍스트 검출만(월 1,000유닛 무료, 이후 $1.50/1,000유닛) —
  영수증 필드 구조화는 미제공, 별도 파싱 필요 (F004).
- 로컬 OSS(Tesseract kor/EasyOCR ko/PaddleOCR korean)는 한국어 지원하나 영수증 특화 가중치
  없음. 한국어 영수증 데이터셋은 UpstageAILab 대회 산출물(CC-BY-NC, 검출 전용)이 최선(워커 B, F013).

## 4. LLM 비전 실전 패턴 (축 C — 설계에 직접 반영할 것들)

- **검토·수정 UI는 전 사례 공통**: "Review and edit extracted data before creating
  transactions"(firefly-receipt-scanner), TaxHacker도 동일. → 기존 AI 초안 위저드
  (입력→검토 팝업→확정) 패턴 재사용.
- **환각 방지 = 스키마 + 도메인 규칙 재검증**: instructor 공식 예제가 Pydantic validator로
  "항목 합계=총액" 검증. 같은 예제에서 영수증 2개 중 1개는 파싱 실패 — LLM 비전은 완벽하지
  않으므로 검증·플래그가 필수 (F008).
- **필드별 신뢰도 플래그**: 0.70 미만 필드를 플래그해 사용자 주의 유도(F012 사례).
- **PDF는 이미지 변환 후 동일 파이프라인** (F014).
- **비용 감각**: Gemini Flash 계열 이미지당 ~$0.0004 수준(F007, 2025-10 기준 해당 저장소 주장).
  월 수백 장 처리에도 부담 없음. 단, "LLM 비전이 전통 OCR보다 X% 정확" 류 수치는 검증된 출처가
  없어 **폐기**(F015) — PRD에 인용 금지.
- **프롬프트 커스터마이징**(TaxHacker) — 기존 M16 기초 지침 편집 인프라와 동형(F006).

## 5. 대상 앱 통합 지점 (코드 확인 사실)

- `src/ai/llm.py complete_json()`은 **텍스트 전용** → 이미지(base64) 파라미터 확장 필요.
  3사 모두 API가 base64 이미지 입력 지원.
- 문서 유형 추가 선례 = 회의록: `src/minutes/` + `src/ai/minutes.py` + `src/api/minutes.py`
  + 사이드바 뷰. 영수증도 동일 골격.
- 파일 드롭존 인프라(`ApiCore._DROPZONES`), 키 DPAPI 저장, 프롬프트 편집(M16), Excel(openpyxl)
  모두 기존 자산 재사용 가능. PDF 입력은 이미지 변환(pypdfium2가 이미 검증 도구로 사용된 바 있음).

## 6. 미해결·확인 필요 (PRD에 그대로 반영할 것)

1. **CLOVA OCR 요금** — 콘솔 확인 필요 [확인 필요] (F010)
2. Upstage 영수증 prebuilt의 승인번호·카드번호 마스킹 필드 지원 여부 [확인 필요]
3. 내비온 실무의 영수증 필드 확정(상호/사업자번호/일시/공급가/부가세/합계/승인번호/카드번호 마스킹)
   — 정산 양식과 대조해 확정 필요
4. LLM 비전의 한국어 영수증 실측 정확도 — 공개 벤치마크 부재, 자체 파일럿(실영수증 N장)으로
   MVP 게이트 판정 권장
