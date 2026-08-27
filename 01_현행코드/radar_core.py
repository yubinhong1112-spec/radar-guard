"""radar_core.py — Radar-Guard 관제 앱의 로직·공용 위젯 계층 [노트북]

  이 파일은 단독 실행하지 않는다. 화면은 console_ui.py 가 그린다.
      [내 PC PowerShell]
      cd "C:\\Users\\82102\\OneDrive\\문서\\Claude\\Projects\\공모전\\01_현행코드"
      python console_ui.py                        # 데모 모드 (젯슨 불필요)
      python console_ui.py --live 192.168.0.50    # 젯슨 실데이터
      python console_ui.py --live 127.0.0.1       # sim_jetson.py 와 루프백 검증

═══ 이 파일이 존재하는 이유 (2026-08-01) ═══
  원래 console_ui.py 한 파일에 '데이터 수신 · 자세추정 · SOP 검색 · 화면' 이
  전부 들어 있었다(2,801줄). UI 를 v2 로 다시 그리면서 화면만 갈아끼우려 했는데,
  화면과 로직이 같은 파일에 있으면 '화면을 바꿨다' 와 '판정 경로를 건드렸다' 를
  코드로 구분할 수 없다 — 안전 시스템에서 이건 설명 책임의 문제다.
  → 화면에서 독립적인 것만 여기로 내렸다. console_ui.py 는 이 파일을 import 한다.
    두 파일이 같은 객체를 쓰므로 로직이 갈라질 수 없다.

═══ 여기 있는 것 ═══
  RadarLink      젯슨 UDP 수신 스레드 + 제어 명령 송신
  PoseEstimator  N프레임 누적 + PCA 자세 추정 (표시 전용 — 판정에 되먹이지 않는다)
  SopEngine      pgvector 매뉴얼 검색 + Gemma 요약 (전부 백그라운드)
  Track3D        3D 포인트 클라우드 위젯 (OpenGL 없으면 2D 자동 대체)
  PreparePage    빈방 스캔 → 기준 수집 → AE 학습 화면
  SettingsPopup / EvidencePopup / PowerPopup / RestorePopup / QueryPopup / GraphPopup
  lb·btn·panel·titled·confirm·Dialog·md_to_html   공용 위젯 헬퍼
  _DemoSource    젯슨 없이 화면을 보기 위한 가짜 데이터

═══ 여기 없는 것 (console_ui.py 로 갔다) ═══
  메인 창, 사이드 네비, 대시보드, 실시간 감시 화면, 조치 가이드 드로어,
  이벤트 로그·SOP 가이드 페이지, 실행 진입점.
  v1 의 화면 클래스(Console·StartupPage·FacilityMap·Readout·LiveLog·
  CheckRow·ZoneCard·SopPopup·EventLogPopup)는 v2 가 대체했으므로 삭제했다.
  원본은 _구버전보관/코드/console_ui_v1_0801_최종.py 에 있다.

═══ 경보 상태기계 (ISA-18.2) — 상수는 이 파일이 소유한다 ═══
  NORMAL ──경보──> UNACK(점멸·소리) ──확인함──> ACK(점멸·소리 정지, 상황 지속)
         <──상황 종료(사람이 누름)──
  ⚠ 자동 해제는 없다. 작업자가 아직 바닥에 있는데 화면이 초록으로 돌아가면 안 된다.
  ⚠ '확인함'은 소리를 끄는 것이지 경보를 지우는 것이 아니다.

═══ 이 앱이 하지 않는 것 ═══
  판정·차단 실행은 전부 젯슨이 한다. 여기는 표시와 '요청'만 한다.
  링크가 끊겨도 젯슨은 독립적으로 판정·차단을 계속한다(fail-safe).
"""
import os
import time
import math
import json
import re
import socket
import threading
from collections import deque
from functools import lru_cache
from html import escape as html_escape

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from radar_common import (
    DATA_PORT, CTRL_PORT, HELLO_SEC, LINK_TIMEOUT, SCHEMA_VERSION,
    CMD_HELLO, CMD_START, CMD_TRAIN, CMD_RESET,
    CEILING_H, HISTORY_LEN, FRAME_INNER_HALF,
    PH_READY, PH_WARMUP, PH_WAIT_TRAIN, PH_TRAINING, PH_WAIT_ARM, PH_LIVE,
    PHASE_ORDER, PHASE_KO, PHASE_ACTION,
    EVENT_KO, EVENT_CATEGORY, ZONE_IDS, ZONE_KO, RADAR_ZONE, pg_conn_str,
    SEV_KO, GATE_META, REJECT_KO, EVIDENCE_KO, SOP_CATEGORIES,
    CURR_LIMIT, VOLT_MIN, LEAK_LIMIT, VIB_DS_THRESH,
    BG, PANEL, PANEL_HI, PANEL_LO, EDGE, TXT, DIM, FAINT,
    CYAN, GREEN, AMBER, RED, GRID, sev_color,
    SP_XS, SP_S, SP_M, SP_L, SP_XL,
    FS_TITLE, FS_BODY, FS_LABEL, FS_CAPTION,
)

FONT = 'Malgun Gothic'
pg.setConfigOptions(background=PANEL, foreground=DIM, antialias=True)

# ── 노트북 로컬 RAG / LLM ──
#  접속 계정은 하드코딩하지 않는다 — 실제 컨테이너 계정은 admin 이었고
#  'postgres:password' 로는 접속 자체가 안 됐다 (sop_doctor.py 확인).
CONN_STR = pg_conn_str()
OLLAMA_URL = 'http://localhost:11434/api/generate'
LLM_MODEL = 'gemma2:2b'
EMBED_MODEL = 'bge-m3'          # SOP DB 적재 시와 반드시 동일해야 검색이 맞는다
USE_LLM_SUMMARY = True          # 노트북이라 젯슨 OOM 위험 없음 → 기본 ON
WRAP_WIDTH = 64

try:
    from langchain_ollama import OllamaEmbeddings
    from langchain_community.vectorstores import PGVector
    RAG_OK = True
except Exception:
    RAG_OK = False

try:
    import pyqtgraph.opengl as gl
    HAS_GL = True
except Exception:
    HAS_GL = False


# ══════════════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════════════
def lb(text, size=11, color=TXT, bold=False, wrap=False, center=False):
    w = QtWidgets.QLabel(text)
    f = QtGui.QFont(FONT, size)
    f.setBold(bold)
    w.setFont(f)
    w.setStyleSheet(f'color:{color};border:none;background:transparent;')
    w.setWordWrap(wrap)
    if center:
        w.setAlignment(QtCore.Qt.AlignCenter)
    return w


def btn(text, size=11, accent=False, height=36, primary=False):
    """accent=위험(빨강) / primary=주 액션(시안) / 기본=보조(회색)

    색은 의미를 갖는다. 빨강은 위험·경보 전용이다 — 시작·확인 버튼에 쓰지 말 것.
    """
    b = QtWidgets.QPushButton(text)
    b.setFont(QtGui.QFont(FONT, size))
    b.setMinimumHeight(height)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    if primary:
        b.setStyleSheet(
            f'QPushButton{{border:1px solid {CYAN};border-radius:5px;color:#04121c;'
            f'background:{CYAN};padding:4px 10px;font-weight:bold;}}'
            f'QPushButton:hover{{background:#4dd8ff;}}'
            f'QPushButton:disabled{{color:#556677;background:{PANEL};'
            f'border-color:{EDGE};font-weight:normal;}}')
    elif accent:
        b.setStyleSheet(
            f'QPushButton{{border:1px solid {RED};border-radius:5px;color:#fff;'
            f'background:{RED};padding:4px 10px;}}'
            f'QPushButton:hover{{background:#ff5555;}}'
            f'QPushButton:disabled{{color:{DIM};background:{PANEL};border-color:{EDGE};}}')
    else:
        b.setStyleSheet(
            f'QPushButton{{border:1px solid {EDGE};border-radius:5px;color:{TXT};'
            f'background:{PANEL};padding:4px 10px;}}'
            f'QPushButton:hover{{background:#151530;border-color:{CYAN};}}'
            f'QPushButton:disabled{{color:#445566;border-color:{GRID};}}')
    return b


def panel(accent=False, hi=False):
    """영역 구분은 배경 밝기로 한다. 테두리는 accent(경보) 일 때만."""
    f = QtWidgets.QFrame()
    bg = PANEL_HI if hi else PANEL
    if accent:
        f.setStyleSheet(f'QFrame{{border:1px solid {RED};border-radius:8px;'
                        f'background:{bg};}}')
    else:
        f.setStyleSheet(f'QFrame{{border:none;border-radius:8px;background:{bg};}}')
    return f


def titled(title, accent=False, hi=False):
    f = panel(accent, hi)
    v = QtWidgets.QVBoxLayout(f)
    v.setContentsMargins(SP_M, SP_M, SP_M, SP_M)
    v.setSpacing(SP_S)
    t = lb(title.upper(), FS_CAPTION, DIM)
    t.setStyleSheet(f'color:{DIM};border:none;background:transparent;'
                    f'letter-spacing:1px;')
    v.addWidget(t)
    return f, v


TABLE_QSS = (f'QTableWidget{{background:{PANEL};color:{TXT};gridline-color:{GRID};'
             f'border:1px solid {EDGE};border-radius:5px;}}'
             f'QHeaderView::section{{background:#12122a;color:{DIM};'
             f'border:none;padding:5px;}}')
EDIT_QSS = (f'background:{PANEL};color:{TXT};border:1px solid {EDGE};'
            f'border-radius:5px;padding:8px;')


def confirm(parent, title, text, yes='예', no='아니오', danger=False):
    """확인 대화. 버튼 라벨을 한글로 쓰고 색을 직접 지정한다.

    ⚠ [7/31] QMessageBox 의 기본 버튼은 우리 다크 테마에서 '어두운 배경 + 어두운
      글씨' 가 되어 실측 스크린샷에서 Yes/No 가 거의 안 보였다. QMessageBox 에
      QLabel 색만 주고 QPushButton 을 빼먹으면 이렇게 된다.
      → 버튼을 직접 만들고 스타일을 지정한다. 라벨도 영문 Yes/No 를 쓰지 않는다.
    """
    d = QtWidgets.QDialog(parent)
    d.setWindowTitle(title)
    d.setMinimumWidth(460)
    d.setStyleSheet(f'QDialog{{background:{BG};}}')
    v = QtWidgets.QVBoxLayout(d)
    v.setContentsMargins(SP_XL, SP_L, SP_XL, SP_L)
    v.setSpacing(SP_L)
    v.addWidget(lb(title, FS_TITLE, RED if danger else TXT, bold=True))
    v.addWidget(lb(text, FS_BODY, TXT, wrap=True))
    v.addStretch()
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(SP_S)
    bn = btn(no, FS_BODY, height=42)
    by = btn(yes, FS_BODY, height=42, accent=danger, primary=not danger)
    bn.clicked.connect(d.reject)
    by.clicked.connect(d.accept)
    row.addStretch()
    row.addWidget(bn)
    row.addWidget(by, 1)
    v.addLayout(row)
    return d.exec_() == QtWidgets.QDialog.Accepted



def md_to_html(t):
    """LLM 출력의 마크다운을 HTML 로 바꾼다.

    ⚠ [7/31] 실측 스크린샷에서 '**낙상 발생 장소를 확인하고**' 처럼 별표가
      그대로 찍혔다. gemma2 는 지시하지 않아도 마크다운을 쓴다.
      "쓰지 마라"고 프롬프트로 막는 건 신뢰할 수 없으므로 출력에서 변환한다.
    """
    t = html_escape(t)
    t = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', t)
    t = re.sub(r'(?m)^\s*[-•]\s+', '· ', t)      # 불릿 정리
    t = re.sub(r'(?m)^\s*#{1,6}\s*', '', t)      # 헤딩 기호 제거
    return t.replace('\n', '<br>')

class Dialog(QtWidgets.QDialog):
    def __init__(self, parent, title, w=640, h=440):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(w, h)
        self.setStyleSheet(f'QDialog{{background:{BG};}} QLabel{{color:{TXT};}} '
                           f'QCheckBox{{color:{TXT};}}')
        self.v = QtWidgets.QVBoxLayout(self)
        self.v.setContentsMargins(SP_XL, SP_L, SP_XL, SP_L)
        self.v.setSpacing(SP_M)
        self.v.addWidget(lb(title, FS_TITLE, TXT, bold=True))


# ══════════════════════════════════════════════════════════════════════
# 1. 링크 — UDP 수신 QThread  (젯슨 ← HELLO → 노트북)
# ══════════════════════════════════════════════════════════════════════
class RadarLink(QtCore.QThread):
    """젯슨 UDP 패킷 수신 전용 스레드.

    ⚠ 왜 스레드인가:
      이전 설계는 QTimer(100ms) 안에서 poll(0) 을 했다. 레이더도 100ms 주기라
      두 주기가 서서히 밀리면서 어떤 tick 엔 0프레임, 다음 tick 엔 2프레임이
      들어와 화면이 주기적으로 툭툭 끊겼다(앨리어싱).
      → 수신은 블로킹으로 받고 '도착 즉시' 시그널을 쏜다. 앨리어싱 원천 소멸.

    ⚠ 시계:
      젯슨은 RTC 배터리가 없어 부팅마다 시계가 틀어진다. 경과시간을 젯슨 ts 로
      계산하면 배너에 '-3600초 경과'가 뜬다. → 모든 경과시간은 '노트북 수신 시각'
      기준. 젯슨 ts 는 참고 표시용으로만 쓴다.
    """
    packet = QtCore.pyqtSignal(dict)
    linkstate = QtCore.pyqtSignal(bool)

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._run = True
        self.last_rx = 0.0
        self.seq = 0
        self.lost = 0
        self.peak_bytes = 0
        self._alive = None
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # ── 노트북 → 젯슨 ──
    def send_cmd(self, cmd, **kw):
        """제어 명령. 실패해도 조용히 넘어간다 — 젯슨 로컬 제어가 본체다."""
        try:
            self._tx.sendto(json.dumps(dict(kw, cmd=cmd)).encode('utf-8'),
                            (self.host, CTRL_PORT))
            return True
        except OSError:
            return False

    def _hello_loop(self):
        """HELLO 를 계속 보내야 젯슨이 이 노트북 주소로 데이터를 보낸다.
        (IP 하드코딩 불필요 — 젯슨이 HELLO 발신지로 회신)"""
        while self._run:
            self.send_cmd(CMD_HELLO)
            time.sleep(HELLO_SEC)

    def run(self):
        threading.Thread(target=self._hello_loop, daemon=True).start()
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        try:
            sk.bind(('0.0.0.0', DATA_PORT))
        except OSError as e:
            print(f'[LINK] 포트 {DATA_PORT} 바인드 실패: {e}')
            return
        sk.settimeout(0.5)
        print(f'[LINK] {self.host} 구독 시작 (data :{DATA_PORT} / ctrl :{CTRL_PORT})')
        while self._run:
            try:
                data, _ = sk.recvfrom(65535)
            except socket.timeout:
                self._emit_link()
                continue
            except OSError:
                continue
            try:
                pkt = json.loads(data.decode('utf-8'))
            except Exception:
                continue                       # 단편화 손실 등 → 다음 패킷이 복구
            self.peak_bytes = max(self.peak_bytes, len(data))
            s = pkt.get('seq')
            if s is not None:
                if self.seq and s > self.seq + 1:
                    self.lost += s - self.seq - 1
                self.seq = s
            v = pkt.get('schema_version')
            if v is not None and v > SCHEMA_VERSION:
                print(f'[LINK] 경고: 젯슨 schema_version={v} > 노트북 {SCHEMA_VERSION}. '
                      f'모르는 필드는 무시하고 계속 동작합니다.')
            self.last_rx = time.time()
            self._emit_link()
            self.packet.emit(pkt)

    def _emit_link(self):
        alive = bool(self.last_rx) and (time.time() - self.last_rx) < LINK_TIMEOUT
        if alive != self._alive:
            self._alive = alive
            self.linkstate.emit(alive)

    def age(self):
        """마지막 수신 이후 경과 초. 한 번도 못 받았으면 None (아직 '연결 전').

        ⚠ 이전엔 1e9 를 돌려줬는데 그게 화면에 '수신 1000000000초 전' 으로 그대로
          찍혔다. 센티넬 값을 UI 로 새어나가게 두면 안 된다.
        """
        return (time.time() - self.last_rx) if self.last_rx else None

    def stop(self):
        self._run = False


