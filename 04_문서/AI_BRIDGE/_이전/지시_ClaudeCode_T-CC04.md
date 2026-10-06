# 지시 — Claude Code

> 이 파일은 Cowork 가 쓴다. Claude Code 는 읽고 수행만 한다(수정 금지 — 이견은 보고 파일 '판단 요청'에).
> 사용자 입력은 "지시 파일 읽고 수행해" 한 줄이다. 아래 `상태`를 먼저 본다.

| 항목 | 값 |
|---|---|
| 작업 ID | **T-CC04** — 개선계획 0단계(계측 정비) |
| 상태 | **대기** (대기=착수 보고 후 확인받고 시작 / 진행중=이어서 / 완료=아무것도 하지 말고 "T-CC04 완료 상태"라고 답) |
| 계획 문서 | `04_문서/설계/RAG_LLM_고도화_1001/개선계획_1003_1021.md` (문제 ID A~L 은 여기 기준) — 먼저 읽는다 |
| 권장 모델 | Opus 5.5 |
| 병렬 상대 | Codex T-CX05(`safety_manual_v2` 삭제 후 재적재, Ollama bge-m3 사용, 약 3분). **사용자가 "T-CX05 끝남"이라고 하기 전에는 Ollama·DB 를 쓰는 실행(§4)을 하지 않는다.** 코드 작성(§1~3)은 그 전에 해도 된다. `sop_ingest_v2.py`·`tmp/ingest_v2/`·`보고_Codex.md` 는 열지 않는다 |
| 이전 작업 | T-CC03 완료(`f07b9f1`, `74b7fc7`). 지시: `_이전/지시_ClaudeCode_T-CC03.md` |
| 우선순위 | 이 파일 > `HANDOFF.md` |

## 공통 규칙

1. 착수 전 `CLAUDE.md` §10 '착수 보고' 6항목 → 사용자 "진행" 후 시작.
2. 동결 해시: CLAUDE.md 표 대조 + 시작==종료.
3. `git add -A`/`git add .` 금지. 경로 지정 커밋.
4. 실행·검증하지 않은 것을 완료라고 쓰지 않는다.
5. 지시서에 없는 변형·측정을 즉석 추가하지 않는다. 필요하면 '판단 요청'에.
6. **이번 작업은 제품 코드(`console_ui.py`·`radar_core.py`·`radar_common.py`·`facility.py`)를 한 줄도 바꾸지 않는다.** 측정은 `eval/` 안에서 기존 함수를 호출·인자 주입으로 한다. 제품 코드 변경이 꼭 필요하면 멈추고 판단 요청.
7. 측정 전 가용 메모리(`\Memory\Available MBytes`) ≥ 2000 확인. 미달이면 시작하지 말고 멈춰 보고.

## 왜 이 작업인가

10/03 확인: 경보 SOP 6종(`SopEngineV2.PREPARE_EVENTS`)은 앱 시작 때 `qwen2.5:3b`(PREPARE_MODEL, console_ui.py 2831)가 v1 매뉴얼 발췌 700자로 4줄을 생성하고, `_cacheable_sop`(2978)는 **형식만** 검사한다. 내용을 잰 적이 없다(문제 A). 사전 생성이 실패한 유형은 경보 순간 `gemma2:2b` 가 실측값과 함께 스트리밍 생성한다(2915). 이 경로가 심사에서 가장 먼저 보이는 화면인데 평가셋 30문항에 없다. 또 모델 통일(문제 B)을 결정하려면 두 경로를 같은 기준으로 재야 한다. 이번 작업은 **그 자를 만드는 것**이고, 결정은 다음 단계다.

## 1. 경보 SOP 평가 도구 `01_현행코드/eval/sop_eval.py` (신규)

- 인자: `--model {qwen2.5:3b-instruct-q4_K_M, gemma2:2b}` · `--collection {safety_manual, safety_manual_v2}` · `--facts {none, sample}` · `--tag`.
- 대상: `PREPARE_EVENTS` 6종 전부.
- 절차(제품 코드와 같은 함수를 쓴다): 검색은 `core.search_sop_documents(vs, ev, core.SOP_QUERY..., core.EVENT_CATEGORY...)` 를 `--collection` 으로 만든 `vs` 로 호출(=`SopEngineV2._search` 와 같은 순서, 컬렉션만 인자). 생성은 `SopEngineV2._gen_facts(ev, ctx, facts)` 를 호출하되 모델은 `SopEngineV2.PREPARE_MODEL`(facts 없음)·`core.LLM_MODEL`(facts 있음)을 **스크립트 안에서만** 임시 교체해 주입한다. 옵션(temperature 0, num_predict 160, num_ctx 1536)은 제품 그대로.
- `--facts sample`: 이벤트별 실측값 예시는 `eval/chat_eval_set.json` 의 alert evidence 가 있으면 그것을, 없으면 `events_ui_animation_confirmed_0824.jsonl` 에서 해당 유형 첫 줄을 쓴다. 출처를 결과에 적는다.
- 기록(jsonl, 이벤트당 1줄): 사용 청크 **전문**·`chunk_id`(v2)/`source_file`·`page`, 생성문 전문, `_cacheable_sop` 통과 여부, 줄별 글자 수, elapsed·load·eval 시간, eval_count, 가용 메모리(시작·끝).
- 채점은 아직 하지 않는다 — 정답 기준(`eval/sop_eval_set.json`)은 Cowork 가 초안을 쓰고 사용자가 승인한다(계획 0-b). 파일이 있으면 `must_include`(조치 핵심어)·`must_not_include`(금지 표현)로 채점하고, 없으면 채점 열을 "기준 미승인"으로 둔다.

