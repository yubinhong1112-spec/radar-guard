# 보고 — Claude Code

| 항목 | 값 |
|---|---|
| 작업 ID | **T-CC06d** — gemma2 vs EXAONE 3.5 최종 비교 |
| 상태 | **중간 보고 — 측정 전**(재부팅 대기). 측정·블라인드 파일·최종 검증은 아직 하지 않았다 |
| 작성 | 2026-10-08 · Claude Code |
| 0단계 커밋 | `2fc284b` |

## 끝난 것

1. **0단계 커밋** `2fc284b` — 지시서가 정한 4개 파일만 경로 지정으로 커밋했다.
2. **`chat_eval.py` 추가분**(제품 코드 변경 0)
   - `cat` 만 있는 문항 파일 읽기(`type` 이 없으면 `cat` 을 쓴다).
   - K3 유출 표지(`k3_hits`) · K4 언어 순도(`k4_hits`) — 채점 행의 별개 열, 요약 md 에 문항 ID 와 걸린 표지만 적는다. 자체 검사 7항목 통과.
   - `--route-dump` — 측정과 같은 코드로 경로만 찍는다(LLM 생성 없음).
   - `--cond A|B` — 메타(`cond` · `incident_p50_sec` · `cut`)와 파일명에 적는다.
3. **route 덤프** — `01_현행코드/eval/results/routedump_final_highsec_1008_J3.md`
   - 28문항 중 고정 답 경로 **0문항**. 측정 대상 **28문항 전부**.
   - route 분포: other 9 · system 9 · incident 10.

## route 덤프에서 보인 것 (문항·코드는 고치지 않았다)

- **F-07**(낙상 판정 기준값을 바꿀 수 있나, K1) 과 **S-03**(움직이지 않는 사람은 왜 놓치나, K2) 이 경보 없이 `incident` 로 간다. 사고 대응 프롬프트(매뉴얼 발췌 + 조치 3개)를 받으므로 두 모델 모두 질문과 어긋난 답을 낼 가능성이 있다(추정 — 아직 재지 않았다).
- 그래서 `incident` p50 에는 K6 8문항 + F-07·S-03, 모두 10문항이 들어간다.

## 남은 것 (재부팅 뒤 새 세션에서)

1. 재부팅 → Docker Desktop 만 켠다(Claude 데스크톱·Codex·Edge 는 띄우지 않는다). `LastBootUpTime` 기록.
2. `docker start radar-guard-db` · `ollama serve`.
3. 조건 A — 모델마다 2회. 회차 전 메모리 확보(모델 내림 → 검색 호스트 종료 → 25초 대기).
   `python 01_현행코드\eval\chat_eval.py --variant J3 --model gemma2:2b --set 01_현행코드\eval\final_highsec_1008.json --cond A`
   (`exaone3.5:2.4b` 도 같은 명령)
4. 조건 B — `console_ui.py` + `replay_jsonl.py` 재생 중에 같은 명령을 `--cond B` 로 1회씩.
5. 블라인드 파일 `blind_T-CC06d.md` · `blind_T-CC06d_key.json`(조건 A 1회차).
6. 제품 코드 변경 0 확인 · ui-verify · 동결 해시(시작 == 종료) · 최종 보고 · 커밋 · push.

## 동결 해시 (시작 시)

- `jetson_sender.py` `afb5470a…361ac8` · `verify_jetson_safe.py` `75f10ee4…509621d` · `train_fall_safety.py` `30023bdf…409adc5` — CLAUDE.md 표와 일치.

## 판단 요청

1. **`console_ui` 화면 갱신 지연**은 제품 코드를 건드리지 않고 잴 방법을 찾지 못하면 "측정 안 함" 으로 적는다.
2. **K3 의 "SYSTEM_CONTEXT 20자 연속 일치"** 는 K2 문항에서 시스템 명세 문장을 그대로 옮긴 맞는 답도 실패로 센다. 규칙은 그대로 적용하고, 걸린 문항 ID 만 보고한다.
3. T-CC06c 보고에 '다음에 필요한 것' 절이 없다. 시작 방법은 그 보고 '지시와 다르게 한 것' 2번의 절차로 이해했다.
