"""console_ui.py — Radar-Guard 관제 앱 [노트북]  · 화면 계층 v2.0

  실행: [내 PC PowerShell]
      cd "C:\\Users\\82102\\OneDrive\\문서\\Claude\\Projects\\공모전\\01_현행코드"
      python console_ui.py                        # 데모 모드 (젯슨 불필요)
      python console_ui.py --live 192.168.0.50    # 젯슨 실데이터
      python console_ui.py --live 127.0.0.1       # sim_jetson.py 와 루프백 검증

  필요 패키지: pip install pyqt5 pyqtgraph numpy pyopengl
    (pyopengl 이 없으면 3D 대신 2D 측면도로 자동 대체된다 — 동작엔 지장 없음)
  RAG/LLM 은 있을 때만 쓴다(없어도 탐지·표시·즉시조치는 정상):
      ollama serve && ollama pull gemma2:2b && ollama pull bge-m3
      docker start radar-guard-db

═══ 이 파일이 하는 일 / 하지 않는 일 ═══
  한다   : 화면 구조 · 정보 위계 · 색 · 간격 · 창 구성
  안 한다: 판정, 레이더 수신, 차단기 제어, SOP 검색/LLM 호출
           ↑ 전부 radar_core.py 의 클래스를 그대로 import 해서 쓴다.
             복붙이 아니라 같은 코드 객체다 — 로직이 갈라질 수 없다.

  ⚠ 2026-08-01 파일 구성이 바뀌었다:
      radar_core.py   수신·자세추정·SOP검색·공용위젯   (화면과 무관한 전부)
      console_ui.py   이 파일. 화면 전부 + 실행 진입점
    v1 화면은 _구버전보관/코드/console_ui_v1_0801_최종.py 에 보관.

═══ v1 대비 무엇을 왜 바꿨나 ═══
  1. 좌측 고정 네비게이션
       v1 은 화면 이동이 '← 개요' 버튼 하나 + 하단 버튼 9개였다. 지금 어느 화면에
       있는지, 갈 수 있는 곳이 어디인지가 화면에 없었다.
       ⚠ 단, 미확인(UNACK) 경보 중에는 네비가 잠긴다. 경보를 두고 다른 화면으로
         떠나는 경로가 있으면 안 된다 (v1 의 강제 복귀 로직과 동일한 목적).

  2. 조치 가이드를 별도 OS 창 → 인앱 우측 드로어
       v1 의 SopPopup 은 독립 최상위 윈도우였다. 듀얼모니터·화면 밖·뒤로 숨음이
       전부 가능했고 경보 상황에서 창을 찾아 헤매게 된다.
       → 본 창 안에서 오른쪽으로 밀려 들어온다. 절대 화면 밖으로 못 나간다.

  3. 개발 정보와 관제 정보 분리
       v1 개요 화면에 127.0.0.1 / seq 1734 / pgvector / bge-m3 / gemma2:2b /
       임계 0.1898 이 그대로 있었다. 근무자가 볼 정보가 아니다.
       → 대시보드에는 '연결됨 · 96ms · 유실 0' 처럼 운용상 의미 있는 것만 남기고,
         모델명·포트·접속 문자열·임계값은 [설정] 의 AI·SOP / 판정 / 진단 탭으로
         보낸다. 삭제가 아니라 이동이다 — 원래 그 탭들이 이미 있었다.

  4. 빨강의 역할을 하나로
       v1 경보 화면은 배너·수치·로그·버튼·구역카드가 전부 빨강이었다. 다 중요해
       보이면 아무것도 중요하지 않다.
       → 빨강 = ① 활성 경보 배너 ② 지금 눌러야 할 [조치 방법] ③ 차단된 구역.
         수치는 경보 중에도 흰색을 유지한다. 시선의 종착점을 하나로 만든다.
       → 드로어의 '확인했습니다' 는 빨강에서 시안으로. 확인은 위험한 조작이 아니다.

  5. 확신도를 지표로 승격 / '포인트 개수' 는 강등
       v1 은 대형 지표 4개가 높이·움직임·포인트수·이상도였고, 판정 확신도는
       로그 한 줄에만 있었다. 포인트 1개를 크게 띄우는 건 "판정이 점 1개에
       걸려 있다"를 광고하는 셈이다.
       → 확신도는 경보 요약 패널 상단으로, 포인트 수는 4번째 지표로 유지하되
         '누적 기준'을 함께 적어 오해를 없앤다.

  6. 로그 출처 구분
       젯슨이 보낸 영문 원문과 이 앱이 만든 한글 문장이 섞여 같은 사건이 두 번
       찍혔다. 지우지 않는다(감사 기록이다). 대신 젯슨 원문에는 태그를 달고
       색을 낮춰, 중복이 아니라 '원문 + 해석'으로 읽히게 한다.

═══ 경보 상태기계 (ISA-18.2) — v1 과 완전히 동일 ═══
  NORMAL ──경보──> UNACK(점멸·소리) ──확인함──> ACK(점멸·소리 정지, 상황 지속)
         <──상황 종료(사람이 누름)──
  ⚠ 자동 해제는 없다. ⚠ '확인함'은 소리를 끄는 것이지 경보를 지우는 것이 아니다.
"""
import sys
import time
import json
import threading
import argparse
from collections import deque
from html import escape as html_escape

from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

# ── 로직 계층. 복사하지 않고 import 한다 → 같은 코드 객체 ─────────────────
import facility as fac
import radar_core as core
from radar_core import (
    RadarLink, INSTANT_ACTION, INSTANT_ACTION_UNKNOWN,
    PowerPopup, RestorePopup, GraphPopup, SettingsPopup,
    RAG_OK, EMBED_MODEL, HAS_GL,
    ST_NORMAL, ST_UNACK, ST_ACK,
)
from radar_common import (
    LINK_TIMEOUT, CMD_RESOLVE, CMD_RESTORE, CMD_ENTER, CMD_EXIT, CEILING_H,
    PH_READY, PH_LIVE, PHASE_KO, parse_pre_alert,
    EVENT_KO, ZONE_IDS, ZONE_KO, RADAR_ZONE, ZONE_DEVICE, zone_equipped,
    SEV_KO, GATE_META, REJECT_KO, EVIDENCE_KO, AUTO_TRIP_EVENTS,
    sev_color, event_sev,
    BG, PANEL, PANEL_HI, PANEL_LO, EDGE, TXT, DIM, FAINT,
    CYAN, GREEN, AMBER, RED, GRID,
    BG_ALERT, BG_OK, BG_WARN, BG_SEL, SEV_BG, SEV_BG_HI, RADIUS, RADIUS_SM,
)

APP_VERSION = 'v2.0'
# Ollama는 한 번에 한 작업만 수행한다. SOP 사전 준비와 질의가 겹치면 CPU를
# 서로 빼앗아 둘 다 늦어지므로, UI 밖 작업 스레드에서만 직렬화한다.
AI_WORK_LOCK = threading.Lock()

# ══════════════════════════════════════════════════════════════════════
# 0. 타이포그래피
# ══════════════════════════════════════════════════════════════════════
#  ⚠ Pretendard 는 좋은 선택이지만 없는 PC 에서 조용히 다른 폰트로 대체되면서
#    글자폭이 달라져 레이아웃이 밀린다. 설치돼 있을 때만 쓰고, 아니면 윈도우
#    기본 탑재 고딕으로 간다. '있으면 좋고 없어도 안 깨진다'가 기준이다.
FONT_CANDIDATES = ['Pretendard', 'Pretendard Variable', 'Noto Sans KR',
                   'Malgun Gothic', 'Gulim']
FONT = 'Malgun Gothic'


def resolve_font():
    """설치된 폰트 중 우선순위가 가장 높은 것을 고른다. QApplication 생성 후 호출."""
    global FONT
    fams = set(QtGui.QFontDatabase().families())
    if not fams:
        # ⚠ [8/02 실측] families() 가 비면 아래 루프가 한 번도 매칭되지 않아 108줄
        #   초기값이 조용히 유지된다. 데모 PC 에서 폰트 캐시가 깨지거나 Noto Sans KR
        #   이 빠지면 지금 구조로는 아무 신호 없이 글자가 밀린다 — 그래서 경고만
        #   남긴다. 동작(초기값 유지)은 바꾸지 않는다.
        print(f'⚠ QFontDatabase 가 폰트를 하나도 찾지 못함 — {FONT}(초기값)로 진행',
              file=sys.stderr)
    for f in FONT_CANDIDATES:
        if f in fams:
            FONT = f
            break
    core.FONT = FONT        # v1 에서 가져다 쓰는 팝업들도 같은 폰트를 쓰게 한다
    return FONT


# 8px 그리드
SP1, SP2, SP3, SP4, SP5, SP6 = 4, 8, 12, 16, 24, 32

# 폰트 단계 (pt)
F_DISPLAY = 26     # 대시보드 제목
F_HERO = 30        # 주 수치
F_H1 = 19          # 화면 제목 · 상태 제목
F_H2 = 15          # 카드 제목
F_BODY = 11        # 본문 · 버튼
F_LABEL = 10       # 라벨
F_CAP = 9          # 캡션


# ══════════════════════════════════════════════════════════════════════
# 1. UI 키트
# ══════════════════════════════════════════════════════════════════════
def f(size=F_BODY, bold=False):
    q = QtGui.QFont(FONT, size)
    q.setBold(bold)
    return q


f_ = f          # paintEvent 안에서 f 는 좌표변환에 쓰므로 폰트용 별칭


def lb(text='', size=F_BODY, color=TXT, bold=False, wrap=False, align=None,
       spacing=0):
    w = QtWidgets.QLabel(text)
    w.setFont(f(size, bold))
    css = f'color:{color};border:none;background:transparent;'
    if spacing:
        css += f'letter-spacing:{spacing}px;'
    w.setStyleSheet(css)
    w.setWordWrap(wrap)
    if align is not None:
        w.setAlignment(align)
    return w


def eyebrow(text):
    """카드 상단 소제목 — 대문자 + 자간. 정보가 아니라 '구획 이름'임을 알린다."""
    return lb(text, F_CAP, DIM, spacing=1)


def card(bg=PANEL, border=None, radius=RADIUS):
    fr = QtWidgets.QFrame()
    b = f'1px solid {border}' if border else 'none'
    fr.setStyleSheet(f'QFrame{{background:{bg};border:{b};border-radius:{radius}px;}}')
    return fr


def vbox(w, m=SP4, s=SP3):
    v = QtWidgets.QVBoxLayout(w)
    v.setContentsMargins(m, m, m, m)
    v.setSpacing(s)
    return v


def hbox(w=None, m=0, s=SP3):
    h = QtWidgets.QHBoxLayout(w) if w is not None else QtWidgets.QHBoxLayout()
    h.setContentsMargins(m, m, m, m)
    h.setSpacing(s)
    return h


# ── 버튼 ──────────────────────────────────────────────────────────────
#   kind: 'primary'(시안·주 액션) / 'danger'(빨강·경보 대응) /
#         'ghost'(외곽선) / 'quiet'(배경만)
def button(text, kind='ghost', size=F_BODY, height=36, width=None):
    b = QtWidgets.QPushButton(text)
    b.setFont(f(size, bold=(kind in ('primary', 'danger'))))
    b.setMinimumHeight(height)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    if width:
        b.setFixedWidth(width)
    if kind == 'primary':
        css = (f'QPushButton{{background:{CYAN};color:#062028;border:none;'
               f'border-radius:{RADIUS_SM}px;padding:4px 14px;}}'
               f'QPushButton:hover{{background:#67E8F9;}}'
               f'QPushButton:pressed{{background:#0EA5BE;}}'
               f'QPushButton:disabled{{background:{PANEL_HI};color:{FAINT};}}')
    elif kind == 'danger':
        css = (f'QPushButton{{background:{RED};color:#FFFFFF;border:none;'
               f'border-radius:{RADIUS_SM}px;padding:4px 14px;}}'
               f'QPushButton:hover{{background:#F87171;}}'
               f'QPushButton:pressed{{background:#DC2626;}}'
               f'QPushButton:disabled{{background:{PANEL_HI};color:{FAINT};}}')
    elif kind == 'quiet':
        css = (f'QPushButton{{background:{PANEL_HI};color:{TXT};border:none;'
               f'border-radius:{RADIUS_SM}px;padding:4px 14px;}}'
               f'QPushButton:hover{{background:{BG_SEL};color:{CYAN};}}'
               f'QPushButton:disabled{{background:{PANEL_LO};color:{FAINT};}}')
    else:
        css = (f'QPushButton{{background:transparent;color:{TXT};'
               f'border:1px solid {EDGE};border-radius:{RADIUS_SM}px;'
               f'padding:4px 14px;}}'
               f'QPushButton:hover{{border-color:{CYAN};color:{CYAN};'
               f'background:{BG_SEL};}}'
               f'QPushButton:disabled{{color:{FAINT};border-color:{GRID};}}')
    b.setStyleSheet(css)
    return b


INPUT_QSS = (f'background:{PANEL_HI};color:{TXT};border:1px solid {EDGE};'
             f'border-radius:{RADIUS_SM}px;padding:6px 10px;')
TABLE_QSS = (f'QTableWidget{{background:{PANEL};color:{TXT};'
             f'gridline-color:{GRID};border:1px solid {EDGE};'
             f'border-radius:{RADIUS_SM}px;}}'
             f'QTableWidget::item{{padding:4px 6px;}}'
             f'QHeaderView::section{{background:{PANEL_HI};color:{DIM};'
             f'border:none;padding:7px 6px;}}')
SCROLL_QSS = (f'QScrollArea{{background:transparent;border:none;}}'
              f'QScrollBar:vertical{{background:transparent;width:9px;margin:0;}}'
              f'QScrollBar::handle:vertical{{background:{EDGE};'
              f'border-radius:4px;min-height:30px;}}'
              f'QScrollBar::handle:vertical:hover{{background:{FAINT};}}'
              f'QScrollBar::add-line,QScrollBar::sub-line{{height:0;}}'
              f'QScrollBar::add-page,QScrollBar::sub-page{{background:transparent;}}')
# ⚠ 본문 배경은 PANEL_HI 를 쓴다. PANEL_LO(#0D1420)는 창 배경(#090D18)과 거의
#   같아서 SOP 가이드 화면에서 본문 영역의 경계가 보이지 않았다.
TEXT_QSS = (f'QTextEdit{{background:{PANEL_HI};color:{TXT};'
            f'border:1px solid {EDGE};border-radius:{RADIUS_SM}px;padding:10px;}}'
            + SCROLL_QSS.replace('QScrollArea', 'QTextEdit'))


def styled_combo(items):
    c = QtWidgets.QComboBox()
    c.addItems(items)
    c.setFont(f(F_BODY))
    c.setMinimumHeight(34)
    c.setStyleSheet(
        f'QComboBox{{{INPUT_QSS}}}'
        f'QComboBox:hover{{border-color:{CYAN};}}'
        f'QComboBox::drop-down{{border:none;width:22px;}}'
        f'QComboBox QAbstractItemView{{background:{PANEL_HI};color:{TXT};'
        f'border:1px solid {EDGE};outline:none;'
        f'selection-background-color:{CYAN};selection-color:#062028;}}')
    return c


def styled_line(placeholder=''):
    e = QtWidgets.QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFont(f(F_BODY))
    e.setMinimumHeight(34)
    e.setStyleSheet(f'QLineEdit{{{INPUT_QSS}}}QLineEdit:focus{{border-color:{CYAN};}}')
    return e