## 2. 챗봇 평가 보강 `01_현행코드/eval/chat_eval.py` (수정)

- `--temperature` 인자 추가. 기본값은 지금 값(0.2) 유지 — 기존 결과와 비교가 깨지지 않게.
- 검색 본문 `context` 를 jsonl 에 기록(T-CC03 발견 4).
- preflight 청크 수를 컬렉션별로 출력(T-CC03 판단 요청 5).
- **사실 오류 검사 열 `fact_errors` 를 별도로 추가**(문제 J). 기존 `must_not_include` 는 건드리지 않는다(이전 점수 유지). 첫 항목: OS-06 계열 — 답에 `영상을 분석`·`영상 분석`·`CCTV 영상을 활용` 이 있으면 사실 오류. 목록은 `eval/fact_error_terms.json` 으로 분리해 Cowork 가 늘릴 수 있게.

## 3. 조각 선택 덤프 `01_현행코드/eval/chunk_select_dump.py` (신규)

- `SOP_RESPONSE_SOURCE` 의 (이벤트, 카테고리) 쌍마다, 고정 출처 문서의 **모든 후보 청크**를 `SOP_RESPONSE_TERMS` 점수와 함께 순위대로 나열하고, 실제 선택되는 상위 N개(코드와 같은 규칙)를 표시한다.
- 출력: `eval/results/chunk_select_<컬렉션>_<날짜>.md` — 후보마다 순위·점수·chunk_id(또는 page)·본문 앞 200자.
- 목적: 문제 E(EA-05 감전 질문에 화상 절) 원인 확인과, 다음 단계 '사람이 검토한 chunk_id 고정'의 재료.

## 4. 실행 (사용자가 "T-CX05 끝남" 이라고 한 뒤에만)

순서와 각 조건 사이 `ollama stop`:
1. `chunk_select_dump` — v1, v2 (Ollama 불필요, DB 만)
2. `sop_eval` 현행 운영 조건: `--model qwen2.5:3b… --collection safety_manual --facts none` (= 지금 시연에 뜨는 것)
3. `sop_eval --model gemma2:2b --collection safety_manual --facts sample` (= 사전 생성 실패 시 뜨는 것)
4. `chat_eval` baseline `--temperature 0` (재현성 확인용 1회. 같은 조건 2회 돌려 답이 바이트 동일한지 30문항 비교)

모델×컬렉션 4조건 전체 비교는 다음 단계(T-CC05)에서 한다 — 이번에는 위 4개만.

## 5. 끝나기 전

1. 제품 코드 무변경 확인(`git diff --stat` 에 console_ui·radar_core·radar_common·facility 없음).
2. ui-verify 4종·`test_chat_prompt_same.py`·`test_sop_collection_filter.py` 통과(제품 코드 무변경이라 같은 결과여야 한다).
3. 동결 해시 시작==종료. `sync_agent_docs.py --check`·`validate_ai_bridge.py` 0.
4. 커밋·push. 0단계로 Cowork 변경분(`04_문서/설계/RAG_LLM_고도화_1001/개선계획_1003_1021.md`, `04_문서/AI_BRIDGE/COWORK_인계.md`, `04_문서/AI_BRIDGE/지시_ClaudeCode.md`, `04_문서/AI_BRIDGE/_이전/지시_ClaudeCode_T-CC03.md`, `04_문서/AI_BRIDGE/_대기/T-CX05_Codex_프롬프트.md`)을 먼저 따로 커밋한다.
5. 보고 `보고_ClaudeCode.md` 덮어쓰기(양식 `04_문서/설계/RAG_LLM_고도화_1001/보고/_양식.md`). 핵심 표: 경보 SOP 6종 × 2조건의 생성문 전문(표 아래 원문 그대로)·형식 통과·시간, 재현성 결과(30문항 중 바이트 동일 수), 조각 선택 덤프 요약(이벤트별 1위 청크가 무슨 절인지).
