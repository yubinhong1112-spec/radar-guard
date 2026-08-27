# HANDOFF

## State

- Updated: 2026-08-27 · Codex
- Branch: main
- Commit: ROI·관제 AI 표시 계층 변경 커밋 직전
- Working tree: 이번 세션 추적 파일만 커밋하며 사용자 문서·백업 JSON·분석 출력은 제외한다.

## Current objective

영상 시연에서 위험구역 ROI와 상태 기반 관제 AI가 판정 경로와 독립적으로 동작하는 기준선을 유지한다.

## Verified baseline

- pyflakes 경고 0건 · UI 결함 재발 60항목 실패 0건 · 4해상도 잘림·겹침 0건.
- 실데이터 재생 73항목 실패 0건 · 평면도 경보 흐름 16항목 실패 0건 · 젯슨 안전성 102항목 실패 0건.
- 실화면 SOP 답변 27.93초 · UI 하트비트 최대 지연 367ms 1회이며 AI 미사용 기준은 409ms 1회다.
- 젯슨 sender SHA-256 `afb5470a1e9e77ee87f4b38fc45ea5dbaaf93cebb5786a2aca8361e421361ac8`이며 parser·sender·사용자 UI와 PC 관제 UI 연결을 확인했다.

## Next actions

1. 실장비 warning·critical 경보에서 ROI가 주황·빨강으로 바뀌고 점군을 가리지 않는지 영상으로 확인한다.
2. 승원·성준·민석·재국의 정상 서기·걷기·일반 웅크리기 자료를 동일 게이트에서 수집한다.
3. 긴 SOP 질의 27.93초가 시연 흐름에 부담이면 생성 길이 축소 여부를 사용자 승인으로 결정한다.

## Blockers / unknowns

- 신규 4명의 정상 동작 대조 자료가 없어 R2 협착 판정의 사람 일반화 오탐률을 알 수 없다.

## Acceptance

- ROI와 관제 AI는 젯슨 판정값을 읽기만 하며 판정·차단·임계값에 영향을 주지 않는다.
- 상태·다음 조치·재투입 문의는 LLM 없이 즉시 응답하고, 긴 생성 중에도 UI 이벤트 루프가 유지된다.
