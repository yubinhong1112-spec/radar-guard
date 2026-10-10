"""test_chat_stream.py — 챗봇 줄 단위 스트리밍 표시 검사 (T-CC07c)

  실행: [내 PC PowerShell]
      python 01_현행코드\\eval\\test_chat_stream.py

무엇을 보나
  1. 안전망 주입 — 위험 표현이 둘째 줄에 오는 가짜 응답. 첫 줄만 보였다가
     지워지고 즉시조치 + 안내 문장으로 바뀌는지, 요청이 중간에 닫히는지.
  2. 정상 응답 — 화면에 남는 최종 답이 같은 원문의 비스트리밍 후처리 결과와
     글자가 같은지(마크다운 제거 · 꼬리 문장 한 번).
  3. 답이 흘러나오는 중에 들어온 고정 답 질문이 지워지지 않는지.
  4. 문장 단위(T-CC07d) — 한 줄짜리 답의 둘째 문장에 위험 표현이 있을 때 첫
     문장만 보였다가 지워지는지, 소수점·번호에서 끊기지 않는지.

  DB·Ollama 는 띄우지 않는다 — urlopen 과 build_chat_request 를 가짜로 바꾼다.
  보는 것은 표시 경로이지 답의 품질이 아니다.
"""
import json
import os
import sys
import time
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(HERE))
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from PyQt5 import QtWidgets        # noqa: E402
import console_ui as ui            # noqa: E402

LINE1 = '환자를 **움직이지** 않는다.'
BAD = '상태가 좋아 보이면 일으켜 세워도 좋다.'
GOOD = ['* 119 에 신고한다.', '# 호흡과 의식을 확인한다.']


class FakeStream:
    """Ollama 스트림 대역. 줄·문장이 끝난 조각 뒤에 gap 초를 쉰다."""

    def __init__(self, lines, gap, parts=None):
        text = '\n'.join(lines)
        self.parts = parts or [text[i:i + 7] for i in range(0, len(text), 7)]
        self.gap, self.sent, self.closed = gap, 0, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def __iter__(self):
        for i, part in enumerate(self.parts):
            self.sent += 1
            yield json.dumps({'response': part,
                              'done': i == len(self.parts) - 1}
                             ).encode('utf-8') + b'\n'
            if '\n' in part or part.endswith(' '):
                time.sleep(self.gap)


def drawer(alert):
    console = types.SimpleNamespace(alert=alert, pkt={})
    d = ui.AssistantDrawer(console=console)
    d._local_answer = lambda q: None        # 규칙 응답 경로는 여기서 안 본다
    return d