# ══════════════════════════════════════════════════════════════════════
# 2. 누적 · 자세 추정  (표시 전용 — 판정에 절대 되먹이지 않는다)
# ══════════════════════════════════════════════════════════════════════
class PoseEstimator:
    """N프레임 centroid 정합 누적 + PCA 자세 추정.

    IWR6843ISK-ODS 는 x/y 각 4개 가상안테나 → 각분해능 약 28°.
    센서 2.3m 에서 가로 약 1.2m 가 한 덩어리로 뭉개진다(어깨너비 0.45m).
    → 단일 프레임에서 사람 실루엣은 물리적으로 불가능하며 연산 위치와 무관하다.
      해법은 점을 더 뽑는 게 아니라 이미 받은 점을 시간축으로 쌓는 것.

    ⚠ 표시 전용. 판정에 되먹이지 말 것 — 이 클래스가 판정에 영향을 주지
      않는다는 점이 "판정 우선 원칙을 지키며 시각화를 개선했다"의 근거다.
    """

    UP = np.array([0.0, -1.0, 0.0])   # cy = 센서로부터의 거리 → 위쪽은 -y
    HEAD_PCT = 0.15                   # 상위 15% 를 머리 후보로 본다
    HEAD_ALIGN_MIN = 0.35             # 축과 머리증거가 이만큼 정렬돼야 신뢰

    # ── 자세 판단 (⚠ PCA 를 쓰지 않는다) ──────────────────────────────
    #  [8/01 실측으로 폐기] v1~v2 는 PCA 제1주축의 수직도로 서있음/누움을 갈랐다.
    #    그런데 이 센서는 천장 하방이고 각분해능이 28° 다. 2.3 m 아래의 사람은
    #    가로로 1.5 m 번져 보이고, 반사는 머리·어깨 윗면에서만 온다.
    #    → 서 있는 사람의 점군도 '납작한 수평 원반' 이라 주축이 항상 수평이다.
    #    실측(events_still.jsonl): **보행 중인데도 85.3 % 가 'lying' 으로 나왔다**
    #      (normal 76.7 / wave 77.9 / fall 76.5 / still 98.3 / vib 88.5 %)
    #    이 센서로 몸통 방향은 측정되지 않는다. 측정되는 건 '높이' 다.
    #  → 젯슨 classify 와 같은 근거를 쓴다: 높이. (h_drop 게이트가 그 증거다)
    STAND_H = 1.15        # 머리 높이가 이 이상이면 서 있음
    LIE_H = 0.85          # 이 이하면 누움 — 사이 구간은 직전 상태 유지
    STAND_SPAN_MIN = 0.35  # 서 있다고 하려면 점군 높이 폭이 이만큼은 있어야 한다
    LIE_BODY_MIN = 0.05    # 누운 몸의 점군 중심이 이 아래면 바닥반사다
    LIE_BODY_MAX = 0.90    # 이 위면 누운 게 아니다

    # ── 인체 도식을 그려도 되는 최소 조건 ─────────────────────────────
    #  ⚠ [8/01] 실측 재생에서 누적 점 6~8개, 폭 4 cm 짜리 덩어리 위에 1.4 m
    #    사람이 그려지는 경우가 나왔다. 그 점군은 사람의 형상이 아니라 그냥
    #    반사 하나다 — 거기에 사람을 그리면 화면이 없는 것을 지어낸 것이 된다.
    #    (PCA 도 점 6개로는 방향이 노이즈다. 실제로 그런 표본은 서 있는데도
    #     verticality 0.12 로 '누움' 이 나왔다)
    #  실측 분포(events_still.jsonl, 누적 10프레임 기준 p10):
    #    보행 33 · 낙상 23 · 정지 26 · 팔흔듦 19 · 진동 40  vs  정상(원거리) 8
    #  → 18점 미만이거나 주축 길이가 0.35 m 미만이면 형상을 그리지 않는다.
    #    점군과 수치는 그대로 보여 준다. 모르는 것은 안 그리는 게 맞다.
    MIN_SHAPE_PTS = 18
    MIN_SHAPE_LEN = 0.35

    def __init__(self, n_frames=10, vertical_deg=45.0):
        self.n_frames = n_frames
        self.cos_thr = np.cos(np.deg2rad(vertical_deg))
        self.buf = deque(maxlen=n_frames)
        self._head_axis = None        # 마지막으로 '믿을 만하게' 정해진 머리 방향
        self._posture = None          # 자세 히스테리시스

    @staticmethod
    def _xyz(points):
        """젯슨 패킷은 [{'x':..,'y':..,'z':..,'i':..}, ...] 로 온다.
        구형 ZMQ 스키마는 [[x,y,z], ...] 였다 — 둘 다 받는다."""
        if isinstance(points[0], dict):
            return np.asarray([[p.get('x', 0.0), p.get('y', 0.0), p.get('z', 0.0)]
                               for p in points], dtype=np.float32)
        return np.asarray(points, dtype=np.float32).reshape(-1, 3)

    def push(self, points, centroid=None, tracked=True):
        # 젯슨이 사람 트랙을 잃었다고 명시하면 과거 누적점을 즉시 폐기한다.
        # 빈 프레임에서 return만 하던 이전 동작은 빈방에 STICKMAN을 남겼다.
        if not tracked:
            self.clear()
            return
        if not points:
            self.clear()
            return
        p = self._xyz(points)
        c = (np.asarray([centroid['cx'], centroid['cy'], centroid['cz']], dtype=np.float32)
             if isinstance(centroid, dict) else
             (np.asarray(centroid, dtype=np.float32) if centroid is not None
              else p.mean(axis=0)))
        # ★ 정합: 각 점을 자기 프레임 centroid 기준 상대좌표로 저장.
        #   이걸 안 하면 사람이 걸을 때 누적이 궤적으로 길게 늘어져 뭉개진다.
        self.buf.append((c, p - c))

    def clear(self):
        self.buf.clear()
        self._head_axis = None
        self._posture = None

    def _head_point(self, pts):
        """머리 추정점 — 누적 점군에서 '가장 높은 쪽' 상위 15% 의 평균.

        ⚠ 왜 이게 성립하나:
          센서는 천장에 하방(nadir) 설치다. y 는 센서로부터 아래로의 거리이므로
          y 가 작을수록 높다. 그리고 하방 레이더는 물체의 '윗면' 을 주로 본다 —
          서 있는 사람이면 반사의 대부분이 머리·어깨에서 온다.
          → 최고점은 머리로 보는 게 물리적으로 타당하다.

        ⚠ 단일 최고점이 아니라 상위 15% 평균을 쓰는 이유:
          프레임당 8점이라 최고점 하나는 노이즈에 통째로 흔들린다.
          누적 80점의 상위 12점 평균이면 팔을 든 순간에도 크게 안 튄다.
        """
        k = max(3, int(len(pts) * self.HEAD_PCT))
        idx = np.argsort(pts[:, 1])[:k]        # y 오름차순 = 높은 쪽부터
        return pts[idx].mean(axis=0)

    def cloud(self):
        if not self.buf:
            return np.empty((0, 3), dtype=np.float32)
        c_now = self.buf[-1][0]
        return np.vstack([rel + c_now for _, rel in self.buf])

    def estimate(self):
        """자세·머리·형상 추정.  ⚠ 표시 전용 — 판정에 되먹이지 않는다."""
        pts = self.cloud()
        if len(pts) < 6:
            return None
        center = pts.mean(axis=0)
        heights = CEILING_H - pts[:, 1]
        h_span = float(heights.max() - heights.min())
        head = self._head_point(pts)                 # 최고점 상위 15% 평균
        head_h = float(CEILING_H - head[1])

        # ── ① 자세: 높이로 판단한다 (PCA 아님. 위 주석 참조) ──────────
        if head_h >= self.STAND_H:
            posture = 'standing'
        elif head_h <= self.LIE_H:
            posture = 'lying'
        else:
            posture = self._posture or 'standing'    # 중간대는 직전 상태 유지
        self._posture = posture

        # ── ② 몸통 축 ────────────────────────────────────────────────
        #   서 있음: 수직. (측정한 게 아니라 '서 있으면 수직' 이라는 자명한 사실)
        #   누움   : 바닥평면(x,z) 2D PCA. 이 축은 실제로 측정 가능하다 —
        #            누운 몸은 바닥에서 한 방향으로 길게 퍼지기 때문.
        floor = np.column_stack([pts[:, 0], pts[:, 2]])
        fc = floor - floor.mean(axis=0)
        try:
            fw, fv = np.linalg.eigh(np.cov(fc.T) + np.eye(2) * 1e-9)
            fdir = fv[:, int(np.argmax(fw))]
        except np.linalg.LinAlgError:
            fdir = np.array([1.0, 0.0])
        proj = fc @ fdir
        floor_len = float(proj.max() - proj.min())

        if posture == 'standing':
            axis = self.UP.copy()
            head_src = '최고점'
            self._head_axis = None
        else:
            axis = np.array([fdir[0], 0.0, fdir[1]], dtype=float)
            # 부호(머리 쪽)는 측정 불가 → 직전 프레임과의 연속성만 지킨다.
            #   (넘어지기 전 마지막으로 정해진 방향이 그대로 이어진다)
            if self._head_axis is not None and float(np.dot(axis, self._head_axis)) < 0:
                axis = -axis
            self._head_axis = axis.copy()
            head_src = '바닥축 · 방향 유지'

        vert = abs(float(np.dot(axis, self.UP)))

        # ── ③ 크기와 머리 위치 ────────────────────────────────────────
        if posture == 'standing':
            length = float(np.clip(head_h, 1.30, 1.95))
            # 높이는 최고점, 수평은 점군 중심 — 축마다 가장 안정적인 추정
            head_pos = np.array([center[0], head[1], center[2]], dtype=float)
        else:
            length = float(np.clip(floor_len, 1.30, 1.85))
            head_pos = center + axis * (0.5 * length)

        # ── ④ 형상을 그려도 되는가 ────────────────────────────────────
        #  ⚠ [8/01 실측] 낙상 직후 점군은 사람이 아니다. 정지한 사람은 도플러가
        #    없어 레이더가 놓치고, 그 자리를 '일정 거리 링'(다중경로·바닥반사)이
        #    채운다 — 실측에서 마지막 프레임 6점 중 5점이 전부 y≈1.00 (높이 1.30 m)
        #    인데 x·z 만 ±0.8 m 로 흩어져 있었다. 누운 사람이면 높이 0.1~0.4 m 여야
        #    한다. 그걸 사람으로 그리면 낙상 경보 옆에 '서 있는 사람' 이 뜬다.
        #  → 높이 폭이 없는(=거리 껍질) 점군에는 형상을 그리지 않는다.
        #    서 있다고 말하려면 실제로 위아래로 퍼져 있어야 한다.
        if posture == 'standing':
            shape_ok = (len(pts) >= self.MIN_SHAPE_PTS
                        and h_span >= self.STAND_SPAN_MIN)
        else:
            # ⚠ 누운 사람은 몸이 바닥 위 0.1~0.4 m 에 있다. 점군 중심이 바닥
            #   아래이거나 허리 높이보다 높으면 그건 사람이 아니라 바닥반사·
            #   클러터다 (실측: 보행 표본에서 중심 -0.06 m 짜리 점군에 사람이
            #   그려져 도식이 바닥을 뚫었다).
            body_h = float(CEILING_H - center[1])
            shape_ok = (len(pts) >= self.MIN_SHAPE_PTS
                        and floor_len >= self.MIN_SHAPE_LEN
                        and self.LIE_BODY_MIN <= body_h <= self.LIE_BODY_MAX)
        shape_why = ('' if shape_ok else
                     ('점 부족' if len(pts) < self.MIN_SHAPE_PTS else '형상 불명확'))

        return {
            'center': center.tolist(), 'axis': axis.tolist(),
            'length': round(length, 3),
            'floor_len': round(floor_len, 3),
            'h_span': round(h_span, 3),
            'head_h': round(head_h, 3),
            'verticality': round(vert, 3),
            'posture': posture,
            'n_points': int(len(pts)), 'n_frames': len(self.buf),
            'head': head_pos.tolist(),
            'head_top': head.tolist(),
            'head_src': head_src,
            'shape_ok': bool(shape_ok),
            'shape_why': shape_why,
            # 화면에 반드시 이 라벨을 띄울 것 — 추정임을 숨기지 않는다.
            'label': (f'레이더 점군 · 최근 {len(self.buf)}프레임 누적 · 위치·자세 추정'
                      if shape_ok else
                      f'형상 표시 안 함 ({shape_why}) · 최근 {len(self.buf)}프레임 '
                      f'{len(pts)}점 · 높이폭 {h_span:.2f} m'),
        }


# ── 인체 도식 (관절 없음) ────────────────────────────────────────────────
#  ⚠ 이 도형이 주장하는 것과 주장하지 않는 것을 명확히 한다.
#     주장한다   : 위치(center) · 몸통 축 방향(axis) · 축 방향 길이(length)
#                  · 머리가 축의 어느 끝인지
#                  ↑ 전부 누적 점군에서 실제로 계산한 값이다.
#     주장 안 한다: 관절 각도 · 정면 방향 · 팔다리 위치
#                  ↑ 프레임당 8점, 각분해능 28° 로는 측정 자체가 불가능하다.
#                    그래서 팔다리는 '고정 각도'로만 그리고, 절대 움직이지 않는다.
#                    움직이는 스켈레톤은 측정이 아니라 애니메이션이 된다.
#  비율은 인체 표준(머리 1/7.5, 어깨너비 0.26H)에 맞춘 값 — t 는 축 방향으로
#  -0.5(발) ~ +0.5(머리끝), s 는 좌우 방향. 둘 다 length 배율.
STICK = {
    'head_t': 0.435, 'head_r': 0.062,
    'neck': 0.373, 'shoulder': 0.300, 'hip': -0.020,
    'sh_w': 0.130, 'hand_t': 0.020, 'hand_w': 0.210,
    'foot_t': -0.500, 'foot_w': 0.110,
}


def stick_segments(center, axis, length, right, spread=1.0):
    """인체 도식을 선분 쌍 배열 (2N, 3) 로 돌려준다. mode='lines' 용.

    spread — 몸통에 수직인 방향(팔·다리 벌어짐)의 배율.
      측면도에서 누운 사람은 팔다리가 대부분 '화면 안쪽' 으로 뻗는다.
      1.0 로 두면 그 벌어짐이 전부 위아래로 그려져 팔이 공중 0.36 m 로 솟고
      다리가 바닥을 뚫는다(실측: 누운 도식이 0.10~0.82 m 를 차지). → 눌러 준다.
    """
    a = np.asarray(axis, dtype=float)
    a = a / (np.linalg.norm(a) or 1.0)
    r = np.asarray(right, dtype=float)
    r = r - a * float(np.dot(a, r))                 # 축과 직교화
    n = np.linalg.norm(r)
    r = r / n if n > 1e-6 else np.array([1.0, 0.0, 0.0])
    C = np.asarray(center, dtype=float)
    L = float(length)
    K = STICK

    def P(t, s=0.0):
        return C + a * (t * L) + r * (s * L * spread)

    segs = []

    def line(p, q):
        segs.append(p)
        segs.append(q)

    hc, hr = P(K['head_t']), K['head_r'] * L
    ring = [hc + a * (hr * np.sin(x)) + r * (hr * np.cos(x))
            for x in np.linspace(0, 2 * np.pi, 21)]
    for i in range(len(ring) - 1):
        line(ring[i], ring[i + 1])
    line(P(K['neck']), P(K['hip']))                                  # 몸통
    line(P(K['shoulder'], -K['sh_w']), P(K['shoulder'], K['sh_w']))  # 어깨
    for sgn in (-1, 1):                                              # 팔
        line(P(K['shoulder'], sgn * K['sh_w']),
             P(K['hand_t'], sgn * K['hand_w']))
    for sgn in (-1, 1):                                              # 다리
        line(P(K['hip']), P(K['foot_t'], sgn * K['foot_w']))
    return np.asarray(segs, dtype=np.float32)


