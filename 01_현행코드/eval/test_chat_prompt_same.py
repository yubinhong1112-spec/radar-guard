"""test_chat_prompt_same.py — 프롬프트 조립 분리 전/후 바이트 동일성 검사

  실행: [내 PC PowerShell]
      python 01_현행코드\\eval\\test_chat_prompt_same.py          # 비교 (기본)
      python 01_현행코드\\eval\\test_chat_prompt_same.py --dump   # 기준 덤프 재생성

무엇을 증명하나
  `AssistantDrawer._work_locked` 에서 "검색 → 프롬프트 조립"을 모듈 함수
  `build_chat_request` 로 떼어내면서 Ollama 에 보내는 요청 본문이 한 바이트도
  바뀌지 않았음을 증명한다. 질문 5개(낙상·감전·협착·키워드없음 2) ×
  alert 있음/없음 = 10건.

어떻게 재나
  prompt 를 만드는 코드를 건드리지 않고, `urllib.request.urlopen` 을 가로채
  실제로 전송되는 JSON 본문을 그대로 받아 적는다. 따라서 prompt 뿐 아니라
  model·stream·options 까지 함께 고정된다.
  DB·Ollama 는 띄우지 않는다 — 검색 결과를 고정 stub 으로 바꿔 넣으므로
  Docker 가 꺼져 있어도 같은 결과가 나온다. 이 테스트가 보는 것은 검색 품질이
  아니라 조립 결과다.

기준 파일
  fixtures/prompts_before.json — 분리 직전 코드로 --dump 해서 만든 것.
  이 파일은 다시 만들지 않는다. 분리 후 코드로 --dump 하면 증명이 무의미해진다.
"""
import io
import json
import os
import sys
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, 'fixtures', 'prompts_before.json')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, CODE)

import radar_core as core          # noqa: E402
import console_ui as ui            # noqa: E402

# ── 고정 입력 ────────────────────────────────────────────────────────
# 검색 결과 stub. 내용이 무엇이든 분리 전/후가 같은 값을 받으면 비교는 성립한다.
STUB_DOCS = (
    ('환자를 함부로 움직이지 말고 그 자리에서 고정한다.',
     '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf'),
    ('119 에 신고하고 의료진이 올 때까지 호흡과 의식을 확인한다.',
     '감전시응급조치.pdf'),
)

# alert/pkt 고정값. 실측 재현이 목적이 아니라 live 블록이 붙는 경로를
# 통과시키는 것이 목적이므로 값은 고정이면 된다.
ALERT = {
    'type': 'fall_detected',
    'zone': 'Z1',
    'conf': 0.87,
    'evidence': {'height_start': 1.62, 'height_end': 0.31, 'h_drop': 1.31,
                 'horiz_range': 0.94, 'ds_last': 0.42},
}
PKT = {'height': 0.31, 'breaker': {'state': {'Z1': 'ON'}, 'src': 'modbus'}}

QUESTIONS = (
    ('fall', '작업자가 낙상했는데 뭐부터 해야 돼?'),
    ('shock', '감전 사고가 났을 때 조치 순서 알려줘'),
    ('pinch', '협착 사고 대응 절차가 뭐야?'),
    ('free1', '사람이 기계에 손이 말려 들어갔어'),
    ('free2', '작업자가 바닥에 엎어져서 안 움직여'),
)


class _Console:
    def __init__(self, alert):
        self.alert = alert
        self.pkt = PKT


class _Emit:
    """answer_ready / answer_failed 대역. 실패를 조용히 삼키지 않는다."""

    def __init__(self, name, failures):
        self.name = name
        self.failures = failures

    def emit(self, *args):
        if self.name == 'failed':
            self.failures.append(args[0])


def _fake_self(alert, failures):
    """QApplication 없이 _work_locked 를 호출하기 위한 최소 대역."""
    obj = types.SimpleNamespace(
        console=_Console(alert),
        chat_variant='baseline',    # [10/09 T-CC07b] 제품 기본은 J3 — 기준선을 명시
        SYSTEM_CONTEXT=ui.AssistantDrawer.SYSTEM_CONTEXT,
        _event_for=ui.AssistantDrawer._event_for,
        answer_ready=_Emit('ready', failures),
        answer_failed=_Emit('failed', failures),
    )
    return obj


