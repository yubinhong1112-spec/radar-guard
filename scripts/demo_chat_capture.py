"""demo_chat_capture.py — 실제 관제 창에서 챗봇 질문을 넣고 화면을 캡처한다 (T-CC07d)

  실행: [내 PC PowerShell, 저장소 루트] — DB · ollama serve · 재생기가 떠 있어야 한다
      python 01_현행코드\\replay_jsonl.py --seq normal        # 다른 창
      python scripts\\demo_chat_capture.py 04_문서\\시연\\캡처_1010

무엇을 하나
  console_ui.build_app 으로 **화면에 보이는** 관제 창을 띄우고(offscreen 아님),
  워밍업이 끝나면 대화창에 질문 3개(사고 · 시스템 · 고정 답)를 차례로 넣는다.
  질문 입력은 사람이 치는 대신 AssistantDrawer.ask 를 부른다 — 그 뒤 경로는
  앱 그대로다. 단계마다 대화창을 PNG 로 저장하고 시간을 잰다.

  재는 것: 워밍업 총시간(창 표시 → 준비 완료), 질문별 머리줄 · 첫 문장 · 완료까지.
  제품 런타임에 포함되지 않는 검증 도구다.
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, '01_현행코드'))
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from PyQt5 import QtCore                    # noqa: E402
import console_ui as ui                     # noqa: E402

QUESTIONS = (
    ('사고', '감전 사고가 났을 때 조치 순서 알려줘'),
    ('시스템', '젯슨에 RTC가 없는데 경과시간은 어떤 기준으로 계산해?'),
    ('고정', '이 구역 출입 보안 등급이 어떻게 돼?'),
    # 둘째 LLM 질문 시간을 따로 보려고 사고 질문을 하나 더 넣는다.
    ('사고2', '사람이 기계에 손이 말려 들어갔어'),
)


def main():
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    app, w, link = ui.build_app(['--live', '127.0.0.1'])
    link.start()
    w.show()
    d = w.assistant
    t = {'shown': time.perf_counter(), 'questions': []}
    state = {'i': -1, 'q0': 0.0, 'first': False, 'row': None}

    def shot(name, widget=None):
        path = os.path.join(out, name + '.png')
        (widget or d).grab().save(path)
        print('캡처', os.path.basename(path), flush=True)

    def later(ms, fn):
        QtCore.QTimer.singleShot(ms, fn)

    def next_question():
        state['i'] += 1
        if state['i'] >= len(QUESTIONS):
            print(json.dumps(t, ensure_ascii=False), flush=True)
            later(300, w.close)
            return
        tag, q = QUESTIONS[state['i']]
        state.update(q0=time.perf_counter(), first=False,
                     row={'tag': tag, 'question': q})
        t['questions'].append(state['row'])
        n = state['i'] + 1
        d.ask(q)
        if d._busy:
            later(50, lambda: shot(f'{n}_{tag}_1_근거확인중'))
        else:       # 고정 답 — 즉시 전체 표시
            state['row']['done_sec'] = round(time.perf_counter() - state['q0'], 3)
            later(200, lambda: (shot(f'{n}_{tag}_즉시답'), next_question()))

    def on_head(_):
        state['row']['head_sec'] = round(time.perf_counter() - state['q0'], 3)
        n, tag = state['i'] + 1, state['row']['tag']
        later(30, lambda: shot(f'{n}_{tag}_2_머리줄_작성중'))

    def on_line(_):
        if state['first']:
            return
        state['first'] = True
        state['row']['first_sec'] = round(time.perf_counter() - state['q0'], 3)
        n, tag = state['i'] + 1, state['row']['tag']
        later(320, lambda: shot(f'{n}_{tag}_3_첫문장'))

    def on_ready(answer, source, elapsed):
        state['row']['done_sec'] = round(time.perf_counter() - state['q0'], 3)
        n, tag = state['i'] + 1, state['row']['tag']

        def fin():
            shot(f'{n}_{tag}_4_완료')
            if n == 1:      # 관제 창과 함께 보이는 화면 전체 한 장
                app.primaryScreen().grabWindow(0).save(
                    os.path.join(out, '0_화면전체.png'))
            next_question()
        later(700, fin)

    def on_warmed(err):
        t['warm_sec'] = round(time.perf_counter() - t['shown'], 3)
        t['warm_err'] = err
        if err:
            print(json.dumps(t, ensure_ascii=False), flush=True)
            later(300, w.close)
            return
        w.assistant.open_drawer()
        later(500, lambda: (shot('0_준비완료_관제창', w), next_question()))

    d.answer_head.connect(on_head)
    d.answer_line.connect(on_line)
    d.answer_ready.connect(on_ready)
    w.chat_warmed.connect(on_warmed)
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