@lru_cache(maxsize=4)
def _load_mannequin_mesh(filename, upright=True):
    """MakeHuman 인체 OBJ의 body 표면만 읽어 화면 좌표로 정규화한다."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        filename)
    vertices, faces = [], []
    group = None
    with open(path, encoding='utf-8', errors='replace') as src:
        for line in src:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == 'v':
                vertices.append(tuple(map(float, fields[1:4])))
            elif fields[0] in ('g', 'o'):
                group = fields[1] if len(fields) > 1 else None
            elif fields[0] == 'f' and group == 'body':
                ids = [int(value.split('/')[0]) - 1 for value in fields[1:]]
                faces.extend((ids[0], ids[i], ids[i + 1])
                             for i in range(1, len(ids) - 1))
    if not vertices or not faces:
        raise ValueError(f'마네킹 OBJ가 비어 있습니다: {path}')

    # MakeHuman 원본에는 리깅용 joint/helper 면도 함께 있다. 화면에는 실제
    # 일체형 인체인 body 그룹만 남기고 정점 번호를 촘촘하게 다시 매긴다.
    used = np.unique(np.asarray(faces, dtype=np.uint32))
    remap = np.full(len(vertices), -1, dtype=np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)
    source = np.asarray(vertices, dtype=np.float32)[used]
    faces = remap[np.asarray(faces, dtype=np.uint32)]
    mesh = np.column_stack((source[:, 0], source[:, 2], source[:, 1]))
    mesh[:, 2] -= mesh[:, 2].min()
    scale = mesh[:, 2].max() if upright else np.ptp(mesh, axis=0).max()
    mesh /= scale
    mesh[:, 0] -= 0.5 * (mesh[:, 0].min() + mesh[:, 0].max())
    mesh[:, 1] -= np.median(mesh[:, 1])

    return mesh.astype(np.float32), faces.astype(np.uint32)


@lru_cache(maxsize=1)
def _mannequin_mesh():
    """정상 추정 자세용 일체형 CC0 인체 OBJ를 정규화한다."""
    return _load_mannequin_mesh('mannequin_cc0.obj')


@lru_cache(maxsize=3)
def _incident_mannequin_mesh(kind):
    """Blender 표준 골격으로 미리 굽혀 둔 사고별 정적 메시를 읽는다."""
    if kind not in ('fall', 'electric', 'pinching'):
        return _mannequin_mesh()
    return _load_mannequin_mesh(f'mannequin_{kind}.obj', upright=(kind != 'fall'))


def body_mesh(center, axis, length, right):
    """서 있는 추정 형상에 CC0 단일 표면 마네킹을 맞춘다.

    위치·키·머리 기준점만 점군 추정값이다. 마네킹 자세는 항상 같고,
    낙상·추적 소실 때는 호출하지 않는다.
    """
    unit, faces = _mannequin_mesh()
    a = np.asarray(axis, dtype=float)
    a /= np.linalg.norm(a) or 1.0
    r = np.asarray(right, dtype=float)
    r -= a * float(np.dot(a, r))
    r /= np.linalg.norm(r) or 1.0
    d = np.cross(a, r)
    basis = np.column_stack((r, d, a))
    local = unit.copy()
    local[:, 2] -= 0.5
    return (local @ basis.T * float(length)
            + np.asarray(center, dtype=float)), faces


# ══════════════════════════════════════════════════════════════════════
# 3. SOP 엔진 — pgvector 검색 + Gemma 상세 SOP  (전부 노트북 로컬)
# ══════════════════════════════════════════════════════════════════════
# 검색 질의문 — 매뉴얼에 실제로 쓰인 표현에 가깝게 쓴다.
#   이벤트 라벨('FALL DETECTED')을 그대로 넣으면 한글 코퍼스와 안 맞는다.
SOP_QUERY = {
    'fall_detected':       '추락 넘어짐 재해 발생 시 응급처치와 재해자 이송 방법',
    'stationary_anomaly':  '작업자 무응답 감전 협착 사고 발견 시 초동 대응',
    'electric_shock_risk': '감전 사고 발생 시 응급조치 및 전원 차단 잠금 표지',
    'electric_shock_risk_confirmed': '감전 사고 발생 시 응급조치 심폐소생술 119 신고',
    'leakage_current':     '누설전류 전기 설비 이상 시 차단 및 절연 점검 절차',
    'pinching_suspected':  '회전기계 협착 의심 시 비상정지 및 작업자 확인 절차',
    'pinching':            '회전기계 끼임 협착 재해 발생 시 구조 및 정지 절차',
    'vibration_anomaly':   '설비 진동 이상 상태감시 진단 및 점검 조치',
    'overcurrent':         '과전류 전기 설비 이상 시 차단 및 점검 절차',
    'voltage_drop':        '전압 강하 전기 설비 이상 시 점검 절차',
}

# 사고 후 조치 화면에서는 같은 분류의 예방 문서보다 응급조치 원문을 우선한다.
# 파일명은 sop_doctor.py로 확인한 DB metadata의 실제 값이다.
SOP_RESPONSE_SOURCE = {
    'fall_detected': {
        '00_응급처치_공통': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
        '03_낙상_응급처치': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
    },
    'stationary_anomaly': {
        '01_감전_대응': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
        '02_협착_예방': 'B-M-37-2026 회전기계 등의 끼임·절단재해 예방을 위한 기술지원규정.pdf',
        '01_감전_LOTO': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
        '02_협착_끼임': 'B-M-37-2026 회전기계 등의 끼임·절단재해 예방을 위한 기술지원규정.pdf',
    },
    'electric_shock_risk': {
        '01_감전_대응': '감전시응급조치.pdf',
        '00_응급처치_공통': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
        '01_감전_LOTO': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
    },
    'electric_shock_risk_confirmed': {
        '01_감전_대응': '감전시응급조치.pdf',
        '00_응급처치_공통': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
        '01_감전_LOTO': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
    },
    'pinching_suspected': {
        '02_협착_예방': 'B-M-37-2026 회전기계 등의 끼임·절단재해 예방을 위한 기술지원규정.pdf',
        '02_협착_끼임': 'B-M-37-2026 회전기계 등의 끼임·절단재해 예방을 위한 기술지원규정.pdf',
    },
    'pinching': {
        '00_응급처치_공통': '산업재해 형태별 응급처치 (골절화상뇌진탕 등).pdf',
        '02_협착_예방': 'B-M-37-2026 회전기계 등의 끼임·절단재해 예방을 위한 기술지원규정.pdf',
        '02_협착_끼임': 'B-M-37-2026 회전기계 등의 끼임·절단재해 예방을 위한 기술지원규정.pdf',
    },
    'overcurrent': {
        '01_감전_예방': 'Radar-Guard_설비전기이상_대응_SOP.pdf',
    },
    'leakage_current': {
        '01_감전_예방': 'Radar-Guard_설비전기이상_대응_SOP.pdf',
    },
}

SOP_RESPONSE_TERMS = {
    'fall_detected': ('척추', '움직이지', '뇌진탕', '골절', '119', '이송'),
    'stationary_anomaly': ('전원', '호흡', '심정지', '비상정지', '구조', '끼임'),
    'electric_shock_risk': ('전원', '호흡', '심정지', '119', '환자'),
    'electric_shock_risk_confirmed': ('전원', '호흡', '심정지', '119', '환자'),
    'pinching_suspected': ('비상정지', '확인', '전원', '끼임'),
    'pinching': ('비상정지', '정지', '전원', '구조', '끼임'),
    'overcurrent': ('과전류', '차단', '재투입', '발열', '정격'),
    'leakage_current': ('누설전류', '외함', '접촉', '절연', '접지'),
}


def search_sop_documents(vs, ev_type, situation, category):
    """사건 대응 문서를 우선하되, 지정 출처가 없으면 기존 분류 검색을 쓴다."""
    sources = SOP_RESPONSE_SOURCE.get(ev_type, {})
    categories = category if isinstance(category, (list, tuple)) else (category,)
    docs = []
    for cat in categories:
        source = sources.get(cat)
        count = 1 if isinstance(category, (list, tuple)) else 2
        if source:
            # 사고 종류와 공식 대응 문서가 이미 정해졌는데 경보마다 임베딩하면
            # bge-m3 ↔ gemma2 모델 교체 때문에 수십 초가 더 걸린다. 해당 문서의
            # 청크만 읽어 응급조치 용어가 많은 순으로 고르면 더 빠르고 결정적이다.
            import psycopg2
            from types import SimpleNamespace
            with psycopg2.connect(CONN_STR) as cn:
                with cn.cursor() as cur:
                    cur.execute(
                        "SELECT document, cmetadata FROM langchain_pg_embedding "
                        "WHERE cmetadata->>'source_file' = %s",
                        (source,))
                    rows = cur.fetchall()
            terms = SOP_RESPONSE_TERMS.get(ev_type, ())
            rows.sort(key=lambda row: sum(row[0].count(term) for term in terms),
                      reverse=True)
            docs += [SimpleNamespace(page_content=text, metadata=metadata)
                     for text, metadata in rows[:count]]
        else:
            metadata_filter = {'category': cat} if cat else None
            docs += vs.similarity_search(situation, k=count, filter=metadata_filter)
    return docs

# ══ 출처 표기 ═════════════════════════════════════════════════════════
#  [8/25] 화면에 파일명을 그대로 찍고 있었다. 그러면 공식 KOSHA 지침과 우리가 만든
#    프로젝트 SOP 가 나란히 뜨면서 둘 다 똑같은 "매뉴얼" 로 보인다.
#    사업장이 법령·KOSHA 를 근거로 자체 SOP 를 만드는 건 현장에서 정상이지만,
#    화면에서 구분이 안 되면 "이 SOP 출처가 뭐냐" 는 질문에 답이 무너진다.
#    → 문서 등급(공식/자체)과 정식 명칭을 함께 표기하고, 자체 작성은 근거를 붙인다.
#
#  ⚠ basis 에는 실재를 확인한 번호만 적는다. 미확인 번호를 화면에 띄우면
#    출처를 밝히는 목적 자체가 뒤집힌다.
#    (8/25 확인: E-91-2016, E-88-2011 / 미확인: E-86-2011, E-54-2012, E-57-2020)
SRC_OFFICIAL = 'official'      # 공표된 공식 지침·규정
SRC_SELF = 'self'              # 프로젝트에서 작성한 SOP
SRC_UNKNOWN = 'unknown'        # 등록되지 않은 파일 — 출처를 단정하지 않는다

SOURCE_META = (
    # (파일명에 포함된 조각, 등급, 화면에 쓸 정식 명칭, 근거)
    ('산업재해 형태별 응급처치', SRC_OFFICIAL,
     'KOSHA GUIDE H-187-2021 · 산업재해 형태별 응급처치 요령', ''),
    ('감전시응급조치', SRC_OFFICIAL,
     'KOSHA GUIDE E-14-2012 · 감전시 응급조치에 관한 기술지침', ''),
    ('E-105-2011', SRC_OFFICIAL,
     'KOSHA GUIDE E-105-2011 · 전기작업안전에 관한 기술지침', ''),
    ('M-59-2012', SRC_OFFICIAL,
     'KOSHA GUIDE M-59-2012 · 서비스업종에서의 넘어짐 위험성 평가', ''),
    ('M-121-2012', SRC_OFFICIAL,
     'KOSHA GUIDE M-121-2012 · 기계의 상태감시와 진단', ''),
    ('M-123-2012', SRC_OFFICIAL,
     'KOSHA GUIDE M-123-2012 · 기계류의 위험성평가', ''),
    ('M-131-2012', SRC_OFFICIAL,
     'KOSHA GUIDE M-131-2012 · 기계의 결함진단을 위한 자료해석', ''),
    ('M-146-2012', SRC_OFFICIAL,
     'KOSHA GUIDE M-146-2012 · 고령화 설비의 손상평가와 수명예측', ''),
    ('B-M-19-2026', SRC_OFFICIAL,
     'KOSHA 기술지원규정 B-M-19-2026 · 배관 주요사고 대비 비상계획', ''),
    ('B-M-24-2026', SRC_OFFICIAL,
     'KOSHA 기술지원규정 B-M-24-2026 · 안전대의 죔줄', ''),
    ('B-M-25-2026', SRC_OFFICIAL,
     'KOSHA 기술지원규정 B-M-25-2026 · 에너지 차단장치의 잠금·표지', ''),
    ('B-M-37-2026', SRC_OFFICIAL,
     'KOSHA 기술지원규정 B-M-37-2026 · 회전기계 등의 끼임·절단재해 예방', ''),
    ('OSHA3120', SRC_OFFICIAL,
     'OSHA 3120 (2002) · Control of Hazardous Energy (LOTO) · 영문', ''),
    ('Radar-Guard_설비전기이상_대응_SOP', SRC_SELF,
     'Radar-Guard 설비 전기이상 대응 SOP',
     '산업안전보건기준에 관한 규칙 제91·92·304·305·318·319조 · '
     'KOSHA GUIDE E-91-2016, E-88-2011'),
)

SRC_BADGE = {
    SRC_OFFICIAL: '공식 지침',
    SRC_SELF: '프로젝트 자체 작성',
    SRC_UNKNOWN: '출처 미등록',
}


def source_label(source_file):
    """검색 결과의 source_file → (등급, 화면 명칭, 근거).

    등록되지 않은 파일은 공식인 척하지 않는다 — 파일명 그대로 두고 '출처 미등록'.
    """
    name = str(source_file or '?')
    for needle, kind, title, basis in SOURCE_META:
        if needle in name:
            return kind, title, basis
    return SRC_UNKNOWN, name, ''


# ══ 즉시 조치 ═══════════════════════════════════════════════════════
#  화면 [즉시 조치 · 확정 절차] 에 뜨는 문구. LLM 이 만들지 않는다 — 사람이 쓴
#  확정 문구이고, 검색·생성이 실패해도 이것만은 항상 뜬다.
#
#  [8/25 전면 점검] 발견한 문제:
#   1) overcurrent · voltage_drop · fall_suspected 항목이 아예 없어서
#      `INSTANT_ACTION.get(et, INSTANT_ACTION['fall_detected'])` 로 떨어졌다.
#      → 과전류 경보에 "환자를 움직이지 마십시오 — 척추 손상 위험" 이 떴다.
#      설비 전기 이상인데 다친 사람 응급처치가 뜨는 것이다. 셋 다 추가하고
#      폴백도 낙상이 아니라 중립 문구(INSTANT_ACTION_UNKNOWN)로 바꾼다.
#   2) electric_shock_risk 가 "주 배전반 차단" 을 지시했다. 젯슨이 실제로 내리는
#      것은 작업 대상 설비 회로 1개다(BREAKER_SCOPE, 8/20 결정). 주 배전반을
#      내리면 조명·비상등까지 꺼진다 — 사고 현장에서 조명을 끄는 지시가 된다.
#   3) pinching_suspected 가 "차단 상태 유지" 라고 썼는데 이 이벤트는
#      AUTO_TRIP_EVENTS 에 없다. 차단된 적이 없는 상태를 유지하라는 문구였다.
#   4) 낙상에 심폐소생술이 빠져 있었다(감전 확정에는 있음). H-187 4.6 · 2025 한국
#      심폐소생술 가이드라인 기준 의식·호흡이 없으면 CPR 이 최우선이다.
#   5) 누설전류에 사람 보호 지시가 없고 점검 절차만 있었다. 누설은 설비 외함이
#      활선일 수 있다 — 자체 SOP 3절의 "외함을 충전 상태로 간주" 를 1순위로 올린다.
#   6) 감전 확정에 "경미해 보여도 병원 이송" 이 빠져 있었다(H-187 4.5(4)).
#      전기화상은 지연성 부정맥 위험이 있어 자각 증상만으로 판단하면 안 된다.
INSTANT_ACTION = {
    'fall_detected': [
        ('인력', ['환자를 움직이지 마십시오 — 척추 손상 위험',
                  '의식·호흡 확인 후 119 신고',
                  '호흡이 없으면 즉시 심폐소생술',
                  '안전관리자 호출']),
        ('장비', ['환자 주변 장비 정지 및 반경 확보', '이동식 설비는 전원 분리 후 이격']),
    ],
    'fall_suspected': [
        ('확인', ['해당 구역 작업자 상태를 즉시 육안 확인']),
        ('대기', ['확정 전까지 설비를 차단하지 않습니다',
                  '무응답이면 낙상으로 간주하고 119 신고']),
    ],
    'stationary_anomaly': [
        ('상태', ['사고 상황은 해소됐지만 작업자의 움직임이 확인되지 않습니다']),
        ('확인', ['현장 작업자의 의식과 부상 여부를 즉시 확인',
                  '무응답이면 119 신고 후 호흡 확인',
                  '호흡이 없으면 즉시 심폐소생술']),
        ('전원', ['설비 회로 차단을 유지하고 안전 확인 전 재투입 금지']),
    ],
    'vibration_anomaly': [
        ('확인', ['해당 구역 설비 육안 점검', '이상 소음·진동원 특정']),
        ('조치', ['이상 진동이 계속되면 설비 정지 후 정비 요청']),
    ],
    'electric_shock_risk': [
        ('전원', ['차단은 작업 대상 설비 회로에 한정됩니다 — 현장에서 차단 상태 확인',
                  '조명·비상등 회로는 내리지 마십시오']),
        ('인력', ['맨손 접촉 절대 금지',
                  '119 신고 먼저 — 이후 절연 장구·절연봉으로만 접근']),
    ],
    'electric_shock_risk_confirmed': [
        ('전원', ['차단 상태 확인 전 접근·접촉 절대 금지', 'LOTO 상태 유지']),
        ('인력', ['119에 즉시 신고',
                  '의식·호흡 확인 후 필요 시 심폐소생술',
                  '경미해 보여도 전기화상 환자는 반드시 병원 이송']),
    ],
    'leakage_current': [
        ('인력', ['설비 외함·배관·주변 금속부를 충전 상태로 간주 — 맨손 접촉 금지']),
        ('확인', ['누설 분기와 차단 상태를 즉시 확인']),
        ('전원', ['원인 확인 전 재투입 금지', '절연·접지 상태 점검 요청']),
    ],
    'overcurrent': [
        ('전원', ['차단 상태를 유지하고 재투입하지 마십시오']),
        ('점검', ['설비 정격과 실제 부하 비교',
                  '전선·단자·차단기의 변색·탄화·발열 흔적 확인',
                  '유자격자 확인 전 차단기 교체·정격 상향 금지']),
    ],
    'voltage_drop': [
        ('전원', ['차단 상태를 유지하고 원인 확인 전 재투입 금지']),
        ('점검', ['해당 회로 전압과 결선 풀림·접촉 불량 확인',
                  '전원 용량 점검 요청']),
    ],
    'pinching_suspected': [
        ('확인', ['작업자 상태를 즉시 확인하세요']),
        ('설비', ['필요 시 비상정지 — 역방향 강제 구동 금지']),
    ],
    'pinching': [
        ('설비', ['설비 즉시 정지 — 역방향 강제 구동 금지']),
        ('인력', ['무리한 견인 금지', '119 신고 후 구조대 지시 대기',
                  '출혈 시 직접 압박 — 지혈대는 재접합을 방해하므로 임의 사용 금지']),
    ],
}

#  ⚠ 등록되지 않은 이벤트의 폴백. 예전엔 낙상 즉시조치로 떨어졌다 — 모르는 경보에
#    특정 사고의 응급처치를 내미는 것은 설계 오류다. 이벤트가 새로 추가돼도
#    조용히 틀린 조치가 뜨지 않도록 중립 문구로 떨어뜨린다.
INSTANT_ACTION_UNKNOWN = [
    ('확인', ['등록되지 않은 경보 유형입니다',
              '현장 상태를 육안 확인하고 안전관리자에게 보고']),
    ('금지', ['임의 조치·전원 조작을 하지 마십시오']),
]


class SopEngine(QtCore.QObject):
    """경보 시 매뉴얼 검색 + LLM 요약. 전부 백그라운드 — UI 를 절대 막지 않는다.

    ⚠ LLM 은 안전 조치의 필수 경로가 아니다. 실패해도 즉시조치(하드코딩)와
      검색 원문은 이미 화면에 있다. 그래서 실패를 조용히 삼키지 않고 표시만 한다.
    """
    ready = QtCore.pyqtSignal(str, list, str)     # ev_type, sources, ai_text
    status = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._cache = {}

    def request(self, ev_type):
        threading.Thread(target=self._work, args=(ev_type,), daemon=True).start()

    def _work(self, ev_type):
        srcs, ai = [], ''
        if not RAG_OK:
            self.status.emit('매뉴얼 검색 불가 — langchain 미설치 (즉시조치만 표시)')
            self.ready.emit(ev_type, [], '')
            return
        try:
            self.status.emit('안전 매뉴얼 검색 중…')
            # ⚠ [7/31] 이전엔 'FALL DETECTED detected' 같은 영문으로 검색했다.
            #   DB 는 KOSHA 한글 지침이라 질의도 한글이어야 맞는다(bge-m3 가
            #   다국어라 아주 못 찾는 건 아니지만 품질이 크게 떨어진다).
            #   매뉴얼에 실제로 쓰인 표현에 가깝게 쓴다.
            situation = SOP_QUERY.get(ev_type) or f'{EVENT_KO.get(ev_type, ev_type)} 조치'
            cat = EVENT_CATEGORY.get(ev_type)
            emb = OllamaEmbeddings(model=EMBED_MODEL)
            vs = PGVector(connection_string=CONN_STR, embedding_function=emb,
                          collection_name='safety_manual')
            docs = search_sop_documents(vs, ev_type, situation, cat)
            for d in docs:
                srcs.append((d.metadata.get('source_file', '?'),
                             ' '.join(d.page_content.split())[:360]))
            ctx = ' '.join(d.page_content for d in docs)[:1500]
            self.status.emit(f'매뉴얼 {len(docs)}건 검색됨')
        except Exception as e:
            self.status.emit(f'매뉴얼 검색 실패: {e}  (docker start radar-guard-db)')
            ctx = ''
        # ── LLM 요약: 검색 원문을 이미 띄운 '뒤'에 덧붙인다 ──
        if USE_LLM_SUMMARY:
            cached = self._cache.get(ev_type)
            if cached:
                ai = cached
                self.status.emit('AI 요약 (사전 생성분)')
            else:
                try:
                    self.status.emit(f'AI 요약 생성 중… ({LLM_MODEL})')
                    ai = self._gen(ev_type, ctx)
                    self._cache[ev_type] = ai
                    self.status.emit('AI 요약 완료')
                except Exception as e:
                    self.status.emit(f'AI 요약 건너뜀: {e}  (ollama serve 확인)')
        self.ready.emit(ev_type, srcs, ai)

    @staticmethod
    def _gen(ev_type, ctx):
        import urllib.request
        label = EVENT_KO.get(ev_type, ev_type)
        blk = f'\n참고 안전매뉴얼 발췌:\n{ctx[:1200]}\n' if ctx else ''
        prompt = (f'너는 산업 현장 안전관리자다. 방금 "{label}"이(가) 감지됐다.{blk}\n'
                  f'현장 작업자가 지금 즉시 따라야 할 초동 조치를 한국어로 작성하라. '
                  f'번호를 매긴 4~5단계, 각 단계는 짧은 한 문장. 서론·부연 없이 조치만.')
        body = json.dumps({'model': LLM_MODEL, 'prompt': prompt, 'stream': False,
                           'keep_alive': '10m',
                           'options': {'num_ctx': 1024, 'num_predict': 200,
                                       'temperature': 0.2}}).encode('utf-8')
        req = urllib.request.Request(OLLAMA_URL, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode('utf-8')).get('response', '').strip()

    def prewarm(self):
        """LIVE 진입 후 유형별 SOP 를 미리 만들어 둔다 → 경보 순간 지연 0."""
        def _w():
            for et in ('fall_detected', 'fall_suspected',
                       'stationary_anomaly', 'vibration_anomaly'):
                if et in self._cache:
                    continue
                try:
                    self._cache[et] = self._gen(et, '')
                except Exception as e:
                    # ollama 없으면 나머지도 실패 → 중단. 단, 조용히 넘어가지 않는다 —
                    #   CLAUDE.md §9. 실패해도 경보 시 그때 생성해 화면은 정상 동작한다.
                    self.status.emit(f'AI 요약 사전 생성 실패: {e}  (ollama serve 확인) '
                                     f'— 경보 시 그때 생성합니다')
                    return
            self.status.emit('AI 요약 사전 생성 완료 (경보 시 즉시 표시)')
        threading.Thread(target=_w, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════
# 4. 3D 포인트 클라우드 — 화면의 메인
# ══════════════════════════════════════════════════════════════════════
def _facility_scene_geometry():
    """데모 변전실과 건식 변압기를 점군·와이어프레임으로 만든다.

    실측 점군이 아니라 facility.py와 같은 '시설 배치 시각화'다.
    판정·자세 추정에는 절대 사용하지 않는다.
    """
    edge_index = ((0, 1), (0, 2), (1, 3), (2, 3),
                  (4, 5), (4, 6), (5, 7), (6, 7),
                  (0, 4), (1, 5), (2, 6), (3, 7))

    def add_box(box, lines, dots, step=0.10):
        x1, x2, y1, y2, z1, z2 = box
        corners = np.array(((x1, y1, z1), (x2, y1, z1),
                            (x1, y2, z1), (x2, y2, z1),
                            (x1, y1, z2), (x2, y1, z2),
                            (x1, y2, z2), (x2, y2, z2)), dtype=np.float32)
        lines.extend(corners[list(pair)] for pair in edge_index)
        xs = np.linspace(x1, x2, max(2, int((x2 - x1) / step) + 1))
        ys = np.linspace(y1, y2, max(2, int((y2 - y1) / step) + 1))
        zs = np.linspace(z1, z2, max(2, int((z2 - z1) / step) + 1))
        for x in (x1, x2):
            dots.extend((x, y, z) for y in ys for z in zs)
        for y in (y1, y2):
            dots.extend((x, y, z) for x in xs for z in zs)
        for z in (z1, z2):
            dots.extend((x, y, z) for x in xs for y in ys)

    def add_front_details(x1, x2, y, height, lines, dots):
        """배전반 전면을 도어·계기·환기구 점군으로 나눈다."""
        width = x2 - x1
        for z in (0.12, 0.62, height - 0.22):
            lines.append(np.array(((x1 + 0.05, y, z),
                                   (x2 - 0.05, y, z)), dtype=np.float32))
        for x in (x1 + 0.05, x2 - 0.05):
            lines.append(np.array(((x, y, 0.10),
                                   (x, y, height - 0.08)), dtype=np.float32))
        # 계기창과 하부 환기구는 면 전체를 채우지 않고 스캔 반환점처럼 끊는다.
        add_box((x1 + width * 0.25, x1 + width * 0.75,
                 y - 0.012, y, height * 0.63, height * 0.75),
                lines, dots, 0.055)
        for x in np.linspace(x1 + 0.12, x2 - 0.12, 4):
            lines.append(np.array(((x, y - 0.014, 0.25),
                                   (x, y - 0.014, 0.43)), dtype=np.float32))

    def add_trench_segment(x1, x2, y1, y2, lines, dots):
        add_box((x1, x2, y1, y2, 0.008, 0.035), lines, dots, 0.065)
        if x2 - x1 >= y2 - y1:
            for x in np.arange(x1 + 0.06, x2, 0.12):
                lines.append(np.array(((x, y1, 0.038), (x, y2, 0.038))))
        else:
            for y in np.arange(y1 + 0.06, y2, 0.12):
                lines.append(np.array(((x1, y, 0.038), (x2, y, 0.038))))

    fixed_lines, fixed_dots = [], []
    # ROI는 ±0.72m 그대로 두고 시설 시각화만 약 9×7m로 넓힌다.
    # 후면 서비스 벽 외에는 낮은 경계만 두어 점검·피난 통로를 열어 둔다.
    add_box((-4.45, 4.45, 3.30, 3.40, 0.0, 2.25),
            fixed_lines, fixed_dots, 0.14)
    for x1, x2 in ((-4.45, -0.72), (0.72, 4.45)):
        add_box((x1, x2, -3.40, -3.30, 0.0, 0.16),
                fixed_lines, fixed_dots, 0.12)
    for x1, x2 in ((-4.45, -4.35), (4.35, 4.45)):
        add_box((x1, x2, -3.30, 3.30, 0.0, 0.18),
                fixed_lines, fixed_dots, 0.12)

    # 북측 개방면을 변압기에 내주고 고압·저압 배전반 5면은 동측 벽으로 옮긴다.
    for y1 in (-2.55, -1.69, -0.83, 0.03, 0.89):
        add_box((3.35, 4.15, y1, y1 + 0.80, 0.0, 1.95),
                fixed_lines, fixed_dots, 0.085)
        for z in (0.12, 0.62, 1.73):
            fixed_lines.append(np.array(((3.345, y1 + 0.05, z),
                                         (3.345, y1 + 0.75, z))))
        for y in (y1 + 0.05, y1 + 0.75):
            fixed_lines.append(np.array(((3.345, y, 0.10),
                                         (3.345, y, 1.87))))
    # 서측 보호·제어반 2면. ROI와 출입 동선 사이를 비운다.
    for y1, y2, height in ((0.55, 1.55, 1.60), (-1.55, -0.55, 1.45)):
        add_box((-4.05, -3.35, y1, y2, 0.0, height),
                fixed_lines, fixed_dots, 0.08)
        for y in np.linspace(y1 + 0.16, y2 - 0.16, 3):
            fixed_lines.append(np.array(((-3.345, y, 0.12),
                                         (-3.345, y, height - 0.10))))

    # 동측 배전반 전면 트렌치와 변압기 단자함으로 향하는 한 갈래.
    add_trench_segment(3.02, 3.16, -2.70, 2.18, fixed_lines, fixed_dots)
    add_trench_segment(-1.10, 3.16, 2.04, 2.18, fixed_lines, fixed_dots)
    add_trench_segment(-1.10, -0.96, 0.72, 2.04, fixed_lines, fixed_dots)

    # 배전반 상부 케이블 래더·버스덕트: 두 레일과 촘촘한 가로대.
    for y in (3.02, 3.20):
        fixed_lines.append(np.array(((-3.65, y, 2.18),
                                     (3.55, y, 2.18))))
    for x in np.arange(-3.65, 3.56, 0.22):
        fixed_lines.append(np.array(((x, 3.02, 2.18), (x, 3.20, 2.18))))
    for x in (3.72, 3.90):
        fixed_lines.append(np.array(((x, -2.75, 2.18), (x, 3.20, 2.18))))
    for y in np.arange(-2.75, 3.21, 0.22):
        fixed_lines.append(np.array(((3.72, y, 2.18), (3.90, y, 2.18))))

    # 후면 양끝 급·배기 루버와 남측 배수 그레이팅.
    for x1, x2 in ((-3.85, -2.95), (2.45, 3.35)):
        add_box((x1, x2, 3.18, 3.29, 0.35, 1.45),
                fixed_lines, fixed_dots, 0.07)
        for z in np.arange(0.45, 1.40, 0.11):
            fixed_lines.append(np.array(((x1 + 0.08, 3.175, z),
                                         (x2 - 0.08, 3.175, z))))
    add_box((1.30, 2.10, -3.12, -2.72, 0.006, 0.028),
            fixed_lines, fixed_dots, 0.055)
    for x in np.arange(1.36, 2.08, 0.10):
        fixed_lines.append(np.array(((x, -3.10, 0.032),
                                     (x, -2.74, 0.032))))

    machine_lines, machine_dots = [], []
    # 대형 2.6×1.6m 건식 변압기. 북측 개방면에서 ROI 상단과 일부만 겹친다.
    add_box((-1.30, 1.30, 0.42, 2.02, 0.05, 0.16),
            machine_lines, machine_dots, 0.07)
    theta = np.linspace(0.0, 2.0 * np.pi, 48, endpoint=False)
    # 3상 몰드 코일: 수직 반복 링과 모선이 회전축이 아닌 변압기임을 드러낸다.
    coil_centers = (-0.70, 0.0, 0.70)
    for cx in coil_centers:
        for z in np.linspace(0.48, 1.62, 12):
            ring = np.column_stack((cx + 0.27 * np.cos(theta),
                                    1.28 + 0.27 * np.sin(theta),
                                    np.full_like(theta, z)))
            machine_lines.extend(np.array((ring[i], ring[(i + 1) % len(ring)]))
                                 for i in range(len(ring)))
            machine_dots.extend(ring[::2])
        for angle in theta[::8]:
            machine_lines.append(np.array(((cx + 0.27 * np.cos(angle),
                                             1.28 + 0.27 * np.sin(angle), 0.48),
                                            (cx + 0.27 * np.cos(angle),
                                             1.28 + 0.27 * np.sin(angle), 1.62))))
    # 상·하부 철심 프레임, 절연 지지대와 상부 단자.
    for z1, z2 in ((0.27, 0.42), (1.66, 1.82)):
        add_box((-1.04, 1.04, 0.94, 1.62, z1, z2),
                machine_lines, machine_dots, 0.065)
    for cx in coil_centers:
        add_box((cx - 0.08, cx + 0.08, 1.16, 1.40, 0.16, 0.47),
                machine_lines, machine_dots, 0.05)
        add_box((cx - 0.07, cx + 0.07, 1.19, 1.37, 1.82, 2.02),
                machine_lines, machine_dots, 0.045)
    machine_lines.extend(np.array(((coil_centers[i], 1.28, 1.96),
                                   (coil_centers[i + 1], 1.28, 1.96)))
                         for i in range(2))
    # 우측 전면 강제냉각 팬. 실제 선풍기 위치가 이 설비 부분과 겹치도록 보인다.
    for cx in (0.20, 0.65, 1.10):
        center = np.array((cx, 0.49, 0.42))
        for radius in (0.10, 0.23):
            ring = np.column_stack((cx + radius * np.cos(theta),
                                    np.full_like(theta, 0.49),
                                    0.42 + radius * np.sin(theta)))
            machine_lines.extend(np.array((ring[i], ring[(i + 1) % len(ring)]))
                                 for i in range(len(ring)))
            machine_dots.extend(ring[::2])
        for angle in theta[::8]:
            rim = np.array((cx + 0.22 * np.cos(angle), 0.49,
                            0.42 + 0.22 * np.sin(angle)))
            machine_lines.append(np.array((center, rim)))
    # 좌측 단자함과 우측 팬을 분리해 정지형 이상의 위치 문맥을 읽게 한다.
    add_box((-1.28, -0.45, 0.72, 1.84, 0.22, 1.42),
            machine_lines, machine_dots, 0.06)
    add_front_details(-1.28, -0.45, 0.715, 1.42,
                      machine_lines, machine_dots)

    # 접지 동바는 등급색이 아닌 시설 식별용 저채도 황동색으로 별도 렌더링한다.
    ground_lines = [np.array(((-4.18, -3.08, 0.20),
                              (-4.18, 3.12, 0.20))),
                    np.array(((-4.18, 3.12, 0.20),
                              (3.95, 3.12, 0.20)))]
    for target in ((-3.35, 1.05, 0.12), (-3.35, -1.05, 0.12),
                   (3.35, -1.25, 0.12), (-1.30, 1.18, 0.12)):
        ground_lines.append(np.array(((-4.18, target[1], 0.20), target)))
    return (np.vstack(fixed_lines), np.asarray(fixed_dots, dtype=np.float32),
            np.vstack(machine_lines), np.asarray(machine_dots, dtype=np.float32),
            np.vstack(ground_lines))


class Track3D(QtWidgets.QWidget):
    """점 8개를 10프레임(1초) 누적해 약 80점으로 만들고 자세 캡슐을 씌운다."""

    def __init__(self):
        super().__init__()
        self.pose = PoseEstimator(n_frames=10)
        self.trail = deque(maxlen=60)
        self.stale = False
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.gl = None
        if HAS_GL:
            try:
                self._build_gl(v)
            except Exception as e:
                print(f'[경고] OpenGL 초기화 실패 → 2D 대체: {e}')
                self.gl = None
        if self.gl is None:
            self._build_2d(v)
        # 데이터 없음 오버레이 — 화면이 조용히 거짓말하지 않게
        self.veil = QtWidgets.QLabel(self)
        self.veil.setAlignment(QtCore.Qt.AlignCenter)
        self.veil.setFont(QtGui.QFont(FONT, 15, QtGui.QFont.Bold))
        self.veil.setStyleSheet(
            'color:#ffaa00;background:rgba(8,8,26,215);border:1px solid #ffaa00;'
            'border-radius:6px;')
        self.veil.hide()

    def resizeEvent(self, e):
        self.veil.setGeometry(self.rect())
        super().resizeEvent(e)

    def set_stale(self, stale, msg=''):
        """수신이 끊기면 3D 를 덮는다. 마지막 점군을 현재 상황처럼 보여주지 않는다."""
        self.stale = stale
        if stale:
            self.veil.setText(msg)
            self.veil.setGeometry(self.rect())
            self.veil.show()
            self.veil.raise_()
        else:
            self.veil.hide()

    def _build_gl(self, v):
        self.gl = gl.GLViewWidget()
        self.gl.setBackgroundColor(pg.mkColor(PANEL))
        self.gl.setCameraPosition(distance=19.0, elevation=30, azimuth=48)
        self._cam0 = dict(distance=19.0, elevation=30, azimuth=48)
        g = gl.GLGridItem()
        g.setSize(10, 8)
        g.setSpacing(0.5, 0.5)
        g.setColor(pg.mkColor(GRID))
        self.gl.addItem(g)
        # 판정 ROI를 그대로 표시만 한다. 평상시는 점군보다 낮은 채도의 파란색,
        # 사고 중에는 젯슨이 보낸 경보 등급 색으로 바꿔 위치와 심각도를 함께 보인다.
        h = FRAME_INNER_HALF
        roi_pos = np.array([[-h, -h, 0.02], [h, -h, 0.02],
                            [h, h, 0.02], [-h, h, 0.02],
                            [-h, -h, 0.02]], dtype=np.float32)
        self.roi = gl.GLLinePlotItem(
            pos=roi_pos, color=self.roi_color('normal'), width=1.8,
            antialias=True)
        self.gl.addItem(self.roi)
        self._roi_sev = 'normal'
        # 1 m 높이 눈금 — 캡슐 크기를 눈으로 가늠할 기준
        for h in (1.0, 2.0):
            ring = np.column_stack([1.0 * np.cos(np.linspace(0, 2 * np.pi, 48)),
                                    1.0 * np.sin(np.linspace(0, 2 * np.pi, 48)),
                                    np.full(48, h)])
            self.gl.addItem(gl.GLLinePlotItem(pos=ring, color=(0.13, 0.2, 0.3, 0.55),
                                              width=1.0, antialias=True))
        # ⚠ 실측이 아닌 데모 설비 배치. 남색으로 낮춰 원시 점군(청록)과
        #   경보 색(빨강·주황)을 가리지 않고, 카메라 복원처럼 보이지 않게 한다.
        (env_lines, env_dots, machine_lines, machine_dots,
         ground_lines) = _facility_scene_geometry()
        self.env_lines = gl.GLLinePlotItem(
            pos=env_lines, color=(0.10, 0.24, 0.38, 0.62), width=1.0,
            antialias=True, mode='lines')
        self.env_dots = gl.GLScatterPlotItem(
            pos=env_dots, color=(0.12, 0.30, 0.46, 0.46), size=2.0)
        self.machine_lines = gl.GLLinePlotItem(
            pos=machine_lines, color=(0.34, 0.28, 0.66, 0.76), width=1.1,
            antialias=True, mode='lines')
        self.machine_dots = gl.GLScatterPlotItem(
            pos=machine_dots, color=(0.38, 0.32, 0.72, 0.62), size=2.4)
        self.ground_lines = gl.GLLinePlotItem(
            pos=ground_lines, color=(0.55, 0.38, 0.10, 0.72), width=1.2,
            antialias=True, mode='lines')
        self.gl.addItem(self.env_lines)
        self.gl.addItem(self.env_dots)
        self.gl.addItem(self.machine_lines)
        self.gl.addItem(self.machine_dots)
        self.gl.addItem(self.ground_lines)
        # 점군보다 먼저 그려 점이 반투명 형상 뒤에 묻히지 않게 한다.
        body_unit, body_faces = _mannequin_mesh()
        body_unit = body_unit.copy()
        body_unit[:, 2] -= 0.5
        self.body = gl.GLMeshItem(vertexes=body_unit, faces=body_faces,
                                  color=(0.78, 0.88, 0.92, 0.34), smooth=True,
                                  shader='shaded', glOptions='translucent')
        self.body.hide()
        self.gl.addItem(self.body)
        # 채워진 유리 표면 위에 약한 림 셰이더를 겹쳐 홀로그램 외곽만 살린다.
        self.body_rim = gl.GLMeshItem(
            vertexes=body_unit, faces=body_faces,
            color=(0.0, 0.85, 1.0, 0.13), smooth=True,
            shader='balloon', glOptions='translucent')
        self.body_rim.hide()
        self.gl.addItem(self.body_rim)
        self._body_sev = None
        self._incident_kind = None
        self._body_unit = body_unit
        self._body_faces = body_faces
        self.sc = gl.GLScatterPlotItem(size=5.0, color=(*pg.glColor(CYAN)[:3], 0.38))
        self.gl.addItem(self.sc)
        # 인체 도식 — mode='lines' 로 끊긴 선분들을 한 아이템에 그린다
        self.cap = gl.GLLinePlotItem(color=pg.glColor(GREEN), width=2.2,
                                     antialias=True, mode='lines')
        self.gl.addItem(self.cap)
        # 머리 추정점 — 도식이 아니라 '실측에서 나온 점' 이므로 따로 강조한다
        self.hd = gl.GLScatterPlotItem(size=11.0, color=pg.glColor(AMBER))
        self.gl.addItem(self.hd)
        self.anchor = gl.GLScatterPlotItem(size=15.0, color=pg.glColor(AMBER))
        self.gl.addItem(self.anchor)
        self.tr = gl.GLLinePlotItem(color=(0.53, 0.6, 0.73, 0.6), width=1.2,
                                    antialias=True)
        self.gl.addItem(self.tr)
        self._equipment_alarm = False
        v.addWidget(self.gl, 1)

    def _build_2d(self, v):
        self.plot = pg.PlotWidget()
        self.plot.setAspectLocked(True)
        self.plot.setXRange(-1.6, 1.6)
        self.plot.setYRange(-0.15, 2.4)
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.getPlotItem().hideButtons()
        self.plot.setLabel('bottom', 'X (m)', color=DIM)
        self.plot.setLabel('left', 'Height (m)', color=DIM)
        self.sc = pg.ScatterPlotItem(size=6, brush=pg.mkBrush(0, 204, 255, 160), pen=None)
        self.cap = pg.PlotCurveItem(pen=pg.mkPen(GREEN, width=2.2), connect='pairs')
        self.hd = pg.ScatterPlotItem(size=11, brush=pg.mkBrush(AMBER), pen=None)
        self.anchor = pg.ScatterPlotItem(size=15, symbol='x',
                                         pen=pg.mkPen(AMBER, width=2), brush=None)
        floor = pg.PlotCurveItem(pen=pg.mkPen(EDGE, width=1.5))
        floor.setData([-1.6, 1.6], [0, 0])
        for it in (floor, self.sc, self.cap, self.hd, self.anchor):
            self.plot.addItem(it)
        v.addWidget(self.plot, 1)

    def reset_camera(self):
        if self.gl is not None:
            self.gl.setCameraPosition(**self._cam0)

    def set_equipment_alarm(self, active):
        """과전류·누설전류 경보를 변압기 형상에도 표시한다."""
        active = bool(active)
        if self.gl is None or active == self._equipment_alarm:
            return
        self._equipment_alarm = active
        if active:
            color = pg.glColor(sev_color('critical'))
            self.machine_lines.setData(color=(*color[:3], 0.92))
            self.machine_dots.setData(color=(*color[:3], 0.78))
        else:
            self.machine_lines.setData(color=(0.34, 0.28, 0.66, 0.76))
            self.machine_dots.setData(color=(0.38, 0.32, 0.72, 0.62))

    # ── 경보 등급별 색 ────────────────────────────────────────────────
    #  ⚠ v1 은 bool(alert) 하나로 '빨강이냐 아니냐' 만 정했다. 그래서 정지형
    #    이상(주의)도 낙상(위험)과 똑같이 빨갛게 나왔다 — 색만 봐서는 무슨
    #    일이 난 건지 구분할 수 없었다. 등급을 그대로 받아 색을 나눈다.
    #      정상 초록 · 주의 주황 · 위험 빨강
    @staticmethod
    def sev_colors(sev):
        """(점 색, 도식 색, 머리점 색) 을 돌려준다."""
        if sev in ('warning', 'critical'):
            c = sev_color(sev)
            return c, c, c
        return CYAN, GREEN, AMBER

    @staticmethod
    def roi_color(sev):
        """표시 전용 ROI 색. 정상 경계는 상태색 초록 대신 절제된 파란색이다."""
        if sev in ('warning', 'critical'):
            color = pg.glColor(sev_color(sev))
            return (*color[:3], 0.96)
        return (0.08, 0.38, 0.62, 0.58)

    def push(self, st, sev='normal', hide_shape=False, incident=None):
        incident = incident if incident is not None else getattr(self, '_incident', None)
        track_state = st.get('track_state', 'tracking')
        self.pose.push(st.get('points') or [], st.get('centroid'),
                       tracked=(track_state == 'tracking'))
        c = st.get('centroid') or {}
        if track_state == 'tracking' and c:
            self.trail.append([c.get('cx', 0), c.get('cz', 0),
                               CEILING_H - c.get('cy', CEILING_H)])
        elif track_state != 'tracking':
            self.trail.clear()
        anchor = st.get('track_anchor') or {}
        if track_state == 'lost_in_zone' and anchor:
            x = float(anchor.get('cx', 0))
            h = CEILING_H - float(anchor.get('cy', CEILING_H))
            z = float(anchor.get('cz', 0))
            if self.gl is not None:
                self.anchor.setData(pos=np.array([[x, z, h]], dtype=np.float32))
            else:
                self.anchor.setData([x], [h])
        elif self.gl is not None:
            self.anchor.setData(pos=np.zeros((0, 3), dtype=np.float32))
        else:
            self.anchor.setData([], [])
        return self.redraw(sev, hide_shape, incident)

    def redraw(self, sev='normal', hide_shape=False, incident=None):
        """새 프레임을 먹지 않고 이미 누적된 값으로만 다시 그린다.

        ⚠ [8/02] push() 를 다시 부르면 pose.push() 가 같은 프레임을 누적
          버퍼에 중복 적재한다. 모드 전환처럼 새 패킷 없이 화면만 맞춰야
          할 때(ConsoleV2._refresh_scene) 는 이 쪽을 쓴다.
        """
        incident = incident if incident is not None else getattr(self, '_incident', None)
        pts = self.pose.cloud()
        p = self.pose.estimate()
        pt_c, fig_c, hd_c = self.sev_colors(sev)
        if self.gl is not None:
            roi_sev = sev if sev in ('warning', 'critical') else 'normal'
            if roi_sev != self._roi_sev:
                self.roi.setData(color=self.roi_color(roi_sev))
                self._roi_sev = roi_sev
            incident_kind = incident and incident.get('kind')
            if incident_kind != self._incident_kind:
                if incident_kind:
                    # 전체 시설 시점에서는 사고 인체가 설비 점군에 묻힌다.
                    # 시연 사고 동안만 ROI 남측에서 가까이 보고 해제 시 복귀한다.
                    self.gl.setCameraPosition(distance=10.5, elevation=26, azimuth=-48)
                    posed, faces = _incident_mannequin_mesh(incident_kind)
                    self.body.setMeshData(vertexes=posed, faces=faces)
                    self.body_rim.setMeshData(vertexes=posed, faces=faces)
                elif self._incident_kind:
                    self.gl.setCameraPosition(**self._cam0)
                    self.body.setMeshData(vertexes=self._body_unit,
                                          faces=self._body_faces)
                    self.body_rim.setMeshData(vertexes=self._body_unit,
                                              faces=self._body_faces)
                self._incident_kind = incident_kind
            if len(pts):
                arr = np.column_stack([pts[:, 0], pts[:, 2], CEILING_H - pts[:, 1]])
                color = pg.glColor(pt_c)
                self.sc.setData(pos=arr, color=(*color[:3], 0.50), size=5.2)
            if len(self.trail) > 2:
                self.tr.setData(pos=np.array(self.trail))
            if incident:
                # 사고 포즈는 센서 자세 복원이 아니라 사건 종류를 빠르게 읽게 하는
                # 시각화다. 운영 패킷에는 없고 명시적 시연 패킷에서만 활성화한다.
                self.cap.setData(pos=np.zeros((0, 3), dtype=np.float32))
                self.hd.setData(pos=np.zeros((0, 3), dtype=np.float32))
                transform = self._incident_transform(incident)
                self.body.setTransform(transform)
                self.body_rim.setTransform(transform)
                color = pg.glColor(fig_c)
                self.body.setColor((*color[:3], 0.60))
                self.body_rim.setColor((*color[:3], 0.26))
                self._body_sev = sev
                self.body.show()
                self.body_rim.show()
            elif p and p['shape_ok'] and not hide_shape:
                self.hd.setData(pos=np.array([self.to_disp(p['head'])]),
                                color=pg.glColor(hd_c))
                self.cap.setData(pos=np.zeros((0, 3), dtype=np.float32),
                                 color=pg.glColor(fig_c))
                transform = self._body_transform(p)
                self.body.setTransform(transform)
                self.body_rim.setTransform(transform)
                if self._body_sev != sev:
                    color = (pg.glColor(fig_c)
                             if sev in ('warning', 'critical')
                             else (0.78, 0.88, 0.92, 1.0))
                    self.body.setColor((*color[:3], 0.52))
                    rim = color[:3] if sev in ('warning', 'critical') \
                        else (0.0, 0.85, 1.0)
                    self.body_rim.setColor((*rim, 0.22))
                    self._body_sev = sev
                self.body.show()
                self.body_rim.show()
            else:
                self.cap.setData(pos=np.zeros((0, 3), dtype=np.float32))
                self.hd.setData(pos=np.zeros((0, 3), dtype=np.float32))
                self.body.hide()
                self.body_rim.hide()
        else:
            if len(pts):
                self.sc.setData(pts[:, 0], CEILING_H - pts[:, 1])
                b = QtGui.QColor(pt_c)
                b.setAlpha(165)
                self.sc.setBrush(pg.mkBrush(b))
            if p and p['shape_ok'] and not hide_shape:
                seg = self.stick2d(p)
                self.cap.setData(seg[:, 0], seg[:, 1])
                self.cap.setPen(pg.mkPen(fig_c, width=2.2))
                hx, hy = p['head'][0], CEILING_H - p['head'][1]
                self.hd.setData([hx], [hy])
                self.hd.setBrush(pg.mkBrush(hd_c))
            else:
                self.cap.setData([], [])
                self.hd.setData([], [])
        return p

    @staticmethod
    def _incident_transform(incident):
        """사고 OBJ의 발/바닥과 설비 접근 방향을 맞춘다."""
        kind = incident.get('kind', 'stationary')
        x, y = incident.get('pos', (0.0, 0.0))
        phase = np.sin(time.monotonic() * 5.0)
        length = 1.62
        posed, _ = _incident_mannequin_mesh(kind)
        floor_z = -float(posed[:, 2].min()) * length
        # 낙상은 카메라에서 서 있는 투영으로 보이지 않게 바닥 면에서
        # 가로로 누이고, 감전·협착은 팔이 설비(+Y)를 향하게 돌린다.
        angle = np.radians(90.0 if kind in ('electric', 'pinching') else 0.0)
        rotation = np.array(((np.cos(angle), -np.sin(angle), 0.0),
                             (np.sin(angle), np.cos(angle), 0.0),
                             (0.0, 0.0, 1.0))) * length
        if kind == 'fall':
            center = np.array((x, y, floor_z))
        elif kind == 'electric':
            center = np.array((x, y - 0.015 * phase, floor_z))
        elif kind == 'pinching':
            center = np.array((x + 0.015 * phase, y, floor_z))
        else:
            center = np.array((x, y, floor_z))
        matrix = np.eye(4, dtype=float)
        matrix[:3, :3] = rotation
        matrix[:3, 3] = center
        return pg.Transform3D(matrix)

    # ── 좌표 변환 ─────────────────────────────────────────────────────
    #  레이더 원좌표 (x, y=센서로부터의 거리, z) → 화면 (x, z, 바닥기준 높이)
    #  y 축이 뒤집히므로 벡터는 부호까지 같이 바꿔야 한다.
    @staticmethod
    def to_disp(p):
        return np.array([p[0], p[2], CEILING_H - p[1]], dtype=float)

    @staticmethod
    def vec_disp(v):
        return np.array([v[0], v[2], -v[1]], dtype=float)

    @staticmethod
    def body_length(p):
        """도식 길이.

        ⚠ 서 있을 때는 PCA 길이가 아니라 '머리 높이' 를 쓴다.
          하방 레이더는 머리·어깨에서 반사가 몰려 점군이 상반신에 치우친다.
          그래서 PCA 로 잰 퍼짐(length)은 실제 키보다 짧게 나온다.
          반면 머리 높이는 바닥부터의 실측값이고, 서 있는 사람에게 그건 곧 키다.
        """
        if p['posture'] == 'standing':
            return float(np.clip(CEILING_H - p['head'][1], 1.30, 1.95))
        return float(np.clip(p['length'], 1.30, 1.85))

    @staticmethod
    def figure_center(head_disp, axis_disp, length):
        """도식의 기준점.

        ⚠ 점군 중심(centroid)에 도식을 걸면 안 된다. 위 이유로 centroid 가
          몸 중심보다 위에 있어서, 대칭으로 그리면 발이 바닥에서 0.5 m 뜬다
          (실측 스크린샷에서 확인). 우리가 '직접 측정한' 것은 머리이므로
          머리 끝(t=+0.5)을 실측 머리점에 맞추고 거기서 아래로 편다.
        """
        return np.asarray(head_disp, dtype=float) - np.asarray(axis_disp) * (0.5 * length)

    def _stick3d(self, p):
        a = self.vec_disp(p['axis'])
        a = a / (np.linalg.norm(a) or 1.0)
        L = self.body_length(p)
        C = self.figure_center(self.to_disp(p['head']), a, L)
        up = np.array([0.0, 0.0, 1.0])
        if p['posture'] == 'standing':
            # 서 있음 — 축 중심 회전(정면 방향)은 측정할 수 없다.
            #   임의 방향으로 두면 카메라를 돌릴 때마다 옆모습·정면이 바뀌어
            #   '측정된 방향' 처럼 오해된다. → 항상 카메라를 향하게 고정한다.
            az = np.deg2rad(self.gl.opts.get('azimuth', 48.0)) if self.gl else 0.0
            right = np.array([-np.sin(az), np.cos(az), 0.0])
        else:
            # 누움 — 몸통 축이 수평이므로 팔다리는 바닥면에 편다(위에서 잘 읽힌다)
            right = np.cross(a, up)
        return stick_segments(C, a, L, right)

    def _body_transform(self, p):
        """고정 GPU 메쉬를 추정 위치·키에 맞추는 변환 행렬."""
        a = self.vec_disp(p['axis'])
        a /= np.linalg.norm(a) or 1.0
        L = self.body_length(p)
        C = self.figure_center(self.to_disp(p['head']), a, L)
        az = np.deg2rad(self.gl.opts.get('azimuth', 48.0))
        right = np.array([-np.sin(az), np.cos(az), 0.0])
        right -= a * float(np.dot(a, right))
        right /= np.linalg.norm(right) or 1.0
        depth = np.cross(a, right)
        matrix = np.eye(4, dtype=float)
        linear = np.column_stack((right, depth, a)) * L
        matrix[:3, :3] = linear
        # 중심을 임의로 내리지 않고, 실제 변환된 메시의 최저 정점만 z=0에
        # 맞춘다. 따라서 발은 뜨지도 않고 지면 아래로 들어가지도 않는다.
        floor_z = float((self._body_unit @ linear.T)[:, 2].min())
        matrix[:3, 3] = (C[0], C[1], -floor_z)
        return pg.Transform3D(matrix)

    @staticmethod
    def stick2d(p):
        """측면도(x-높이 평면)용. 3D 축을 평면에 투영해 쓴다.

        ⚠ 3D 축을 그대로 투영하면 안 된다. 측면도는 깊이축(z)을 접으므로,
          몸이 z 방향으로 누워 있으면 투영된 축의 x 성분이 0 에 가까워지고
          정규화 과정에서 높이 성분이 지배해 '비스듬히 선 사람' 이 된다
          (실측: 누운 사람의 도식이 0.11~0.86 m 에 걸쳐 세워짐).
          → 측면도에서는 자세(standing/lying)로 축을 직접 잡는다. 좌우 방향만
            3D 축의 x 부호를 따라가 점군이 퍼진 쪽과 맞춘다.
        """
        L = Track3D.body_length(p)
        head = np.array([p['head'][0], CEILING_H - p['head'][1]])
        if p['posture'] == 'standing':
            a = np.array([0.0, 1.0])
        else:
            a = np.array([1.0 if p['axis'][0] >= 0 else -1.0, 0.0])
        C = Track3D.figure_center(head, a, L)
        right = np.array([a[1], -a[0]])
        seg = stick_segments(np.append(C, 0.0), np.append(a, 0.0), L,
                             np.append(right, 0.0),
                             spread=1.0 if p['posture'] == 'standing' else 0.40)
        return seg[:, :2]




# ══════════════════════════════════════════════════════════════════════
# 5. 준비 화면 · 팝업 (v2 가 그대로 재사용한다)
# ══════════════════════════════════════════════════════════════════════
class PreparePage(QtWidgets.QWidget):
    """[2] 현장 준비 — 빈방 스캔 → 기준 수집 → AE 학습.

    ⚠ [7/31] 이전에는 팝업(QDialog)이었다. 팝업은 "잠깐 보고 닫는 것"에 쓰는
      물건인데 이 절차는 스캔 12초 + 수집 15초 + 학습 30초에, 중간에 사람이
      감지 구역을 나갔다 들어와야 하는 1분짜리 '모드'다.
      → 전체 화면으로 분리한다. 흐름이 화면 단위로 읽힌다:
          [1] 연결·세션  →  [2] 현장 준비  →  [3] 관제
      학습 후 '감시 시작'을 눌러 LIVE 가 되면 관제 화면으로 자동 전환된다.
    """
    back = QtCore.pyqtSignal()

    def __init__(self, link):
        super().__init__()
        self.link = link
        self.phase = None
        # True = 감시 시작 후 관제로 자동 복귀. 사용자가 '빈방 스캔'을 눌러
        #   들어온 경우에만 켠다(이미 LIVE 인 상태로 구경하러 온 것과 구분).
        self.autoback = False
        outer = QtWidgets.QHBoxLayout(self)
        outer.addStretch()
        holder = QtWidgets.QWidget()
        holder.setFixedWidth(760)
        v = QtWidgets.QVBoxLayout(holder)
        v.setContentsMargins(0, SP_XL, 0, SP_XL)
        v.setSpacing(SP_L)
        outer.addWidget(holder)
        outer.addStretch()

        head = QtWidgets.QHBoxLayout()
        head.addWidget(lb('현장 준비', 20, CYAN, bold=True))
        head.addWidget(lb('정상 상태를 학습시켜야 이상을 판별할 수 있습니다',
                          FS_BODY, DIM))
        head.addStretch()
        self.zone_lb = lb('', FS_LABEL, TXT)
        head.addWidget(self.zone_lb)
        v.addLayout(head)

        # ── 단계 표시 ──
        self.steps = []
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(SP_S)
        for i, ph in enumerate(PHASE_ORDER):
            f = panel(hi=True)
            fv = QtWidgets.QVBoxLayout(f)
            fv.setContentsMargins(SP_M, SP_M, SP_M, SP_M)
            fv.setSpacing(SP_XS)
            num = lb(f'{i + 1}', FS_LABEL, FAINT, center=True)
            t = lb(PHASE_KO[ph], FS_BODY, DIM, center=True)
            fv.addWidget(num)
            fv.addWidget(t)
            self.steps.append((ph, f, t, num))
            row.addWidget(f)
        v.addLayout(row)

        # ── 지금 해야 할 행동 (화면의 주인) ──
        act = panel(hi=True)
        av = QtWidgets.QVBoxLayout(act)
        av.setContentsMargins(SP_XL, SP_XL, SP_XL, SP_XL)
        av.setSpacing(SP_M)
        self.big = lb('젯슨 연결을 기다리는 중…', 22, TXT, bold=True,
                      center=True, wrap=True)
        self.big.setMinimumHeight(72)
        self.sub = lb('', FS_TITLE, CYAN, center=True, wrap=True)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setStyleSheet(
            f'QProgressBar{{background:{PANEL_LO};border:none;border-radius:4px;}}'
            f'QProgressBar::chunk{{background:{CYAN};border-radius:4px;}}')
        for w in (self.big, self.sub, self.bar):
            av.addWidget(w)
        v.addWidget(act)

        # ── 주의 ──
        warn = panel()
        wv = QtWidgets.QVBoxLayout(warn)
        wv.setContentsMargins(SP_M, SP_M, SP_M, SP_M)
        self.warn = lb('시작하면 먼저 빈 방을 스캔합니다.', FS_BODY, AMBER,
                       wrap=True)
        wv.addWidget(self.warn)
        v.addWidget(warn)
        v.addStretch()

        self.go = btn('기준 수집 시작', FS_TITLE, primary=True, height=60)
        self.go.clicked.connect(self._go)
        v.addWidget(self.go)

        r2 = QtWidgets.QHBoxLayout()
        r2.setSpacing(SP_S)
        self.rst = btn('기준 초기화', FS_BODY, height=38)
        self.rst.clicked.connect(self._confirm_reset)
        bk = btn('←  개요', FS_BODY, height=38)
        bk.clicked.connect(self.back.emit)
        self.skip = btn('관제 화면으로', FS_BODY, height=38)
        for b in (bk, self.rst):
            r2.addWidget(b)
        r2.addStretch()
        r2.addWidget(self.skip)
        v.addLayout(r2)

    def set_zone(self, z):
        self.zone_lb.setText(f'{z} {ZONE_KO.get(z, "")} · 레이더 #1')

    def _go(self):
        cmd = CMD_TRAIN if self.phase == PH_WAIT_TRAIN else CMD_START
        self.autoback = True          # 이 절차가 끝나면 관제로 돌아간다
        self.link.send_cmd(cmd)
        self.go.setEnabled(False)
        QtCore.QTimer.singleShot(1500, lambda: self.go.setEnabled(True))

    def _confirm_reset(self):
        if confirm(self, '기준 초기화',
                   '학습한 정상 기준을 버리고 처음부터 다시 수집합니다.\n'
                   '초기화 후에는 빈 방 스캔부터 다시 진행해야 합니다.',
                   yes='초기화', no='취소', danger=True):
            self.autoback = True
            self.link.send_cmd(CMD_RESET)

    def update_phase(self, pkt):
        ph = pkt.get('phase') or PH_READY
        self.phase = ph
        idx = PHASE_ORDER.index(ph) if ph in PHASE_ORDER else 0
        for i, (p, f, t, num) in enumerate(self.steps):
            done, cur = i < idx, (i == idx)
            col = CYAN if cur else (GREEN if done else FAINT)
            f.setStyleSheet(
                f'QFrame{{border:{"2px solid " + CYAN if cur else "none"};'
                f'border-radius:8px;'
                f'background:{"#0d2436" if cur else (PANEL_HI if done else PANEL_LO)};}}')
            t.setStyleSheet(f'color:{col};border:none;background:transparent;')
            num.setText('✓' if done else str(i + 1))
            num.setStyleSheet(f'color:{col};border:none;background:transparent;')
        step = pkt.get('prepare_step') or ''
        self.big.setText(PHASE_ACTION.get(ph, ''))
        self.warn.setText('시작하면 먼저 빈 방을 스캔합니다.')
        wc = pkt.get('warmup_count') or 0
        nw = ((pkt.get('cfg') or {}).get('N_WARMUP')) or 150
        left = pkt.get('scan_left')
        if ph == PH_WARMUP and step == 'step_out':
            # [8/11] 버튼을 누른 사람이 나갈 시간을 준다. 이전에는 START 즉시
            # 스캔해 사람 몸통이 빈 방 클러터로 학습됐다.
            self.big.setText('지금 감지 구역 밖으로 나가세요')
            self.sub.setText(f'빈 방 스캔 시작까지  {left:.0f}초')
            self.warn.setText('이 카운트가 끝나면 스캔이 시작됩니다. 그 전에 완전히 벗어나세요.')
            self.bar.setRange(0, 100)
            sec = ((pkt.get('cfg') or {}).get('STEP_OUT_SEC')) or 5.0
            self.bar.setValue(int(100 * (1 - left / max(sec, 1e-6))))
        elif ph == PH_WARMUP and step == 'empty_scan':
            self.big.setText('빈 방 스캔 중 — 감지 구역 밖으로 나가 주세요')
            self.sub.setText(f'빈 방 스캔  {left:.0f}초 남음')
            self.warn.setText('이 단계에만 사람이 없어야 합니다. 사람이 남으면 정상 배경으로 학습됩니다.')
            self.bar.setRange(0, 100)
            sec = ((pkt.get('cfg') or {}).get('SCAN_SEC')) or 12.0
            self.bar.setValue(int(100 * (1 - left / max(sec, 1e-6))))
        elif ph == PH_WARMUP and step == 'step_in':
            self.big.setText('지금 감지 구역 안으로 들어가세요')
            self.sub.setText(f'정상 기준 수집 시작까지  {left:.0f}초')
            self.warn.setText('프레임 안에 서서 작은 움직임을 준비하세요.')
            self.bar.setRange(0, 100)
            sec = ((pkt.get('cfg') or {}).get('STEP_IN_SEC')) or 5.0
            self.bar.setValue(int(100 * (1 - left / max(sec, 1e-6))))
        elif ph == PH_WARMUP:
            self.big.setText('정상 동작 기준 수집 중 — 작게 움직여 주세요')
            self.sub.setText(f'정상 기준 수집  {wc} / {nw} 프레임')
            self.warn.setText('사람이 감지 구역 안에서 서기와 작은 자연 동작을 보여야 합니다.')
            self.bar.setRange(0, nw)
            self.bar.setValue(wc)
        elif ph == PH_TRAINING:
            self.sub.setText('LSTM-AE 학습 중 — 20~30초')
            self.warn.setText('학습 중에는 감지 구역을 비워 두세요.')
            self.bar.setRange(0, 0)
        elif ph == PH_WAIT_TRAIN:
            self.big.setText('정상 기준 수집 완료')
            self.sub.setText('기준 수집 완료 — 사람이 퇴장한 뒤 학습을 시작하세요')
            self.warn.setText('사람이 감지 구역에서 완전히 나온 뒤 학습을 시작하세요.')
            self.bar.setRange(0, 100)
            self.bar.setValue(100)
        elif ph == PH_WAIT_ARM:
            self.sub.setText('준비 완료 — 감시 시작 전까지 경보가 발생하지 않습니다')
            self.warn.setText('관제 화면에서 감시를 시작할 수 있습니다.')
            self.bar.setRange(0, 100)
            self.bar.setValue(100)
        elif ph == PH_LIVE:
            self.sub.setText('관제 화면으로 전환합니다')
            self.bar.setRange(0, 100)
            self.bar.setValue(100)
        else:
            self.sub.setText('')
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
        self.go.setText({PH_WAIT_TRAIN: '사람 퇴장 후 학습 시작',
                         PH_WAIT_ARM: '감시 시작'}.get(ph, '기준 수집 시작'))
        self.go.setVisible(ph in (PH_READY, PH_WAIT_TRAIN, PH_WAIT_ARM))
        # 관제로 나가는 길은 항상 열어 둔다 (기준이 없으면 화면이 그걸 알린다)
        self.skip.setEnabled(True)


class SettingsPopup(Dialog):
    """설정.

    ⚠ 판정 임계값(h_drop 0.43, 임펄스비 2.2, STAT_MISS_TOL …)은 여기 넣지 않는다.
      세 가지 이유:
        1. 실측 데이터로 캘리브레이션한 값이라 근무자가 슬라이더로 만질 성질이 아니다
        2. 노트북에서 바꿀 수 있게 하면 "판정은 젯슨이 독립 수행" 이라는
           fail-safe 논리가 코드로 거짓이 된다
        3. 사고 조사에서 "누가 언제 문턱을 바꿨나" 가 추적 불가능해진다
      대신 젯슨이 보낸 현재 값을 '읽기 전용' 으로 보여준다.
    """
    # 백그라운드 스레드 → UI 스레드 (Qt 위젯은 워커 스레드에서 만지면 안 된다)
    sop_prescanned = QtCore.pyqtSignal(int, int, str)
    sop_progress = QtCore.pyqtSignal(int, str, int)
    sop_loaded = QtCore.pyqtSignal(list, str)

    def __init__(self, parent=None, link=None, console=None):
        super().__init__(parent, '설정', 820, 620)
        self.link, self.console = link, console
        self.sop_prescanned.connect(self._on_prescan)
        self.sop_progress.connect(self._on_progress)
        self.sop_loaded.connect(self._on_loaded)
        tabs = QtWidgets.QTabWidget()
        tabs.setFont(QtGui.QFont(FONT, FS_BODY))
        tabs.setStyleSheet(
            f'QTabWidget::pane{{border:1px solid {EDGE};border-radius:6px;'
            f'background:{PANEL};}}'
            f'QTabBar::tab{{background:{PANEL_LO};color:{DIM};padding:8px 16px;'
            f'border-top-left-radius:6px;border-top-right-radius:6px;}}'
            f'QTabBar::tab:selected{{background:{PANEL};color:{CYAN};}}')
        self.v.addWidget(tabs, 1)

        # ── 연결 ──
        w1, f1 = self._form()
        self.host = self._line(link.host if link else '192.168.0.50')
        f1.addRow(self._lab('젯슨 IP'), self.host)
        f1.addRow(self._lab('데이터 포트'), self._ro(str(DATA_PORT)))
        f1.addRow(self._lab('제어 포트'), self._ro(str(CTRL_PORT)))
        f1.addRow(self._lab('HELLO 주기'), self._ro(f'{HELLO_SEC:.0f} 초'))
        f1.addRow(self._lab('링크 타임아웃'), self._ro(f'{LINK_TIMEOUT:.0f} 초'))
        tb = btn('연결 테스트', FS_BODY, height=34)
        tb.clicked.connect(self._test_link)
        self.link_res = lb('', FS_LABEL, DIM, wrap=True)
        f1.addRow(tb, self.link_res)
        tabs.addTab(w1, '연결')

        # ── 경보 ──
        w2, f2 = self._form()
        self.snd = QtWidgets.QCheckBox('미확인 경보에 소리 사용')
        self.snd.setChecked(True)
        self.snd.setStyleSheet(f'color:{TXT};')
        self.blink_cb = QtWidgets.QCheckBox('미확인 경보 배너 점멸')
        self.blink_cb.setChecked(True)
        self.blink_cb.setStyleSheet(f'color:{TXT};')
        self.autopop = QtWidgets.QCheckBox('경보 시 조치 가이드 자동 표시')
        self.autopop.setChecked(True)
        self.autopop.setStyleSheet(f'color:{TXT};')
        for c in (self.snd, self.blink_cb, self.autopop):
            f2.addRow(c)
        f2.addRow(self._lab('위험 · 주의 색'),
                  self._ro(f'critical {RED}   warning {AMBER}'))
        tabs.addTab(w2, '경보')

        # ── AI · SOP ──
        w3, f3 = self._form()
        f3.addRow(self._lab('Ollama URL'), self._ro(OLLAMA_URL))
        f3.addRow(self._lab('생성 모델'), self._ro(LLM_MODEL))
        f3.addRow(self._lab('임베딩 모델'), self._ro(EMBED_MODEL))
        f3.addRow(self._lab('SOP DB'), self._ro(CONN_STR.split('@')[-1]))
        f3.addRow(self._lab('langchain'), self._ro('설치됨' if RAG_OK else '미설치'))
        ab = btn('AI 연결 테스트', FS_BODY, height=34)
        ab.clicked.connect(self._test_ai)
        self.ai_res = lb('', FS_LABEL, DIM, wrap=True)
        f3.addRow(ab, self.ai_res)
        f3.addRow(self._lab(''),
                  lb('임베딩 모델은 SOP DB 적재 때와 반드시 같아야 검색이 맞습니다.',
                     FS_CAPTION, AMBER, wrap=True))
        tabs.addTab(w3, 'AI · SOP')

        # ── SOP 관리 ──
        tabs.addTab(self._sop_tab(), 'SOP 관리')

        # ── 판정 (읽기 전용) ──
        w4, f4 = self._form()
        f4.addRow(lb('아래 값은 젯슨이 소유합니다. 노트북에서 바꿀 수 없습니다 — '
                     '판정과 차단은 링크가 끊겨도 젯슨이 독립 수행해야 하기 때문입니다.',
                     FS_LABEL, AMBER, wrap=True))
        self.thr_rows = {}
        for k, name in (('threshold', '이상점수 임계 (AE)'),
                        ('n_warmup', '기준 수집 프레임'),
                        ('scan_sec', '빈 방 스캔 시간'),
                        ('ceiling', '천장 높이'),
                        ('curr', '과전류 임계'),
                        ('volt', '전압강하 임계'),
                        ('vib', '설비진동 임계')):
            r = self._ro('—')
            self.thr_rows[k] = r
            f4.addRow(self._lab(name), r)
        tabs.addTab(w4, '판정 (읽기 전용)')

        # ── 진단 ──
        w5, f5 = self._form()
        self.diag = {}
        for k, name in (('seq', '수신 seq'), ('lost', '패킷 유실'),
                        ('peak', '최대 패킷 크기'), ('schema', '스키마 버전'),
                        ('gl', '3D 렌더')):
            r = self._ro('—')
            self.diag[k] = r
            f5.addRow(self._lab(name), r)
        eb = btn('경보 기록 CSV 내보내기', FS_BODY, height=34)
        eb.clicked.connect(lambda: console.evlog._export() if console else None)
        f5.addRow(eb)
        tabs.addTab(w5, '진단')

        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        cl = btn('닫기', FS_BODY, height=38)
        cl.clicked.connect(self.accept)
        row.addWidget(cl)
        self.v.addLayout(row)

        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.refresh)
        self.tick.start(1000)

    # ══════════════════════════════════════════════════════════════════
    # SOP 관리 — PDF 를 올려 청킹·색인한다
    # ══════════════════════════════════════════════════════════════════
    #  ⚠ 청킹에 LLM 을 쓰지 않는다. 경계 결정은 결정론적으로 해결된 문제이고
    #    (RecursiveCharacterTextSplitter), LLM 으로 하면 같은 파일이 매번 다르게
    #    쪼개져 재현이 안 되고 13 tok/s 로는 몇십 분이 걸린다.
    #    LLM 이 쓸모 있는 건 '분류' 인데, 그것도 키워드 규칙을 먼저 태우고
    #    애매한 것만 넘긴다. 그리고 최종 확정은 사람이 드롭다운으로 한다 —
    #    안전 문서에서 자동 분류를 고칠 수 없으면 쓸 수 없다.
    def _sop_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(SP_L, SP_L, SP_L, SP_L)
        v.setSpacing(SP_M)

        head = QtWidgets.QHBoxLayout()
        head.addWidget(lb('안전 매뉴얼 PDF', FS_BODY, TXT, bold=True))
        head.addStretch()
        self.sop_meta = lb('', FS_LABEL, DIM)
        head.addWidget(self.sop_meta)
        v.addLayout(head)

        drop = btn('＋  PDF 파일 선택  (여러 개 가능)', FS_BODY, height=48)
        drop.clicked.connect(self._sop_pick)
        v.addWidget(drop)

        self.sop_tbl = QtWidgets.QTableWidget(0, 4)
        self.sop_tbl.setHorizontalHeaderLabels(['문서', '청크', '카테고리', '상태'])
        self.sop_tbl.horizontalHeader().setStretchLastSection(True)
        self.sop_tbl.setColumnWidth(0, 300)
        self.sop_tbl.verticalHeader().setVisible(False)
        self.sop_tbl.setFont(QtGui.QFont(FONT, FS_LABEL))
        self.sop_tbl.setStyleSheet(TABLE_QSS)
        self.sop_tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.sop_tbl, 1)

        self.sop_stat = lb('', FS_LABEL, DIM, wrap=True)
        v.addWidget(self.sop_stat)
        self.sop_bar = QtWidgets.QProgressBar()
        self.sop_bar.setTextVisible(False)
        self.sop_bar.setFixedHeight(6)
        self.sop_bar.setStyleSheet(
            f'QProgressBar{{background:{PANEL_LO};border:none;border-radius:3px;}}'
            f'QProgressBar::chunk{{background:{CYAN};border-radius:3px;}}')
        self.sop_bar.hide()
        v.addWidget(self.sop_bar)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(SP_S)
        b1 = btn('DB 현황 새로고침', FS_BODY, height=36)
        b1.clicked.connect(self._sop_refresh)
        row.addWidget(b1)
        row.addStretch()
        self.sop_go = btn('색인 실행', FS_BODY, primary=True, height=36)
        self.sop_go.setEnabled(False)
        self.sop_go.clicked.connect(self._sop_ingest)
        row.addWidget(self.sop_go)
        v.addLayout(row)
        v.addWidget(lb('색인은 기존 문서를 지우지 않고 추가합니다. 같은 파일을 다시 올리면 '
                       '중복되므로, 교체할 때는 해당 문서를 먼저 삭제하세요.',
                       FS_CAPTION, DIM, wrap=True))
        self._sop_pending = []
        QtCore.QTimer.singleShot(400, self._sop_refresh)
        return w

    _KW = {                       # 키워드 규칙 — LLM 부르기 전에 먼저 태운다
        '01_감전_LOTO': ['감전', 'LOTO', '활선', '전로', '정전작업', '절연', '접지'],
        '02_협착_끼임': ['협착', '끼임', '회전기계', '절단', '방호덮개', '컨베이어'],
        '03_낙상_응급처치': ['추락', '넘어짐', '전도', '응급처치', '안전대', '골절'],
        '04_예지보전': ['진동', '상태감시', '수명예측', '베어링', '열화', '진단'],
        '05_위험성평가_비상': ['위험성평가', '비상', '대피', '비상계획'],
    }

    def _guess_cat(self, text):
        sc = {c: sum(text.count(k) for k in ks) for c, ks in self._KW.items()}
        best = max(sc, key=sc.get)
        return best if sc[best] > 0 else None

    def _sop_pick(self):
        fs, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, '안전 매뉴얼 PDF 선택', '', 'PDF (*.pdf)')
        if not fs:
            return
        for f in fs:
            r = self.sop_tbl.rowCount()
            self.sop_tbl.insertRow(r)
            self.sop_tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(os.path.basename(f)))
            self.sop_tbl.setItem(r, 1, QtWidgets.QTableWidgetItem('—'))
            cb = QtWidgets.QComboBox()
            cb.addItems(SOP_CATEGORIES)
            cb.setStyleSheet(f'QComboBox{{background:{PANEL_HI};color:{TXT};'
                             f'border:1px solid {EDGE};border-radius:4px;padding:2px 6px;}}'
                             f'QComboBox QAbstractItemView{{background:{PANEL_HI};'
                             f'color:{TXT};selection-background-color:{CYAN};}}')
            self.sop_tbl.setCellWidget(r, 2, cb)
            it = QtWidgets.QTableWidgetItem('대기 — 분류 중')
            it.setForeground(QtGui.QColor(AMBER))
            self.sop_tbl.setItem(r, 3, it)
            self._sop_pending.append({'path': f, 'row': r, 'combo': cb})
        self.sop_go.setEnabled(True)
        threading.Thread(target=self._sop_prescan, daemon=True).start()

    def _sop_prescan(self):
        """PDF 를 읽어 청크 수를 세고 카테고리를 규칙으로 추정한다(색인 전 미리보기)."""
        for p in list(self._sop_pending):
            if p.get('chunks') is not None:
                continue
            try:
                from langchain_community.document_loaders import PyPDFLoader
                from langchain_text_splitters import RecursiveCharacterTextSplitter
                docs = PyPDFLoader(p['path']).load()
                sp = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=80)
                ch = [c for c in sp.split_documents(docs)
                      if len(c.page_content.strip()) >= 50]      # 머리말·꼬리말 제거
                p['chunks'] = ch
                cat = self._guess_cat(' '.join(c.page_content for c in ch[:60]))
                self.sop_prescanned.emit(p['row'], len(ch), cat or '')
            except Exception as e:
                p['chunks'] = []
                self.sop_prescanned.emit(p['row'], -1, f'ERR:{type(e).__name__}')

    def _on_prescan(self, row, n, cat):
        if n < 0:
            it = QtWidgets.QTableWidgetItem(f'읽기 실패 ({cat})')
            it.setForeground(QtGui.QColor(RED))
            self.sop_tbl.setItem(row, 3, it)
            return
        self.sop_tbl.setItem(row, 1, QtWidgets.QTableWidgetItem(str(n)))
        cb = self.sop_tbl.cellWidget(row, 2)
        if cb and cat in SOP_CATEGORIES:
            cb.setCurrentText(cat)
        it = QtWidgets.QTableWidgetItem('대기' + ('' if cat else ' — 분류 확인 필요'))
        it.setForeground(QtGui.QColor(AMBER if cat else RED))
        self.sop_tbl.setItem(row, 3, it)

    def _sop_ingest(self):
        todo = [p for p in self._sop_pending if p.get('chunks')]
        if not todo:
            self.sop_stat.setText('색인할 문서가 없습니다 (PDF 읽기가 끝나길 기다리세요)')
            return
        total = sum(len(p['chunks']) for p in todo)
        if not confirm(self, 'SOP 색인',
                       f'{len(todo)}개 문서 · {total:,}청크를 색인합니다.\n'
                       f'임베딩({EMBED_MODEL}) 생성에 수 분이 걸릴 수 있습니다.\n'
                       f'기존 문서는 지워지지 않습니다.', yes='색인 시작', no='취소'):
            return
        for p in todo:
            cb = p['combo']
            p['cat'] = cb.currentText()
        self.sop_go.setEnabled(False)
        self.sop_bar.setRange(0, 0)
        self.sop_bar.show()
        threading.Thread(target=self._sop_worker, args=(todo,), daemon=True).start()

    def _sop_worker(self, todo):
        try:
            from langchain_ollama import OllamaEmbeddings
            from langchain_community.vectorstores import PGVector
            emb = OllamaEmbeddings(model=EMBED_MODEL)
            vs = PGVector(connection_string=CONN_STR, embedding_function=emb,
                          collection_name='safety_manual')
            for p in todo:
                name = os.path.basename(p['path'])
                self.sop_progress.emit(p['row'], f'색인 중… ({len(p["chunks"])}청크)', 0)
                for c in p['chunks']:
                    c.page_content = c.page_content.replace('\x00', '')
                    c.metadata['category'] = p['cat']
                    c.metadata['source_file'] = name
                vs.add_documents(p['chunks'])
                self.sop_progress.emit(p['row'], '색인됨', 1)
            self.sop_progress.emit(-1, '색인 완료', 2)
        except Exception as e:
            self.sop_progress.emit(-1, f'색인 실패: {type(e).__name__}: {e}', -1)

    def _on_progress(self, row, msg, code):
        if row >= 0:
            it = QtWidgets.QTableWidgetItem(('● ' if code == 1 else '') + msg)
            it.setForeground(QtGui.QColor(GREEN if code == 1 else AMBER))
            self.sop_tbl.setItem(row, 3, it)
            return
        self.sop_bar.hide()
        self.sop_stat.setText(msg)
        self.sop_stat.setStyleSheet(f'color:{RED if code < 0 else GREEN};border:none;')
        self.sop_go.setEnabled(code < 0)
        if code == 2:
            self._sop_pending = []
            self._sop_refresh()

    def _sop_refresh(self):
        """DB 에 실제로 들어 있는 문서 목록을 SQL 로 읽어 표를 채운다."""
        def _w():
            try:
                import psycopg2
                cn = psycopg2.connect(CONN_STR)
                cu = cn.cursor()
                cu.execute("SELECT uuid FROM langchain_pg_collection WHERE name=%s",
                           ('safety_manual',))
                r = cu.fetchone()
                if not r:
                    self.sop_loaded.emit([], '컬렉션이 없습니다 — 아래에서 PDF 를 올리세요')
                    return
                cu.execute("""SELECT cmetadata->>'source_file', cmetadata->>'category',
                                     count(*)
                              FROM langchain_pg_embedding WHERE collection_id=%s
                              GROUP BY 1,2 ORDER BY 3 DESC""", (r[0],))
                rows = cu.fetchall()
                cu.execute("SELECT count(*) FROM langchain_pg_embedding "
                           "WHERE collection_id=%s", (r[0],))
                n = cu.fetchone()[0]
                cn.close()
                self.sop_loaded.emit(rows,
                                     f'{len(rows)}개 문서 · {n:,}청크 · {EMBED_MODEL}')
            except Exception as e:
                self.sop_loaded.emit([], f'DB 조회 실패: {type(e).__name__} '
                                         f'(docker start radar-guard-db)')
        threading.Thread(target=_w, daemon=True).start()

    def _on_loaded(self, rows, meta):
        self.sop_meta.setText(meta)
        self.sop_tbl.setRowCount(0)
        self._sop_pending = []
        for f, cat, k in rows:
            r = self.sop_tbl.rowCount()
            self.sop_tbl.insertRow(r)
            eng = (f or '').lower().startswith('osha')
            self.sop_tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(f or '(없음)'))
            self.sop_tbl.setItem(r, 1, QtWidgets.QTableWidgetItem(str(k)))
            self.sop_tbl.setItem(r, 2, QtWidgets.QTableWidgetItem(cat or '(미분류)'))
            it = QtWidgets.QTableWidgetItem('⚠ 영문 — 한글 질의 오염' if eng else '● 색인됨')
            it.setForeground(QtGui.QColor(AMBER if eng else GREEN))
            self.sop_tbl.setItem(r, 3, it)
        self.sop_go.setEnabled(False)

    # ── 위젯 헬퍼 ──
    def _form(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        f.setContentsMargins(SP_L, SP_L, SP_L, SP_L)
        f.setSpacing(SP_M)
        return w, f

    def _lab(self, t):
        return lb(t, FS_LABEL, DIM)

    def _line(self, val=''):
        e = QtWidgets.QLineEdit(val)
        e.setFont(QtGui.QFont(FONT, FS_BODY))
        e.setMinimumHeight(32)
        e.setStyleSheet(f'background:{PANEL_HI};color:{TXT};border:1px solid {EDGE};'
                        f'border-radius:6px;padding:2px 8px;')
        return e

    def _ro(self, val):
        l = lb(val, FS_BODY, TXT)
        l.setStyleSheet(f'color:{TXT};background:{PANEL_LO};border:none;'
                        f'border-radius:4px;padding:4px 8px;')
        return l

    # ── 동작 ──
    def _test_link(self):
        if not self.link:
            self.link_res.setText('링크 미설정 — --live 로 실행하세요')
            return
        age = self.link.age()
        if age is None:
            self.link_res.setText(f'{self.link.host} 로 HELLO 발신 중 · 아직 응답 없음')
            self.link_res.setStyleSheet(f'color:{AMBER};border:none;')
        elif age > LINK_TIMEOUT:
            self.link_res.setText(f'끊김 — 마지막 수신 {int(age)}초 전')
            self.link_res.setStyleSheet(f'color:{RED};border:none;')
        else:
            self.link_res.setText(f'정상 · {int(age * 1000)}ms · seq {self.link.seq}')
            self.link_res.setStyleSheet(f'color:{GREEN};border:none;')

    def _test_ai(self):
        self.ai_res.setText('확인 중…')
        self.ai_res.setStyleSheet(f'color:{DIM};border:none;')

        def _w():
            import urllib.request
            try:
                urllib.request.urlopen(
                    OLLAMA_URL.replace('/api/generate', '/api/tags'), timeout=4).read()
                msg, col = f'Ollama 응답 정상 · {LLM_MODEL}', GREEN
            except Exception as e:
                msg, col = f'실패: {type(e).__name__} — ollama serve 확인', RED
            self.ai_res.setText(msg)
            self.ai_res.setStyleSheet(f'color:{col};border:none;')

        threading.Thread(target=_w, daemon=True).start()

    def refresh(self):
        if not self.isVisible():
            return
        pkt = (self.console.pkt if self.console else {}) or {}
        cfg = pkt.get('cfg') or {}
        thr = pkt.get('threshold')
        self.thr_rows['threshold'].setText(
            '규칙 전용 (AE 비활성)' if (thr is not None and thr < 0)
            else (f'{thr:.5f}' if thr else '—'))
        self.thr_rows['n_warmup'].setText(str(cfg.get('N_WARMUP', '—')))
        self.thr_rows['scan_sec'].setText(f"{cfg.get('SCAN_SEC', '—')} 초")
        self.thr_rows['ceiling'].setText(f"{cfg.get('CEILING_H', CEILING_H)} m")
        self.thr_rows['curr'].setText(f"{cfg.get('CURR_LIMIT', CURR_LIMIT)} A")
        self.thr_rows['volt'].setText(f"{cfg.get('VOLT_MIN', VOLT_MIN)} V")
        self.thr_rows['vib'].setText(str(cfg.get('VIB_DS_THRESH', VIB_DS_THRESH)))
        if self.link:
            self.diag['seq'].setText(str(self.link.seq))
            self.diag['lost'].setText(str(self.link.lost))
            self.diag['peak'].setText(
                f'{self.link.peak_bytes} B'
                + ('  (MTU 초과 — 단편화)' if self.link.peak_bytes > 1472 else ''))
        self.diag['schema'].setText(
            f"젯슨 {pkt.get('schema_version', '—')} / 노트북 {SCHEMA_VERSION}")
        self.diag['gl'].setText(
            'OpenGL' if (self.console and self.console.track.gl) else '2D 대체')


class EvidencePopup(Dialog):
    """L2 판단 근거 — 젯슨 classify() 가 사실로 확정해 보낸 수치만 표시.

    ⚠ 이 화면의 숫자는 LLM 이 만들지 않는다. 전부 젯슨이 계산해 보낸 값이다.
      LLM 이 근거를 지어낼 여지를 구조적으로 없앤 것이 이 설계의 핵심이다.
    """

    def __init__(self, parent=None):
        super().__init__(parent, '판단 근거', 720, 560)
        self.head = lb('최근 경보 없음', FS_BODY, DIM)
        self.v.addWidget(self.head)
        self.tbl = QtWidgets.QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(['항목', '측정값', '기준', '의미'])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setFont(QtGui.QFont(FONT, FS_BODY))
        self.tbl.setStyleSheet(TABLE_QSS)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.v.addWidget(self.tbl, 2)
        self.rej = lb('', FS_LABEL, DIM, wrap=True)
        self.v.addWidget(self.rej)
        self.v.addWidget(lb('원시 측정값', FS_LABEL, DIM))
        self.raw = QtWidgets.QTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(QtGui.QFont(FONT, FS_CAPTION))
        self.raw.setStyleSheet(EDIT_QSS)
        self.raw.setMaximumHeight(120)
        self.v.addWidget(self.raw)
        b = btn('닫기')
        b.clicked.connect(self.accept)
        self.v.addWidget(b)

    def set_event(self, ev, rx_ts):
        et = ev.get('type') or ev.get('event_type')
        self.head.setText(
            f"{EVENT_KO.get(et, '-')} · Zone {ev.get('zone')} · "
            f"{SEV_KO.get(ev.get('sev'), '')} · 판정 점수 {ev.get('conf', 0):.2f} · "
            f"{time.strftime('%H:%M:%S', time.localtime(rx_ts))}")
        self.head.setStyleSheet(f'color:{RED};border:none;')
        g = ev.get('gates') or {}
        self.tbl.setRowCount(len(g))
        if not g:
            self.tbl.setRowCount(1)
            it = QtWidgets.QTableWidgetItem(
                '판단 근거 없음 — 피처 계산 전 조기 판정 경로 (evidence=None)')
            it.setForeground(QtGui.QColor(AMBER))
            self.tbl.setItem(0, 0, it)
            self.tbl.setSpan(0, 0, 1, 4)
        for r, (k, d) in enumerate(g.items()):
            meta = GATE_META.get(k, {})
            unit = d.get('unit', '')
            cells = (meta.get('ko', k),
                     f"{d.get('value')} {unit}".strip(),
                     f"{d.get('cmp', '>=')} {d.get('thr')}  "
                     f"{'통과' if d.get('pass') else '미달'}",
                     meta.get('why', ''))
            for c, t in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(str(t))
                it.setForeground(QtGui.QColor(
                    (GREEN if d.get('pass') else RED) if c == 2
                    else (DIM if c == 3 else TXT)))
                if c == 0 and meta.get('src'):
                    it.setToolTip(f"실측 근거: {meta['src']}")
                self.tbl.setItem(r, c, it)
        self.tbl.resizeColumnsToContents()
        rj = ev.get('rejected') or []
        self.rej.setText(
            '제외한 후보 · ' + ' · '.join(
                f"{REJECT_KO.get(r.get('candidate'), r.get('candidate'))} "
                f"({r.get('reason')})" for r in rj) if rj else '')
        e = ev.get('evidence') or {}
        self.raw.setPlainText(
            '  '.join(f'{EVIDENCE_KO.get(k, k)}={v}' for k, v in e.items()
                      if v is not None) or '(없음)')


class PowerPopup(Dialog):
    BUF = 300

    def __init__(self, parent=None, link=None):
        super().__init__(parent, '전기 설비', 730, 590)
        self.link = link
        self.buf = {'curr': [], 'volt': []}
        self.snap = {}
        self.src = lb('', FS_CAPTION, AMBER)
        self.v.addWidget(self.src)
        self.p1 = pg.PlotWidget(title=f'전류 (A) · 임계 {CURR_LIMIT}')
        self.p2 = pg.PlotWidget(title=f'전압 (V) · 임계 {VOLT_MIN}')
        for p, y in ((self.p1, CURR_LIMIT), (self.p2, VOLT_MIN)):
            p.showGrid(x=True, y=True, alpha=0.12)
            p.addLine(y=y, pen=pg.mkPen(RED, style=QtCore.Qt.DashLine))
            p.setMinimumHeight(130)
            self.v.addWidget(p)
        self.c1 = self.p1.plot(pen=pg.mkPen(AMBER, width=1.6))
        self.c2 = self.p2.plot(pen=pg.mkPen(CYAN, width=1.6))
        self.tbl = QtWidgets.QTableWidget(len(ZONE_IDS), 3)
        self.tbl.setHorizontalHeaderLabels(['구역', '차단기', '비고'])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setFont(QtGui.QFont(FONT, FS_BODY))
        self.tbl.setStyleSheet(TABLE_QSS)
        self.tbl.setMaximumHeight(128)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.v.addWidget(self.tbl)
        self.restore_btn = btn('전력 복구', 12, height=42)
        self.restore_btn.setEnabled(False)
        self.v.addWidget(self.restore_btn)
        self.v.addWidget(lb('차단·재투입 실행은 젯슨이 합니다. 이 화면은 상태 표시와 '
                            '복구 요청만 합니다 (링크가 끊겨도 차단은 유지됩니다).',
                            9, DIM, wrap=True))

    def push(self, st):
        p = st.get('power') or {}
        for k in ('curr', 'volt'):
            value = p.get(k)
            self.buf[k].append(float(value) if value is not None else float('nan'))
            del self.buf[k][:-self.BUF]
        self.snap = (st.get('breaker') or {}).get('state') or {}
        if p.get('src') == 'ina226':
            leak = (f"{p['leak_curr']:.4f} A"
                    if p.get('leak_curr') is not None else '—')
            self.src.setText(
                f"실측 (INA226) · {p.get('volt', 0):.3f} V · "
                f"부하 {p.get('curr', 0):.3f} A · 누설 {leak} "
                f"(임계 {LEAK_LIMIT:.3f} A) · {p.get('watt', 0):.2f} W")
        else:
            self.src.setText('INA226 연결 오류 · 전류·전압 측정값 없음')
        if self.isVisible():
            self.c1.setData(self.buf['curr'])
            self.c2.setData(self.buf['volt'])
            self.refresh()

    def tripped(self):
        return [z for z, s in self.snap.items() if s != 'ON']

    def refresh(self):
        for r, z in enumerate(ZONE_IDS):
            off = self.snap.get(z, 'ON') != 'ON'
            for c, t in enumerate((f'Zone {z} · {ZONE_KO.get(z, "")}',
                                   '차단됨' if off else '투입',
                                   '수동 복구 대기' if off else '')):
                it = QtWidgets.QTableWidgetItem(t)
                it.setForeground(QtGui.QColor((RED if off else GREEN) if c == 1 else TXT))
                self.tbl.setItem(r, c, it)
        self.restore_btn.setEnabled(bool(self.tripped()))


class RestorePopup(Dialog):
    def __init__(self, parent=None):
        super().__init__(parent, '전력 복구 확인', 490, 340)
        self.v.addWidget(lb('아래 구역의 전원을 다시 투입합니다', FS_BODY, RED, bold=True))
        self.zones = lb('', FS_BODY, TXT, bold=True)
        self.v.addWidget(self.zones)
        self.checks = []
        for t in ('작업자 안전을 직접 확인했습니다', '설비 이상 원인이 해소됐습니다',
                  '주변 인원에게 재투입을 알렸습니다'):
            c = QtWidgets.QCheckBox(t)
            c.setFont(QtGui.QFont(FONT, FS_BODY))
            c.setStyleSheet(f'color:{TXT};')
            c.stateChanged.connect(self._sync)
            self.checks.append(c)
            self.v.addWidget(c)
        self.v.addStretch()
        row = QtWidgets.QHBoxLayout()
        self.ok = btn('전원 투입', 12, accent=True, height=42)
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        no = btn('취소', 12, height=42)
        no.clicked.connect(self.reject)
        row.addWidget(no)
        row.addWidget(self.ok, 2)
        self.v.addLayout(row)

    def _sync(self):
        self.ok.setEnabled(all(c.isChecked() for c in self.checks))

    def ask(self, zones):
        self.zones.setText(', '.join(f'Zone {z} · {ZONE_KO.get(z, "")}' for z in zones))
        for c in self.checks:
            c.setChecked(False)
        return self.exec_() == QtWidgets.QDialog.Accepted


class QueryPopup(Dialog):
    """L4 질의 — 이벤트 이력 자연어 질의.

    ⚠ 숫자는 LLM 이 만들지 않는다. 아래 순서를 지킨다:
        자연어 → (LLM) 조회 조건 → (파이썬) 실제 집계 → (LLM) 문장 연결
      LLM 이 숫자를 지어내는 것이 구조적으로 불가능해진다.
      지금은 1·3단계가 미연동 — 로컬 집계(2단계)만 동작한다.
    """

    def __init__(self, parent=None, console=None):
        super().__init__(parent, '문의', 660, 480)
        self.console = console
        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QtGui.QFont(FONT, FS_BODY))
        self.log.setStyleSheet(EDIT_QSS)
        self.v.addWidget(self.log, 1)
        row = QtWidgets.QHBoxLayout()
        self.inp = QtWidgets.QLineEdit()
        self.inp.setFont(QtGui.QFont(FONT, FS_BODY))
        self.inp.setMinimumHeight(36)
        self.inp.setStyleSheet(EDIT_QSS)
        self.inp.setPlaceholderText('예: 오늘 경보 몇 건이야?')
        self.inp.returnPressed.connect(self._send)
        row.addWidget(self.inp, 1)
        b = btn('보내기', 11, height=36)
        b.clicked.connect(self._send)
        row.addWidget(b)
        self.v.addLayout(row)
        q = QtWidgets.QHBoxLayout()
        for s in ('오늘 경보 몇 건', '마지막 경보 언제', '지금 차단된 구역'):
            c = btn(s, 9, height=30)
            c.clicked.connect(lambda _, x=s: (self.inp.setText(x), self._send()))
            q.addWidget(c)
        self.v.addLayout(q)
        self.v.addWidget(lb('숫자는 전부 로컬 집계 결과입니다. LLM 은 문장 연결만 합니다.',
                            9, DIM))

    def _send(self):
        s = self.inp.text().strip()
        if not s:
            return
        self.inp.clear()
        self.log.append(f'<span style="color:{CYAN}"><b>나</b></span> · '
                        f'<span style="color:{TXT}">{s}</span>')
        self.log.append(f'<span style="color:{AMBER}">{self._answer(s)}</span><br>')

    def _answer(self, q):
        """로컬 집계 — 여기서 나온 숫자만 신뢰할 수 있다."""
        c = self.console
        if c is None:
            return '연결된 데이터가 없습니다.'
        incs = c.incidents
        if '차단' in q:
            tz = c.pwr.tripped()
            return (f'현재 차단된 구역: {", ".join(tz)} (재투입은 전기 설비 화면에서)'
                    if tz else '차단된 구역이 없습니다. 전 구역 투입 상태입니다.')
        if '마지막' in q or '언제' in q:
            if not incs:
                return '오늘 기록된 경보가 없습니다.'
            last = incs[-1]
            return (f"마지막 경보는 {last.get('detected')} "
                    f"Zone {last.get('zone')} "
                    f"{EVENT_KO.get(last.get('type'), last.get('type'))}입니다.")
        if '몇' in q or '건' in q:
            done = sum(1 for i in incs if i.get('resolved'))
            return (f'오늘 경보 {len(incs)}건입니다. '
                    f'{done}건은 종료 처리됐고 {len(incs) - done}건이 진행 중입니다.'
                    if incs else '오늘 경보는 0건입니다.')
        return ('아직 답할 수 없는 질문입니다. (LLM 질의 연동 예정 — '
                '지금은 경보 건수·시각·차단 구역만 답합니다)')


class GraphPopup(Dialog):
    BUF = HISTORY_LEN

    def __init__(self, parent=None):
        super().__init__(parent, '신호 그래프', 760, 560)
        self.p1 = pg.PlotWidget(title=f'움직임 세기 dop_std · 진동 임계 {VIB_DS_THRESH}')
        self.p2 = pg.PlotWidget(title='바닥 기준 높이 (m)')
        self.p3 = pg.PlotWidget(title='이상 점수 (LSTM-AE)')
        for p in (self.p1, self.p2, self.p3):
            p.showGrid(x=True, y=True, alpha=0.12)
            p.setMinimumHeight(120)
            self.v.addWidget(p)
        self.p1.addLine(y=VIB_DS_THRESH, pen=pg.mkPen(AMBER, style=QtCore.Qt.DashLine))
        self.thr_line = self.p3.addLine(y=0.025, pen=pg.mkPen(RED,
                                                              style=QtCore.Qt.DashLine))
        self.c1 = self.p1.plot(pen=pg.mkPen(RED, width=1.6))
        self.c2 = self.p2.plot(pen=pg.mkPen(CYAN, width=1.6))
        self.c3 = self.p3.plot(pen=pg.mkPen(AMBER, width=1.6))

    def push(self, st):
        if not self.isVisible():
            return
        self.c1.setData(st.get('ds') or [])
        self.c2.setData(st.get('cz') or [])
        self.c3.setData(st.get('sc') or [])
        if st.get('threshold'):
            self.thr_line.setValue(st['threshold'])


# ══════════════════════════════════════════════════════════════════════
# 6. 경보 상태기계 상수 (ISA-18.2)
# ══════════════════════════════════════════════════════════════════════
ST_NORMAL, ST_UNACK, ST_ACK = 'NORMAL', 'UNACK', 'ACK'
# ══════════════════════════════════════════════════════════════════════
# 7. 데모 소스 (젯슨 없이 화면만 보고 싶을 때)
# ══════════════════════════════════════════════════════════════════════
class _DemoSource:
    """14초 주기: 정상 보행(0~6s) → 낙상(6s) → 누움 유지.
    ⚠ 프로토콜 검증용이 아니다. 그건 sim_jetson.py 로 한다.

    ⚠ [8/01] 이벤트·zone_state·breaker 가 전부 'C' 로 고정돼 있었다. 레이더
      실물은 RADAR_ZONE('A') 한 대뿐이고 C 는 '장비 미설치' 다 — 즉 데모를
      돌리면 장비가 없는 구역에서 사람이 넘어지고, 정작 사람이 있는 A 는
      '전원 투입' 인 화면이 나왔다. radar_common 의 EVENT_ZONE 주석이 지적한
      것과 같은 버그가 데모 경로에 남아 있었다."""

    def __init__(self):
        self.t0 = time.time()
        self.eid = 0
        self.fired = False
        self.hist = {'cz': deque([1.15] * HISTORY_LEN, maxlen=HISTORY_LEN),
                     'ds': deque([0.1] * HISTORY_LEN, maxlen=HISTORY_LEN),
                     'sc': deque([0.008] * HISTORY_LEN, maxlen=HISTORY_LEN)}

    def read(self):
        t = (time.time() - self.t0) % 14.0
        if t < 1.0:
            self.fired = False
        fallen = t > 6.0
        sx, sy, sz = (0.42, 0.08, 0.24) if fallen else (0.10, 0.30, 0.10)
        cx, cz = 0.45 * math.sin(t * 0.7), 0.30 * math.cos(t * 0.5)
        cy = 1.20 if not fallen else 1.95
        ds = 0.05 + (1.8 if 6.0 < t < 7.2 else 0.18)
        self.hist['cz'].append(CEILING_H - cy)
        self.hist['ds'].append(ds)
        self.hist['sc'].append(0.041 if fallen else 0.008)
        ev = {'active': False, 'type': None, 'sev': 'normal', 'conf': 0.0,
              'zone': RADAR_ZONE, 'id': self.eid, 'ts': time.time(),
              'evidence': None, 'gates': None, 'rejected': []}
        if fallen:
            if not self.fired:
                self.fired = True
                self.eid += 1
            ev.update({
                'active': True, 'type': 'fall_detected', 'sev': 'critical',
                'conf': 0.87, 'id': self.eid,
                'evidence': {'height_start': 1.62, 'height_end': 0.31,
                             'impulse_ratio': 8.9, 'h_drop': 1.37,
                             'horiz_range': 1.12, 'ds_last': 0.44, 'ds_broad': 4,
                             'ae_score': 0.0412, 'ae_thr': 0.0250},
                'gates': {
                    'impulse': {'value': 8.90, 'thr': 2.2, 'cmp': '>=', 'unit': '비율', 'pass': True},
                    'h_drop': {'value': 1.37, 'thr': 0.43, 'cmp': '>=', 'unit': 'm', 'pass': True},
                    'horiz': {'value': 1.12, 'thr': 0.6, 'cmp': '>=', 'unit': 'm', 'pass': True},
                    'ds_last': {'value': 0.44, 'thr': 1.0, 'cmp': '<=', 'unit': 'm/s', 'pass': True},
                    'ds_broad': {'value': 4, 'thr': 2, 'cmp': '>=', 'unit': '프레임', 'pass': True}},
                'rejected': [
                    {'candidate': 'fast_sit', 'reason': 'horiz_range 1.12 >= 0.6'},
                    {'candidate': 'vibration', 'reason': 'h_drop 1.37 >= 0.5'}]})
        return {
            'schema_version': SCHEMA_VERSION, 'seq': 0, 'ts': time.time(),
            'phase': PH_LIVE, 'warmup_count': 150, 'threshold': 0.025,
            'data_ok': True, 'data_age': 0.1, 'scan_left': None, 'pre_alert': '',
            'centroid': {'cx': cx, 'cy': cy, 'cz': cz},
            'height': round(CEILING_H - cy, 2), 'n_pts': 8, 'dop_std': round(ds, 3),
            'zone_state': {z: ('ALERT' if (z == RADAR_ZONE and fallen) else 'NORMAL')
                           for z in ZONE_IDS},
            'points': [{'x': cx + np.random.normal(0, sx),
                        'y': cy + np.random.normal(0, sy),
                        'z': cz + np.random.normal(0, sz), 'i': 20.0} for _ in range(8)],
            'power': {'curr': float(np.random.normal(1.0, 0.04)),
                      'volt': float(np.random.normal(220.0, 0.4)), 'src': 'sim'},
            'breaker': {'state': {z: ('TRIPPED' if (z == RADAR_ZONE and fallen)
                                      else 'ON') for z in ZONE_IDS},
                        'reason': {}},
            'cz': list(self.hist['cz']), 'ds': list(self.hist['ds']),
            'sc': list(self.hist['sc']),
            'logs': [], 'incidents': [], 'ev': ev,
            'cfg': {'N_WARMUP': 150, 'SCAN_SEC': 12.0, 'CEILING_H': CEILING_H},
        }
