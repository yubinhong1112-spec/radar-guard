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
import types
import urllib.request

# 프롬프트를 발췌 앞/뒤로 가르는 표지. _gen_facts 의 조립 순서에서 온다.
CTX_HEAD = '[공식 매뉴얼 발췌]\n'
CTX_TAIL = '\n규칙: 반드시 1.부터 4.까지'

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
    """fn 을 돌리는 동안 Ollama 요청·응답 원문을 모은다.

    반환: (결과, 응답원문들, 요청본문들, 초)
    _gen_facts 는 생성문만 돌려주고 load_duration·eval_count·프롬프트를 버린다.
    제품 코드를 고치지 않고 그 값을 얻기 위해 urlopen 을 여기서만 감싼다.
    """
    seen, sent = [], []
    orig = urllib.request.urlopen

    def wrapped(req, timeout=None):
        try:
            sent.append(json.loads(req.data.decode('utf-8')))
        except Exception:
            sent.append({})
        with orig(req, timeout=timeout) as resp:
            raw = resp.read()
        seen.append(raw)
        return _Tee(raw)

    urllib.request.urlopen = wrapped
    started = time.perf_counter()
    try:
        return fn(), seen, sent, time.perf_counter() - started
    finally:
        urllib.request.urlopen = orig


def split_prompt(prompt):
    """프롬프트를 (발췌 앞, 발췌, 발췌 뒤) 로 가른다. 못 가르면 None."""
    if CTX_HEAD not in prompt or CTX_TAIL not in prompt:
        return None
    head, rest = prompt.split(CTX_HEAD, 1)
    body, tail = rest.split(CTX_TAIL, 1)
    return head + CTX_HEAD, body, CTX_TAIL + tail


def prove_ctx(path700, pathfull):
    """--ctx-limit full 이 발췌 길이 외에는 제품 프롬프트와 같음을 증명한다."""
    a = {r['event']: r for r in
         (json.loads(l) for l in open(path700, encoding='utf-8'))}
    b = {r['event']: r for r in
         (json.loads(l) for l in open(pathfull, encoding='utf-8'))}
    ng = []
    print(f'{"이벤트":32s} {"앞":>6s} {"뒤":>6s} {"발췌 700⊂full":>14s} '
          f'{"700자":>7s} {"full자":>7s}')
    for ev in a:
        if ev not in b:
            ng.append(f'{ev}: full 쪽에 없다')
            continue
        pa, pb = split_prompt(a[ev]['prompt']), split_prompt(b[ev]['prompt'])
        if not pa or not pb:
            ng.append(f'{ev}: 프롬프트를 표지로 가르지 못했다')
            continue
        head_ok = pa[0].encode('utf-8') == pb[0].encode('utf-8')
        tail_ok = pa[2].encode('utf-8') == pb[2].encode('utf-8')
        sub_ok = pb[1].startswith(pa[1])
        if not (head_ok and tail_ok and sub_ok):
            ng.append(f'{ev}: 앞 {head_ok} 뒤 {tail_ok} 포함 {sub_ok}')
        print(f'{ev:32s} {"동일" if head_ok else "다름":>6s} '
              f'{"동일" if tail_ok else "다름":>6s} '
              f'{"예" if sub_ok else "아니오":>14s} '
              f'{len(pa[1]):>7d} {len(pb[1]):>7d}')
    print(f'\n발췌 외 프롬프트 동일 — {len(a)}이벤트 중 NG {len(ng)}건')
    for line in ng:
        print('NG', line)
    return 1 if ng else 0


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


def score_set(path):
    """채점 기준. {이벤트: {pinned_chunks, must_include, must_not_include}}

    파일은 이벤트 이름을 최상위 키로 쓰고 `_note` 로 설명을 단다.
    """
    try:
        with open(path, encoding='utf-8') as fp:
            d = json.load(fp)
        return {k: v for k, v in d.items() if not k.startswith('_')}
    except FileNotFoundError:
        return None


def pinned_context(chunk_ids, collection='safety_manual_v2'):
    """지정 chunk_id 의 본문을 **지정한 순서대로** 돌려준다. 검색을 쓰지 않는다.

    사람이 검토한 절만 쓰는 것이 목적이므로 용어 빈도 정렬도, 임베딩도 타지
    않는다. 하나라도 없으면 조용히 빼지 않고 예외를 낸다 — 빠진 채로 생성하면
    그 결과가 무엇에 근거한 것인지 알 수 없다.
    """
    import psycopg2
    with psycopg2.connect(core.CONN_STR) as cn:
        with cn.cursor() as cur:
            cur.execute(
                "SELECT e.cmetadata->>'chunk_id', e.document, e.cmetadata "
                'FROM langchain_pg_embedding e JOIN langchain_pg_collection c '
                "ON c.uuid = e.collection_id WHERE c.name = %s "
                "AND e.cmetadata->>'chunk_id' = ANY(%s)",
                (collection, list(chunk_ids)))
            found = {cid: (doc, meta) for cid, doc, meta in cur.fetchall()}
    missing = [c for c in chunk_ids if c not in found]
    if missing:
        raise SystemExit(
            f"측정 중단 — {collection} 에 없는 chunk_id: {', '.join(missing)}. "
            '채점 기준의 pinned_chunks 를 확인하라.')
    docs = []
    for cid in chunk_ids:
        doc, meta = found[cid]
        docs.append(types.SimpleNamespace(page_content=doc, metadata=meta))
    return docs


