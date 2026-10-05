"""sop_eval.py — 경보 SOP 6종 생성 결과를 재는 자 (내용 채점은 기준 승인 후)

  실행: [내 PC PowerShell]  — pgvector + Ollama 필요
      # 지금 시연에 뜨는 것 (앱 시작 때 사전 생성되는 경로)
      python 01_현행코드\\eval\\sop_eval.py --model qwen2.5:3b-instruct-q4_K_M `
          --collection safety_manual --facts none
      # 사전 생성이 실패한 유형에서 경보 순간 뜨는 것
      python 01_현행코드\\eval\\sop_eval.py --model gemma2:2b `
          --collection safety_manual --facts sample

왜 만드나
  경보 화면의 SOP 4줄은 앱 시작 때 qwen2.5:3b 가 만들고, `_cacheable_sop` 는
  **형식(4줄·25자·마크다운 없음)만** 검사한다. 내용을 잰 적이 없다
  (개선계획 문제 A). 심사에서 가장 먼저 보이는 화면인데 평가셋 30문항에
  이 경로가 없다. 이 스크립트는 그 경로를 그대로 재현해 기록만 한다 —
  채점 기준(sop_eval_set.json)은 Cowork 초안 + 사용자 승인 뒤에 붙는다.

제품 코드를 한 줄도 바꾸지 않는다 (T-CC04 공통규칙 6)
  검색은 `core.search_sop_documents`, 생성은 `SopEngineV2._gen_facts` 를 그대로
  부른다. 모델만 스크립트 안에서 임시 교체한다(`PREPARE_MODEL`/`core.LLM_MODEL`).
  컬렉션은 `vs` 인자로 넣는다 — `SopEngineV2._search` 는 컬렉션 이름이
  'safety_manual' 로 박혀 있어 쓸 수 없고, 그 함수와 다른 점은 컬렉션뿐이다.
  `_gen_facts` 는 load_duration·eval_count 를 버리므로 `urllib.request.urlopen`
  을 이 스크립트 안에서만 감싸 원본 JSON 을 가로채 기록한다. 프롬프트·옵션
  (temperature 0 · num_predict 160 · num_ctx 1536)은 건드리지 않는다.

  ⚠ --facts sample 은 `stream: True` 경로이고 --facts none 은 `format` 스키마
    JSON(비스트리밍) 경로다(console_ui.py 3011~3019). 모델만 다른 것이 아니라
    응답 형식도 다르므로 두 조건을 나란히 볼 때 이 차이를 기억해야 한다.
    가로채기 때문에 본문을 먼저 다 읽고 넘기지만, on_update 를 쓰지 않으므로
    생성 결과는 같다.

출력
  eval/results/sop_<모델>_<컬렉션>_<facts><태그>_<날짜>.jsonl / .md
"""
import argparse
import ctypes
import io
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, 'results')
EVAL_SET = os.path.join(HERE, 'chat_eval_set.json')
SCORE_SET = os.path.join(HERE, 'sop_eval_set.json')
FRAMES = os.path.join(os.path.dirname(CODE), '03_데이터',
                      'UI_애니메이션_실측_20260824',
                      'events_ui_animation_confirmed_0824.jsonl')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, CODE)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import radar_core as core        # noqa: E402
import console_ui as ui          # noqa: E402

MODELS = ('qwen2.5:3b-instruct-q4_K_M', 'gemma2:2b')
# jsonl 의 label → 이벤트. 없는 유형은 실측값을 지어내지 않고 비워 둔다.
LABEL_EVENT = {'fall': 'fall_detected', 'pinch': 'pinching',
               'shock': 'electric_shock_risk_confirmed'}