def scrollable(inner, hbar=False):
    """1440×900 보다 좁은 화면에서 카드가 잘리거나 겹치지 않도록 감싼다.

    ⚠ QScrollArea 의 viewport 는 Qt 기본 팔레트(밝은 회색)로 '직접 칠한다'.
      스타일시트로 QScrollArea 배경만 지정하면 viewport 는 그대로 흰색이라
      다크 화면에 흰 판이 깔리고 흰 글씨가 사라진다(실측: 대시보드 제목 실종).
      → viewport 의 자동 배경 채움을 끄고 부모 배경이 비치게 한다.
    """
    sa = QtWidgets.QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QtWidgets.QFrame.NoFrame)
    sa.setStyleSheet(SCROLL_QSS)
    sa.setHorizontalScrollBarPolicy(
        QtCore.Qt.ScrollBarAsNeeded if hbar else QtCore.Qt.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    sa.viewport().setAutoFillBackground(False)
    inner.setAutoFillBackground(False)
    return sa


def as_page(w):
    """스택 페이지의 배경을 명시한다.

    QMainWindow{background:…} 는 자식 위젯까지 내려가지 않는다. 배경을 칠하지
    않는 QWidget 은 부모 것이 비치지만, 스타일시트가 걸린 조상이 하나라도 있으면
    Qt 가 기본 팔레트로 되돌린다. 페이지마다 한 번씩 못 박는 편이 안전하다.
    """
    w.setObjectName('rgPage')
    w.setStyleSheet(f'#rgPage{{background:{BG};}}')
    return w


def apply_dark_palette(app):
    """앱 전역 다크 팔레트.

    스타일시트를 안 건 위젯(QFileDialog, QMessageBox, 콤보 팝업, 테이블 편집기…)이
    밝은 기본값으로 뜨는 것을 막는다. 스타일시트는 '보이는 것'만 고치지만
    팔레트는 '아직 안 만든 것'까지 고친다.
    """
    C = QtGui.QColor
    p = QtGui.QPalette()
    p.setColor(QtGui.QPalette.Window, C(BG))
    p.setColor(QtGui.QPalette.WindowText, C(TXT))
    p.setColor(QtGui.QPalette.Base, C(PANEL))
    p.setColor(QtGui.QPalette.AlternateBase, C(PANEL_HI))
    p.setColor(QtGui.QPalette.Text, C(TXT))
    p.setColor(QtGui.QPalette.Button, C(PANEL))
    p.setColor(QtGui.QPalette.ButtonText, C(TXT))
    p.setColor(QtGui.QPalette.BrightText, C(RED))
    p.setColor(QtGui.QPalette.ToolTipBase, C(PANEL_HI))
    p.setColor(QtGui.QPalette.ToolTipText, C(TXT))
    p.setColor(QtGui.QPalette.Link, C(CYAN))
    p.setColor(QtGui.QPalette.Highlight, C(CYAN))
    p.setColor(QtGui.QPalette.HighlightedText, C('#062028'))
    try:
        p.setColor(QtGui.QPalette.PlaceholderText, C(FAINT))
    except AttributeError:
        pass                      # Qt 5.12 이하
    for role in (QtGui.QPalette.Text, QtGui.QPalette.ButtonText,
                 QtGui.QPalette.WindowText):
        p.setColor(QtGui.QPalette.Disabled, role, C(FAINT))
    app.setPalette(p)


# ══════════════════════════════════════════════════════════════════════
# 2. 상태 표시 부품
# ══════════════════════════════════════════════════════════════════════
class StatusDot(QtWidgets.QWidget):
    """색 단독 의존을 피한다 — 채움(정상) / 테두리만(대기) / 사선(경보)."""

    def __init__(self, d=9):
        super().__init__()
        self.d = d
        self.setFixedSize(d + 2, d + 2)
        self.color = FAINT
        self.filled = False

    def set(self, color, filled=True):
        self.color, self.filled = color, filled
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c = QtGui.QColor(self.color)
        p.setPen(QtGui.QPen(c, 1.6))
        p.setBrush(QtGui.QBrush(c) if self.filled else QtCore.Qt.NoBrush)
        r = self.d / 2
        p.drawEllipse(QtCore.QPointF(self.width() / 2, self.height() / 2), r, r)
        p.end()


class StatusCard(QtWidgets.QFrame):
    """대시보드 상단 장비 카드 한 장.

      [라벨]        ●
      연결됨                ← 상태 (색으로 의미)
      96ms · 유실 0         ← 운용상 의미 있는 상세만. 모델명·IP·임계값은 [설정]으로.
    """

    def __init__(self, title):
        super().__init__()
        self.setMinimumWidth(168)
        self.setMinimumHeight(96)
        v = vbox(self, SP3, SP1)
        top = hbox(s=SP2)
        top.addWidget(eyebrow(title))
        top.addStretch()
        self.dot = StatusDot()
        top.addWidget(self.dot)
        v.addLayout(top)
        self.value = lb('확인 중', F_H2, DIM, bold=True)
        v.addWidget(self.value)
        self.detail = lb('', F_CAP, FAINT, wrap=True)
        self.detail.setMinimumHeight(26)
        v.addWidget(self.detail)
        v.addStretch()
        self._paint(EDGE)

    def _paint(self, border):
        self.setStyleSheet(f'QFrame{{background:{PANEL};border:1px solid {border};'
                           f'border-radius:{RADIUS}px;}}')

    def set(self, ok, value, detail='', color=None):
        self.ok = ok
        c = color or (GREEN if ok else AMBER)
        self.dot.set(c, filled=ok)
        self.value.setText(value)
        self.value.setStyleSheet(f'color:{c};border:none;background:transparent;')
        self.detail.setText(detail)
        self._paint(RED if c == RED else EDGE)


class MetricTile(QtWidgets.QFrame):
    """큰 수치 한 칸.  라벨 / 값+단위 / 기준선(작은 글씨)

    ⚠ 최소 높이를 못 박는다. 세로 공간이 모자라면 Qt 는 위젯을 겹쳐서라도
      집어넣는데(실측: 라벨과 숫자가 포개짐), 관제 화면에서 숫자가 겹치는 건
      '못 읽는 것'이 아니라 '잘못 읽는 것'이라 더 위험하다.
    """

    def __init__(self, name, unit='', note='', size=21):
        super().__init__()
        self.setMinimumHeight(84)
        self.setMinimumWidth(96)
        self.setStyleSheet(f'QFrame{{background:{PANEL_HI};border:none;'
                           f'border-radius:{RADIUS_SM}px;}}')
        v = vbox(self, SP2, 2)
        v.addWidget(lb(name, F_LABEL, DIM))
        # ⚠ 숫자와 단위를 나란히 두면 칸이 좁아질 때(드로어를 열면 왼쪽 열이
        #   420px 줄어든다) Qt 가 둘을 겹쳐 그린다 — 실측에서 '0.35m'의 m 이
        #   숫자 위에 포개졌다. 단위는 아래 캡션으로 내려 겹칠 수 없게 만든다.
        #   값 글자 크기는 칸 폭에 맞춘 값이다. 30pt 는 '0.35'가 잘려 '0.3'으로
        #   보였다 — 계측값이 잘리는 건 오독이므로 크기보다 정확성이 우선이다.
        self.val = lb('—', size, TXT, bold=True)
        v.addWidget(self.val)
        self._unit = unit
        self._note = note
        self.note = lb(self._caption(note), F_CAP, FAINT)
        v.addWidget(self.note)

    def _caption(self, note):
        parts = [p for p in (self._unit, note) if p]
        return ' · '.join(parts)

    def set(self, text, color=TXT, note=None):
        self.val.setText(text)
        self.val.setStyleSheet(f'color:{color};border:none;background:transparent;')
        if note is not None:
            self._note = note
        self.note.setText(self._caption(self._note))


class NavButton(QtWidgets.QPushButton):
    def __init__(self, icon, text):
        super().__init__(f'  {icon}   {text}')
        self.setFont(f(F_BODY))
        self.setCheckable(True)
        self.setMinimumHeight(42)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet(
            f'QPushButton{{background:transparent;color:{DIM};border:none;'
            f'border-radius:{RADIUS_SM}px;text-align:left;padding-left:8px;}}'
            f'QPushButton:hover{{background:{PANEL_HI};color:{TXT};}}'
            f'QPushButton:checked{{background:{BG_SEL};color:{CYAN};'
            f'font-weight:bold;}}'
            f'QPushButton:disabled{{color:{FAINT};background:transparent;}}')


# ══════════════════════════════════════════════════════════════════════
# 3. 장면 — 3D 포인트 클라우드 / 2D 측면도
# ══════════════════════════════════════════════════════════════════════
class SideView(QtWidgets.QWidget):
    """2D 측면도 — 가로축 X(m), 세로축 바닥 기준 높이(m).

    ⚠ 왜 탑다운 평면도가 아니라 측면도인가:
      IWR6843ISK-ODS 는 각분해능 약 28°. 수평(x·z) 좌표가 가장 부정확한 축이다.
      반대로 낙상 판정의 핵심 근거는 h_drop·height 즉 수직축이다.
      탑다운은 제일 못 믿을 축을 크게 보여주고 결정적인 축을 버린다.
      측면도는 '바닥 0m — 지금 0.57m — 서있음 1.6m' 가 그림 자체로 읽힌다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget()
        self.plot.setBackground(PANEL)
        self.plot.setAspectLocked(True)
        self.plot.setXRange(-1.7, 1.7)
        self.plot.setYRange(-0.2, CEILING_H + 0.15)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.getPlotItem().hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.10)
        for ax in ('bottom', 'left'):
            a = self.plot.getAxis(ax)
            a.setPen(pg.mkPen(EDGE))
            a.setTextPen(pg.mkPen(FAINT))
        # 축 제목을 따로 쓰지 않는다 — 기준선 캡션(바닥 / 서 있음 기준 / 센서)이
        #   이미 무슨 축인지 말한다. 세로 축 제목은 눈금 숫자와 겹쳤다(실측).
        self.plot.setLabel('bottom', '수평 위치 (m)', color=FAINT)
        # 기준선 — 이 세 줄이 곧 판정 근거의 눈금이다
        self._ref(0.0, EDGE, '바닥')
        self._ref(1.60, GRID, '서 있음 기준')
        self._ref(CEILING_H, GRID, f'센서 {CEILING_H:.2f}m')
        self.sc = pg.ScatterPlotItem(size=7, pen=None,
                                     brush=pg.mkBrush(34, 211, 238, 150))
        # connect='pairs' — 인체 도식은 끊긴 선분 모음이라 이어 그리면 안 된다
        self.cap = pg.PlotCurveItem(pen=pg.mkPen(GREEN, width=2.2), connect='pairs')
        self.hd = pg.ScatterPlotItem(size=11, pen=None, brush=pg.mkBrush(AMBER))
        for it in (self.sc, self.cap, self.hd):
            self.plot.addItem(it)
        v.addWidget(self.plot, 1)

    def _ref(self, y, color, text):
        ln = pg.InfiniteLine(pos=y, angle=0,
                             pen=pg.mkPen(color, width=1,
                                          style=QtCore.Qt.DashLine))
        self.plot.addItem(ln)
        t = pg.TextItem(text, color=FAINT, anchor=(0, 1))
        t.setFont(f(F_CAP))
        t.setPos(-1.65, y)
        self.plot.addItem(t)

    def render(self, cloud, pose, sev='normal', hide_shape=False):
        pt_c, fig_c, hd_c = core.Track3D.sev_colors(sev)
        if len(cloud):
            b = QtGui.QColor(pt_c)
            b.setAlpha(160)
            self.sc.setData(cloud[:, 0], CEILING_H - cloud[:, 1])
            self.sc.setBrush(pg.mkBrush(b))
        if pose and pose['shape_ok'] and not hide_shape:
            seg = core.Track3D.stick2d(pose)
            self.cap.setData(seg[:, 0], seg[:, 1])
            self.cap.setPen(pg.mkPen(fig_c, width=2.2))
            self.hd.setData([pose['head'][0]], [CEILING_H - pose['head'][1]])
            self.hd.setBrush(pg.mkBrush(hd_c))
        else:
            # 점이 모자라면 형상을 그리지 않는다 — 없는 것을 지어내지 않는다
            self.cap.setData([], [])
            self.hd.setData([], [])


class SceneView(QtWidgets.QWidget):
    """3D 포인트 클라우드 ↔ 2D 측면도 전환 컨테이너.

    ⚠ 3D 위젯은 v1 의 Track3D 를 그대로 쓴다(검증된 코드). 자세 추정도 그쪽
      PoseEstimator 하나만 쓰고 2D 는 그 결과를 받아 그리기만 한다 —
      두 화면이 서로 다른 것을 말할 수 없다.
    """
    MODE_3D, MODE_2D = 0, 1

    def __init__(self):
        super().__init__()
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.stack = QtWidgets.QStackedWidget()
        self.track = core.Track3D()
        self.side = SideView()
        self.stack.addWidget(self.track)
        self.stack.addWidget(self.side)
        v.addWidget(self.stack, 1)
        # stale 은 컨테이너 레벨에서 한 번만 덮는다 (두 화면 모두 가려야 한다)
        self.veil = QtWidgets.QLabel(self)
        self.veil.setAlignment(QtCore.Qt.AlignCenter)
        self.veil.setFont(f(14, True))
        self.veil.setStyleSheet(
            f'color:{AMBER};background:rgba(9,13,24,222);'
            f'border:1px solid {AMBER};border-radius:{RADIUS}px;')
        self.veil.hide()
        self._mode = self.MODE_3D if HAS_GL and self.track.gl else self.MODE_2D
        self.stack.setCurrentIndex(self._mode)

    def resizeEvent(self, e):
        self.veil.setGeometry(self.rect())
        super().resizeEvent(e)

    def set_mode(self, mode):
        if mode == self.MODE_3D and not (HAS_GL and self.track.gl):
            return False        # OpenGL 이 없으면 3D 로 갈 수 없다 — 조용히 거짓말하지 않는다
        self._mode = mode
        self.stack.setCurrentIndex(mode)
        return True

    def mode(self):
        return self._mode

    def has_3d(self):
        return bool(HAS_GL and self.track.gl)

    def set_stale(self, stale, msg=''):
        self.track.set_stale(False)     # 이중 오버레이 방지 — 우리 것만 쓴다
        if stale:
            self.veil.setText(msg)
            self.veil.setGeometry(self.rect())
            self.veil.show()
            self.veil.raise_()
        else:
            self.veil.hide()

    def push(self, st, sev='normal', hide_shape=False, incident=None):
        pose = self.track.push(st, sev, hide_shape, incident)  # 3D 갱신 + 자세 추정
        if self._mode == self.MODE_2D:
            self.side.render(self.track.pose.cloud(), pose, sev, hide_shape)
        return pose

    def redraw(self, sev='normal', hide_shape=False, incident=None):
        """새 패킷 없이 이미 누적된 값으로 두 위젯을 다시 그린다.

        ⚠ [8/02] set_mode() 는 모드만 바꾸고 렌더는 안 했다 — 3D(track)는
          push() 가 모드와 무관하게 매 패킷 갱신하지만 2D(side)는
          MODE_2D 일 때만 갱신돼서, 전환 직후엔 다음 패킷이 올 때까지
          죽은 색(생성 시 기본값)이 그대로 보였다. ConsoleV2._refresh_scene
          가 모드 전환 직후 이걸 불러 즉시 맞춘다.
        """
        pose = self.track.redraw(sev, hide_shape, incident)
        if self._mode == self.MODE_2D:
            self.side.render(self.track.pose.cloud(), pose, sev, hide_shape)
        return pose

    def reset_camera(self):
        self.track.reset_camera()


class ZoneStrip(QtWidgets.QWidget):
    """실시간 감시 화면의 '구역 현황' — A/B/C 를 가로로."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(86)
        self.state = {z: {'bad': False, 'off': False, 'power': 'unknown'}
                      for z in ZONE_IDS}
        self.sev = 'normal'

    def update_state(self, st, sev='normal'):
        zs = st.get('zone_state') or {}
        bs = ((st.get('breaker') or {}).get('state')) or {}
        power = st.get('power') or {}
        volt = power.get('volt')
        measured = power.get('connected') is True and volt is not None
        power_state = ('ok' if measured and volt > 0
                       else 'missing' if measured else 'unknown')
        for z in ZONE_IDS:
            self.state[z] = {'bad': zs.get(z, 'NORMAL') == 'ALERT',
                             'off': bs.get(z, 'ON') != 'ON',
                             'power': power_state if z == RADAR_ZONE else 'unknown'}
        self.sev = sev
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        W, H = self.width(), self.height()
        n = len(ZONE_IDS)
        gap = SP2
        zw = (W - gap * (n - 1)) / n
        for i, z in enumerate(ZONE_IDS):
            s, eq = self.state[z], zone_equipped(z)
            x = i * (zw + gap)
            hot = eq and (s['bad'] or s['off'] or s['power'] != 'ok')
            # 경보 중이면 그 등급 색, 차단만 남은 상태면 빨강(전원이 끊긴 사실)
            hot_c = (sev_color(self.sev) if (s['bad'] and self.sev != 'normal')
                     else RED if s['off'] else AMBER)
            if hot:
                bd, fill = hot_c, SEV_BG.get(self.sev, BG_ALERT)
            elif eq:
                bd, fill = EDGE, PANEL_HI
            else:
                bd, fill = None, PANEL_LO
            p.setPen(QtGui.QPen(QtGui.QColor(bd), 1.6) if bd else QtCore.Qt.NoPen)
            p.setBrush(QtGui.QBrush(QtGui.QColor(fill)))
            p.drawRoundedRect(QtCore.QRectF(x, 0, zw, H), RADIUS_SM, RADIUS_SM)
            pad = SP3
            # 경보 배지를 먼저 오른쪽 위에 놓고, 남은 폭 안에서 이름을 줄인다.
            #   (v1 은 고정 오프셋으로 그려서 폭이 좁아지면 이름과 겹쳤다)
            name_w = zw - pad * 2
            # 배지를 붙이면 이름 칸이 줄어든다. 칸이 좁으면 배지를 포기한다 —
            #   경보는 이미 카드 테두리(빨강)로 드러나므로 구역 이름이 우선이다.
            if eq and s['bad'] and zw >= 150:
                p.setFont(f(F_CAP, True))
                bw = p.fontMetrics().width('경보') + 12
                br = QtCore.QRectF(x + zw - pad - bw, 8, bw, 18)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QBrush(QtGui.QColor(hot_c)))
                p.drawRoundedRect(br, 9, 9)
                p.setPen(QtGui.QColor('#FFFFFF'))
                p.drawText(br, QtCore.Qt.AlignCenter, '경보')
                name_w -= bw + SP2
            p.setPen(QtGui.QColor(TXT if eq else FAINT))
            p.setFont(f(F_BODY, eq))
            nm = p.fontMetrics().elidedText(f'{z}  {ZONE_KO.get(z, "")}',
                                            QtCore.Qt.ElideRight, int(name_w))
            p.drawText(QtCore.QRectF(x + pad, 6, name_w, 24),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, nm)
            p.setFont(f(F_CAP))
            base = QtCore.QRectF(x + pad, H - 30, zw - pad * 2, 22)
            if not eq:
                p.setPen(QtGui.QColor(FAINT))
                p.drawText(base, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                           '장비 미설치')
                continue
            status = ('설비 회로 차단됨' if s['off'] else
                      '전원 미검출 (0 V)' if s['power'] == 'missing' else
                      '전원 계측 미확인' if s['power'] == 'unknown' else
                      '설비 회로 정상')
            p.setPen(QtGui.QColor(RED if s['off'] else
                                  GREEN if s['power'] == 'ok' else AMBER))
            p.drawText(base, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                       status)
        p.end()


# ══════════════════════════════════════════════════════════════════════
# 3-B. 시설 평면도 — 평상시 메인 화면
# ══════════════════════════════════════════════════════════════════════
class FacilityPlan(QtWidgets.QWidget):
    """facility.py 의 좌표만 보고 그린다. 이 클래스에 치수가 하나도 없다.

    ⚠ 그것이 "실사용 시 시설 도면으로 교체한다" 를 말이 아니라 사실로 만든다.
      facility.py 만 바꾸면 이 화면이 그 현장이 된다.

    ═══ 색이 말하는 것 ═══
      초록 감시 중        · 레이더가 살아 있고 이상 없음
      주황 사전경보       · 정지형 카운트다운 중 (경보 아님. 점멸·소리 없음)
      빨강 경보(점멸)      · 확정 사고
      회색 점선 장비 미설치 · 레이더가 없는 구역. 감시한다고 말하지 않는다.
      회색 실선 연결 대기   · 레이더는 있는데 링크가 없음
    """
    zone_clicked = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(360)
        self.setCursor(QtCore.Qt.ArrowCursor)
        self.setMouseTracking(True)
        self.state = {z: {'sev': None, 'live': False, 'pre': None, 'et': None}
                      for z in fac.ZONES}
        self.worker = None            # (zone, plan_x, plan_y)
        self.blink = False
        self._hot = None              # 마우스가 올라간 구역
        self._rects = {}              # 화면 좌표 캐시 (클릭 판정용)

    # ── 상태 갱신 ────────────────────────────────────────────────────
    def set_zone(self, zone, live=False, sev=None, pre=None, et=None):
        if zone in self.state:
            self.state[zone] = {'live': live, 'sev': sev, 'pre': pre, 'et': et}

    def set_worker(self, zone, cx, cz):
        p = fac.to_plan(zone, cx, cz)
        self.worker = None if p is None else (zone, p[0], p[1])

    def set_blink(self, on):
        if on != self.blink:
            self.blink = on
            self.update()

    # ── 좌표 변환 ────────────────────────────────────────────────────
    def _fit(self):
        W, H = fac.SIZE
        pad = SP2
        sx = (self.width() - pad * 2) / W
        sy = (self.height() - pad * 2 - 18) / H     # 하단 캡션 자리
        s = max(min(sx, sy), 1.0)
        ox = (self.width() - W * s) / 2
        oy = (self.height() - 18 - H * s) / 2
        return s, ox, oy

    def _pt(self, x, y, f):
        s, ox, oy = f
        return QtCore.QPointF(ox + x * s, oy + y * s)

    def _rect(self, r, f):
        s, ox, oy = f
        return QtCore.QRectF(ox + r[0] * s, oy + r[1] * s, r[2] * s, r[3] * s)

    # ── 입력 ─────────────────────────────────────────────────────────
    def _zone_at(self, pos):
        for z, r in self._rects.items():
            if r.contains(pos) and self.state.get(z, {}).get('live'):
                return z
        return None

    def mouseMoveEvent(self, e):
        z = self._zone_at(e.pos())
        if z != self._hot:
            self._hot = z
            self.setCursor(QtCore.Qt.PointingHandCursor if z
                           else QtCore.Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hot = None
        self.setCursor(QtCore.Qt.ArrowCursor)
        self.update()

    def mousePressEvent(self, e):
        z = self._zone_at(e.pos())
        if z and e.button() == QtCore.Qt.LeftButton:
            self.zone_clicked.emit(z)

    # ── 렌더 ─────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        f = self._fit()
        self._rects = {}

        # 바닥
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(QtGui.QColor(PANEL_LO)))
        p.drawRect(self._rect((0, 0) + tuple(fac.SIZE), f))

        # 설비 윤곽 (도면 밀도용 — 판정과 무관하므로 가장 낮은 대비)
        p.setPen(QtGui.QPen(QtGui.QColor(GRID), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        for item in fac.EQUIPMENT:
            if not isinstance(item[0], str):
                p.drawRect(self._rect(item, f))
                continue
            kind, *v = item
            if kind == 'pipe':
                p.drawLine(self._pt(v[0], v[1], f), self._pt(v[2], v[3], f))
            elif kind == 'tank':
                c = self._pt(v[0], v[1], f)
                radius = v[2] * f[0]
                p.drawEllipse(c, radius, radius)
            else:
                p.drawRect(self._rect(v[:4], f))

        # 구역
        for z, d in fac.ZONES.items():
            st = self.state.get(z, {})
            rect = self._rect(d['rect'], f)
            self._rects[z] = rect.toRect()
            sev, live, pre = st.get('sev'), st.get('live'), st.get('pre')
            if sev in ('critical', 'warning'):
                col = sev_color(sev)
                on = (sev != 'critical') or self.blink
                p.setPen(QtGui.QPen(QtGui.QColor(col), 2.5 if on else 1.5))
                p.setBrush(QtGui.QBrush(QtGui.QColor(
                    SEV_BG_HI.get(sev) if on else SEV_BG.get(sev))))
            elif pre:
                p.setPen(QtGui.QPen(QtGui.QColor(AMBER), 2,
                                    QtCore.Qt.DashLine))
                p.setBrush(QtGui.QBrush(QtGui.QColor(BG_WARN)))
            elif live:
                p.setPen(QtGui.QPen(QtGui.QColor(GREEN), 1.6))
                p.setBrush(QtGui.QBrush(QtGui.QColor(BG_OK)))
            elif not zone_equipped(z):
                p.setPen(QtGui.QPen(QtGui.QColor(EDGE), 1.2,
                                    QtCore.Qt.DashLine))
                p.setBrush(QtGui.QBrush(QtGui.QColor(PANEL_LO)))
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(FAINT), 1.4))
                p.setBrush(QtGui.QBrush(QtGui.QColor(PANEL)))
            p.drawRect(rect)
            if z == self._hot:
                p.setPen(QtGui.QPen(QtGui.QColor(CYAN), 2))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRect(rect.adjusted(2, 2, -2, -2))

        # 감지영역 — 실측 DANGER_ZONES 스케일. 여기만 '진짜 치수' 다.
        for z in fac.RADARS:
            cov = fac.coverage(z)
            if not cov:
                continue
            st = self.state.get(z, {})
            col = (sev_color(st['sev']) if st.get('sev') else
                   (AMBER if st.get('pre') else
                    (CYAN if st.get('live') else FAINT)))
            c = QtGui.QColor(col)
            p.setPen(QtGui.QPen(c, 1.4, QtCore.Qt.DashLine))
            c.setAlpha(28)
            p.setBrush(QtGui.QBrush(c))
            p.drawRect(self._rect(cov, f))

        # 벽 (가장 위에 — 도면의 골격)
        p.setPen(QtGui.QPen(QtGui.QColor(EDGE), 2))
        for x1, y1, x2, y2 in fac.WALLS:
            p.drawLine(self._pt(x1, y1, f), self._pt(x2, y2, f))

        self._draw_evac(p, f)
        self._draw_radars(p, f)
        self._draw_labels(p, f)
        self._draw_worker(p, f)

        p.end()

    def _draw_evac(self, p, f):
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor(GREEN), 1.4, QtCore.Qt.DashLine))
        for route in fac.EVAC_ROUTES:
            for i in range(len(route) - 1):
                p.drawLine(self._pt(*route[i], f), self._pt(*route[i + 1], f))
        p.setFont(f_(F_CAP, True))
        ew = p.fontMetrics().width('비상구') + 12
        for x, y, _d in fac.EXITS:
            c = self._pt(x, y, f)
            box = QtCore.QRectF(c.x() - ew / 2, c.y() - 9, ew, 18)
            p.setPen(QtGui.QPen(QtGui.QColor(GREEN), 1.6))
            p.setBrush(QtGui.QBrush(QtGui.QColor(BG_OK)))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QtGui.QColor(GREEN))
            p.drawText(box, QtCore.Qt.AlignCenter, '비상구')

    def _draw_radars(self, p, f):
        for z, r in fac.RADARS.items():
            st = self.state.get(z, {})
            col = (sev_color(st['sev']) if st.get('sev') else
                   (AMBER if st.get('pre') else
                    (CYAN if st.get('live') else FAINT)))
            c = self._pt(r['pos'][0], r['pos'][1], f)
            p.setPen(QtGui.QPen(QtGui.QColor(col), 1.6))
            p.setBrush(QtGui.QBrush(QtGui.QColor(BG)))
            p.drawEllipse(c, 7, 7)
            p.setBrush(QtGui.QBrush(QtGui.QColor(col)))
            p.drawEllipse(c, 2.5, 2.5)

    def _draw_labels(self, p, f):
        for z, d in fac.ZONES.items():
            st = self.state.get(z, {})
            rect = self._rect(d['rect'], f)
            sev, pre, live = st.get('sev'), st.get('pre'), st.get('live')
            if sev:
                col, txt = sev_color(sev), EVENT_KO.get(st.get('et'), '경보')
            elif pre:
                col, txt = AMBER, pre
            elif live:
                col, txt = GREEN, '감시 중'
            elif not zone_equipped(z):
                col, txt = FAINT, '장비 미설치'
            else:
                col, txt = FAINT, '연결 대기'
            # ⚠ 두 줄을 폰트 높이로 띄운다. 고정 오프셋을 쓰면 폰트가 바뀔 때
            #   이름 위에 상태가 포개진다(실측).
            p.setFont(f_(F_H2, True))
            fm1 = p.fontMetrics()
            w_avail = int(rect.width() - SP3 * 2)
            y0 = rect.top() + SP2
            p.setPen(QtGui.QColor(TXT if (live or sev) else FAINT))
            p.drawText(QtCore.QRectF(rect.left() + SP3, y0, w_avail,
                                     fm1.height()),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                       fm1.elidedText(f"{z}  {d['name']}",
                                      QtCore.Qt.ElideRight, w_avail))
            p.setFont(f_(F_CAP, bool(sev or pre)))
            fm2 = p.fontMetrics()
            p.setPen(QtGui.QColor(col))
            p.drawText(QtCore.QRectF(rect.left() + SP3, y0 + fm1.height() + 2,
                                     w_avail, fm2.height()),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                       fm2.elidedText(txt, QtCore.Qt.ElideRight, w_avail))

    def _draw_worker(self, p, f):
        if not self.worker:
            return
        z, x, y = self.worker
        st = self.state.get(z, {})
        col = QtGui.QColor(sev_color(st['sev']) if st.get('sev') else
                           (AMBER if st.get('pre') else CYAN))
        c = self._pt(x, y, f)
        halo = QtGui.QColor(col)
        halo.setAlpha(60)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(halo))
        p.drawEllipse(c, 11, 11)
        p.setBrush(QtGui.QBrush(col))
        p.setPen(QtGui.QPen(QtGui.QColor(BG), 1.5))
        p.drawEllipse(c, 5, 5)


