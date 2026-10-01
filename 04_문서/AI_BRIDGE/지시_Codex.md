# 지시 — Codex

> 이 파일은 Cowork 가 쓴다. Codex 는 읽고 수행만 한다(수정 금지 — 이견은 보고 파일 '판단 요청'에).
> 사용자 입력은 "지시 파일 읽고 수행해" 한 줄이다. 아래 `상태`를 먼저 본다.

| 항목 | 값 |
|---|---|
| 작업 ID | **T-CX03** |
| 상태 | **대기** (대기=착수 보고 후 확인받고 시작 / 진행중=이어서 / 완료=아무것도 하지 말고 "T-CX03 완료 상태"라고 답) |
| 작업 | v2.1 청크 538개를 새 컬렉션 `safety_manual_v2` 에 적재하고 적재 결과를 검증 |
| 권장 모델 | GPT-6.1 Sol / 추론 Medium (정해진 스크립트 실행·검증 위주) |
| 병렬 상대 | Claude Code — 커밋·push·재채점 후 Ollama 측정. **이번 작업은 Ollama(bge-m3)를 쓰므로 Claude Code 측정과 동시에 돌면 안 된다.** 사용자가 순서를 조율한다. `console_ui.py`, `01_현행코드/eval/`, `지시_ClaudeCode.md`, `보고_ClaudeCode.md` 는 열지 않는다 |
| 이전 작업 | T-CX02(v2.1 dry) 완료 — Cowork 재계산 일치: 538청크, 평균 425.48/최대 500자, 공백 소실 25(전부 B-M-37), 개요·각주·쪽번호 잔존 0 |

## 공통 규칙 (모든 작업)

1. 착수 전 `CLAUDE.md`/`AGENTS.md` §10 '착수 보고' 6항목 형식으로 보고하고, 사용자가 "확인"·"진행"이라고 답한 뒤에만 시작한다.
2. 기존 파일 수정은 지시가 명시한 것만. 동결 파일은 열지 않는다.
3. git 쓰기 명령 금지. 읽기는 `git --no-optional-locks` 로만. 커밋은 Claude Code 가 한다.
4. OUTBOX 쓰기 금지. 판단 요청은 보고 파일에 쓴다.
5. Ollama·DB 는 지시가 허용할 때만. 애매하면 보수적으로(지우지 않는 쪽) 처리하고 후보로 남긴다.
6. 실행·검증하지 않은 것을 완료라고 쓰지 않는다. 안 돌렸으면 "실행 안 함".

## 보고 규칙

- 끝나거나, 막혀서 멈추거나, 세션이 끊기기 직전이면 `04_문서/AI_BRIDGE/보고_Codex.md` 를 **덮어쓴다.** 형식은 `04_문서/설계/RAG_LLM_고도화_1001/보고/_양식.md`. 첫 줄에 작업 ID.
- 마지막에 채팅에 "보고 작성 완료: T-CX03" 한 줄.

---

## T-CX03 상세

### 목적
v2.1 청크를 실제 벡터 DB 에 넣어, 다음 단계에서 Claude Code 가 같은 평가셋으로 기존 DB(v1) 와 새 DB(v2) 를 비교할 수 있게 한다. **운영 컬렉션 `safety_manual` 은 건드리지 않는다** — 화면은 계속 v1 을 쓴다.

### 미리 필요한 것 (착수 보고 5번에 적을 것)
- Docker Desktop 켜짐(`docker ps` 에 `radar-guard-db`), Ollama 에 `bge-m3`.
- Claude Code 가 Ollama 측정 중이 아님(사용자 확인).

### 할 일
1. `python 01_현행코드\sop_ingest_v2.py` dry 를 한 번 더 돌려 `chunks_v2.jsonl` SHA 가 T-CX02 보고 값(`651BEE29…16D704`)과 같은지 확인. 다르면 멈추고 보고.
2. 적재 전 DB 상태 기록(읽기 전용): 컬렉션 목록과 컬렉션별 청크 수. `safety_manual` 청크 수(기대 640)를 적어 둔다.
3. `--apply` 실행 → `safety_manual_v2` 에 적재. 이미 존재하면 덮어쓰지 말고 멈춰서 보고(사용자 결정).
4. 적재 후 검증(읽기 전용):
   - `safety_manual_v2` 청크 수 = 538, 카테고리별 수가 report.md 와 일치
   - 임베딩 차원 1024, `page`·`doc_id`·`category`·`ingest_version="v2"` 메타데이터 누락 0
   - **`safety_manual` 청크 수가 적재 전과 동일(640)** — 운영 DB 무변경 증명
   - 검색 맛보기: 아래 3개 질의를 `safety_manual` 과 `safety_manual_v2` 양쪽에 같은 카테고리 필터로 `similarity_search(k=2)` 해서 1·2등 `doc_id`·`page`·본문 앞 120자를 나란히 기록. 판정은 하지 않는다(Claude Code 평가셋이 판정한다).
     - fall_detected / `00_응급처치_공통`: `radar_core.SOP_QUERY['fall_detected']`
     - electric_shock_risk / `01_감전_대응`: `radar_core.SOP_QUERY['electric_shock_risk']`
     - pinching / `02_협착_예방`: `radar_core.SOP_QUERY['pinching']`
     질의문·카테고리 값은 코드에서 import 해 쓴다(하드코딩 금지).
5. 적재 소요 시간 기록.

### 되돌리는 법 (보고에 그대로 적을 것)
`safety_manual_v2` 컬렉션만 삭제하면 원상복구. 삭제 명령은 실행하지 말고 문구로만 남긴다.

### 하지 말 것
- `safety_manual` 쓰기·삭제, `radar_core.py`·`console_ui.py`·`radar_common.py` 수정.
- `sop_ingest_v2.py` 로직 변경(버그로 적재가 실패하면 고치지 말고 오류 원문과 함께 멈춰서 보고).
- git 쓰기.

### 보고
`04_문서/AI_BRIDGE/보고_Codex.md` 를 덮어쓴다(첫 줄 `T-CX03`). 이번부터 이 위치만 쓴다.