class FullCtx(str):
    """`ctx[:700]` 을 전체 문자열로 되돌리는 str.

    제품 `_gen_facts` 는 발췌를 `ctx[:700]` 으로 자른다(console_ui.py). 지정
    조각 전체를 넣어 보려면 그 슬라이스만 무력화해야 하는데 제품 코드는 고칠
    수 없다. 그래서 슬라이스를 무시하는 str 하위 클래스를 주입한다.
    **발췌 길이 말고는 프롬프트가 제품과 같다** — `--prove` 가 두 조건의
    프롬프트를 앞뒤로 갈라 바이트 비교해 그것을 증명한다.
    """

    def __getitem__(self, key):
        if isinstance(key, slice):
            return str(self)
        return str.__getitem__(self, key)


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
    ap.add_argument('--pinned', action='store_true',
                    help='채점 기준의 pinned_chunks 를 지정 순서대로 쓴다'
                         '(safety_manual_v2, 검색 함수 미사용)')
    ap.add_argument('--ctx-limit', default='700', choices=['700', 'full'],
                    help='제품은 ctx[:700] 로 자른다. full 은 지정 조각 전체')
    ap.add_argument('--rules', default=SCORE_SET, help='채점 기준 파일')
    ap.add_argument('--prove', nargs=2, metavar=('JSONL700', 'JSONLFULL'),
                    help='두 조건의 프롬프트가 발췌 외 바이트 동일한지 증명')
    ap.add_argument('--min-mem', type=int, default=2000,
                    help='측정 전 요구 가용 메모리(MB). 기본 2000. 낮추면 '
                         '모델이 페이징돼 **지연 수치가 오염된다** — 품질'
                         '(필수·금지·형식)은 영향 없다. 결과에 기록된다')
    args = ap.parse_args()

    if args.prove:
        return prove_ctx(*args.prove)

    mem_start = avail_mb()
    print(f'가용 메모리 시작 {mem_start} MB · 모델 {args.model} · '
          f'컬렉션 {args.collection} · facts {args.facts}')
    if mem_start < args.min_mem:
        print(f'중단: 가용 메모리 {mem_start} MB < {args.min_mem} MB',
              file=sys.stderr)
        return 2
    if args.min_mem < 2000:
        print(f'⚠ 메모리 하한을 {args.min_mem} MB 로 낮췄다 — 모델이 '
              '페이징될 수 있어 지연 수치(초·적재·생성)를 신뢰하지 마라. '
              '품질(필수·금지·형식)은 영향 없다.')

    vs = core.PGVector(
        connection_string=core.CONN_STR,
        embedding_function=core.OllamaEmbeddings(model=core.EMBED_MODEL),
        collection_name=args.collection)

    rules = score_set(args.rules)
    if rules is None:
        print(f'채점 기준({args.rules}) 없음 — 채점 열은 "기준 미승인"')
    else:
        print(f'채점 기준: {os.path.basename(args.rules)}')
    if args.pinned and not rules:
        print('중단: --pinned 는 채점 기준의 pinned_chunks 가 필요하다',
              file=sys.stderr)
        return 2

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
            if args.pinned:
                ids = rules.get(ev, {}).get('pinned_chunks') or []
                if not ids:
                    raise SystemExit(
                        f'측정 중단 — {ev} 의 pinned_chunks 가 비어 있다')
                docs = pinned_context(ids)
            else:
                docs = core.search_sop_documents(
                    vs, ev, core.SOP_QUERY.get(ev, ''),
                    core.EVENT_CATEGORY.get(ev))
            ctx = '\n'.join(d.page_content for d in docs)
            # full 은 제품의 ctx[:700] 슬라이스만 무력화한다. 조립은 제품 코드.
            ctx_arg = FullCtx(ctx) if args.ctx_limit == 'full' else ctx
            chunks = [{'label': chunk_label(d.metadata),
                       'source_file': d.metadata.get('source_file'),
                       'category': d.metadata.get('category'),
                       'text': d.page_content} for d in docs]
            try:
                text, raws, sents, elapsed = capture(
                    lambda: ui.SopEngineV2._gen_facts(ev, ctx_arg, facts))
                err = None
            except Exception as e:
                text, raws, sents, elapsed = '', [], [], 0.0
                err = f'{type(e).__name__}: {e}'
            meta = last_meta(raws)
            prompt = (sents[-1].get('prompt', '') if sents else '')
            parts = split_prompt(prompt)
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
                'chunk_mode': 'pinned' if args.pinned else 'search',
                'ctx_limit': args.ctx_limit,
                'chunks': chunks, 'context_chars': len(ctx),
                'context_sent_chars': len(parts[1]) if parts else None,
                'prompt': prompt, 'prompt_chars': len(prompt),
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
                row['must_include_missed'] = [
                    g for g in groups if not any(w in text for w in g)]
                row['must_not_include_hits'] = [
                    w for w in (r.get('must_not_include') or []) if w in text]
            rows.append(row)
            sc = (f"필수 {row['must_include_passed']}/"
                  f"{row['must_include_total']} 금지 "
                  f"{len(row['must_not_include_hits'])} "
                  if 'must_include_total' in row else '채점 미적용 ')
            print(f"  {ev:30s} {elapsed:6.1f}초 {sc}형식 "
                  f"{'통과' if row['cacheable'] else '실패'} "
                  f"{row['line_count']}줄 {row['line_lens']} "
                  f"청크 {len(chunks)}개 발췌 "
                  f"{row['context_sent_chars']}/{len(ctx)}자 "
                  f"모델 {effective.split(':')[0]}"
                  + ('' if row['model_as_requested'] else ' ⚠요청과 다름')
                  + (f' 오류 {err}' if err else ''))
    finally:
        core.LLM_MODEL, ui.SopEngineV2.PREPARE_MODEL = keep

    mem_end = avail_mb()
    os.makedirs(RESULTS, exist_ok=True)
    name = (f"sop_{args.model.split(':')[0]}_"
            f"{'pinned' if args.pinned else args.collection}_"
            f"ctx{args.ctx_limit}_{args.facts}"
            f"{('_' + args.tag) if args.tag else ''}_"
            f"{time.strftime('%Y%m%d_%H%M')}")
    base = os.path.join(RESULTS, name)
    with open(base + '.jsonl', 'w', encoding='utf-8') as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')

    chunk_mode = ('고정 조각(pinned, safety_manual_v2)' if args.pinned
                  else f'현행 검색({args.collection})')
    out = [f'# 경보 SOP 생성 — {args.model} · {chunk_mode} · '
           f'ctx {args.ctx_limit} · facts {args.facts}', '',
           f'- {time.strftime("%Y-%m-%d %H:%M")} · 가용 메모리 '
           f'{mem_start} → {mem_end} MB (하한 {args.min_mem} MB)',]
    if args.min_mem < 2000:
        out += ['- ⚠ **지연 수치를 신뢰하지 말 것** — 메모리 하한을 '
                f'{args.min_mem} MB 로 낮춰 측정했다. 모델(1.6~1.9 GB)이 '
                '페이징될 수 있다. 품질(필수·금지·형식)은 영향 없다.']
    out += [
           '- 옵션은 제품 그대로다 — temperature 0 · num_predict 160 · '
           'num_ctx 1536',
           '- 발췌 절단: '
           + ('제품 그대로 ctx[:700]' if args.ctx_limit == '700'
              else '지정 조각 전체(FullCtx 주입 — 발췌 외 프롬프트는 제품과 동일, '
                   '`--prove` 로 증명)'),
           f'- 응답 형식: {"스트리밍(facts 있음)" if args.facts == "sample" else "format 스키마 JSON(facts 없음)"}',
           f'- 채점: {os.path.basename(args.rules) if rules else "**기준 없음** — 형식·시간만 기록"}',
           '', '## 요약', '',
           '| 이벤트 | 실제 모델 | 필수 | 금지 | 형식 통과 | 줄 수 | '
           '줄별 글자 | 초 | 적재 | 생성 | eval_count | 청크 | 발췌/전체자 |',
           '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        mk = r['model'].split(':')[0] + ('' if r['model_as_requested']
                                         else ' ⚠')
        inc = (f"{r['must_include_passed']}/{r['must_include_total']}"
               if 'must_include_total' in r else '—')
        ban = (f"{len(r['must_not_include_hits'])}건"
               if 'must_not_include_hits' in r else '—')
        out.append(
            f"| {r['event']} | {mk} | {inc} | {ban} | "
            f"{'통과' if r['cacheable'] else '**실패**'} | "
            f"{r['line_count']} | {r['line_lens']} | {r['elapsed']:.1f} | "
            f"{r['load_sec']} | {r['eval_sec']} | {r['eval_count']} | "
            f"{len(r['chunks'])} | "
            f"{r['context_sent_chars']}/{r['context_chars']} |")
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
                    f"{len(r['must_not_include_hits'])}건"]
            if r['must_include_missed']:
                out.append('- 빠진 필수: '
                           + ' · '.join('/'.join(g)
                                        for g in r['must_include_missed']))
            if r['must_not_include_hits']:
                out.append('- 금지 적발: '
                           + ' · '.join(f"`{w}`"
                                        for w in r['must_not_include_hits']))
            out.append('')

    with open(base + '.md', 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(out) + '\n')
    ok = sum(1 for r in rows if r['cacheable'])
    print(f'형식 통과 {ok}/{len(rows)} · 가용 메모리 끝 {mem_end} MB')
    print(f'기록: {base}.jsonl / {base}.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