# ══════════════════════════════════════════════════════════════════════
# 4. 이벤트 타임라인 (하단 접이식)
# ══════════════════════════════════════════════════════════════════════
class Timeline(QtWidgets.QFrame):
    """v1 의 '최근 기록'. 접을 수 있고, 젯슨 원문과 앱 문장을 구분해 보여준다."""
    ROWS = 4

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f'QFrame{{background:{PANEL};border:none;'
                           f'border-radius:{RADIUS}px;}}')
        v = vbox(self, SP3, SP2)
        head = hbox(s=SP2)
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setArrowType(QtCore.Qt.DownArrow)
        self.toggle.setStyleSheet('QToolButton{border:none;background:transparent;}')
        self.toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self.toggle.clicked.connect(self._flip)
        head.addWidget(self.toggle)
        head.addWidget(eyebrow('이벤트 타임라인'))
        head.addStretch()
        self.count = lb('', F_CAP, FAINT)
        head.addWidget(self.count)
        v.addLayout(head)
        self.body = QtWidgets.QWidget()
        bv = QtWidgets.QVBoxLayout(self.body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(3)
        self.rows = []
        for _ in range(self.ROWS):
            r = hbox(s=SP2)
            ts = lb('', F_CAP, FAINT)
            ts.setFixedWidth(64)
            tag = lb('', F_CAP, FAINT)
            tag.setFixedWidth(34)
            msg = lb('', F_CAP, DIM)
            r.addWidget(ts)
            r.addWidget(tag)
            r.addWidget(msg, 1)
            bv.addLayout(r)
            self.rows.append((ts, tag, msg))
        v.addWidget(self.body)
        self.buf = deque(maxlen=self.ROWS)
        self.total = 0
        # 중복 방지 키. ⚠ v1 은 set 이라 무한히 커졌다 — 젯슨 로그 문자열이
        #   전부 쌓이므로 하루 종일 켜 두면 계속 자란다. 최근 것만 기억한다
        #   (같은 로그가 400줄 뒤에 다시 오면 그건 실제로 다시 일어난 사건이다).
        self._seen = set()
        self._seen_q = deque(maxlen=400)

    def _flip(self):
        vis = not self.body.isVisible()
        self.body.setVisible(vis)
        self.toggle.setArrowType(QtCore.Qt.DownArrow if vis else QtCore.Qt.RightArrow)

    def add(self, text, color=DIM, key=None, src='앱'):
        """src='젯슨' 은 장비가 보낸 원문. 지우지 않되 태그로 구분한다."""
        if key is not None:
            if key in self._seen:
                return
            if len(self._seen_q) == self._seen_q.maxlen:
                self._seen.discard(self._seen_q[0])
            self._seen_q.append(key)
            self._seen.add(key)
        self.total += 1
        self.buf.appendleft((time.strftime('%H:%M:%S'), src, text, color))
        for i, (ts, tag, msg) in enumerate(self.rows):
            if i < len(self.buf):
                t, s, m, c = self.buf[i]
                ts.setText(t)
                tag.setText(s)
                tag.setStyleSheet(f'color:{FAINT if s == "젯슨" else DIM};'
                                  f'border:none;background:transparent;')
                msg.setText(m)
                msg.setStyleSheet(f'color:{c};border:none;background:transparent;')
            else:
                ts.setText('')
                tag.setText('')
                msg.setText('')
        self.count.setText(f'{self.total}건')


# ══════════════════════════════════════════════════════════════════════
# 5. 조치 가이드 · 판단 근거  (드로어 내용 — 별도 창이 아니다)
# ══════════════════════════════════════════════════════════════════════
class SopView(QtWidgets.QWidget):
    """즉시조치 → 공식 매뉴얼 → 실측 브리핑 → AI 생성 SOP 순.

    ⚠ 순서가 곧 권위 순서다. v1 은 AI 요약이 매뉴얼과 같은 위계로 붙어 있었고,
      실제로 SOP('환자를 움직이지 마십시오')와 AI 문장('움직여야 합니다')이
      한 화면에서 충돌했다. AI 생성문은 맨 아래 참고용으로만 두고, 그보다
      먼저 즉시조치·공식 원문·젯슨 실측값을 결정적으로 표시한다.
    """

    def __init__(self):
        super().__init__()
        v = vbox(self, 0, SP3)
        self.head = lb('', F_H1, DIM, bold=True)
        v.addWidget(self.head)
        self.done_box = card(BG_OK, GREEN, RADIUS_SM)
        dv = vbox(self.done_box, SP3, 0)
        self.done = lb('', F_BODY, GREEN, bold=True, wrap=True)
        dv.addWidget(self.done)
        v.addWidget(self.done_box)
        v.addWidget(lb('즉시 조치 · 확정 절차', F_LABEL, TXT, bold=True))
        self.body = QtWidgets.QTextEdit()
        self.body.setReadOnly(True)
        self.body.setFont(f(F_BODY))
        self.body.setStyleSheet(TEXT_QSS)
        v.addWidget(self.body, 2)
        self.stat = lb('', F_CAP, DIM, wrap=True)
        v.addWidget(self.stat)
        v.addWidget(lb('공식 안전 매뉴얼 · 실측 브리핑 · AI 보조 요약', F_LABEL, DIM))
        self.src = QtWidgets.QTextEdit()
        self.src.setReadOnly(True)
        self.src.setFont(f(F_LABEL))
        self.src.setStyleSheet(TEXT_QSS)
        v.addWidget(self.src, 3)
        self._et = 'fall_detected'

    def show_for(self, et='fall_detected', done=None, title=None, sev=None):
        self._et = et
        self.head.setText(title or EVENT_KO.get(et, str(et)))
        self.head.setStyleSheet(
            f'color:{sev_color(sev or event_sev(et))};border:none;background:transparent;')
        default_done = ('경보 전파 완료 · 설비 회로 자동 차단 대상 아님'
                        if et not in AUTO_TRIP_EVENTS else
                        '작업 대상 설비 회로 차단 완료 · 젯슨 자동 실행')
        self.done.setText(done or default_done)
        html = []
        # ⚠ [8/25] 폴백이 INSTANT_ACTION['fall_detected'] 였다. 등록되지 않은
        #   이벤트에 낙상 응급처치가 떴다 — 과전류 경보에 "환자를 움직이지
        #   마십시오" 가 나오던 원인이다. 중립 문구로 떨어뜨린다.
        for cat, lines in INSTANT_ACTION.get(et, INSTANT_ACTION_UNKNOWN):
            html.append(f'<p style="color:{CYAN};margin:2px 0 4px">'
                        f'<b>[{cat}]</b></p>'
                        f'<ul style="margin:0 0 10px 14px;color:{TXT}">')
            html += [f'<li style="margin-bottom:4px">{t}</li>' for t in lines]
            html.append('</ul>')
        self.body.setHtml(''.join(html))
        self.src.setHtml(f'<p style="color:{DIM}">매뉴얼 검색 중…</p>')

    def set_status(self, msg):
        self.stat.setText(msg)

    def set_sources(self, et, srcs, ai):
        if et != self._et:
            return
        h = []
        for n, t in srcs:
            # [8/25] 파일명을 그대로 찍지 않는다. 공식 지침과 프로젝트 자체 SOP 가
            #   나란히 뜨면 둘 다 같은 "매뉴얼" 로 보인다 → 등급을 먼저 밝힌다.
            #   자체 작성은 근거 조항을 함께 적어 근거 없는 문서가 아님을 보인다.
            kind, title, basis = core.source_label(n)
            col = CYAN if kind == core.SRC_OFFICIAL else AMBER
            h.append(f'<p style="color:{col};margin:0 0 2px">'
                     f'<b>[{core.SRC_BADGE[kind]}]</b> {title}</p>')
            if basis:
                h.append(f'<p style="color:{FAINT};margin:0 0 3px">'
                         f'근거: {basis}</p>')
            h.append(f'<p style="color:{DIM};margin:0 0 12px">{t}</p>')
        brief, sep, generated = ai.partition('\n---AI_SOP---\n')
        if brief:
            h.append(f'<p style="color:{AMBER};margin:8px 0 3px">'
                     f'<b>실측 상황 브리핑</b></p>'
                     f'<p style="color:{DIM};margin:0 0 10px">'
                     f'{core.md_to_html(brief)}</p>')
        if sep and generated:
            h.append(f'<p style="color:{CYAN};margin:8px 0 3px">'
                     f'<b>AI 보조 요약 · 공식 매뉴얼 기반</b></p>'
                     f'<p style="color:{TXT};margin:0 0 10px">'
                     f'{core.md_to_html(generated)}</p>')
        if not h:
            h.append(f'<p style="color:{DIM}">검색 결과 없음 — '
                     f'위 [즉시 조치]를 따르십시오.</p>')
        self.src.setHtml(''.join(h))


class EvidenceView(QtWidgets.QWidget):
    """L2 판단 근거 — 젯슨 classify() 가 보낸 수치만. LLM 은 이 화면에 개입하지 않는다."""

    def __init__(self):
        super().__init__()
        v = vbox(self, 0, SP3)
        self.head = lb('최근 경보 없음', F_BODY, DIM, wrap=True)
        v.addWidget(self.head)
        # ⚠ 드로어 폭(420px)에 4열은 안 들어간다 — '의미' 열이 가로 스크롤 밖으로
        #   밀려 표에 스크롤바가 생겼다(실측). 의미는 항목 칸의 툴팁으로 옮긴다.
        self.tbl = QtWidgets.QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(['항목', '측정값', '기준'])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setFont(f(F_LABEL))
        self.tbl.setStyleSheet(TABLE_QSS)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.tbl.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        v.addWidget(self.tbl, 3)
        self.why = lb('', F_CAP, DIM, wrap=True)
        v.addWidget(self.why)
        self.rej = lb('', F_CAP, DIM, wrap=True)
        v.addWidget(self.rej)
        v.addWidget(lb('원시 측정값', F_LABEL, DIM))
        self.raw = QtWidgets.QTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(f(F_CAP))
        self.raw.setStyleSheet(TEXT_QSS)
        self.raw.setMaximumHeight(110)
        v.addWidget(self.raw)

    def set_event(self, ev, rx_ts):
        et = ev.get('type') or ev.get('event_type')
        sev = ev.get('sev') or event_sev(et)
        self.head.setText(
            f"{EVENT_KO.get(et, '-')} · {ev.get('zone')} "
            f"{ZONE_KO.get(ev.get('zone'), '')} · {SEV_KO.get(sev, '')} · "
            f"판정 점수 {ev.get('conf', 0):.2f} · "
            f"{time.strftime('%H:%M:%S', time.localtime(rx_ts))}")
        self.head.setStyleSheet(
            f'color:{sev_color(sev)};border:none;background:transparent;')
        g = ev.get('gates') or {}
        self.tbl.setRowCount(max(len(g), 1))
        if not g:
            it = QtWidgets.QTableWidgetItem(
                '판단 근거 없음 — 피처 계산 전 조기 판정 경로 (evidence=None)')
            it.setForeground(QtGui.QColor(AMBER))
            self.tbl.setItem(0, 0, it)
            self.tbl.setSpan(0, 0, 1, 3)
        whys = []
        for r, (k, d) in enumerate(g.items()):
            meta = GATE_META.get(k, {})
            unit = d.get('unit', '')
            cells = (meta.get('ko', k),
                     f"{d.get('value')} {unit}".strip(),
                     f"{d.get('cmp', '>=')} {d.get('thr')}  "
                     f"{'통과' if d.get('pass') else '미달'}")
            for c, t in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(str(t))
                it.setForeground(QtGui.QColor(
                    (GREEN if d.get('pass') else RED) if c == 2 else TXT))
                if c == 0:
                    tip = meta.get('why', '')
                    if meta.get('src'):
                        tip += f"\n실측 근거: {meta['src']}"
                    if tip:
                        it.setToolTip(tip.strip())
                self.tbl.setItem(r, c, it)
            if meta.get('why'):
                whys.append(f"{meta.get('ko', k)} = {meta['why']}")
        self.why.setText(' · '.join(whys))
        self.tbl.resizeColumnsToContents()
        rj = ev.get('rejected') or []
        self.rej.setText(
            '제외한 후보 · ' + ' · '.join(
                f"{REJECT_KO.get(r.get('candidate'), r.get('candidate'))} "
                f"({r.get('reason')})" for r in rj) if rj else '')
        e = ev.get('evidence') or {}
        self.raw.setPlainText(
            '   '.join(f'{EVIDENCE_KO.get(k, k)}={v}' for k, v in e.items()
                       if v is not None) or '(없음)')


class ActionDrawer(QtWidgets.QFrame):
    """우측 슬라이드 드로어.

    ⚠ 이것이 v1 대비 가장 중요한 구조 변경이다. v1 의 조치 가이드는 독립
      최상위 윈도우(QDialog)라서 본 창 밖으로 나갈 수 있었다 — 듀얼모니터에서
      다른 화면에 뜨거나 본 창 뒤로 숨을 수 있었고, 경보 중에 창을 찾아 헤매게
      된다. 드로어는 부모 레이아웃 안에 있으므로 그럴 수 없다.
    """
    WIDTH = 420
    visibility_changed = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f'QFrame{{background:{PANEL};'
                           f'border-left:1px solid {EDGE};border-radius:0px;}}')
        self.setMaximumWidth(0)
        v = vbox(self, SP4, SP3)
        head = hbox(s=SP2)
        self.title = lb('조치 가이드', F_H1, TXT, bold=True)
        head.addWidget(self.title)
        head.addStretch()
        close = QtWidgets.QToolButton()
        close.setText('✕')
        close.setFont(f(F_BODY))
        close.setCursor(QtCore.Qt.PointingHandCursor)
        close.setStyleSheet(f'QToolButton{{border:none;background:transparent;'
                            f'color:{DIM};}}QToolButton:hover{{color:{TXT};}}')
        close.clicked.connect(self.close_drawer)
        head.addWidget(close)
        v.addLayout(head)

        tabs = hbox(s=SP2)
        self.tab_sop = button('조치 가이드', 'quiet', F_LABEL, 32)
        self.tab_evi = button('판단 근거', 'quiet', F_LABEL, 32)
        self.tab_sop.clicked.connect(lambda: self.open_at(0))
        self.tab_evi.clicked.connect(lambda: self.open_at(1))
        tabs.addWidget(self.tab_sop)
        tabs.addWidget(self.tab_evi)
        tabs.addStretch()
        v.addLayout(tabs)

        self.stack = QtWidgets.QStackedWidget()
        self.sop = SopView()
        self.evi = EvidenceView()
        self.stack.addWidget(self.sop)
        self.stack.addWidget(self.evi)
        v.addWidget(self.stack, 1)

        # 경보가 없을 때 '확인했습니다'가 떠 있으면 무엇을 확인하는지 알 수 없다.
        #   활성 경보가 있을 때만 나타난다.
        self.ack = button('확인했습니다', 'primary', F_BODY, 44)
        self.ack.hide()
        v.addWidget(self.ack)

        self._open = False
        self._extra_width = 0

    def set_extra_width(self, width):
        self._extra_width = max(0, int(width))

    def open_at(self, idx):
        self.stack.setCurrentIndex(idx)
        self.title.setText('조치 가이드' if idx == 0 else '판단 근거')
        for i, b in enumerate((self.tab_sop, self.tab_evi)):
            b.setStyleSheet(b.styleSheet())
            b.setFont(f(F_LABEL, bold=(i == idx)))
        if not self._open:
            self._animate(True)

    def close_drawer(self):
        self._animate(False)

    def toggle(self, idx=0):
        if self._open and self.stack.currentIndex() == idx:
            self.close_drawer()
        else:
            self.open_at(idx)

    def is_open(self):
        return self._open

    def _animate(self, opening):
        self._open = opening
        self.visibility_changed.emit(opening)
        # 3D OpenGL 화면과 폭 애니메이션을 함께 돌리면 매 프레임 전체 레이아웃을
        # 다시 계산해 170ms 설정보다 훨씬 느리게 보인다. 안전 조치는 즉시 연다.
        self.setMaximumWidth(self.WIDTH + self._extra_width if opening else 0)


# ══════════════════════════════════════════════════════════════════════
# 6. 대시보드 (개요)
# ══════════════════════════════════════════════════════════════════════


