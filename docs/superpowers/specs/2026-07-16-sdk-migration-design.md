# Phase 2: google-genai SDK 마이그레이션 설계

날짜: 2026-07-16 / 상태: 승인됨 (전체 페이즈 일괄 승인)

## 목적

`google.generativeai`(지원 종료, FutureWarning 발생) → 신규 `google-genai` SDK 전환.
부수 효과: 전역 `genai.configure()` 제거 — 클라이언트가 호출별 생성되어
스레드 안전성 개선 (Phase 1 병렬화와 시너지).

## 설계

- 어댑터 `_make_model(api_key, system_instruction, temperature, json_mode=False)`:
  `genai.Client` + `GenerateContentConfig`를 감싸고 `.generate_content(prompt)`
  메서드를 노출 — 기존 `_gemini_text`/`_gemini_json` 재시도 래퍼와
  호출부 시그니처를 그대로 유지 (최소 diff).
- 7개 Gemini 함수: `genai.configure + GenerativeModel` 3~9줄 →
  `_make_model(...)` 1줄로 교체.
- requirements.txt: `google-generativeai` → `google-genai>=1.0.0`.
- 재시도 대상 오류 매칭은 메시지 문자열 기반이라 SDK 교체와 무관하게 동작.

## 검증

1. 재시도 단위 테스트 6종 재실행 (스텁 — 어댑터 인터페이스 불변이라 그대로 통과해야 함).
2. 실 API 병렬 검토 테스트 재실행 (신 SDK 경유).
3. FutureWarning 사라짐 확인.