def ask(d, app, lines, gap, extra=None, parts=None):
    """질문 하나를 보내고 끝날 때까지 화면 글자를 0.02초마다 받아 적는다."""
    stream = FakeStream(lines, gap, parts)
    done, shots = [], []
    d.answer_ready.connect(lambda a, s, e: done.append(a))
    d.answer_failed.connect(lambda err: done.append(RuntimeError(err)))
    orig = urllib.request.urlopen, ui.build_chat_request
    urllib.request.urlopen = lambda req, timeout=None: stream
    ui.build_chat_request = lambda q, **kw: {
        'prompt': 'p', 'sources': [], 'route': 'incident', 'event': 'fall_detected',
        'fixed_answer': None, 'guard_key': 'fall_detected',
        'answer_suffix': ui.CHAT_INCIDENT_SUFFIX}
    try:
        d.ask('작업자가 쓰러졌어')
        t0 = time.time()
        while time.time() - t0 < 10 and (d._busy or d._mark is not None):
            app.processEvents()
            shots.append(d.log.toPlainText())
            if extra and LINE1.replace('**', '') in shots[-1]:
                extra()
                extra = None
            time.sleep(0.02)
    finally:
        urllib.request.urlopen, ui.build_chat_request = orig
    return stream, done, shots


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    ng = []

    def check(ok, what):
        print(('OK ' if ok else 'NG ') + what)
        if not ok:
            ng.append(what)

    first = LINE1.replace('**', '')
    alert = {'type': 'fall_detected', 'zone': 'A'}

    # 1. 안전망 주입
    d = drawer(alert)
    stream, done, shots = ask(d, app, [LINE1, BAD, '뒤따르는 줄'], gap=0.6)
    end = d.log.toPlainText()
    check(any('질문 분류: 사고 대응' in s for s in shots), '머리줄(질문 분류 · 근거) 표시')
    check(any(first in s for s in shots), '첫 줄이 화면에 보였다')
    check(not any('일으켜' in s for s in shots), '위험 줄은 한 번도 안 보였다')
    check(first not in end, '걸린 뒤 보였던 줄이 지워졌다')
    check(ui.GUARD_NOTICE in end, '안전 검사 안내 문장 표시')
    check(all(l in end for l in ui._ia_lines('fall_detected')), '즉시조치 줄 표시')
    check(ui.CHAT_INCIDENT_SUFFIX not in end, '걸린 답에는 꼬리 문장을 안 붙인다')
    check(stream.closed and stream.sent < len(stream.parts),
          f'요청을 중간에 닫았다({stream.sent}/{len(stream.parts)} 조각)')

    # 2. 정상 응답 = 비스트리밍 후처리와 같은 글자
    d = drawer(alert)
    lines = [LINE1] + GOOD
    stream, done, shots = ask(d, app, lines, gap=0.2)
    want = (ui.strip_chat_markdown('\n'.join(lines).strip()).strip()
            + '\n' + ui.CHAT_INCIDENT_SUFFIX)
    end = d.log.toPlainText()
    check(done == [want], '최종 답이 비스트리밍 후처리 결과와 같다')
    check(all(l in end for l in want.split('\n')), '최종 답 전 줄이 화면에 있다')
    check(end.count('※') == 1, '꼬리 문장은 끝에 한 번')
    check('**' not in ''.join(shots) and '#' not in ''.join(shots),
          '마크다운 기호가 한 번도 안 보였다')
    grow = [s for s in shots if first in s and GOOD[1][2:] not in s]
    check(bool(grow), '줄이 차례로 붙었다(첫 줄만 보인 순간이 있다)')

    # 3. 답이 흘러나오는 중의 고정 답 질문
    d = drawer(alert)
    stream, done, shots = ask(d, app, lines, gap=0.4,
                              extra=lambda: d.ask('임계값 좀 낮춰줘'))
    end = d.log.toPlainText()
    check(ui.CHAT_TUNE_REPLY in end and '임계값 좀 낮춰줘' in end,
          '중간에 들어온 질문과 고정 답이 남아 있다')
    check(end.index(ui.CHAT_TUNE_REPLY) < end.index('호흡과 의식'),
          '스트리밍 답이 그 뒤에 이어 붙었다')
    check(end.count(first) == 1, '미리보기가 겹쳐 남지 않았다')

    # 4. 문장 단위 — 한 줄짜리 답
    got = []
    ui.read_chat_stream(
        FakeStream([], 0, ['1. 레이더는 5.6 m 안', '에서 봅니다. 맞나', '요? 네! 끝']),
        True, got.append)
    check(got == ['1. 레이더는 5.6 m 안에서 봅니다. ', '맞나요? ', '네! ', '끝 '],
          f'문장 끝에서만 끊는다(소수점·번호 제외) → {got}')
    d = drawer(alert)
    sent1 = '환자를 움직이지 않습니다.'
    stream, done, shots = ask(d, app, [], gap=0.6, parts=[
        '환자를 움직이지 ', '않습니다. ', '상태가 좋아 보이면 일', '으켜 세워도 좋습니다. ',
        '뒤 문장입니다.'])
    end = d.log.toPlainText()
    check(any(sent1 in s for s in shots), '한 줄 답의 첫 문장이 먼저 보였다')
    check(not any('일으켜' in s for s in shots), '위험 문장은 한 번도 안 보였다')
    check(sent1 not in end and ui.GUARD_NOTICE in end,
          '걸린 뒤 첫 문장이 지워지고 안내 문장으로 바뀌었다')
    check(stream.closed and stream.sent < len(stream.parts),
          f'요청을 중간에 닫았다({stream.sent}/{len(stream.parts)} 조각)')

    print(f'스트리밍 표시 — NG {len(ng)}건')
    return 1 if ng else 0


if __name__ == '__main__':
    sys.exit(main())