class DashboardPage(QtWidgets.QWidget):
    """[1] 대시보드 — 장비 상태와 시설 현황. 여기서 구역을 골라 감시로 들어간다."""
    enter_zone = QtCore.pyqtSignal(dict)
    open_settings = QtCore.pyqtSignal()

    def __init__(self, link=None, demo=False):
        super().__init__()
        self.link, self.demo = link, demo
        self._pkt = {}
        self._link_ok = bool(demo)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        inner = QtWidgets.QWidget()
        v = vbox(inner, SP5, SP4)
        outer.addWidget(scrollable(inner))

        # ── 제목 줄 ──
        head = hbox(s=SP3)
        tcol = QtWidgets.QVBoxLayout()
        tcol.setSpacing(0)
        tcol.addWidget(lb('대시보드', F_DISPLAY, TXT, bold=True))
        tcol.addWidget(lb('산업 현장 안전 관제 시스템', F_BODY, DIM))
        head.addLayout(tcol)
        head.addStretch()
        self.alarm_chip = lb('', F_LABEL, FAINT)
        self.alarm_chip.setAlignment(QtCore.Qt.AlignCenter)
        self.alarm_chip.setMinimumWidth(96)
        self.alarm_chip.setMinimumHeight(30)
        head.addWidget(self.alarm_chip)
        self.clock = lb('', F_H2, TXT, bold=True)
        self.clock.setAlignment(QtCore.Qt.AlignCenter)
        self.clock.setMinimumWidth(104)
        self.clock.setMinimumHeight(30)
        self.clock.setStyleSheet(f'color:{TXT};background:{PANEL};'
                                 f'border:1px solid {EDGE};'
                                 f'border-radius:{RADIUS_SM}px;')
        head.addWidget(self.clock)
        v.addLayout(head)

        # ── 장비 상태 카드 5장 ──
        srow = hbox(s=SP3)
        self.cards = {}
        for k, name in (('radar', '레이더 상태'), ('engine', '판정 엔진'),
                        ('breaker', '스마트 차단기'), ('sop', 'SOP 데이터베이스'),
                        ('llm', 'AI 엔진')):
            c = StatusCard(name)
            self.cards[k] = c
            srow.addWidget(c)
        v.addLayout(srow)

        # ── 시설 현황 (평면도) ──
        #   ⚠ 카드 3장 → 평면도 하나로 바꿨다. 카드는 '구역이 3개 있다' 만
        #     말했지 그것들이 어디에 있는지, 사고가 어느 쪽에서 났는지는
        #     말하지 못했다. 관제에서 첫 질문은 언제나 '어디' 다.
        head2 = hbox(s=SP3)
        head2.addWidget(lb('시설 현황', F_H1, TXT, bold=True))
        head2.addWidget(lb('구역을 누르면 실시간 감시 화면으로 들어갑니다',
                           F_CAP, FAINT))
        head2.addStretch()
        for mark, color, text in (('■', GREEN, '감시 중'),
                                  ('■', AMBER, '사전경보'),
                                  ('■', RED, '경보'),
                                  ('□', FAINT, '장비 미설치')):
            head2.addWidget(lb(mark, F_CAP, color))
            head2.addWidget(lb(text, F_CAP, FAINT))
        v.addLayout(head2)

        prow = hbox(s=SP3)
        pbox = card()
        pv = vbox(pbox, SP3, SP2)
        self.plan = FacilityPlan()
        self.plan.zone_clicked.connect(self._enter)
        pv.addWidget(self.plan, 1)
        prow.addWidget(pbox, 1)

        # 평면도 옆 요약 — 레이더가 있는 구역의 실시간 값
        sbox = card()
        sv2 = vbox(sbox, SP4, SP2)
        sbox.setFixedWidth(268)
        sv2.addWidget(eyebrow(f'{RADAR_ZONE} {ZONE_KO.get(RADAR_ZONE, "")}'
                              f' · 실시간'))
        self.side_metrics = {}
        for k, name, unit in (('height', '높이', 'm'), ('dop', '움직임', ''),
                              ('pts', '포인트', '개'), ('ae', '이상도', '배')):
            row = hbox(s=SP2)
            row.addWidget(lb(name, F_CAP, DIM))
            row.addStretch()
            val = lb('—', F_H2, TXT, bold=True)
            self.side_metrics[k] = val
            row.addWidget(val)
            row.addWidget(lb(unit, F_CAP, FAINT))
            sv2.addLayout(row)
        sv2.addSpacing(SP2)
        sv2.addWidget(eyebrow('최근 이벤트'))
        self.side_events = [lb('', F_CAP, FAINT, wrap=True) for _ in range(4)]
        for r in self.side_events:
            sv2.addWidget(r)
        sv2.addStretch()
        self.plan_hint = lb('', F_CAP, FAINT, wrap=True)
        sv2.addWidget(self.plan_hint)
        prow.addWidget(sbox)
        v.addLayout(prow, 1)

        # ── 근무 정보 ──
        wbox = card()
        wv = hbox(wbox, SP4, SP3)
        wv.addWidget(lb('근무 정보', F_LABEL, DIM))
        self.shift = styled_combo(['주간조', '야간조', '휴일조'])
        self.shift.setFixedWidth(150)
        self.operator = styled_line('담당자 이름')
        self.who_state = lb('담당자 미지정', F_CAP, AMBER)
        self.operator.textChanged.connect(self._who_changed)
        wv.addWidget(self.shift)
        wv.addWidget(self.operator, 1)
        wv.addWidget(self.who_state)
        gear = button('⚙  설정', 'ghost', F_BODY, 34, 104)
        gear.clicked.connect(self.open_settings.emit)
        wv.addWidget(gear)
        v.addWidget(wbox)

        self.why = lb('', F_BODY, AMBER, wrap=True)
        v.addWidget(self.why)
        v.addStretch()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(700)
        self.refresh()

    def _who_changed(self, t):
        ok = bool(t.strip())
        self.who_state.setText('적용됨' if ok else '담당자 미지정')
        self.who_state.setStyleSheet(f'color:{GREEN if ok else AMBER};'
                                     f'border:none;background:transparent;')

    def _enter(self, zid):
        self.enter_zone.emit({'zone': zid,
                              'shift': self.shift.currentText(),
                              'operator': self.operator.text().strip() or '미지정'})

    def on_packet(self, pkt):
        self._pkt = pkt

    def push(self, pkt, sev='normal'):
        # ⚠ 구역 색을 refresh(700ms) 에만 맡기면 경보가 떠도 맵이 최대 0.7초
        #   늦게 붉어진다. 1.5초 뒤 자동 전환이라 그 지연이 그대로 보인다.
        #   → 구역 상태는 패킷마다(10 Hz) 갱신하고, refresh 는 장비 카드만 맡는다.
        self.update_zones(pkt)
        c = pkt.get('centroid') or {}
        self.plan.set_worker(RADAR_ZONE, c.get('cx', 0.0), c.get('cz', 0.0))
        ev = (pkt.get('ev') or {}).get('evidence') or {}
        sc, th = pkt.get('_sc', 0.0), pkt.get('threshold', 0.0)
        ratio = ((ev.get('ae_score', sc) or 0) /
                 max(ev.get('ae_thr', th) or 1e-9, 1e-9)) if (ev or th) else 0
        col = sev_color(sev) if sev != 'normal' else TXT
        for k, t in (('height', f"{pkt.get('height') or 0:.2f}"),
                     ('dop', f"{pkt.get('dop_std', 0):.2f}"),
                     ('pts', f"{pkt.get('n_pts', 0)}"),
                     ('ae', f'{ratio:.1f}' if ratio else '—')):
            self.side_metrics[k].setText(t)
            self.side_metrics[k].setStyleSheet(
                f'color:{col};border:none;background:transparent;')
        self.plan.update()

    def set_events(self, lines):
        for i, r in enumerate(self.side_events):
            if i < len(lines):
                ts, txt, c = lines[i]
                r.setText(f'{ts}  {txt}')
                r.setStyleSheet(f'color:{c};border:none;background:transparent;')
            else:
                r.setText('')

    def set_blink(self, on):
        self.plan.set_blink(on)

    def update_zones(self, pkt):
        """평면도 구역 상태. 경보 > 사전경보 > 감시중 > 미설치 순으로 결정한다."""
        ev = pkt.get('ev') or {}
        live_ph = pkt.get('phase') == PH_LIVE
        alarm_zone = ev.get('zone') if ev.get('active') else None
        asev = (ev.get('sev') or event_sev(ev.get('type'))) if alarm_zone else None
        pre = parse_pre_alert(pkt.get('pre_alert'))
        for z in fac.ZONES:
            self.plan.set_zone(
                z,
                live=bool(self._link_ok and zone_equipped(z) and live_ph),
                sev=asev if z == alarm_zone else None,
                pre=(pre['text'] if (pre and pre['zone'] == z
                                     and z != alarm_zone) else None),
                et=ev.get('type') if z == alarm_zone else None)
        self.plan.update()

    def set_alarm_count(self, n_active, n_today, sev='critical'):
        if n_active:
            c = sev_color(sev)
            self.alarm_chip.setText(f'경보 {n_active}건')
            self.alarm_chip.setStyleSheet(
                f'color:{c};background:{SEV_BG.get(sev, BG_ALERT)};'
                f'border:1px solid {c};border-radius:{RADIUS_SM}px;')
        else:
            self.alarm_chip.setText(f'오늘 {n_today}건')
            self.alarm_chip.setStyleSheet(
                f'color:{DIM};background:{PANEL};border:1px solid {EDGE};'
                f'border-radius:{RADIUS_SM}px;')

    # ── 장비 상태 갱신 ──
    #   ⚠ 여기 detail 에는 '근무자가 판단에 쓸 수 있는 것'만 쓴다.
    #     IP·포트·모델명·접속문자열·임계값은 [설정] 의 AI·SOP / 판정 / 진단 탭에 있다.
    def refresh(self):
        pkt = self._pkt or {}
        c = self.cards
        self.clock.setText(time.strftime('%H:%M:%S'))

        if self.demo:
            c['radar'].set(True, '데모 모드', '가짜 데이터 · 젯슨 없음', AMBER)
        elif self.link is None:
            c['radar'].set(False, '미설정', '--live <젯슨IP> 로 실행하세요')
        else:
            age = self.link.age()
            if age is None:
                c['radar'].set(False, '연결 대기', '장비 응답을 기다리는 중')
            elif age > LINK_TIMEOUT:
                c['radar'].set(False, '끊김', f'마지막 수신 {int(age)}초 전', RED)
            else:
                c['radar'].set(True, '연결됨',
                               f'지연 {int(age * 1000)}ms · 유실 {self.link.lost}건')

        ph = pkt.get('phase')
        if ph == PH_LIVE:
            c['engine'].set(True, 'LIVE', '정상 기준 학습 완료')
        elif ph:
            c['engine'].set(False, PHASE_KO.get(ph, ph), '현장 준비 화면에서 진행')
        else:
            c['engine'].set(False, '대기', '판정 장비 응답 없음')

        breaker = pkt.get('breaker') or {}
        if breaker.get('src') == 'modbus' and breaker.get('connected'):
            c['breaker'].set(True, '연결됨', 'Modbus 릴레이 응답 정상')
        else:
            c['breaker'].set(False, '미연결', 'Modbus 릴레이 응답 없음', AMBER)

        c['sop'].set(RAG_OK, '정상' if RAG_OK else '비활성',
                     '안전 매뉴얼 검색 사용 가능' if RAG_OK
                     else '매뉴얼 검색 불가 — 즉시 조치만 표시')
        c['llm'].set(True, '대기', '경보 시 요약 생성', AMBER)

        ok = c['radar'].ok
        self._link_ok = ok
        self.update_zones(pkt)
        self.plan_hint.setText(
            f"{RADAR_ZONE} 구역 감지영역 "
            f"{2 * fac.COVER_HALF:.2f} × {2 * fac.COVER_HALF:.2f} m · "
            f"천장 {fac.RADARS[RADAR_ZONE]['ceiling']:.2f} m"
            if RADAR_ZONE in fac.RADARS else '')
        self.why.setText('' if ok else
                         '레이더 링크가 확인되면 구역을 선택할 수 있습니다 — '
                         '젯슨에서 jetson_sender.py 가 실행 중인지 확인하세요')