def avail_mb():
    """가용 물리 메모리(MB). \\Memory\\Available MBytes 와 같은 값이다."""
    class S(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    s = S()
    s.dwLength = ctypes.sizeof(S)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return int(s.ullAvailPhys / (1024 * 1024))


class _Tee:
    """urlopen 응답 대역 — 본문을 적어 두고 read()·순회를 둘 다 지원한다."""

    def __init__(self, raw):
        self.raw = raw
        self._buf = io.BytesIO(raw)

    def read(self, *a):
        return self._buf.read(*a)

    def __iter__(self):
        return iter(self.raw.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def capture(fn):
    """fn 을 돌리는 동안 Ollama 응답 원문을 모은다. (결과, 응답들, 초)"""
    seen = []
    orig = urllib.request.urlopen

    def wrapped(req, timeout=None):
        with orig(req, timeout=timeout) as resp:
            raw = resp.read()
        seen.append(raw)
        return _Tee(raw)

    urllib.request.urlopen = wrapped
    started = time.perf_counter()
    try:
        return fn(), seen, time.perf_counter() - started
    finally:
        urllib.request.urlopen = orig


def last_meta(raws):
    """응답 원문에서 마지막 메타(durations·eval_count)를 꺼낸다."""
    for raw in reversed(raws):
        for line in reversed(raw.splitlines()):
            try:
                d = json.loads(line.decode('utf-8'))
            except Exception:
                continue
            if 'eval_count' in d or 'total_duration' in d:
                return d
    return {}


def ns(d, key):
    v = d.get(key)
    return None if v is None else round(v / 1e9, 3)


def sample_facts(ev):
    """이벤트별 실측값 예시와 그 출처. 없으면 ({}, 사유)."""
    with open(EVAL_SET, encoding='utf-8') as fp:
        for it in json.load(fp)['items']:
            a = it.get('alert')
            if a and a.get('type') == ev:
                return ((a, it.get('pkt') or {}),
                        f"chat_eval_set.json {it['id']} alert")
    want = [lb for lb, e in LABEL_EVENT.items() if e == ev]
    if want and os.path.exists(FRAMES):
        for n, line in enumerate(open(FRAMES, encoding='utf-8'), 1):
            d = json.loads(line)
            if d.get('label') != want[0]:
                continue
            fr = d.get('frames') or []
            if not fr:
                break
            hi = max(fr, key=lambda f: f['height'])
            lo = min(fr, key=lambda f: f['height'])
            alert = {
                'type': ev, 'zone': core.RADAR_ZONE, 'conf': None,
                'evidence': {
                    'height_start': hi['height'], 'height_end': lo['height'],
                    'h_drop': round(hi['height'] - lo['height'], 4),
                    'horiz_range': lo.get('spread_xz'), 'ds_last': None},
            }
            pkt = {'height': lo['height'], 'occupied': True,
                   'breaker': {'state': {core.RADAR_ZONE: 'ON'},
                               'src': 'modbus'}}
            return ((alert, pkt),
                    f'{os.path.basename(FRAMES)} {n}번째 줄 (label={want[0]}, '
                    f"height_start=frame {hi['frame_num']}, "
                    f"height_end=frame {lo['frame_num']}, "
                    f"horiz_range=spread_xz of frame {lo['frame_num']}). "
                    'conf·ds_last 은 이 파일에 없어 비웠다')
    return (({}, {}), '실측 출처 없음 — chat_eval_set 과 실측 jsonl 둘 다에 '
                      '이 유형의 경보 프레임이 없다. facts 를 비우고 돌렸다')


def score_set():
    try:
        with open(SCORE_SET, encoding='utf-8') as fp:
            return {it['event']: it for it in json.load(fp)['items']}
    except FileNotFoundError:
        return None


def chunk_label(meta):
    for key in ('chunk_id', 'id', 'page', 'page_number'):
        if meta.get(key) is not None:
            return f'{key}={meta[key]}'
    return '식별자 없음'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default=ui.SopEngineV2.PREPARE_MODEL,
                    choices=MODELS)
    ap.add_argument('--collection', default='safety_manual',
                    choices=['safety_manual', 'safety_manual_v2'])
    ap.add_argument('--facts', default='none', choices=['none', 'sample'])
    ap.add_argument('--tag', default='')
    args = ap.parse_args()

    mem_start = avail_mb()
    print(f'가용 메모리 시작 {mem_start} MB · 모델 {args.model} · '
          f'컬렉션 {args.collection} · facts {args.facts}')
    if mem_start < 2000:
        print(f'중단: 가용 메모리 {mem_start} MB < 2000 MB', file=sys.stderr)
        return 2

    vs = core.PGVector(
        connection_string=core.CONN_STR,
        embedding_function=core.OllamaEmbeddings(model=core.EMBED_MODEL),
        collection_name=args.collection)

    rules = score_set()
    if rules is None:
        print('채점 기준(sop_eval_set.json) 없음 — 채점 열은 "기준 미승인"')

    # 모델 주입. facts 가 있으면 _gen_facts 가 core.LLM_MODEL 을, 없으면
    # SopEngineV2.PREPARE_MODEL 을 쓴다(console_ui.py 3011).
    keep = (core.LLM_MODEL, ui.SopEngineV2.PREPARE_MODEL)
    if args.facts == 'sample':
        core.LLM_MODEL = args.model
    else:
        ui.SopEngineV2.PREPARE_MODEL = args.model

    rows = []
    try:
        for ev in ui.SopEngineV2.PREPARE_EVENTS:
            (alert, pkt), facts_src = (sample_facts(ev) if args.facts == 'sample'
                                       else (({}, {}), 'facts 없음 (현행 사전 생성 조건)'))
            facts = (ui.SopEngineV2.build_facts(alert, pkt, 0) if alert else {})
            docs = core.search_sop_documents(
                vs, ev, core.SOP_QUERY.get(ev, ''), core.EVENT_CATEGORY.get(ev))
            ctx = '\n'.join(d.page_content for d in docs)
            chunks = [{'label': chunk_label(d.metadata),
                       'source_file': d.metadata.get('source_file'),
                       'category': d.metadata.get('category'),
                       'text': d.page_content} for d in docs]
            try:
                text, raws, elapsed = capture(
                    lambda: ui.SopEngineV2._gen_facts(ev, ctx[:700], facts))
                err = None
            except Exception as e:
                text, raws, elapsed = '', [], 0.0
                err = f'{type(e).__name__}: {e}'
            meta = last_meta(raws)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            # ⚠ _gen_facts 는 fact_block 이 있으면 core.LLM_MODEL, 없으면
            #   PREPARE_MODEL 을 쓴다(console_ui.py 3011). 실측값이 없는
            #   이벤트는 --facts sample 로 불러도 PREPARE_MODEL 경로로 간다.
            #   그래서 요청 모델이 아니라 **실제로 쓰인 모델**을 적는다.
            effective = (core.LLM_MODEL if facts
                         else ui.SopEngineV2.PREPARE_MODEL)
            row = {
                'event': ev, 'event_ko': ui.EVENT_KO.get(ev, ev),
                'model_requested': args.model, 'model': effective,
                'model_as_requested': effective == args.model,
                'collection': args.collection,
                'facts_mode': args.facts, 'facts_source': facts_src,
                'facts': facts, 'tag': args.tag,
                'chunks': chunks, 'context_chars': len(ctx),
                'context_sent_chars': len(ctx[:700]),
                'generated': text, 'error': err,
                'cacheable': ui.SopEngineV2._cacheable_sop(text) if text else False,
                'line_count': len(lines),
                'line_lens': [len(ln) for ln in lines],
                'elapsed': round(elapsed, 3),
                'load_sec': ns(meta, 'load_duration'),
                'prompt_eval_sec': ns(meta, 'prompt_eval_duration'),
                'eval_sec': ns(meta, 'eval_duration'),
                'eval_count': meta.get('eval_count'),
                'done_reason': meta.get('done_reason'),
                'mem_start_mb': mem_start, 'mem_now_mb': avail_mb(),
            }
            if rules and ev in rules:
                r = rules[ev]
                groups = r.get('must_include') or []
                row['must_include_total'] = len(groups)
                row['must_include_passed'] = sum(
                    1 for g in groups if any(w in text for w in g))
                row['must_not_include_hits'] = [
                    w for w in (r.get('must_not_include') or []) if w in text]
            rows.append(row)
            print(f"  {ev:30s} {elapsed:6.1f}초 형식 "
                  f"{'통과' if row['cacheable'] else '실패'} "
                  f"{row['line_count']}줄 {row['line_lens']} "
                  f"청크 {len(chunks)}개 ctx {len(ctx)}자 "
                  f"모델 {effective.split(':')[0]}"
                  + ('' if row['model_as_requested'] else ' ⚠요청과 다름')
                  + (f' 오류 {err}' if err else ''))
    finally:
        core.LLM_MODEL, ui.SopEngineV2.PREPARE_MODEL = keep

    mem_end = avail_mb()
    os.makedirs(RESULTS, exist_ok=True)
    name = (f"sop_{args.model.split(':')[0]}_{args.collection}_{args.facts}"
            f"{('_' + args.tag) if args.tag else ''}_"
            f"{time.strftime('%Y%m%d_%H%M')}")
    base = os.path.join(RESULTS, name)
    with open(base + '.jsonl', 'w', encoding='utf-8') as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')

    out = [f'# 경보 SOP 생성 — {args.model} · {args.collection} · '
           f'facts {args.facts}', '',
           f'- {time.strftime("%Y-%m-%d %H:%M")} · 가용 메모리 '
           f'{mem_start} → {mem_end} MB',
           '- 옵션은 제품 그대로다 — temperature 0 · num_predict 160 · '
           'num_ctx 1536 · 매뉴얼 발췌 700자 절단',
           f'- 응답 형식: {"스트리밍(facts 있음)" if args.facts == "sample" else "format 스키마 JSON(facts 없음)"}',
           f'- 채점: {"기준 적용" if rules else "**기준 미승인** — 형식·시간만 기록"}',
           '', '## 요약', '',
           '| 이벤트 | 실제 모델 | 형식 통과 | 줄 수 | 줄별 글자 | 초 | 적재 | '
           '생성 | eval_count | 청크 | ctx자 |',
           '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        mk = r['model'].split(':')[0] + ('' if r['model_as_requested']
                                         else ' ⚠')
        out.append(
            f"| {r['event']} | {mk} | "
            f"{'통과' if r['cacheable'] else '**실패**'} | "
            f"{r['line_count']} | {r['line_lens']} | {r['elapsed']:.1f} | "
            f"{r['load_sec']} | {r['eval_sec']} | {r['eval_count']} | "
            f"{len(r['chunks'])} | {r['context_chars']} |")
    off = [r['event'] for r in rows if not r['model_as_requested']]
    if off:
        out += ['',
                f"⚠ 요청 모델(`{args.model}`)과 다른 모델로 돌아간 이벤트: "
                f"{', '.join(off)}. `_gen_facts` 는 실측값(fact_block)이 없으면 "
                f"`PREPARE_MODEL` 을 쓴다(console_ui.py 3011). 실측값이 없는 "
                f"유형은 `--facts sample` 로 불러도 사전 생성 경로로 간다 — "
                f"제품 동작 그대로이고 스크립트가 바꾸지 않았다."]

    out += ['', '## 생성문 전문', '']
    for r in rows:
        out += [f"### {r['event']} ({r['event_ko']})", '',
                f"- 실측값 출처: {r['facts_source']}",
                '- 사용 청크: '
                + ' · '.join(f"{c['label']} ({c['source_file']})"
                             for c in r['chunks']),
                f"- 보낸 발췌: {r['context_sent_chars']}자 "
                f"(전체 {r['context_chars']}자에서 앞 700자)", '']
        if r['error']:
            out += [f"**생성 실패** — {r['error']}", '']
            continue
        out += ['```', r['generated'] or '(빈 응답)', '```', '']
        if 'must_include_total' in r:
            out += [f"- 필수 문구 {r['must_include_passed']}/"
                    f"{r['must_include_total']} · 금지 "
                    f"{len(r['must_not_include_hits'])}건", '']

    with open(base + '.md', 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(out) + '\n')
    ok = sum(1 for r in rows if r['cacheable'])
    print(f'형식 통과 {ok}/{len(rows)} · 가용 메모리 끝 {mem_end} MB')
    print(f'기록: {base}.jsonl / {base}.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
