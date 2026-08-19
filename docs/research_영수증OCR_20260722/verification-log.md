# 팀리드 재검증 이력 (2026-07-22)

산출물이 고객용 PDF가 아닌 내부 PRD이므로, 스킬 게이트를 축소 적용:
- 적용: G0 preflight / 병렬 조사(A·B·C, sonnet·background) / G1 raw 보존 / [2] 팀리드 재검증(PRD 인용 fact 전건) / 무출처·불일치 fact 폐기·기권
- 생략: G2 source_capture, G3 manifest/verify_facts, G4/G5 PDF 게이트

## 재검증 결과 (WebFetch 원문 재열람, 2026-07-22)
| Fact | 대상 | 판정 |
|---|---|---|
| F001 | invoice2data OCR 백엔드·정규식 템플릿·MIT | CONFIRMED |
| F002 | CORD = 인도네시아어 영수증 | CONFIRMED |
| F003 | Upstage 공식 가격($0.01~0.06/페이지, VAT 별도) | CONFIRMED |
| F004 | GCV 공식 가격($1.50/1k유닛, 첫 1k 무료) | CONFIRMED |
| F005 | Receipt Wrangler OCR+AI, 프로바이더 4종 | CONFIRMED |
| F006 | TaxHacker 멀티LLM·프롬프트 커스텀·항목분할·MIT | CONFIRMED |
| F007 | hovduc 비전/텍스트 2모드·비용·오프라인 불가 | CONFIRMED |
| F008 | instructor 합계검증 validator·1/2 파싱 실패 | CONFIRMED |
| F009 | CLOVA Receipt(KR) 도메인 공식 존재 | CONFIRMED (가이드 문서로 확인) |
| F010 | CLOVA 요금 구체 금액 | UNRESOLVED — 2차 3건 불일치, [확인 필요] |
| F011 | Donut 2024-07 이후 정체·MIT | CONFIRMED (GitHub API) |
| F012 | devanshsrajput 하이브리드 파이프라인·신뢰도 플래그 | CONFIRMED |
| F013 | OSS 지형 종합(스타수·정체 시점 등) | PENDING(워커 보고) — 배경 맥락용, 단독 수치 인용 금지 |
| F014 | PDF→이미지 변환 동일 파이프라인 패턴 | CONFIRMED(워커 원문 인용 일치) |
| F015 | LLM 비전 정확도 85~94% 수치 | DISCARDED — 단일 비공식 벤치마크 |

## 실패·대체 기록
- ncloud.com 제품 페이지: WebFetch로 본문 미획득(JS 렌더링) → guide.ncloud-docs.com/docs/clovaocr-document 로 대체 확인 성공
- 워커 B: firecrawl 크레딧 소진(402) → WebSearch+WebFetch로 전환 수행
- 워커 C 폐기 3건: kkdai/linebot-receipt-gemini(세부 미확인), IAmTomShaw/receipt-vision(README 404), Akaunting 소스 미열람(2차 출처만)