# ══════════════════════════════════════════════════════════════════════
# 7. 실시간 감시
# ══════════════════════════════════════════════════════════════════════
class MonitorPage(QtWidgets.QWidget):
    """[2] 실시간 감시 — 장면 + 상황 패널 + 타임라인 + 우측 드로어."""
    ack = QtCore.pyqtSignal()
    resolve = QtCore.pyqtSignal()
    prepare = QtCore.pyqtSignal()
    mode_changed = QtCore.pyqtSignal()   # 3D↔2D 전환 — ConsoleV2 가 즉시 재렌더
    enter_confirmed = QtCore.pyqtSignal()
    exit_confirmed = QtCore.pyqtSignal()

    def __init__(self, has_prepare=True):
        super().__init__()
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        main = QtWidgets.QWidget()
        v = vbox(main, SP5, SP3)
        root.addWidget(main, 1)
        self.drawer = ActionDrawer(self)
        root.addWidget(self.drawer)

        # ── 제목 줄 ──
        head = hbox(s=SP3)
        tcol = QtWidgets.QVBoxLayout()
        tcol.setSpacing(0)
        tcol.addWidget(lb('실시간 감시', F_DISPLAY, TXT, bold=True))
        self.sub = lb('', F_BODY, DIM)
        tcol.addWidget(self.sub)
        head.addLayout(tcol)
        head.addStretch()
        self.seg2d = button('2D 측면도', 'quiet', F_LABEL, 30, 92)
        self.seg3d = button('3D', 'quiet', F_LABEL, 30, 56)
        head.addWidget(self.seg2d)
        head.addWidget(self.seg3d)
        self.cam = button('시점 초기화', 'ghost', F_LABEL, 30, 96)
        head.addWidget(self.cam)
        self.link_lb = lb('연결 확인 중', F_LABEL, DIM)
        head.addWidget(self.link_lb)
        self.clock = lb('', F_LABEL, DIM)
        head.addWidget(self.clock)
        v.addLayout(head)

        # 입·퇴실은 센서 추정이 아니라 운영자 확인값이다. 판정 버튼과 섞지 않는다.
        occ = hbox(s=SP2)
        occ.addWidget(eyebrow('작업자 재실'))
        self.occupancy = lb('퇴실', F_LABEL, DIM, bold=True)
        occ.addWidget(self.occupancy)
        occ.addStretch()
        self.enter_btn = button('입실 확인', 'primary', F_LABEL, 30, 96)
        self.exit_btn = button('퇴실 확인', 'ghost', F_LABEL, 30, 96)
        self.enter_btn.clicked.connect(self.enter_confirmed.emit)
        self.exit_btn.clicked.connect(self.exit_confirmed.emit)
        occ.addWidget(self.enter_btn)
        occ.addWidget(self.exit_btn)
        v.addLayout(occ)

        # ── 경보 배너 ──
        self.banner = QtWidgets.QFrame()
        bh = hbox(self.banner, SP4, SP3)
        self.b_left = lb('', F_H1, RED, bold=True)
        self.b_right = lb('', F_BODY, RED, bold=True)
        bh.addWidget(self.b_left)
        bh.addStretch()
        bh.addWidget(self.b_right)
        self.banner.hide()
        v.addWidget(self.banner)

        # ── 정지형 사전경보 줄 (경보 아님 — 점멸·소리 없음) ──
        self.pre_bar = card(BG_WARN, AMBER)
        ph_ = hbox(self.pre_bar, SP4, SP3)
        ph_.addWidget(lb('◐', F_H2, AMBER))
        self.pre_lb = lb('', F_BODY, AMBER, bold=True, wrap=True)
        ph_.addWidget(self.pre_lb, 1)
        self.pre_bar.hide()
        v.addWidget(self.pre_bar)

        # ── 기준 미학습 안내 ──
        self.needcal = card(BG_WARN, AMBER)
        nh = hbox(self.needcal, SP4, SP3)
        self.needcal_lb = lb('', F_BODY, AMBER, wrap=True)
        nh.addWidget(self.needcal_lb, 1)
        self.cal_btn = button('빈방 스캔 실행', 'primary', F_BODY, 36, 148)
        self.cal_btn.clicked.connect(self.prepare.emit)
        nh.addWidget(self.cal_btn)
        self.needcal.hide()
        if not has_prepare:
            self.cal_btn.setEnabled(False)
        v.addWidget(self.needcal)

        # ── 본문 ──
        mid = hbox(s=SP3)
        # ── 왼쪽: 장면 + 실시간 계측 ──
        #   ⚠ 계측 타일은 원래 우측 열에 있었다. 경보가 뜨면 우측에 사고 패널이
        #     들어오면서 계측 4개 중 2개와 구역 현황이 스크롤 아래로 밀려났다
        #     (실측). 경보 순간에 가려지는 정보는 없어야 한다.
        #     → 폭이 넉넉한 왼쪽 열 하단으로 옮겨 4칸을 한 줄에 편다.
        leftw = QtWidgets.QWidget()
        leftv = QtWidgets.QVBoxLayout(leftw)
        leftv.setContentsMargins(0, 0, 0, 0)
        leftv.setSpacing(SP3)
        scene_box = card()
        sv = vbox(scene_box, SP4, SP2)
        head = hbox(s=SP3)
        head.addWidget(eyebrow('현장 포인트 클라우드'))
        head.addStretch()
        # 범례 — 무엇이 실측이고 무엇이 도식인지 화면에서 구분되게 한다.
        #   ⚠ 이 한 줄이 "관절도 측정한 거냐"는 질문을 원천 차단한다.
        #   ⚠ 폭이 모자라면 통째로 접는다. 잘린 범례는 없는 것만 못하고,
        #     같은 내용이 아래 캡션(pose label)에도 들어 있다.
        self.legend = QtWidgets.QWidget()
        lg = hbox(self.legend, s=SP2)
        for mark, color, text in (('●', CYAN, '레이더 점군'),
                                  ('●', AMBER, '위치 추정'),
                                  ('─', GREEN, '자세 추정'),
                                  ('▦', '#1E4C75', '설비 배치'),
                                  ('□', '#F59E0B', '단자함 위험구역'),
                                  ('□', '#A78BFA', '냉각팬 위험구역')):
            lg.addWidget(lb(mark, F_CAP, color))
            lg.addWidget(lb(text, F_CAP, FAINT))
        self._legend_w = self.legend.sizeHint().width()
        head.addWidget(self.legend)
        sv.addLayout(head)
        self._scene_box = scene_box
        self.scene = SceneView()
        # ⚠ 최소 높이는 '창이 가장 작을 때(1180×760) 경보 배너까지 떠 있어도
        #   같은 열의 형제 위젯을 밀어내지 않는' 값이다. 210 으로 뒀더니 세로
        #   합계가 창을 넘어 캡션이 장면 위에 겹쳐 그려졌다.
        self.scene.setMinimumHeight(150)
        sv.addWidget(self.scene, 1)
        # ⚠ 이 캡션은 wrap 을 쓰지 않는다. 두 줄이 되면 세로가 모자라 장면 위젯
        #   위로 겹쳐 그려졌다(실측). 폭이 좁으면 줄이 늘어나는 대신 말줄임한다.
        self.pose_lb = lb('감시 중', F_CAP, DIM)
        self.pose_lb.setFixedHeight(16)
        self._pose_text = '감시 중'
        sv.addWidget(self.pose_lb)
        leftv.addWidget(scene_box, 1)
        mid.addWidget(leftw, 62)

        # ⚠ 우측 열은 반드시 스크롤 가능해야 한다.
        #   세로가 모자라면 Qt 는 최소크기까지 눌러 담고, 그래도 모자라면
        #   위젯을 겹쳐 그린다. 실측 스크린샷에서 [조치 방법] 버튼이
        #   [확인함/상황 종료] 위에 포개졌고 계측 타일 4개의 라벨과 숫자가
        #   서로 겹쳤다. 경보 화면에서 숫자가 겹치는 건 '못 읽는 것'이 아니라
        #   '잘못 읽는 것'이라 훨씬 위험하다.
        rightw = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(rightw)
        right.setContentsMargins(0, 0, SP2, 0)
        right.setSpacing(SP3)
        rightw.setMinimumWidth(372)
        right_sa = scrollable(rightw)
        right_sa.setMinimumWidth(388)

        # 평상시 히어로
        self.hero = card()
        self.hero.setMinimumHeight(150)
        hv = vbox(self.hero, SP4, SP1)
        self.h_ic = lb('✓', 26, GREEN, align=QtCore.Qt.AlignCenter)
        self.h_t = lb('이상 없음', F_H1, TXT, bold=True, align=QtCore.Qt.AlignCenter)
        self.h_quiet = lb('', F_BODY, GREEN, bold=True, align=QtCore.Qt.AlignCenter)
        self.h_s = lb('', F_CAP, DIM, align=QtCore.Qt.AlignCenter, wrap=True)
        for w in (self.h_ic, self.h_t, self.h_quiet, self.h_s):
            hv.addWidget(w)
        right.addWidget(self.hero)

        # 경보 패널 — 시선 순서: ①사고 종류·위험도 ②경과·확신도 ③작업자 상태
        #                        ④자동 차단 결과 ⑤지금 누를 버튼
        self.alert_box = card(PANEL, RED)
        self.alert_box.setMinimumHeight(292)
        av = vbox(self.alert_box, SP4, SP3)
        crow = hbox(s=SP2)
        self.a_kind = lb('', F_H1, RED, bold=True)
        crow.addWidget(self.a_kind)
        crow.addStretch()
        self.a_conf = lb('', F_BODY, TXT, bold=True)
        crow.addWidget(self.a_conf)
        av.addLayout(crow)
        self.msg = lb('', F_BODY, TXT, wrap=True)
        self.msg.setMinimumHeight(44)
        av.addWidget(self.msg)
        self.auto_box = card(BG_OK, GREEN, RADIUS_SM)
        self.auto_box.setMinimumHeight(46)
        auv = vbox(self.auto_box, SP3, 0)
        self.auto_lb = lb('', F_LABEL, GREEN, bold=True, wrap=True)
        auv.addWidget(self.auto_lb)
        av.addWidget(self.auto_box)
        self.a_sop = button('조치 방법 보기', 'danger', F_BODY, 46)
        av.addWidget(self.a_sop)
        row = hbox(s=SP2)
        self.a_ack = button('확인함', 'quiet', F_BODY, 40)
        self.a_end = button('상황 종료', 'ghost', F_BODY, 40)
        self.a_ack.clicked.connect(self.ack.emit)
        self.a_end.clicked.connect(self.resolve.emit)
        row.addWidget(self.a_ack)
        row.addWidget(self.a_end)
        av.addLayout(row)
        self.alert_box.hide()
        right.addWidget(self.alert_box)

        # 구역 현황
        zbox = card()
        zv = vbox(zbox, SP4, SP2)
        zv.addWidget(eyebrow('구역 현황'))
        self.zstrip = ZoneStrip()
        zv.addWidget(self.zstrip)
        right.addWidget(zbox)

        # 감시 요약 — 평상시 우측 여백을 채우는 동시에, 그동안 화면 어디에도
        #   없던 '마지막 데이터 수신 시각'을 둔다. 링크가 살아 있는지를
        #   초록 점 하나가 아니라 숫자로 확인할 수 있어야 한다.
        sbox = card()
        svv = vbox(sbox, SP4, SP2)
        svv.addWidget(eyebrow('감시 요약'))
        self.summary = {}
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SP3)
        grid.setVerticalSpacing(SP2)
        for r, (k, name) in enumerate((('zone', '감시 구역'), ('who', '근무'),
                                       ('rx', '마지막 수신'), ('today', '오늘 경보'))):
            grid.addWidget(lb(name, F_CAP, DIM), r, 0)
            val = lb('—', F_LABEL, TXT)
            self.summary[k] = val
            grid.addWidget(val, r, 1)
        grid.setColumnStretch(1, 1)
        svv.addLayout(grid)
        right.addWidget(sbox)

        right.addStretch()
        mid.addWidget(right_sa, 38)

        # 실시간 계측 — 왼쪽 열 하단, 한 줄 4칸
        mbox = card()
        mv = vbox(mbox, SP4, SP2)
        mv.addWidget(eyebrow('실시간 계측'))
        g = QtWidgets.QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(SP2)
        self.tiles = {
            # 캡션은 좁은 칸에서도 안 잘리는 길이만 쓴다 (자세한 설명은 [판단 근거]에)
            'height': MetricTile('높이', 'm'),
            'dop': MetricTile('움직임', '', 'dop_std'),
            'pts': MetricTile('포인트', '개', '원시'),
            'ae': MetricTile('이상도', '배'),
        }
        for i, k in enumerate(('height', 'dop', 'pts', 'ae')):
            g.addWidget(self.tiles[k], 0, i)
            g.setColumnStretch(i, 1)
        mv.addLayout(g)
        leftv.addWidget(mbox)
        v.addLayout(mid, 1)

        # ── 하단: 타임라인 + 보조 도구 ──
        #   ⚠ 보조 버튼 4개는 원래 우측 열 맨 아래에 있었는데, 경보 패널이
        #     들어오면 스크롤 밖으로 밀려 반쯤 잘린 채 보였다(실측).
        #     항상 같은 자리에 있어야 하는 것은 스크롤 밖에 둔다.
        bottom = hbox(s=SP3)
        self.timeline = Timeline()
        bottom.addWidget(self.timeline, 1)
        tools = card()
        tv = vbox(tools, SP3, SP2)
        tv.addWidget(eyebrow('보조 도구'))
        self.b_evi = button('판단 근거', 'ghost', F_LABEL, 30, 108)
        self.b_pwr = button('전기 설비', 'ghost', F_LABEL, 30, 108)
        self.b_graph = button('그래프', 'ghost', F_LABEL, 30, 108)
        self.b_query = button('문의', 'ghost', F_LABEL, 30, 108)
        tg = QtWidgets.QGridLayout()
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setSpacing(SP2)
        for i, b in enumerate((self.b_evi, self.b_pwr, self.b_graph, self.b_query)):
            tg.addWidget(b, i // 2, i % 2)
        tv.addLayout(tg)
        bottom.addWidget(tools)
        v.addLayout(bottom)

        self.seg2d.clicked.connect(lambda: self._set_mode(SceneView.MODE_2D))
        self.seg3d.clicked.connect(lambda: self._set_mode(SceneView.MODE_3D))
        self.cam.clicked.connect(self.scene.reset_camera)
        self.a_sop.clicked.connect(lambda: self.drawer.open_at(0))
        self.b_evi.clicked.connect(lambda: self.drawer.toggle(1))
        self.drawer.ack.clicked.connect(self._drawer_ack)
        self._sync_seg()

    def fit_pose_text(self):
        """현재 폭에 맞춰 캡션을 다시 말줄임한다.

        ⚠ fit_legend 와 같은 함정에 빠져 있었다 — 드로어가 열리면 형제 위젯의
          maximumWidth 만 바뀌어 이 페이지의 resizeEvent 가 안 뜨는데 장면 폭은
          420px 줄어든다. 그래서 경보로 드로어가 열리면 캡션이 드로어 열리기 전
          폭 기준으로 잘린 채 남아 실제로는 삐져나갔다.
          (8/25 레이아웃 검증 실측: 1366×900 '감시-경보' 에서 422 > 408px)
          → resizeEvent 뿐 아니라 주기 갱신(tick_ui)에서도 부른다.
        """
        text = getattr(self, '_pose_text', None)
        if not text:
            return
        fm = QtGui.QFontMetrics(self.pose_lb.font())
        self.pose_lb.setText(fm.elidedText(
            text, QtCore.Qt.ElideRight, max(self.pose_lb.width() - 4, 40)))

    def set_pose_text(self, text, color):
        """폭에 맞춰 말줄임. 줄바꿈으로 높이가 늘어나면 장면과 겹친다."""
        self._pose_text = text
        self.fit_pose_text()
        self.pose_lb.setStyleSheet(
            f'color:{color};border:none;background:transparent;')
        self.pose_lb.setToolTip(text)

    def fit_legend(self):
        """범례가 통째로 들어갈 때만 보이게 한다.

        ⚠ resizeEvent 만으로는 부족하다. 드로어가 열리면 형제 위젯의
          maximumWidth 만 바뀌므로 이 페이지의 크기 이벤트가 안 뜨는데,
          장면 폭은 420px 줄어든다(실측: 1366폭에서 범례가 잘림).
          → 주기 갱신(tick_ui)에서도 같이 부른다.
        """
        lg = getattr(self, 'legend', None)
        if lg is not None:
            lg.setVisible(self._scene_box.width() > self._legend_w + 210)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit_legend()
        self.fit_pose_text()

    def _drawer_ack(self):
        self.drawer.close_drawer()
        self.ack.emit()

    def _set_mode(self, mode):
        if self.scene.set_mode(mode):
            self._sync_seg()
            self.mode_changed.emit()

    def _sync_seg(self):
        m = self.scene.mode()
        for b, mine in ((self.seg2d, SceneView.MODE_2D),
                        (self.seg3d, SceneView.MODE_3D)):
            on = (m == mine)
            b.setStyleSheet(
                f'QPushButton{{background:{BG_SEL if on else PANEL_HI};'
                f'color:{CYAN if on else DIM};border:none;'
                f'border-radius:{RADIUS_SM}px;padding:4px 10px;}}'
                f'QPushButton:hover{{color:{TXT};}}'
                f'QPushButton:disabled{{color:{FAINT};background:{PANEL_LO};}}')
        self.seg3d.setEnabled(self.scene.has_3d())
        self.cam.setVisible(m == SceneView.MODE_3D)
        if not self.scene.has_3d():
            self.seg3d.setToolTip('OpenGL 미설치 — pip install pyopengl')


# ══════════════════════════════════════════════════════════════════════
# 8. 이벤트 로그 · SOP 가이드 페이지
# ══════════════════════════════════════════════════════════════════════
class EventLogPage(QtWidgets.QWidget):
    """[3] 이벤트 로그 — v1 의 EventLogPopup 과 동일 데이터·동일 CSV 형식."""

    def __init__(self):
        super().__init__()
        v = vbox(self, SP5, SP4)
        head = hbox(s=SP3)
        tcol = QtWidgets.QVBoxLayout()
        tcol.setSpacing(0)
        tcol.addWidget(lb('이벤트 로그', F_DISPLAY, TXT, bold=True))
        tcol.addWidget(lb('경보 이력 · 종료 처리 상태', F_BODY, DIM))
        head.addLayout(tcol)
        head.addStretch()
        b = button('CSV 내보내기', 'ghost', F_BODY, 36, 150)
        b.clicked.connect(self._export)
        head.addWidget(b)
        v.addLayout(head)
        self.tbl = QtWidgets.QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(['시각', '종류', '구역', '확신도', '상태'])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.horizontalHeader().setDefaultSectionSize(140)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setFont(f(F_BODY))
        self.tbl.setStyleSheet(TABLE_QSS)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        v.addWidget(self.tbl, 1)
        self.empty = lb('아직 기록된 경보가 없습니다.', F_BODY, FAINT)
        v.addWidget(self.empty)
        self.rows = []

    def add(self, ts, et, zone, conf, status):
        self.empty.hide()
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        cells = (ts, EVENT_KO.get(et, str(et)),
                 f'{zone} {ZONE_KO.get(zone, "")}', f'{conf:.2f}', status)
        self.rows.append(cells)
        for c, t in enumerate(cells):
            it = QtWidgets.QTableWidgetItem(t)
            it.setForeground(QtGui.QColor(RED if c == 1 else TXT))
            self.tbl.setItem(r, c, it)
        self.tbl.scrollToBottom()

    def mark_resolved(self, ts):
        r = self.tbl.rowCount() - 1
        if r >= 0:
            it = QtWidgets.QTableWidgetItem(f'종료 {ts}')
            it.setForeground(QtGui.QColor(GREEN))
            self.tbl.setItem(r, 4, it)
            self.rows[r] = self.rows[r][:4] + (f'종료 {ts}',)

    def _export(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '경보 기록 저장',
            f'radar_guard_alarms_{time.strftime("%Y%m%d_%H%M")}.csv', 'CSV (*.csv)')
        if not p:
            return
        with open(p, 'w', encoding='utf-8-sig') as fp:
            fp.write('시각,종류,구역,확신도,상태\n')
            for r in self.rows:
                fp.write(','.join(str(x).replace(',', ' ') for x in r) + '\n')


class SopGuidePage(QtWidgets.QWidget):
    """[4] SOP 가이드 — 경보가 나기 전에 미리 읽어 두는 화면.

    ⚠ 경보 중 조치는 드로어에서 본다. 이 페이지는 '평소 학습용'이라
      경보 상황과 성격이 다르므로 빨강을 쓰지 않는다.
    """

    def __init__(self):
        super().__init__()
        v = vbox(self, SP5, SP4)
        tcol = QtWidgets.QVBoxLayout()
        tcol.setSpacing(0)
        tcol.addWidget(lb('SOP 가이드', F_DISPLAY, TXT, bold=True))
        tcol.addWidget(lb('사고 유형별 즉시 조치 절차 — 경보 시 우측 패널에 '
                          '자동으로 표시됩니다', F_BODY, DIM))
        v.addLayout(tcol)
        row = hbox(s=SP3)
        self.list = QtWidgets.QListWidget()
        self.list.setFixedWidth(220)
        self.list.setFont(f(F_BODY))
        self.list.setStyleSheet(
            f'QListWidget{{background:{PANEL};color:{TXT};border:1px solid {EDGE};'
            f'border-radius:{RADIUS_SM}px;padding:6px;outline:none;}}'
            f'QListWidget::item{{padding:9px 8px;border-radius:{RADIUS_SM}px;}}'
            f'QListWidget::item:selected{{background:{BG_SEL};color:{CYAN};}}')
        for et in INSTANT_ACTION:
            it = QtWidgets.QListWidgetItem(EVENT_KO.get(et, et))
            it.setData(QtCore.Qt.UserRole, et)
            self.list.addItem(it)
        self.list.currentRowChanged.connect(self._pick)
        self.list.setMaximumHeight(self.list.count() * 40 + 20)
        lcol = QtWidgets.QVBoxLayout()
        lcol.setContentsMargins(0, 0, 0, 0)
        lcol.addWidget(self.list)
        lcol.addStretch()
        row.addLayout(lcol)
        self.body = QtWidgets.QTextEdit()
        self.body.setReadOnly(True)
        self.body.setFont(f(F_BODY))
        self.body.setStyleSheet(TEXT_QSS)
        # 읽는 글이므로 한 줄이 너무 길어지지 않게 폭을 제한한다
        self.body.setMaximumWidth(820)
        row.addWidget(self.body, 1)
        row.addStretch()
        v.addLayout(row, 1)
        v.addWidget(lb('확정 즉시조치와 공식 안전 매뉴얼을 경보 화면에서 제공합니다.',
                       F_CAP, FAINT, wrap=True))
        self.list.setCurrentRow(0)

    def _pick(self, r):
        it = self.list.item(r)
        if it is None:
            return
        et = it.data(QtCore.Qt.UserRole)
        html = [f'<p style="color:{TXT};font-size:15px;margin:0 0 10px">'
                f'<b>{EVENT_KO.get(et, et)}</b></p>']
        for cat, lines in INSTANT_ACTION[et]:
            html.append(f'<p style="color:{CYAN};margin:6px 0 4px"><b>[{cat}]</b></p>'
                        f'<ul style="margin:0 0 10px 14px;color:{TXT}">')
            html += [f'<li style="margin-bottom:4px">{t}</li>' for t in lines]
            html.append('</ul>')
        self.body.setHtml(''.join(html))


class AiIconButton(QtWidgets.QToolButton):
    """말풍선과 AI 배지를 결합한 전역 보조 AI 아이콘."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip('Radar-Guard AI 열기')
        self.setAccessibleName('Radar-Guard AI')

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        bg = QtGui.QColor('#1A2742' if self.underMouse() else '#111B31')
        p.setPen(QtGui.QPen(QtGui.QColor(EDGE), 1))
        p.setBrush(bg)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor('#34466E'))
        p.drawRoundedRect(8, 20, 24, 15, 6, 6)
        p.drawPolygon(QtGui.QPolygon([QtCore.QPoint(12, 34),
                                     QtCore.QPoint(10, 39),
                                     QtCore.QPoint(18, 35)]))
        p.setBrush(QtGui.QColor('#253658'))
        p.drawRoundedRect(14, 14, 21, 14, 6, 6)
        for x in (14, 20, 26):
            p.setBrush(QtGui.QColor('#91A4CA'))
            p.drawEllipse(x, 26, 3, 3)
        p.setBrush(QtGui.QColor('#526A9A'))
        p.drawEllipse(25, 4, 17, 17)
        p.setPen(QtGui.QColor('#DCE8FF'))
        p.setFont(f(7, bold=True))
        p.drawText(QtCore.QRect(25, 4, 17, 17), QtCore.Qt.AlignCenter, 'AI')
        p.end()


class AssistantDrawer(QtWidgets.QDialog):
    """전역 시스템 보조 AI 팝업. 차단·복구 명령은 실행하지 않는다."""
    visibility_changed = QtCore.pyqtSignal(bool)
    answer_ready = QtCore.pyqtSignal(str, str, float)
    answer_failed = QtCore.pyqtSignal(str)

    SYSTEM_CONTEXT = (
        'Radar-Guard는 IWR6843 mmWave 레이더의 포인트 클라우드를 Jetson Orin '
        'Nano가 판정하고, Windows 노트북은 UDP 수신·관제 UI·SOP RAG를 담당한다. '
        '낙상·감전·협착 판정은 젯슨에서 끝나며, 전기·협착 이상은 '
        '젯슨이 차단한다. LLM은 판정하거나 차단하지 '
        '않는다. 카메라 대신 레이더를 써 개인정보와 조도·연기 제약을 줄인다. '
        '정지한 사람은 도플러가 0에 가까워 추적을 놓칠 수 있고, 각분해능 약 28도로 '
        '포인트가 적어 최근 10프레임을 누적한다. 젯슨에는 RTC가 없어 경과시간은 '
        '노트북 수신 시각을 쓴다. 위험 경보 중 확정 즉시조치와 공식 SOP가 LLM보다 '
        '우선하며 전원 재투입은 확인 절차를 거친다.')

    def __init__(self, parent=None, console=None):
        super().__init__(parent)
        self.console = console
        self.setWindowTitle('Radar-Guard AI')
        self.setModal(False)
        self.resize(720, 620)
        self.setMinimumSize(620, 520)
        self.setStyleSheet(
            f'QDialog{{background:{PANEL};}}QWidget{{color:{TXT};}}')
        self._busy = False
        v = vbox(self, SP4, SP3)
        head = hbox(s=SP2)
        head.addWidget(lb('Radar-Guard AI', F_H1, TXT, bold=True))
        head.addStretch()
        close = QtWidgets.QToolButton()
        close.setText('✕')
        close.setFont(f(F_BODY))
        close.setStyleSheet(f'border:none;background:transparent;color:{DIM};')
        close.clicked.connect(self.close_drawer)
        head.addWidget(close)
        v.addLayout(head)
        v.addWidget(lb('시스템 안내 · 현재 상황 · 안전 매뉴얼', F_CAP, DIM))

        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(f(F_BODY))
        self.log.setStyleSheet(TEXT_QSS)
        self.log.setHtml(self._bubble(
            'Radar-Guard의 기능이나 현재 상황을 물어보세요.<br>'
            '안전 조작은 실행하지 않습니다.', False, 'Radar-Guard AI'))
        v.addWidget(self.log, 1)

        self.waiting = lb('', F_CAP, AMBER)
        v.addWidget(self.waiting)

        chips = hbox(s=SP2)
        for text in ('왜 레이더를 쓰나요?', '현재 상태 알려줘', '낙상 조치 근거는?'):
            b = button(text, 'quiet', F_CAP, 30)
            # QDialog는 첫 QPushButton을 기본 버튼으로 승격할 수 있다. 입력창에서
            # Enter를 누른 같은 키 이벤트로 추천 질문까지 실행되는 것을 막는다.
            b.setAutoDefault(False)
            b.setDefault(False)
            b.clicked.connect(lambda _, q=text: self.ask(q))
            chips.addWidget(b)
        v.addLayout(chips)

        row = hbox(s=SP2)
        self.inp = QtWidgets.QLineEdit()
        self.inp.setFont(f(F_BODY))
        self.inp.setMinimumHeight(38)
        self.inp.setPlaceholderText('Radar-Guard에 질문하기')
        self.inp.setStyleSheet(core.EDIT_QSS)
        self.inp.returnPressed.connect(self._send)
        row.addWidget(self.inp, 1)
        send = button('보내기', 'primary', F_LABEL, 38, 72)
        send.setAutoDefault(False)
        send.setDefault(False)
        send.clicked.connect(self._send)
        row.addWidget(send)
        v.addLayout(row)
        self.answer_ready.connect(self._show_answer)
        self.answer_failed.connect(self._show_error)

    def open_drawer(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.inp.setFocus()
        self.visibility_changed.emit(True)

    def close_drawer(self):
        if self.isVisible():
            self.close()

    def closeEvent(self, event):
        self.visibility_changed.emit(False)
        super().closeEvent(event)

    def _send(self):
        question = self.inp.text().strip()
        if question:
            self.inp.clear()
            self.ask(question)

    def ask(self, question):
        self.log.append(self._bubble(html_escape(question), True, '나'))
        local = self._local_answer(question)
        if local is not None:
            source = ('즉시 응답' if self._is_smalltalk(question)
                      else '실시간 로컬 집계')
            self._append_answer(local, source, 0.0)
            return
        if self._busy:
            self._append_answer(
                '이전 질문의 근거를 확인 중입니다. 완료 후 다시 질문해 주세요.',
                '요청 대기열 보호', 0.0)
            return
        self._busy = True
        self.waiting.setText('근거를 확인하고 있습니다…')
        threading.Thread(target=self._work, args=(question,), daemon=True).start()

    @staticmethod
    def _bubble(text, mine=False, meta=''):
        """QTextEdit가 안정적으로 지원하는 표 정렬로 좌우 말풍선을 만든다."""
        bubble = BG_SEL if mine else PANEL_HI
        text_color = TXT
        spacer = '<td width="18%"></td>'
        body = (f'<td bgcolor="{bubble}" style="padding:10px;color:{text_color};">'
                f'<b>{html_escape(meta)}</b><br>{text}</td>')
        cells = spacer + body if mine else body + spacer
        return (f'<table width="100%" cellspacing="0" cellpadding="8"><tr>{cells}'
                f'</tr></table>')

    @staticmethod
    def _is_smalltalk(question):
        q = ''.join(question.lower().split()).rstrip('!?.')
        return (q in ('안녕', '안녕하세요', '반가워', '반갑습니다', '고마워',
                      '고맙습니다', '감사합니다', '도움말', '도와줘')
                or q.startswith('안녕'))

    def _local_answer(self, question):
        c = self.console
        if c is None:
            return '연결된 관제 데이터가 없습니다.'
        q = ''.join(question.lower().split()).rstrip('!?.')
        if q.startswith('안녕') or q in ('반가워', '반갑습니다'):
            return '안녕하세요. Radar-Guard AI입니다. 현재 상태나 안전 매뉴얼을 물어보세요.'
        if q in ('고마워', '고맙습니다', '감사합니다'):
            return '도움이 되어 다행입니다. 다른 현장 상황도 확인해 드릴게요.'
        if q in ('도움말', '도와줘'):
            return ('현재 상태, 최근 경보, 레이더 사용 이유, 낙상·감전·협착 대응 '
                    '근거를 질문할 수 있습니다. 차단·해제·전원 복구는 실행하지 않습니다.')
        if '무슨 시스템' in question or '뭐 하는' in question:
            return ('Radar-Guard는 카메라 대신 mmWave 레이더로 작업자 낙상·무동작과 '
                    '설비 이상을 감지하고, 젯슨 차단과 RAG 대응 가이드를 결합한 '
                    '산업 안전 관제 시스템입니다.')
        if '레이더' in question and ('왜' in question or '카메라' in question):
            return ('카메라 제한 구역에서도 개인정보를 촬영하지 않고 작업자의 위치와 '
                    '움직임을 감지하며, 조도·분진·연기의 영향을 줄이기 위해 mmWave '
                    '레이더를 사용합니다.')
        if ('젯슨' in question or '노트북' in question) and '역할' in question:
            return ('젯슨은 위험 판정과 차단을 독립 수행하고, 노트북은 UDP 수신·화면·'
                    'SOP RAG를 담당합니다. 노트북 연결이 끊겨도 젯슨 판정은 계속됩니다.')
        if ('정지' in question or '형상' in question) and (
                '사라' in question or '안 보' in question or '왜' in question):
            return ('정지한 사람은 도플러가 0에 가까워 레이더 반사가 줄어 추적을 놓칠 '
                    '수 있습니다. 이때 UI는 거짓 자세를 그리지 않고 추적 소실을 표시합니다.')
        if 'LLM' in question or 'AI 역할' in question:
            return ('AI는 공식 SOP와 젯슨 실측값을 결합해 설명·대응 가이드를 작성하지만 '
                    '위험 판정, 차단, 경보 해제, 전원 복구를 실행하지 않습니다.')
        if ('다음조치' in q or '뭐해야' in q or '해야할' in q
                or '안한조치' in q or '미완료' in q):
            return self._next_action()
        if '재투입' in q or '전력복구' in q:
            if c.alarm != ST_NORMAL:
                return ('현재 경보가 진행 중이므로 전력을 재투입하지 마십시오. 먼저 현장 '
                        '상태를 확인하고 상황 종료 절차를 완료해야 합니다.')
            if c.pwr.tripped():
                return ('차단 상태는 확인되지만 안전한 재투입 여부는 시스템이 보증할 수 '
                        '없습니다. 유자격자가 절연·누설·설비 상태를 확인한 뒤 '
                        '[전기 설비]의 확인 절차를 따르십시오.')
            return '현재 차단된 설비 회로가 없어 재투입할 대상이 없습니다.'
        if '조치이력' in q or '사고이력' in q or '최근이력' in q:
            return self._incident_history()
        if '차단' in question:
            zones = c.pwr.tripped()
            return (f'현재 차단된 설비 회로: {", ".join(zones)}. 재투입은 전기 설비 '
                    f'확인 절차에서만 가능합니다.' if zones
                    else '현재 차단된 설비 회로가 없습니다.')
        if '몇' in question or '건' in question:
            return f'오늘 기록된 경보는 {len(c.incidents)}건입니다.'
        if '마지막' in question or '언제' in question:
            if not c.incidents:
                return '오늘 기록된 경보가 없습니다.'
            last = c.incidents[-1]
            return (f"마지막 경보는 {last.get('detected')} Zone {last.get('zone')} "
                    f"{EVENT_KO.get(last.get('type'), last.get('type'))}입니다.")
        if '현재상태' in q or '상황알려' in q or '상황요약' in q:
            return self._current_brief()
        return None

    def _current_brief(self):
        """현재 UI가 받은 상태만 요약한다. 판정·추정은 추가하지 않는다."""
        c = self.console
        age = c.link.age() if c.link else None
        link = f'젯슨 마지막 수신 {age:.1f}초 전' if age is not None else '젯슨 미수신'
        occupied = c.pkt.get('occupied')
        worker = '작업자 재실' if occupied is True else (
            '작업자 퇴실' if occupied is False else '작업자 재실 정보 없음')
        zones = c.pwr.tripped()
        power = f'차단 회로 {", ".join(zones)}' if zones else '차단 회로 없음'
        if c.alert:
            et = c.alert.get('type')
            z = c.alert.get('zone') or RADAR_ZONE
            alarm = (f'{z} {ZONE_KO.get(z, "")} '
                     f'{EVENT_KO.get(et, et)} {SEV_KO.get(c.cur_sev(), "")} 경보 진행 중')
        else:
            alarm = '진행 중인 경보 없음'
        return f'{link}. {alarm}. {worker}. {power}. {self._next_action()}'

    def _next_action(self):
        """현재 상태에서 이미 화면에 확정된 첫 조치만 안내한다."""
        c = self.console
        if c.alert:
            et = c.alert.get('type')
            actions = INSTANT_ACTION.get(et, INSTANT_ACTION_UNKNOWN)
            first = actions[0][1][0]
            stage = '경보 확인 전' if c.alarm == ST_UNACK else '경보 확인 완료'
            return f'현재 단계: {stage}. 다음 조치: {first}'
        if c.pwr.tripped():
            return ('현재 단계: 상황 종료 후 차단 유지. 다음 조치: 유자격자가 현장과 '
                    '전기 상태를 확인한 뒤 전력 재투입 절차를 진행하십시오.')
        return '현재 단계: 정상 감시. 미완료 안전 조치가 없습니다.'

    def _incident_history(self):
        incidents = self.console.incidents[-3:]
        if not incidents:
            return '오늘 기록된 경보와 조치 이력이 없습니다.'
        rows = []
        for item in reversed(incidents):
            name = EVENT_KO.get(item.get('type'), item.get('type'))
            state = (f"{item.get('resolved')} 상황 종료" if item.get('resolved')
                     else '진행 중')
            rows.append(f"{item.get('detected')} {item.get('zone')} 구역 {name} · {state}")
        return '최근 조치 이력: ' + ' / '.join(rows)

    def _work(self, question):
        with AI_WORK_LOCK:
            self._work_locked(question)

    def _work_locked(self, question):
        import urllib.request
        started = time.perf_counter()
        try:
            event = self._event_for(question)
            sources, context = [], ''
            if event:
                category = core.EVENT_CATEGORY.get(event)
                vectorstore = core.PGVector(
                    connection_string=core.CONN_STR,
                    embedding_function=core.OllamaEmbeddings(model=EMBED_MODEL),
                    collection_name='safety_manual')
                docs = core.search_sop_documents(
                    vectorstore, event, core.SOP_QUERY[event], category)
                context = '\n'.join(d.page_content for d in docs)[:1400]
                sources = sorted({d.metadata.get('source_file', '?') for d in docs})
            live = ''
            c = self.console
            if c and c.alert:
                facts = SopEngineV2.build_facts(c.alert, c.pkt, 0)
                live = SopEngineV2._fact_block(facts)
            task_rule = (
                '공식 매뉴얼에 근거한 조치를 번호로 답하되, 확정 즉시조치와 현장 '
                '책임자 지시가 우선임을 지켜라.' if event else
                '질문의 주어와 이유를 첫 문장에 포함해 2~4문장으로 직접 답하라. '
                '질문을 되묻거나 질문 예시를 만들지 마라.')
            live_section = f'[현재 젯슨 실측]\n{live}\n' if live else ''
            prompt = (
                '너는 Radar-Guard 관제 시스템 전용 보조 AI다. 아래 시스템 명세와 '
                '공식 매뉴얼, 현재 실측값에 있는 내용만 사용해 한국어로 간결하게 '
                '답하라. 모르면 모른다고 말하고 차단·경보해제·전원복구를 실행했다고 '
                f'말하지 마라. {task_rule}\n'
                f'[시스템 명세]\n{self.SYSTEM_CONTEXT}\n'
                f'[공식 매뉴얼]\n{context or "해당 없음"}\n'
                f'{live_section}'
                f'[질문]\n{question}')
            body = json.dumps({
                'model': core.LLM_MODEL, 'prompt': prompt, 'stream': False,
                'keep_alive': '30m',
                'options': {'num_ctx': 2048, 'num_predict': 100,
                            'temperature': 0.2},
            }).encode('utf-8')
            req = urllib.request.Request(
                core.OLLAMA_URL, data=body,
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as response:
                answer = json.loads(response.read().decode('utf-8')).get(
                    'response', '').strip()
            # 질의 답변의 출처도 같은 규칙으로 표기한다(파일명 노출 금지).
            labels = []
            for s_file in sources:
                kind, title, _ = core.source_label(s_file)
                labels.append(f'[{core.SRC_BADGE[kind]}] {title}')
            source_text = ' · '.join(labels) if labels else 'Radar-Guard 내장 시스템 명세'
            self.answer_ready.emit(answer, source_text,
                                   time.perf_counter() - started)
        except Exception as e:
            self.answer_failed.emit(str(e))

    @staticmethod
    def _event_for(question):
        for words, event in (
            (('낙상', '추락', '넘어짐'), 'fall_detected'),
            (('감전',), 'electric_shock_risk'),
            (('협착', '끼임'), 'pinching'),
            (('정지형', '무동작'), 'stationary_anomaly'),
            (('진동',), 'vibration_anomaly')):
            if any(word in question for word in words):
                return event
        return None

    def _show_answer(self, answer, source, elapsed):
        self._busy = False
        self.waiting.clear()
        self._append_answer(answer, source, elapsed)

    def _append_answer(self, answer, source, elapsed):
        content = (f'{core.md_to_html(answer)}<br><span style="color:{FAINT};'
                   f'font-size:9pt;">근거: {html_escape(source)} · '
                   f'{elapsed:.1f}초</span>')
        self.log.append(self._bubble(content, False, 'AI'))

    def _show_error(self, error):
        self._busy = False
        self.waiting.clear()
        self.log.append(self._bubble(
            f'<span style="color:{AMBER};">AI 응답 실패: '
            f'{html_escape(error)}</span>', False, 'AI'))

    def system_notice(self, text, open_drawer=True):
        """젯슨에서 확인된 상태 변화를 AI 대화에 즉시 알린다."""
        self.log.append(self._bubble(html_escape(text), False, 'Radar-Guard AI'))
        if open_drawer:
            self.open_drawer()


# ══════════════════════════════════════════════════════════════════════
# 9. 좌측 네비게이션
# ══════════════════════════════════════════════════════════════════════
class SideNav(QtWidgets.QFrame):
    navigate = QtCore.pyqtSignal(int)
    ITEMS = [('▦', '대시보드'), ('◉', '실시간 감시'), ('▤', '이벤트 로그'),
             ('⌂', '현장 준비'), ('⊞', 'SOP 가이드'), ('⚙', '설정')]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(224)
        self.setStyleSheet(f'QFrame{{background:{PANEL};border:none;'
                           f'border-right:1px solid {EDGE};}}')
        v = vbox(self, SP4, SP2)
        # 햄버거 전용 첫 줄. 로고와 같은 좌표에 겹치지 않는다.
        v.addSpacing(44)
        brand = hbox(s=SP2)
        logo = lb('◈', 17, CYAN, bold=True)
        brand.addWidget(logo)
        # ⚠ 자간을 준 상태로 폭을 재지 않으면 마지막 글자가 잘린다
        #   (실측: 'RADAR-GUARD' 가 'RADAR-GUARI' 로 잘렸다)
        wm = lb('RADAR-GUARD', 12, TXT, bold=True, spacing=1)
        wm.setMinimumWidth(wm.fontMetrics().width('RADAR-GUARD') + 18)
        brand.addWidget(wm)
        brand.addStretch()
        v.addLayout(brand)
        v.addWidget(lb(APP_VERSION, F_CAP, FAINT))
        v.addSpacing(SP3)

        self.buttons = []
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        for i, (ic, name) in enumerate(self.ITEMS):
            b = NavButton(ic, name)
            self.group.addButton(b, i)
            b.clicked.connect(lambda _, k=i: self.navigate.emit(k))
            self.buttons.append(b)
            v.addWidget(b)
        v.addStretch()

        self.lock_note = lb('', F_CAP, RED, wrap=True)
        v.addWidget(self.lock_note)
        self.user = card(PANEL_HI, None, RADIUS_SM)
        uv = vbox(self.user, SP3, 0)
        self.uname = lb('관리자', F_BODY, TXT, bold=True)
        self.ushift = lb('주간조', F_CAP, DIM)
        uv.addWidget(self.uname)
        uv.addWidget(self.ushift)
        v.addWidget(self.user)
        self.buttons[0].setChecked(True)

    def select(self, idx):
        if 0 <= idx < len(self.buttons):
            self.buttons[idx].setChecked(True)

    def set_user(self, shift, operator):
        self.uname.setText(operator or '미지정')
        self.ushift.setText(shift)

    def set_locked(self, locked, allow=()):
        """미확인 경보 중에는 감시 화면을 떠날 수 없다."""
        for i, b in enumerate(self.buttons):
            b.setEnabled((not locked) or i in allow)
        self.lock_note.setText('미확인 경보 진행 중 — 확인 전까지 화면을 '
                               '이동할 수 없습니다' if locked else '')


# ══════════════════════════════════════════════════════════════════════
# 10. 메인 창
# ══════════════════════════════════════════════════════════════════════
PG_DASH, PG_MON, PG_LOG, PG_PREP, PG_SOP = 0, 1, 2, 3, 4
# 경보 발생 후 평면도를 보여 주는 시간. 이 뒤에 감시 화면으로 자동 전환한다.
AUTO_NAV_MS = 1500
NAV_SETTINGS = 5


# ══════════════════════════════════════════════════════════════════════
# 9-B. SOP 엔진 — 프롬프트에 '레이더 실측값' 을 주입한다
# ══════════════════════════════════════════════════════════════════════
#  구역별 위험 성격. 실측 브리핑에 위치의 의미를 덧붙일 때만 쓴다.
#  ⚠ 판정에는 쓰지 않는다. 표시용 배경정보일 뿐이다.
ZONE_HAZARD = {
    'A': '활선 고압 배전 설비가 있는 변전실',
    'B': '회전·절삭 기계가 있는 가공 구역',
    'C': '컨베이어·조립 설비가 있는 조립 구역',
}

# 변압기 앞 표시용 세부 관심영역. 젯슨의 정지형 이상 판정을 바꾸지 않고,
# 마지막 신뢰 위치가 어느 위험원 앞인지에 따라 경보 명칭과 SOP만 좁힌다.
def stationary_display_context(ev):
    """정지형 경보를 위치 추정으로 세분화하지 않고 그대로 표시한다."""
    if ev.get('type') != 'stationary_anomaly':
        return None
    evidence = ev.get('evidence') or {}
    x, z = evidence.get('anchor_cx'), evidence.get('anchor_cz')
    pos = (float(x), float(z)) if x is not None and z is not None else (0.0, 0.0)
    return {'kind': 'stationary', 'name': '정지형 이상',
            'sop_type': 'stationary_anomaly', 'pos': pos}


class SopEngineV2(core.SopEngine):
    """공식 대응 문서를 검색하고 젯슨 실측값을 결정적으로 표시한다.

    ═══ 왜 바꾸나 ═══
      v1 출력 실측:
        1. 낙상 발생 장소를 확인하고 안전한 위치로 이동하세요.
        2. 위험을 최소화하기 위해 작업자의 안전을 보호하는 방식으로 움직여야 합니다.
        3. 사고 사유와 관련된 정보를 기록합니다.
      → 레이더가 준 정보(높이 0.31m, 1.31m 하강, 이후 정지, 변전실=활선구역)가
        한 글자도 안 들어갔다. 이 문장은 LLM 없이 하드코딩해도 똑같이 나온다.
        즉 "왜 LLM을 넣었나"를 설명할 수 없는 상태였다.
      → 경보 안전 경로에서 생성형 모델을 빼고 evidence 를 그대로 표시한다.

    ═══ 숫자를 지어내지 못하게 하는 장치 ═══
      · 표시값은 전부 젯슨 classify() 가 계산해 보낸 값이다.
      · 생성 지연·환각·문장 잘림 없이 매 사건의 현재 값이 즉시 반영된다.
    """

    # ⚠ [8/25] 데몬 스레드가 살아 있는 채로 앱이 닫히면 Qt 객체가 먼저 지워져
    #   signal.emit 자체가 RuntimeError 를 낸다. 그러면 except 절 안의 emit 도
    #   같이 터져 traceback 이 콘솔로 새어 나간다(레이아웃 검증 2회차 실측).
    #   _work_v2 주석이 "무슨 일이 있어도 조용히 끝난다" 라고 적어 둔 불변식이
    #   실제로는 지켜지지 않고 있었다 → emit 을 감싸서 불변식을 코드로 만든다.
    #   삼키는 것은 '이미 창이 닫힌 뒤의 표시 실패' 뿐이고, 검색·생성 실패는
    #   그대로 화면에 뜬다(README §9).
    def _emit_status(self, msg):
        try:
            self.status.emit(msg)
        except RuntimeError:
            pass

    def _emit_ready(self, *args):
        try:
            self.ready.emit(*args)
        except RuntimeError:
            pass

    def request(self, ev_type, facts=None):
        threading.Thread(target=self._work_v2, args=(ev_type, facts or {}),
                         daemon=True).start()

    # ── 검색 (v1 _work 의 앞부분과 동일한 절차) ──
    def _search(self, ev_type):
        if not RAG_OK:
            self._emit_status('매뉴얼 검색 불가 — langchain 미설치 (즉시 조치만 표시)')
            return [], ''
        try:
            self._emit_status('안전 매뉴얼 검색 중…')
            situation = (core.SOP_QUERY.get(ev_type)
                         or f'{EVENT_KO.get(ev_type, ev_type)} 조치')
            cat = core.EVENT_CATEGORY.get(ev_type)
            emb = core.OllamaEmbeddings(model=EMBED_MODEL)
            vs = core.PGVector(connection_string=core.CONN_STR,
                               embedding_function=emb,
                               collection_name='safety_manual')
            docs = core.search_sop_documents(vs, ev_type, situation, cat)
            srcs = [(d.metadata.get('source_file', '?'),
                     ' '.join(d.page_content.split())[:360]) for d in docs]
            self._emit_status(f'매뉴얼 {len(docs)}건 검색됨')
            return srcs, ' '.join(d.page_content for d in docs)[:1500]
        except Exception as e:
            self._emit_status(f'매뉴얼 검색 실패: {e}  (docker start radar-guard-db)')
            return [], ''

    def _work_v2(self, ev_type, facts):
        # ⚠ 데몬 스레드에서 예외가 밖으로 나가면 콘솔에 traceback 이 찍히고,
        #   앱 종료 중이면 Qt 객체가 이미 정리된 상태라 2차 예외까지 난다.
        #   SOP 는 안전 조치의 필수 경로가 아니다(즉시조치는 이미 화면에 있다).
        #   → 무슨 일이 있어도 이 스레드는 조용히 끝난다.
        try:
            with AI_WORK_LOCK:
                self._work_body(ev_type, facts)
        except Exception as e:
            self._emit_status(f'SOP 처리 실패: {e}  (즉시조치는 계속 표시)')

    def _work_body(self, ev_type, facts):
        srcs, ctx = self._search(ev_type)
        brief = self._fact_block(facts).replace('\n- ', ' · ').removeprefix('- ')
        self._emit_status('공식 매뉴얼 표시 · AI 보조 요약 준비 중…')
        self._emit_ready(ev_type, srcs, brief)
        if not core.USE_LLM_SUMMARY:
            return
        try:
            generated = self._gen_facts(ev_type, ctx, facts)
            self._emit_status('AI 보조 요약 표시 · 공식 매뉴얼 기반')
            self._emit_ready(
                ev_type, srcs,
                f'{brief}\n---AI_SOP---\n{generated}')
        except Exception as e:
            self._emit_status(f'AI 보조 요약 실패: {e}  (공식 원문은 계속 표시)')

    # ── 사실 블록 ──
    @staticmethod
    def build_facts(ev, pkt, elapsed_sec=0):
        """젯슨이 보낸 값만 골라 담는다. 노트북이 계산한 값은 넣지 않는다."""
        e = ev.get('evidence') or {}
        z = ev.get('zone') or RADAR_ZONE
        bs = ((pkt.get('breaker') or {}).get('state')) or {}
        src = (pkt.get('breaker') or {}).get('src')
        off = bs.get(z, 'ON') != 'ON'
        no_auto_trip = ev.get('type') not in AUTO_TRIP_EVENTS
        return {
            'zone': f"{z} {ZONE_KO.get(z, '')}",
            'hazard': ZONE_HAZARD.get(z, ''),
            'height_start': e.get('height_start'),
            'height_end': e.get('height_end'),
            'h_drop': e.get('h_drop'),
            'horiz_range': e.get('horiz_range'),
            'ds_last': e.get('ds_last'),
            'conf': ev.get('conf'),
            'height_now': pkt.get('height'),
            'elapsed': elapsed_sec,
            'breaker': ('경보 — 설비 회로 자동 차단 대상 아님' if no_auto_trip
                        else '차단 완료(실측 확인)' if off and src == 'modbus'
                        else '차단 신호 발신(실측 미확인 — 현장 확인 필요)'
                        if off else '차단 미확인'),
        }

    @staticmethod
    def _fact_block(facts):
        L = []
        add = lambda t: L.append(f'- {t}')
        if facts.get('zone'):
            add(f"위치: {facts['zone']}"
                + (f" ({facts['hazard']})" if facts.get('hazard') else ''))
        hs, he = facts.get('height_start'), facts.get('height_end')
        if hs is not None and he is not None:
            add(f'사람 높이: {hs:.2f} m → {he:.2f} m'
                + (f" (총 {facts['h_drop']:.2f} m 하강)"
                   if facts.get('h_drop') is not None else ''))
        elif facts.get('height_now') is not None:
            add(f"현재 높이: {facts['height_now']:.2f} m (바닥 기준)")
        if facts.get('ds_last') is not None:
            add(f"쓰러진 뒤 움직임: {facts['ds_last']:.2f} "
                f"({'거의 정지' if facts['ds_last'] < 1.0 else '지속 중'})")
        if facts.get('horiz_range') is not None:
            add(f"수평으로 퍼진 폭: {facts['horiz_range']:.2f} m")
        if facts.get('elapsed'):
            add(f"무동작 경과: 약 {int(facts['elapsed'])}초")
        if facts.get('conf') is not None:
            add(f"판정 점수: {facts['conf']:.2f}")
        if facts.get('breaker'):
            add(f"해당 설비 회로: {facts['breaker']}")
        return '\n'.join(L)

    @staticmethod
    def _gen_facts(ev_type, ctx, facts):
        import urllib.request
        label = EVENT_KO.get(ev_type, ev_type)
        fact_block = SopEngineV2._fact_block(facts)
        prompt = (
            f'너는 산업 현장 안전관리 보조자다. "{label}" 경보에 대해 아래 '
            f'젯슨 실측값과 공식 매뉴얼 발췌만 근거로 초동 조치 SOP를 작성하라.\n'
            f'[젯슨 실측값]\n{fact_block or "—"}\n'
            f'[공식 매뉴얼 발췌]\n{ctx[:1100] or "검색 결과 없음"}\n'
            f'규칙: 번호를 매긴 4단계, 각 단계는 짧은 한 문장. 없는 수치를 '
            f'만들지 말고 이미 차단된 전원을 다시 차단하라고 하지 마라. '
            f'환자 이동·응급처치는 위 매뉴얼과 충돌하지 않게 쓰고 서론은 생략하라.')
        body = json.dumps({
            'model': core.LLM_MODEL, 'prompt': prompt, 'stream': False,
            'keep_alive': '30m',
            'options': {'num_ctx': 2048, 'num_predict': 220, 'temperature': 0.2},
        }).encode('utf-8')
        req = urllib.request.Request(
            core.OLLAMA_URL, data=body,
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode('utf-8')).get(
                'response', '').strip()

    def prewarm(self):
        """모델만 메모리에 올린다. 경보별 SOP 문장은 실측값 때문에 캐시하지 않는다."""
        if QtCore.qEnvironmentVariable('QT_QPA_PLATFORM') == 'offscreen':
            return
        def worker():
            import urllib.request
            try:
                with AI_WORK_LOCK:
                    body = json.dumps({
                        'model': core.LLM_MODEL, 'prompt': '.', 'stream': False,
                        'keep_alive': '30m', 'options': {'num_predict': 1},
                    }).encode('utf-8')
                    req = urllib.request.Request(
                        core.OLLAMA_URL, data=body,
                        headers={'Content-Type': 'application/json'})
                    urllib.request.urlopen(req, timeout=120).read()
            except Exception as e:
                self._emit_status(f'AI 모델 준비 실패: {e}  (경보 시 다시 시도)')
        threading.Thread(target=worker, daemon=True).start()


class ConsoleV2(QtWidgets.QMainWindow):
    """앱 셸.

    ⚠ 경보 상태기계·패킷 처리 순서는 v1 Console 과 의미가 같다. 위젯 이름만
      새 화면 구조에 맞게 바뀌었다. 바꾼 곳에는 전부 주석을 달았다.
    """
    HIST_KEYS = ('cz', 'ds', 'sc', 'logs', 'incidents')
    NAV_WIDTH = 224

    def __init__(self, link=None, demo=False):
        super().__init__()
        self.link = link
        self.demo = demo
        self.pkt = {}
        self.rx_ts = 0.0
        self.alive = False
        self.alarm = ST_NORMAL
        self.alert = None
        self.alert_t0 = 0.0        # ★ 노트북 수신 시각 기준 (젯슨 시계 안 씀)
        self.last_ev_id = 0
        self.last_ev_rev = 0
        self.incidents = []
        self.today = 0
        self.boot_t = time.time()
        self.quiet_since = None
        self.blink = False
        self._phase = None
        self._prewarmed = False
        self._nav_pending = False
        self._pre = None
        self._recent = deque(maxlen=4)

        self.zone = RADAR_ZONE
        self.shift = '주간조'
        self.operator = '미지정'

        self.setWindowTitle(f'Radar-Guard 관제 {APP_VERSION}')
        self.resize(1440, 900)
        # 1440×900 기준 설계. 이보다 작아지면 경보 배너 + 장면 + 계측 + 타임라인이
        #   세로로 안 들어가 위젯이 겹친다 — 겹치느니 창을 못 줄이게 막는다.
        self.setMinimumSize(1200, 800)
        self.setStyleSheet(
            f'QMainWindow{{background:{BG};}}'
            f'QStackedWidget{{background:{BG};}}'
            f'QWidget{{color:{TXT};}}'
            f'QToolTip{{background:{PANEL_HI};color:{TXT};border:1px solid {EDGE};'
            f'padding:4px;}}')

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        h = QtWidgets.QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self._shell_layout = h
        self._drawer_open = False
        self._nav_was_open = False
        self.menu_gutter = QtWidgets.QWidget()
        self.menu_gutter.setFixedWidth(64)
        h.addWidget(self.menu_gutter)
        self.nav = SideNav()
        self.nav.navigate.connect(self._nav_from_overlay)
        self.nav.hide()
        h.addWidget(self.nav)

        self.stack = QtWidgets.QStackedWidget()
        h.addWidget(self.stack, 1)

        self.menu_btn = QtWidgets.QToolButton(root)
        self.menu_btn.setText('☰')
        self.menu_btn.setToolTip('메뉴 열기')
        self.menu_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.menu_btn.setFixedSize(38, 38)
        self.menu_btn.setFont(f(F_H2, bold=True))
        self.menu_btn.setStyleSheet(
            f'QToolButton{{background:{PANEL};color:{TXT};border:1px solid {EDGE};'
            f'border-radius:19px;}}QToolButton:hover{{color:{CYAN};'
            f'border-color:{CYAN};}}')
        self.menu_btn.clicked.connect(self._toggle_nav)

        # 0 대시보드
        self.dash = DashboardPage(link, demo)
        self.dash.enter_zone.connect(self.begin_session)
        self.dash.open_settings.connect(lambda: self._navigate(NAV_SETTINGS))
        self.stack.addWidget(as_page(self.dash))

        # 1 실시간 감시
        self.monitor = MonitorPage(has_prepare=bool(link))
        self.monitor.ack.connect(self.do_ack)
        self.monitor.resolve.connect(self.do_resolve)
        self.monitor.prepare.connect(self.go_prepare)
        self.monitor.mode_changed.connect(self._refresh_scene)
        self.monitor.enter_confirmed.connect(lambda: self._set_occupancy(True))
        self.monitor.exit_confirmed.connect(lambda: self._set_occupancy(False))
        self.stack.addWidget(as_page(self.monitor))
        self.scene = self.monitor.scene
        self.track = self.scene.track          # SettingsPopup 진단 탭이 참조한다
        self.timeline = self.monitor.timeline
        self.drawer = self.monitor.drawer
        self.drawer.visibility_changed.connect(self._on_action_drawer)

        # 2 이벤트 로그
        self.evlog = EventLogPage()
        self.stack.addWidget(as_page(self.evlog))

        # 3 현장 준비 (링크가 있을 때만 의미가 있다)
        self.prep = core.PreparePage(link) if link else None
        if self.prep:
            self.prep.back.connect(lambda: self._navigate(0))
            self.prep.skip.clicked.connect(self.enter_console)
            self.stack.addWidget(as_page(self.prep))
        else:
            ph = QtWidgets.QWidget()
            pv = vbox(ph, SP5, SP3)
            pv.addWidget(lb('현장 준비', F_DISPLAY, TXT, bold=True))
            pv.addWidget(lb('데모 모드에서는 사용할 수 없습니다 — '
                            '젯슨에 연결한 뒤(--live) 진행하세요.', F_BODY, DIM))
            pv.addStretch()
            self.stack.addWidget(as_page(ph))

        # 4 SOP 가이드
        self.sopguide = SopGuidePage()
        self.stack.addWidget(as_page(self.sopguide))

        # ── 팝업 (v1 그대로 재사용) ──
        self.pwr = PowerPopup(self, link)
        self.restore = RestorePopup(self)
        self.graph = GraphPopup(self)
        self.cfg = SettingsPopup(self, link, self)
        self.engine = SopEngineV2()
        self.engine.ready.connect(self.drawer.sop.set_sources)
        self.engine.status.connect(self.drawer.sop.set_status)
        self.pwr.restore_btn.clicked.connect(self.do_restore)
        self.monitor.b_pwr.clicked.connect(self._show_pwr)
        self.monitor.b_graph.clicked.connect(self.graph.show)

        self.assistant = AssistantDrawer(self, self)
        self.assistant.visibility_changed.connect(self._on_assistant_visible)
        self.ai_btn = AiIconButton(root)
        self.ai_btn.clicked.connect(self._open_assistant)
        self.monitor.b_query.clicked.connect(self._open_assistant)

        self.timeline.add('시스템 시작', GREEN)
        self.timeline.add(f'3D 렌더 {"OpenGL" if self.track.gl else "2D 측면도 대체"}')
        if not RAG_OK:
            self.timeline.add('매뉴얼 검색 비활성 (langchain 미설치)', AMBER)

        # ── 타이머 ──
        #  렌더는 패킷 도착 시그널이 몰고, 이 타이머는 시계·점멸·경과시간만 갱신한다.
        self.ui_timer = QtCore.QTimer(self)
        self.ui_timer.timeout.connect(self.tick_ui)
        self.ui_timer.start(500)
        if link:
            link.packet.connect(self.on_packet)
            link.linkstate.connect(self.on_link)
        if demo:
            self.demo_src = core._DemoSource()
            self.demo_timer = QtCore.QTimer(self)
            self.demo_timer.timeout.connect(
                lambda: self.on_packet(self.demo_src.read()))
            self.demo_timer.start(100)
            self.alive = True
            self._set_link_label('데모 모드', AMBER)
        self.nav.set_user(self.shift, self.operator)
        self._apply_shell_geometry()

    # ══════════════════════════════════════════════════════════════════
    # 화면 이동
    # ══════════════════════════════════════════════════════════════════
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_shell_geometry()

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._apply_shell_geometry)

    def _apply_shell_geometry(self):
        if not hasattr(self, 'stack'):
            return
        if hasattr(self, 'menu_btn'):
            self.menu_btn.move(self.NAV_WIDTH - 48 if self.nav.isVisible() else 13, 12)
            self.menu_btn.raise_()
        if hasattr(self, 'ai_btn'):
            if self.nav.isVisible():
                pos = self.nav.user.mapTo(
                    self.centralWidget(), QtCore.QPoint(0, -58))
                self.ai_btn.move(pos)
            else:
                self.ai_btn.move(9, max(56, self.height() - 70))
            self.ai_btn.raise_()

    def _toggle_nav(self):
        opening = not self.nav.isVisible()
        self.menu_gutter.setVisible(not opening)
        self.nav.setVisible(opening)
        self._apply_shell_geometry()
        self.menu_btn.raise_()

    def _nav_from_overlay(self, idx):
        self.nav.hide()
        self.menu_gutter.show()
        self._navigate(idx)
        self._apply_shell_geometry()

    def _on_action_drawer(self, opening):
        self._drawer_open = opening
        if opening:
            self._nav_was_open = self.nav.isVisible()
            # SOP가 열려도 햄버거용 64px 레일은 남긴다.
            reclaimed = self.NAV_WIDTH - self.menu_gutter.width() \
                if self._nav_was_open else 0
            self.nav.hide()
            self.menu_gutter.show()
            self.drawer.set_extra_width(reclaimed)
        else:
            # 조치 확인 뒤 사이드바를 자동 복원하면 관제 화면 전체가 160px
            # 이동한다. 사용자가 원할 때만 햄버거로 다시 연다.
            self.nav.hide()
            self.menu_gutter.show()
        if hasattr(self, 'assistant'):
            self.assistant.close_drawer()
        if hasattr(self, 'ai_btn'):
            self.ai_btn.setVisible(not opening)
        if not opening:
            self.drawer.set_extra_width(0)
        self._apply_shell_geometry()

    def _open_assistant(self):
        if self.alarm != ST_NORMAL:
            self.timeline.add('경보 중에는 조치 가이드를 우선합니다', AMBER)
            self.drawer.open_at(0)
            return
        self.assistant.open_drawer()

    def _on_assistant_visible(self, visible):
        self.ai_btn.setVisible(not self._drawer_open)

    def _navigate(self, idx):
        if idx == NAV_SETTINGS:
            self.nav.select(self.stack.currentIndex())   # 설정은 페이지가 아니라 모달
            self.cfg.show()
            self.cfg.raise_()
            return
        if idx == PG_PREP and self.prep and self.pkt:
            self.prep.update_phase(self.pkt)
            self.prep.set_zone(self.zone)
        self.stack.setCurrentIndex(idx)
        self.nav.select(idx)
        self._apply_shell_geometry()

    def _set_link_label(self, text, color):
        self.monitor.link_lb.setText(f'● {text}')
        self.monitor.link_lb.setStyleSheet(
            f'color:{color};border:none;background:transparent;')

    def _auto_nav(self):
        """경보 발생 AUTO_NAV_MS 뒤 감시 화면으로. 그 사이 상황이 바뀌면 취소."""
        self._nav_pending = False
        if self.alarm == ST_UNACK and self.stack.currentIndex() != PG_MON:
            self.enter_console()

    def cur_sev(self, pkt=None):
        """지금 화면이 써야 할 경보 등급. 경보가 없으면 'normal'.

        pkt 를 주면 상태기계가 아직 반영하지 못한 '방금 도착한 경보' 도 본다
        (대시보드처럼 상태기계보다 먼저 그려지는 화면용).
        """
        if self.alarm != ST_NORMAL and self.alert:
            return self.alert.get('sev') or event_sev(self.alert.get('type'))
        ev = (pkt or {}).get('ev') or {}
        if ev.get('active'):
            return ev.get('sev') or event_sev(ev.get('type'))
        return 'normal'

    def _incident_visual(self):
        """경보 종류를 실측 자세와 구분된 OBJ 사고 포즈로 반환한다."""
        if not self.alert:
            return None
        types = set(self.alert.get('types') or [self.alert.get('type')])
        if 'fall_detected' in types:
            # 변압기와 겹치지 않는 전면 빈 바닥에 사고 예시 자세를 둔다.
            return {'kind': 'fall', 'pos': (0.0, -0.55)}
        if types & {'pinching', 'pinching_suspected'}:
            # 우측 변압기 강제냉각 팬 앞. 표시 전용 고정 위치다.
            return {'kind': 'pinching', 'pos': (0.65, 0.18)}
        if types & {'electric_shock_risk', 'electric_shock_risk_confirmed'}:
            # 좌측 단자함 앞. 표시 전용 고정 위치로 판정 좌표가 아니다.
            return {'kind': 'electric', 'pos': (-0.82, 0.28)}
        return None

    def _refresh_scene(self):
        """3D↔2D 전환 직후, 새 패킷을 기다리지 않고 즉시 다시 그린다.

        ⚠ [8/02] set_mode() 는 렌더를 안 하고 다음 패킷을 기다렸다. 젯슨
          링크가 끊긴 뒤에도 LINK_TIMEOUT(3초, radar_common.py)까지는 아직
          stale 베일이 안 뜨는데, 그 창 안에서 경보 중 모드를 바꾸면 다음
          패킷이 영영 안 와 방금 활성화된 위젯이 예전 색에 멈춰 있을 수
          있었다. 새 상태를 만들지 않고 이미 있는 self.pkt/self.alarm/
          self.alert 로만 다시 그린다(§1의 '상태를 새로 늘리지 않는다').
        """
        if not self.pkt:
            return
        sev = self.cur_sev()
        lost = (self.alarm != ST_NORMAL
                and (self.alert or {}).get('type') in ('fall_detected', 'fall_suspected',
                                                       'stationary_anomaly'))
        self.scene.track._incident = self._incident_visual()
        self.scene.redraw(sev, hide_shape=lost)

    def _lock_nav(self):
        """미확인(UNACK) 경보 중에는 감시 화면만 남긴다.

        v1 은 '다른 화면에 있으면 강제로 관제로 되돌린다'로 같은 목적을 달성했다.
        네비게이션이 생겼으므로, 되돌리는 대신 아예 못 나가게 하는 편이
        조작 결과가 예측 가능하다 ('눌렀는데 튕겨나감'이 없다).
        """
        self.nav.set_locked(self.alarm == ST_UNACK, allow=(PG_MON,))

    # ══════════════════════════════════════════════════════════════════
    # 링크
    # ══════════════════════════════════════════════════════════════════
    def on_link(self, alive):
        self.alive = alive
        self._set_link_label('연결 정상' if alive else '연결 끊김',
                             GREEN if alive else RED)
        self.timeline.add('젯슨 연결됨' if alive else '젯슨 연결 끊김 — 화면 정지',
                          GREEN if alive else RED)
        if not alive:
            self.scene.set_stale(True, '데이터 없음\n젯슨 연결이 끊겼습니다')

    def _set_occupancy(self, occupied):
        """운영자 재실 확인. 낙상 판정·경보·차단 상태에는 손대지 않는다."""
        if not self.link:
            return
        cmd = CMD_ENTER if occupied else CMD_EXIT
        if self.link.send_cmd(cmd):
            self.timeline.add('작업자 입실 확인' if occupied else '작업자 퇴실 확인',
                              GREEN if occupied else DIM)

    # ══════════════════════════════════════════════════════════════════
    # 패킷 수신 (도착 즉시 렌더)
    # ══════════════════════════════════════════════════════════════════
    #   히스토리(cz/ds/sc/logs/incidents)는 1초에 한 번 오는 'full' 패킷에 실린다.
    #   full 이 아닌 패킷엔 그 키가 없으므로 직전 값을 이어 붙인다. (v1 과 동일)
    def on_packet(self, pkt):
        previous = self.pkt
        if not pkt.get('full'):
            for k in self.HIST_KEYS:
                if k not in pkt and k in self.pkt:
                    pkt[k] = self.pkt[k]
        self.pkt = pkt
        self.rx_ts = time.time()
        self.alive = True
        self.dash.on_packet(pkt)

        prev_breaker = (previous.get('breaker') or {}) if previous else {}
        breaker = pkt.get('breaker') or {}
        if (prev_breaker.get('src') == 'modbus'
                and prev_breaker.get('connected')
                and breaker.get('src') == 'modbus'
                and breaker.get('connected')):
            before = prev_breaker.get('state') or {}
            after = breaker.get('state') or {}
            restored = [z for z in ZONE_IDS
                        if before.get(z) not in (None, 'ON')
                        and after.get(z) == 'ON']
            for z in restored:
                place = ZONE_KO.get(z, f'Zone {z}')
                self.timeline.add(f'{z} {place} 전력 복구 확인', GREEN)
                self.assistant.system_notice(
                    f'{place}에서 전력을 복구했습니다. Modbus 릴레이 응답으로 '
                    '정상 투입 상태를 확인했습니다.')

        ev0 = pkt.get('ev') or {}
        page = self.stack.currentIndex()

        # 미확인 경보가 뜨면 감시 화면으로 데려온다 (v1 과 동일한 안전 장치)
        #   ⚠ 즉시 넘기지 않고 AUTO_NAV_MS 만큼 기다린다. 관제에서 첫 질문은
        #     '어디' 이고, 평면도에서 그 구역이 붉어지는 걸 보고 넘어가는 편이
        #     바로 상세 화면으로 튀는 것보다 상황 파악이 빠르다.
        #     (확인함(ACK) 이후에는 자동으로 끌고 오지 않는다 — 근무자가 이미 안다)
        if page != PG_MON and ev0.get('active') and self.alarm != ST_ACK:
            if not self._nav_pending:
                self._nav_pending = True
                QtCore.QTimer.singleShot(AUTO_NAV_MS, self._auto_nav)

        # RESET으로 현장준비 페이지에 있어도 젯슨의 경보 해소를 먼저 반영한다.
        # 이전에는 PG_PREP 조기 return 뒤에 있어 과거 정지형 경보가 영구 잔류했다.
        self._pump_state(pkt)
        on_alert = self.alarm != ST_NORMAL
        self._show_pre(pkt)          # 화면에 상관없이 항상 최신으로
        # ⚠ 화면 전체가 같은 등급 색을 써야 한다. 배너는 주황인데 점군만
        #   빨강이면 근무자는 두 개의 서로 다른 사건으로 읽는다.
        sev = self.cur_sev(pkt)
        # 대시보드 카드는 어느 화면에 있든 갱신한다 (v1 은 개요에서 렌더를 멈췄지만,
        #   카드 지표가 비어 보이는 원인이었다. 비용은 라벨 4개다.)
        self.dash.push(pkt, sev)
        self.dash.set_alarm_count(1 if on_alert else 0, self.today, sev)

        if page == PG_PREP:
            if self.prep:
                self.prep.update_phase(pkt)
            if pkt.get('phase') == PH_LIVE and self.prep and self.prep.autoback:
                self.prep.autoback = False
                self.enter_console()          # 감시 시작 확인 → 관제로 자동 복귀
            return
        if page != PG_MON:
            return

        self.scene.set_stale(False)

        occupied = bool(pkt.get('occupied'))
        self.monitor.occupancy.setText('재실' if occupied else '퇴실')
        self.monitor.occupancy.setStyleSheet(f'color:{GREEN if occupied else DIM};')
        self.monitor.enter_btn.setEnabled(not occupied)
        self.monitor.exit_btn.setEnabled(occupied)

        ph = pkt.get('phase')
        if ph != self._phase:
            self._phase = ph
            self.monitor.sub.setText(
                f'{self.zone} {ZONE_KO.get(self.zone, "")} · {PHASE_KO.get(ph, ph or "")}')
            self.timeline.add(f'단계 전환 · {PHASE_KO.get(ph, ph)}',
                              GREEN if ph == PH_LIVE else DIM)
            if ph == PH_LIVE and not self._prewarmed:
                self._prewarmed = True
                self.engine.prewarm()

        # ⚠ 상태기계를 '그리기 전에' 돌린다.
        #   v1 은 렌더 → 상태기계 순서였다. 그러면 경보가 시작된 바로 그 패킷은
        #   아직 self.alert 가 비어 있어 등급이 normal 로 계산되고, 점군·인체
        #   도식만 초록으로 한 프레임 늦게 따라간다(실측: 주의 경보인데 도식이
        #   초록으로 남음). 판정 결과는 패킷에 이미 들어 있으므로 먼저 반영한다.
        on_alert = self.alarm != ST_NORMAL
        sev = self.cur_sev()

        ev = pkt.get('ev') or {}
        # ⚠⚠ [8/01 실측] 사람 관련 경보 중인데 살아 있는 점군이 '서 있음' 이면,
        #   그건 사람이 아니라 추적을 놓친 것이다.
        #   정지한 사람은 도플러가 0 이라 FMCW 레이더가 놓치고, 그 자리를
        #   '일정 거리 링'(다중경로·바닥반사)이 채운다. 실측에서 낙상 직후
        #   마지막 프레임 6점 중 5점이 전부 y≈1.00(높이 1.30 m)인데 x·z 만
        #   ±0.8 m 로 흩어져 있었다 — 누운 사람이면 0.1~0.4 m 여야 한다.
        #   → 낙상 경보 옆에 '서 있는 사람' 을 그리면 화면이 거짓말을 한다.
        #     형상을 지우고 이유를 쓴다. 이건 이 레이더의 알려진 한계다.
        lost = (on_alert
                and (self.alert or {}).get('type') in ('fall_detected', 'fall_suspected',
                                                       'stationary_anomaly'))
        incident = self._incident_visual()
        self.scene.track._incident = incident
        types = set((self.alert or {}).get('types') or
                    [(self.alert or {}).get('type')]) if on_alert else set()
        self.scene.track.set_equipment_alarm(
            bool(types & {'overcurrent', 'leakage_current'}))
        pose = self.scene.push(pkt, sev, hide_shape=lost)
        if pose:
            # ⚠ v1 은 zone 이 없을 때 'C' 로 떨어졌다 — 레이더가 없는 구역이다.
            #   레이더 유래 정보는 레이더가 설치된 구역으로 보고한다.
            z = ev.get('zone') or RADAR_ZONE
            if incident:
                shape = (f"{EVENT_KO.get((self.alert or {}).get('type'), '사고 감지')}"
                         ' · 유형 안내')
            elif lost:
                shape = ('추적 소실 — 정지한 대상은 반사가 줄어 놓칩니다')
            elif not pose['shape_ok']:
                shape = f"형상 표시 안 함 ({pose['shape_why']})"
            else:
                shape = '서 있음' if pose['posture'] == 'standing' else '누워 있음'
            self.monitor.set_pose_text(
                f"{z} {ZONE_KO.get(z, '')} · {shape}"
                f"   ·   {pose['label']}",
                sev_color(sev) if on_alert else FAINT)
        elif pkt.get('track_state') == 'lost_in_zone':
            # 정지 인체는 도플러가 0이 되어 형상은 사라져도 젯슨 점유 트랙은 남는다.
            # 없는 자세를 그리지 않고, 빈방과 구분되는 점유 상태만 사실대로 표시한다.
            self.monitor.set_pose_text(
                f"{RADAR_ZONE} {ZONE_KO.get(RADAR_ZONE, '')} · 점유 유지 · 정지로 점군 소실",
                AMBER)
        self.monitor.zstrip.update_state(pkt, sev)
        self._update_tiles(pkt, on_alert)
        self.graph.push(pkt)
        self.pwr.push(pkt)

    def _show_pre(self, pkt):
        """정지형 사전경보 줄.

        ⚠ 이건 경보가 아니다. 점멸·소리·네비 잠금 없이 조용히 알리기만 한다.
          움직이면 젯슨이 pre_alert 를 비우고 줄은 그대로 사라진다.
          경보(UNACK/ACK) 중에는 배너가 이미 있으므로 띄우지 않는다.
        """
        pre = parse_pre_alert(pkt.get('pre_alert')) if self.alarm == ST_NORMAL \
            else None
        self._pre = pre
        if pre:
            z = pre['zone']
            self.monitor.pre_lb.setText(
                f"{z} {ZONE_KO.get(z, '')} · {pre['text']} — "
                f"움직이면 취소됩니다")
            if not self.monitor.pre_bar.isVisible():
                self.timeline.add(f"{z} 구역 사전경보 — {pre['text']}", AMBER)
            self.monitor.pre_bar.show()
        else:
            self.monitor.pre_bar.hide()

    def _pump_state(self, pkt):
        """경보 상태기계 — v1 과 동일."""
        ev = pkt.get('ev') or {}
        eid = ev.get('id') or 0
        rev = ev.get('rev') or 0
        if ev.get('active') and (eid != self.last_ev_id or rev != self.last_ev_rev):
            updating = eid == self.last_ev_id and self.last_ev_id != 0
            self.last_ev_id = eid
            self.last_ev_rev = rev
            self.on_event(ev, updating=updating)
        elif not ev.get('active') and self.alarm != ST_NORMAL:
            # 젯슨이 먼저 해소한 경우(노트북 '상황 종료'의 왕복 결과 포함)
            self.clear_alarm(remote=True)

    def _update_tiles(self, st, alert):
        ev = (st.get('ev') or {}).get('evidence') or {}
        sc, th = st.get('_sc', 0.0), st.get('threshold', 0.0)
        ratio = ((ev.get('ae_score', sc) or 0) /
                 max(ev.get('ae_thr', th) or 1e-9, 1e-9)) if (ev or th) else 0
        # ⚠ 경보 중에도 수치는 흰색을 유지한다. 빨강은 배너와 [조치 방법] 전용.
        #   전부 빨개지면 시선의 종착점이 사라진다.
        t = self.monitor.tiles
        t['height'].set(f"{st.get('height') or 0:.2f}")
        t['dop'].set(f"{st.get('dop_std', 0):.2f}")
        n = st.get('n_pts', 0)
        t['pts'].set(f'{n}', AMBER if n <= 2 else TXT,
                     note='원시 · 누적 표시' if n <= 2 else '원시')
        t['ae'].set(f'{ratio:.1f}' if ratio else '—',
                    AMBER if ratio and ratio >= 1.0 else TXT)

    def _stale_tiles(self):
        for k in ('height', 'dop', 'pts', 'ae'):
            self.monitor.tiles[k].set('—', FAINT)

    # ══════════════════════════════════════════════════════════════════
    # 경보
    # ══════════════════════════════════════════════════════════════════
    def on_event(self, ev, updating=False):
        self.assistant.close_drawer()
        self.alert = dict(ev)
        types = list(dict.fromkeys(ev.get('types') or [ev.get('type')]))
        items = ev.get('items') or {}
        context = stationary_display_context(ev) if len(types) == 1 else None
        if context:
            self.alert['_display_context'] = context
        if not updating:
            self.alert_t0 = time.time()      # ★ 노트북 시각. 젯슨 시계 안 씀.
        self.alarm = ST_UNACK
        if not updating:
            self.today += 1
        z = ev.get('zone') or RADAR_ZONE
        et = ev.get('type')
        sev = ev.get('sev', 'critical')
        name = (context['name'] if context else ' + '.join(
            f'{EVENT_KO.get(t, t)} [{SEV_KO.get((items.get(t) or {}).get("sev"), "")}]'
            if items.get(t) else EVENT_KO.get(t, t) for t in types if t))
        sop_priority = ('electric_shock_risk_confirmed', 'pinching',
                        'pinching_suspected', 'leakage_current',
                        'electric_shock_risk', 'fall_detected',
                        'overcurrent', 'voltage_drop')
        sop_type = (context['sop_type'] if context else
                    next((t for t in sop_priority if t in types), et))
        col = sev_color(sev)

        self.monitor.b_left.setText(
            f'● {name} · {z} {ZONE_KO.get(z, "")} · {SEV_KO.get(sev, "")}')
        self.monitor.banner.show()
        self.monitor.a_kind.setText(name)
        self.monitor.a_kind.setStyleSheet(
            f'color:{col};border:none;background:transparent;')
        self.monitor.a_conf.setText(f'판정 점수 {ev.get("conf", 0):.2f}')
        self.monitor.alert_box.setStyleSheet(
            f'QFrame{{background:{PANEL};border:1px solid {col};'
            f'border-radius:{RADIUS}px;}}')
        # ⚠ '지금 누를 버튼' 도 등급 색을 따른다. 배너는 주황인데 버튼만 빨강이면
        #   근무자는 두 개의 서로 다른 사건으로 읽는다.
        self.monitor.a_sop.setStyleSheet(
            f'QPushButton{{background:{col};color:#0B0F17;border:none;'
            f'border-radius:{RADIUS_SM}px;padding:4px 14px;font-weight:bold;}}'
            f'QPushButton:hover{{background:{col};}}')
        self.monitor.alert_box.show()
        self.monitor.hero.hide()

        self.drawer.evi.set_event(ev, self.alert_t0)
        ts = time.strftime('%H:%M:%S', time.localtime(self.alert_t0))
        if not updating:
            self.incidents.append({'type': et, 'zone': z, 'detected': ts,
                                   'resolved': None})
            self.evlog.add(ts, et, z, ev.get('conf', 0), '진행 중')
        self.timeline.add(f'{name} 감지 · {z} 구역 '
                          f'(판정 점수 {ev.get("conf", 0):.2f})', col)
        self._recent.appendleft((ts, f'{name} 감지', col))
        self.dash.set_events(list(self._recent))
        # ⚠ 순서 주의: show_for() 가 자동조치 배지를 기본 문구
        #   ('전원 차단 완료')로 되돌린다. 실제 차단기 상태를 읽어 쓰는
        #   _set_auto_action() 은 반드시 그 다음에 불러야 한다.
        #   (뒤바뀌어 있어서 본 패널은 주황 '모의값', 드로어는 초록 '차단 완료'로
        #    같은 사건을 두 가지로 말하고 있었다)
        self.drawer.sop.show_for(sop_type, title=name, sev=sev)
        self._set_auto_action(z)
        if not updating:
            # 판정 결과를 다시 계산하지 않고 현재 패킷과 확정 즉시조치만 요약한다.
            # 경보 화면을 가리지 않도록 대화 기록에만 쌓고 팝업은 열지 않는다.
            self.assistant.system_notice(self.assistant._current_brief(),
                                         open_drawer=False)
        self.drawer.ack.show()
        if self.cfg.autopop.isChecked():
            self.drawer.open_at(0)
        # 프롬프트에 넣을 사실은 전부 젯슨이 계산해 보낸 값이다 (evidence/gates).
        #   노트북이 만든 추정치(PoseEstimator)는 넣지 않는다 — LLM 이 표시용
        #   추정을 실측처럼 말하게 되면 판단 근거가 오염된다.
        self.engine.request(sop_type, SopEngineV2.build_facts(ev, self.pkt, 0))
        self._lock_nav()

    def _set_auto_action(self, z):
        """자동 차단이 '실제로' 됐는지를 차단기 상태로 확인해서 쓴다.

        ⚠ v1 은 항상 '전원 차단 완료'라고 썼다. 차단기가 미연결이거나 실패한
          경우에도 완료라고 말하게 된다. 상태를 읽어 다르게 쓴다.
        """
        breaker = self.pkt.get('breaker') or {}
        bs = breaker.get('state') or {}
        reasons = breaker.get('reason') or {}
        src = breaker.get('src')
        off = bs.get(z, 'ON') != 'ON'
        et = (self.alert or {}).get('type')
        reason = reasons.get(z)
        if et not in AUTO_TRIP_EVENTS:
            if off:
                why = EVENT_KO.get(reason, reason or '사유 미확인')
                txt = f'{z} 구역 기존 차단 유지 · {why} (이 경보로 추가 차단 안 함)'
            else:
                txt = '경보 전파 완료 · 설비 회로 자동 차단 대상 아님'
            c, bg = GREEN, BG_OK
        elif off and reason != et:
            why = EVENT_KO.get(reason, reason or '사유 미확인')
            if src == 'modbus':
                txt, c, bg = f'{z} 구역 기존 차단 유지 · {why}', GREEN, BG_OK
            else:
                txt, c, bg = (f'{z} 구역 기존 차단 상태 · {why} '
                              f'(실측 미확인)'), AMBER, BG_WARN
        elif off and src == 'modbus':
            txt, c, bg = f'{z} 구역 작업 대상 설비 회로 차단 완료 · 젯슨 자동 실행', GREEN, BG_OK
        elif off:
            txt, c, bg = (f'{z} 구역 차단 신호 발신 (실측 미확인 — '
                          f'현장 차단 여부를 직접 확인하십시오)'), AMBER, BG_WARN
        else:
            txt, c, bg = (f'{z} 구역 설비 회로 차단이 확인되지 않았습니다 — '
                          f'[전기 설비]에서 확인하십시오'), RED, BG_ALERT
        self.monitor.auto_lb.setText(txt)
        self.monitor.auto_lb.setStyleSheet(
            f'color:{c};border:none;background:transparent;')
        self.monitor.auto_box.setStyleSheet(
            f'QFrame{{background:{bg};border:1px solid {c};'
            f'border-radius:{RADIUS_SM}px;}}')
        self.drawer.sop.done.setText(txt)
        self.drawer.sop.done.setStyleSheet(
            f'color:{c};border:none;background:transparent;')
        self.drawer.sop.done_box.setStyleSheet(
            f'QFrame{{background:{bg};border:1px solid {c};'
            f'border-radius:{RADIUS_SM}px;}}')

    def do_ack(self):
        """확인함 = 소리·점멸만 끈다. 경보는 그대로 남는다 (ISA-18.2)."""
        if self.alarm == ST_UNACK:
            self.alarm = ST_ACK
            self.timeline.add('경보 확인 처리 — 경보음 정지 (상황은 지속 중)', AMBER)
            self._lock_nav()

    def do_resolve(self):
        """상황 종료 = 사람이 현장을 확인했다는 선언. 자동 해제는 없다."""
        if not core.confirm(self, '상황 종료',
                            '현장 상황이 해소된 것을 직접 확인하셨습니까?\n\n'
                            '전원은 자동으로 복구되지 않습니다 (LOTO).\n'
                            '재투입은 [전기 설비]에서 별도로 진행하십시오.',
                            yes='해소 확인', no='취소', danger=True):
            return
        if self.link:
            self.link.send_cmd(CMD_RESOLVE)
        else:
            # 실운용은 젯슨의 ev.active=false 응답을 받은 뒤에만 해제한다.
            # 먼저 지우면 현장 UI는 위험, 관제 UI는 정상으로 갈라진다.
            self.clear_alarm()

    def clear_alarm(self, remote=False):
        if self.alarm == ST_NORMAL:
            return
        self.alarm = ST_NORMAL
        self.alert = None
        self.monitor.banner.hide()
        self.monitor.alert_box.hide()
        self.monitor.hero.show()
        self.drawer.ack.hide()
        self.drawer.close_drawer()
        self.quiet_since = time.time()
        ts = time.strftime('%H:%M:%S')
        for i in reversed(self.incidents):
            if i['resolved'] is None:
                i['resolved'] = ts
                break
        self.evlog.mark_resolved(ts)
        self.timeline.add('상황 종료 처리됨' + (' (젯슨)' if remote else ''), GREEN)
        self._recent.appendleft((ts, '상황 종료', GREEN))
        self.dash.set_events(list(self._recent))
        self.assistant.system_notice(self.assistant._current_brief(),
                                     open_drawer=False)
        self._lock_nav()

    def _show_pwr(self):
        self.pwr.refresh()
        self.pwr.show()
        self.pwr.raise_()

    def do_restore(self):
        zs = self.pwr.tripped()
        if not zs:
            return
        if self.restore.ask(zs):
            if self.link:
                self.link.send_cmd(CMD_RESTORE, zones=zs)
            else:
                for z in zs:
                    self.pwr.snap[z] = 'ON'
                self.pwr.refresh()
            self.timeline.add(f"전원 재투입 요청 · {', '.join(zs)} 구역 (수동)", GREEN)

    # ══════════════════════════════════════════════════════════════════
    # 세션
    # ══════════════════════════════════════════════════════════════════
    def begin_session(self, info):
        self.zone = info.get('zone', RADAR_ZONE)
        self.shift = info.get('shift', '주간조')
        self.operator = info.get('operator', '미지정')
        self.nav.set_user(self.shift, self.operator)
        self.monitor.sub.setText(
            f'{self.zone} {ZONE_KO.get(self.zone, "")} · '
            f'{PHASE_KO.get(self._phase, "연결 대기")}')
        if self.prep:
            self.prep.set_zone(self.zone)
        self.enter_console()

    def enter_console(self):
        """감시 진입. 여기서부터 이 앱은 '감시 중'이라고 말할 자격이 있다."""
        if self.stack.currentIndex() == PG_MON:
            return
        if self.quiet_since is None or self.stack.currentIndex() == PG_DASH:
            self.quiet_since = time.time()
        self._navigate(PG_MON)
        self.timeline.add(f'감시 시작 · {self.zone} {ZONE_KO.get(self.zone, "")} · '
                          f'{self.shift} {self.operator}', GREEN)

    def go_prepare(self):
        if not self.prep:
            return
        self._navigate(PG_PREP)

    # ══════════════════════════════════════════════════════════════════
    # 0.5초 UI 갱신 (시계 · 점멸 · 경과시간 · stale)
    # ══════════════════════════════════════════════════════════════════
    def _tick_summary(self):
        s = self.monitor.summary
        s['zone'].setText(f'{self.zone} {ZONE_KO.get(self.zone, "")} · '
                          f'{ZONE_DEVICE.get(self.zone, "")}')
        s['who'].setText(f'{self.shift} · {self.operator}')
        if self.demo:
            s['rx'].setText('데모 데이터 · 10 Hz')
            s['rx'].setStyleSheet(f'color:{AMBER};border:none;background:transparent;')
        else:
            age = self.link.age() if self.link else None
            if age is None:
                s['rx'].setText('수신 없음')
                c = AMBER
            else:
                s['rx'].setText(f'{age:.1f}초 전 · seq {self.link.seq}')
                c = GREEN if age <= LINK_TIMEOUT else RED
            s['rx'].setStyleSheet(f'color:{c};border:none;background:transparent;')
        done = sum(1 for i in self.incidents if i.get('resolved'))
        s['today'].setText(f'{self.today}건 · 종료 {done}건')

    def tick_ui(self):
        self.blink = not self.blink
        self.dash.set_blink(self.blink)      # 평면도 경보 점멸
        self.monitor.clock.setText(time.strftime('%H:%M:%S'))
        self.monitor.fit_legend()
        self.monitor.fit_pose_text()     # 드로어 개폐는 resizeEvent 를 안 일으킨다
        if self.stack.currentIndex() != PG_MON:
            return
        self._tick_summary()

        # 기준 미학습 안내
        ph = self.pkt.get('phase')
        if self.prep and ph is not None and ph != PH_LIVE:
            self.monitor.needcal_lb.setText(
                '정상 기준이 학습되지 않았습니다 — 빈방 스캔을 실행해야 '
                '이상을 판별할 수 있습니다' if ph == PH_READY else
                f'준비 진행 중 · {PHASE_KO.get(ph, ph)} — 현장 준비 화면에서 확인하세요')
            self.monitor.needcal.show()
        else:
            self.monitor.needcal.hide()

        # stale — 화면이 조용히 거짓말하지 않게
        linked = True
        if self.link and not self.demo:
            age = self.link.age()
            if age is None:
                linked = False
                self.scene.set_stale(True, '젯슨 연결 대기 중\n아직 데이터를 받지 못했습니다')
                self._stale_tiles()
                self._set_link_label('연결 대기', AMBER)
            elif age > LINK_TIMEOUT:
                linked = False
                self.scene.set_stale(True, f'데이터 없음\n마지막 수신 {int(age)}초 전')
                self._stale_tiles()
                self._set_link_label(f'연결 끊김 ({int(age)}s)', RED)

        if self.alarm == ST_NORMAL:
            # ⚠ 링크가 없으면 '이상 없음'이라고 말하지 않는다.
            #   감시하지 않는 상태를 초록으로 표시하는 것이 이 앱이 할 수 있는
            #   가장 위험한 거짓말이다.
            if not linked:
                self._hero('—', AMBER, '감시 중단', AMBER, '',
                           '젯슨에서 데이터를 받지 못하는 동안은 이 화면이 '
                           '현장 상태를 보장하지 않습니다')
                return
            if ph is not None and ph != PH_LIVE:
                self._hero('—', AMBER, '감시 대기', AMBER, '',
                           f'{PHASE_KO.get(ph, ph)} — 정상 기준이 학습되기 전까지는 '
                           f'이상을 판별할 수 없습니다')
                return
            q = int(time.time() - (self.quiet_since or time.time()))
            up = int(time.time() - self.boot_t)
            extra = ''
            if self.link and not self.demo and self.link.lost:
                extra = f' · 패킷 유실 {self.link.lost}'
            self._hero('✓', GREEN, '이상 없음', TXT,
                       f'{q // 3600}시간 {q % 3600 // 60}분 무경보',
                       f'오늘 경보 {self.today}건 · '
                       f'가동 {up // 3600:02d}:{up % 3600 // 60:02d}{extra}')
            return

        # 경보 중 — 미확인이면 점멸 + 소리
        el = int(time.time() - self.alert_t0)
        unack = (self.alarm == ST_UNACK)
        sev = (self.alert or {}).get('sev', 'critical')
        col = sev_color(sev)
        on = (not unack) or self.blink
        self.monitor.banner.setStyleSheet(
            f'QFrame{{background:{SEV_BG_HI.get(sev, BG_ALERT) if on else PANEL};'
            f'border:{2 if unack else 1}px solid {col if on else EDGE};'
            f'border-radius:{RADIUS}px;}}')
        for w in (self.monitor.b_left, self.monitor.b_right):
            w.setStyleSheet(f'color:{col};border:none;background:transparent;')
        self.monitor.b_right.setText(
            f"{el // 60:02d}:{el % 60:02d} 경과 · {'미확인' if unack else '확인됨'}")
        # ⚠ 주의(warning)도 소리는 낸다. 정지형 이상은 감전·협착일 수 있어
        #   조용히 넘어가면 안 된다. 다만 확정 사고(critical)보다 드물게 울려
        #   '가서 확인' 과 '지금 뛰어가' 를 귀로도 구분하게 한다.
        if unack and self.blink and self.cfg.snd.isChecked():
            self._beep_n = getattr(self, '_beep_n', 0) + 1
            if sev == 'critical' or self._beep_n % 4 == 0:
                QtWidgets.QApplication.beep()
        et = (self.alert or {}).get('type')
        context = (self.alert or {}).get('_display_context')
        z = (self.alert or {}).get('zone') or RADAR_ZONE
        if et == 'fall_detected':
            self.monitor.msg.setText(
                f'작업자가 바닥에 누운 채 {el}초째 움직이지 않습니다.\n'
                f'{z} {ZONE_KO.get(z, "")} 구역.')
        elif et == 'fall_suspected':
            self.monitor.msg.setText(
                f'낙상 규칙과 RF 판정이 불일치했습니다. 즉시 현장을 확인하십시오.\n'
                f'{z} {ZONE_KO.get(z, "")} 구역 · {el}초 경과.')
        elif context:
            self.monitor.msg.setText(
                f'{context["name"]} · {z} {ZONE_KO.get(z, "")} 구역에서 '
                f'{el}초째 지속되고 있습니다.\n현장 확인 전까지 의심 경보입니다.')
        else:
            self.monitor.msg.setText(
                f'{EVENT_KO.get(et, "이상")} · {z} {ZONE_KO.get(z, "")} '
                f'구역에서 {el}초째 지속되고 있습니다.')

    def _hero(self, icon, icol, title, tcol, quiet, sub):
        m = self.monitor
        m.h_ic.setText(icon)
        m.h_ic.setStyleSheet(f'color:{icol};border:none;background:transparent;')
        m.h_t.setText(title)
        m.h_t.setStyleSheet(f'color:{tcol};border:none;background:transparent;')
        m.h_quiet.setText(quiet)
        m.h_s.setText(sub)

    def closeEvent(self, e):
        if self.link:
            self.link.stop()
        e.accept()


# ══════════════════════════════════════════════════════════════════════
# 11. 진입점
# ══════════════════════════════════════════════════════════════════════
def build_app(argv=None):
    """QApplication + 창을 만들어 돌려준다. (헤드리스 렌더 검증에서도 쓴다)"""
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', nargs='?', const='127.0.0.1', default=None,
                    metavar='젯슨IP', help='젯슨 실데이터 수신 (기본 127.0.0.1)')
    ap.add_argument('--host', default=None, help='--live 와 동일 (하위호환)')
    a, _ = ap.parse_known_args(argv)
    host = a.host or a.live
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    resolve_font()
    app.setFont(f(F_LABEL))
    apply_dark_palette(app)
    pg.setConfigOptions(background=PANEL, foreground=DIM, antialias=True)
    link = None
    if host:
        link = RadarLink(host)
        print(f'젯슨 {host} 구독 시작 (HELLO 발신 중)…')
    w = ConsoleV2(link, demo=(host is None))
    return app, w, link


def main():
    app, w, link = build_app()
    if link:
        link.start()
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