def collect():
    """10건의 Ollama 요청 본문을 {case_id: body_dict} 로 모은다."""
    sent = {}
    docs = [types.SimpleNamespace(page_content=text,
                                  metadata={'source_file': src})
            for text, src in STUB_DOCS]

    def fake_search(vs, ev_type, situation, category):
        return list(docs)

    class _Vs:
        def __init__(self, *a, **kw):
            pass

        def similarity_search(self, *a, **kw):
            return list(docs)

    def fake_urlopen(req, timeout=None):
        sent['_last'] = json.loads(req.data.decode('utf-8'))
        return io.BytesIO(json.dumps({'response': 'stub'}).encode('utf-8'))

    orig = (core.search_sop_documents, core.PGVector, core.OllamaEmbeddings,
            urllib.request.urlopen)
    core.search_sop_documents = fake_search
    core.PGVector = _Vs
    core.OllamaEmbeddings = lambda *a, **kw: None
    urllib.request.urlopen = fake_urlopen
    try:
        out, failures = {}, []
        for tag, question in QUESTIONS:
            for suffix, alert in (('noalert', None), ('alert', ALERT)):
                sent.pop('_last', None)
                ui.AssistantDrawer._work_locked(
                    _fake_self(alert, failures), question)
                if failures:
                    raise AssertionError(
                        f'{tag}-{suffix} 처리 중 예외: {failures[-1]}')
                assert '_last' in sent, f'{tag}-{suffix}: 요청이 안 나갔다'
                out[f'{tag}-{suffix}'] = sent['_last']
        return out
    finally:
        (core.search_sop_documents, core.PGVector, core.OllamaEmbeddings,
         urllib.request.urlopen) = orig


def main():
    got = collect()
    if '--dump' in sys.argv:
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        with open(FIXTURE, 'w', encoding='utf-8') as fp:
            json.dump(got, fp, ensure_ascii=False, indent=1, sort_keys=True)
        print(f'덤프 {len(got)}건 → {FIXTURE}')
        return 0

    with open(FIXTURE, encoding='utf-8') as fp:
        want = json.load(fp)

    ng = []
    # [10/09 T-CC07a] keep_alive 는 프롬프트 조립이 아니라 모델 유지 시간이다.
    #   '30m' → CHAT_KEEP_ALIVE 로 바뀌어 이 키 하나만 바이트 비교에서 빼고,
    #   대신 실제로 보낸 값이 상수와 같은지를 따로 본다. 다른 키는 그대로 비교한다.
    for body in want.values():
        body.pop('keep_alive', None)
    for case in sorted(got):
        sent_keep = got[case].pop('keep_alive', None)
        if sent_keep != ui.CHAT_KEEP_ALIVE:
            ng.append(f'{case}: keep_alive {sent_keep!r} != '
                      f'CHAT_KEEP_ALIVE {ui.CHAT_KEEP_ALIVE!r}')
    if sorted(want) != sorted(got):
        ng.append(f'케이스 목록 불일치: 기준 {sorted(want)} / 지금 {sorted(got)}')
    for case in sorted(set(want) & set(got)):
        a = json.dumps(want[case], ensure_ascii=False, sort_keys=True)
        b = json.dumps(got[case], ensure_ascii=False, sort_keys=True)
        if a.encode('utf-8') != b.encode('utf-8'):
            wp = want[case].get('prompt', '')
            gp = got[case].get('prompt', '')
            at = next((i for i in range(min(len(wp), len(gp)))
                       if wp[i] != gp[i]), min(len(wp), len(gp)))
            ng.append(f'{case}: 본문 불일치 (prompt {at}번째 문자부터)\n'
                      f'  기준: {wp[at:at + 60]!r}\n'
                      f'  지금: {gp[at:at + 60]!r}')

    print(f'프롬프트 동일성 — {len(got)}건 비교, NG {len(ng)}건')
    for line in ng:
        print('NG', line)
    return 1 if ng else 0


if __name__ == '__main__':
    sys.exit(main())
