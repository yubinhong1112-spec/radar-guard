"""test_sop_collection_filter.py — SOP 고정 출처 질의가 컬렉션을 지키는지

  실행: [내 PC PowerShell]  — pgvector 컨테이너가 떠 있어야 한다
      python 01_현행코드\\eval\\test_sop_collection_filter.py

무엇을 막나
  [10/02] search_sop_documents 의 고정 출처 질의에 컬렉션 조건이 없어서,
  safety_manual_v2 가 적재된 뒤 H-187 문서 조회에서 v1 1개 + v2 1개가
  섞여 선택됐다. 경보 답변 근거가 어느 코퍼스에서 왔는지 보증되지 않았다.
  운영 경보 경로(radar_core.py SopEngine, console_ui.py SopEngineV2)도 같은
  함수를 쓰므로 이 검사가 운영 회귀 검증을 겸한다.

세 가지로 교차 확인한다 (3이벤트 × 3기준 = 9항목, 기준 NG 0건)
  ① T-CC01 baseline(baseline_20261001_1911.jsonl)의 sources 와 같은가
     — v2 가 없던 640청크 시절에 측정한 값이다
  ② 컬렉션을 직접 거른 독립 질의 결과와 본문까지 같은가
  ③ v1 단독(640청크) 시절 실제로 떠본 청크 머리글과 같은가
  + 선택된 청크가 전부 safety_manual 에서 왔는가

종료코드 0 = 통과 / 1 = 위반
"""
import json
import os
import sys

sys.path.insert(0, r'C:\dev\radar-guard\01_현행코드')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
import radar_core as core

BASE = os.path.join(r'C:\dev\radar-guard\01_현행코드\eval\results',
                    'baseline_20261001_1911.jsonl')
COLL = 'safety_manual'

# ③ v1 단독 640청크 시절 직접 떠본 청크 머리글 (10/02 세션 기록)
ANCHOR = {
    'fall_detected': ['KOSHAGUIDEH-187-2021\n-3-\n(4)부종및피부내출혈',
                      'KOSHAGUIDEH-187-2021\n-11-\n를대고즉시병원으로이송한다'],
    'electric_shock_risk': ['KOSHA GUIDE\nE - 14 - 2012\n- 8 -\n구부리지 말고',
                            'KOSHAGUIDEH-187-2021\n-9-\n (나)환자가앉아있거나'],
    'pinching': ['KOSHAGUIDEH-187-2021\n-11-\n를대고즉시병원으로이송한다',
                 'KOSHAGUIDEB–M–37-2026\n-43-\n∙롤의최고회전속도'],
}


class FakeVs:
    """collection_name 만 있는 대역. similarity_search 는 타지 않는다."""
    collection_name = COLL

    def similarity_search(self, *a, **kw):
        raise AssertionError('고정 출처 경로만 봐야 하는데 임베딩 검색이 불렸다')


def independent(ev_type, category):
    """비교용 독립 질의 — 컬렉션을 명시해 직접 고른다."""
    cats = category if isinstance(category, (list, tuple)) else (category,)
    count = 1 if isinstance(category, (list, tuple)) else 2
    terms = core.SOP_RESPONSE_TERMS.get(ev_type, ())
    out = []
    for cat in cats:
        src = core.SOP_RESPONSE_SOURCE.get(ev_type, {}).get(cat)
        if not src:
            out.append(None)
            continue
        with psycopg2.connect(core.CONN_STR) as cn:
            with cn.cursor() as cur:
                cur.execute(
                    "SELECT e.document, e.cmetadata->>'source_file' "
                    "FROM langchain_pg_embedding e "
                    "JOIN langchain_pg_collection c ON c.uuid = e.collection_id "
                    "WHERE e.cmetadata->>'source_file' = %s AND c.name = %s",
                    (src, COLL))
                rows = cur.fetchall()
        rows.sort(key=lambda r: sum(r[0].count(t) for t in terms), reverse=True)
        out += rows[:count]
    return [r for r in out if r]


def main():
    base = {r['id']: r for r in
            (json.loads(l) for l in open(BASE, encoding='utf-8'))}
    ng = []
    for ev, ids in (('fall_detected', ['EA-01', 'EA-04']),
                    ('electric_shock_risk', ['EA-02', 'EA-05']),
                    ('pinching', ['EA-03', 'EA-06'])):
        cat = core.EVENT_CATEGORY.get(ev)
        docs = core.search_sop_documents(FakeVs(), ev, core.SOP_QUERY[ev], cat)
        got_src = sorted({d.metadata.get('source_file', '?') for d in docs})
        got_txt = [d.page_content for d in docs]

        # ① 1단계 sources
        want_src = base[ids[0]]['sources']
        ok1 = got_src == sorted(want_src)
        if not ok1:
            ng.append(f'{ev} ① sources 불일치: 기준 {sorted(want_src)} / 지금 {got_src}')

        # ② 독립 질의와 본문까지 같은가
        ind = independent(ev, cat)
        ok2 = got_txt == [t for t, _ in ind]
        if not ok2:
            ng.append(f'{ev} ② 독립 질의와 본문 불일치')

        # ③ v1 단독 시절 청크 머리글
        ok3 = all(any(t.startswith(a) for t in got_txt) for a in ANCHOR[ev])
        if not ok3:
            miss = [a for a in ANCHOR[ev]
                    if not any(t.startswith(a) for t in got_txt)]
            ng.append(f'{ev} ③ v1 시절 청크 머리글 누락: {miss}')

        # 모든 청크가 v1 컬렉션에서 왔는지
        with psycopg2.connect(core.CONN_STR) as cn:
            with cn.cursor() as cur:
                for t in got_txt:
                    cur.execute(
                        'SELECT c.name FROM langchain_pg_embedding e JOIN '
                        'langchain_pg_collection c ON c.uuid = e.collection_id '
                        'WHERE e.document = %s', (t,))
                    names = {n for (n,) in cur.fetchall()}
                    if names != {COLL}:
                        ng.append(f'{ev} 청크가 {names} 에서 왔다')

        print(f'{ev}: 청크 {len(docs)}개 · ①sources {"OK" if ok1 else "NG"} '
              f'· ②본문 {"OK" if ok2 else "NG"} · ③머리글 {"OK" if ok3 else "NG"}')
        for s in got_src:
            print(f'    {s}')

    print()
    print(f'회귀 확인 — 3이벤트 × 3기준 = 9항목, NG {len(ng)}건')
    for line in ng:
        print('NG', line)
    return 1 if ng else 0


if __name__ == '__main__':
    sys.exit(main())
