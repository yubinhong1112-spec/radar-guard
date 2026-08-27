# -*- coding: utf-8 -*-
"""v1 에서 고쳤던 결함이 v2 에 재발했는지 런타임으로 검사한다."""
import os, sys, time, traceback
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
# ⚠ [8/02 실측] offscreen 플러그인이 번들 Qt5/lib/fonts 를 못 찾아 families()=0 →
#   resolve_font() 가 매칭 못 하고 초기값이 유지되며 합성 폰트 메트릭이 글자 폭을
#   부풀려 허위 잘림을 만든다. 근거: ① offscreen families 0 / 실제 창 349
#   ② QT_QPA_FONTDIR 지정 시 offscreen 도 0→251, 잘림 9→0건 ③ 실제 창(Noto Sans KR)
#   에서 잘림 0건(스크린샷 확인). 젯슨(Linux)에서 돌 수 있으므로 win32 에서만 설정한다.
if sys.platform == 'win32':
    os.environ.setdefault(
        'QT_QPA_FONTDIR',
        os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'))
sys.argv=['console_ui.py']
import console_ui as ui
import radar_core as core
from PyQt5 import QtWidgets, QtGui, QtCore

OK, NG = [], []
def check(name, cond, detail=''):
    (OK if cond else NG).append(f'{name}' + (f'  → {detail}' if detail else ''))

app, w, link = ui.build_app([])
w.resize(1440,900); w.show()

def pump(n=10):
    for _ in range(n):
        w.on_packet(w.demo_src.read()); app.processEvents()

# ── 0. 접힌 메뉴·원래 사이드바·SOP 폭 전환이 서로 겹치지 않는가 ──
app.processEvents()
closed_width = w.stack.width()
check('0a. 사이드바 기본 숨김 · 햄버거 표시',
      not w.nav.isVisible() and w.menu_gutter.isVisible()
      and w.menu_btn.isVisible())
w._toggle_nav(); app.processEvents()
check('0b. 열린 메뉴는 원래 사이드바 레이아웃 복원',
      w.nav.isVisible() and not w.menu_gutter.isVisible()
      and w.stack.width() == w.width() - w.NAV_WIDTH,
      f'closed={closed_width} open={w.stack.width()}px')
w.drawer.open_at(0); app.processEvents()
check('0c. 경보 드로어도 햄버거 레일 64px 유지',
      w.stack.width() == w.width() - w.menu_gutter.width()
      and w.drawer._extra_width == w.NAV_WIDTH - w.menu_gutter.width(),
      f'stack={w.stack.width()} drawer+={w.drawer._extra_width}')
w.drawer.close_drawer(); app.processEvents()
check('0c-2. 조치 확인 뒤 사이드바 자동 복원 없음',
      not w.nav.isVisible() and w.menu_gutter.isVisible()
      and w.stack.width() == w.width() - w.menu_gutter.width(),
      f'stack={w.stack.width()} nav={w.nav.isVisible()}')
check('0d. 안전 질문은 사건별 RAG로 라우팅',
      w.assistant._event_for('낙상 조치 근거는?') == 'fall_detected')
check('0e. 핵심 시스템 질문은 검증된 명세로 즉시 응답',
      '개인정보' in w.assistant._local_answer('왜 카메라 대신 레이더를 쓰나요?'))
check('0f. 인사·도움말은 LLM 없이 즉시 응답',
      '안녕하세요' in w.assistant._local_answer('안녕')
      and w.assistant._is_smalltalk('도움말'))
check('0g. 대화 말풍선은 사용자·AI 좌우 구분',
      'width="18%"' in w.assistant._bubble('질문', True, '나')
      and w.assistant._bubble('질문', True, '나')
      != w.assistant._bubble('답변', False, 'AI'))
check('0h. 입력창 Enter가 추천 질문 버튼을 함께 누르지 않음',
      all(not b.autoDefault() and not b.isDefault()
          for b in w.assistant.findChildren(QtWidgets.QPushButton)))
brief = w.assistant._local_answer('현재 상태 알려줘')
check('0i. 챗봇 현재 브리핑은 수신·경보·재실·전력·다음 조치를 함께 표시',
      all(word in brief for word in ('젯슨', '경보', '작업자', '차단', '현재 단계')),
      brief)
w.assistant._busy = True
w.assistant.ask('등록되지 않은 임의 질문')
check('0j. AI 요청 중 추가 생성 요청을 쌓지 않음',
      w.assistant._busy and '이전 질문의 근거를 확인 중' in w.assistant.log.toPlainText())
w.assistant._busy = False

# ── 1. confirm() 버튼이 다크테마에서 보이는가 (v1 7/31) ──
d_txt = []
orig = QtWidgets.QDialog.exec_
def fake_exec(self):
    for b in self.findChildren(QtWidgets.QPushButton):
        d_txt.append((b.text(), b.styleSheet()))
    return QtWidgets.QDialog.Rejected
QtWidgets.QDialog.exec_ = fake_exec
core.confirm(w, 'T', 'body', yes='해소 확인', no='취소', danger=True)
QtWidgets.QDialog.exec_ = orig
check('1. confirm 버튼 한글 + 명시적 스타일',
      any(t == '해소 확인' for t,_ in d_txt) and all(s for _,s in d_txt),
      f'{[t for t,_ in d_txt]}')

# ── 2. link.age() 가 센티넬(1e9) 을 UI 로 흘리지 않는가 ──
lk = core.RadarLink('127.0.0.1')
check('2. age() 미수신 시 None', lk.age() is None, repr(lk.age()))

# ── 3. SOP 검색 질의가 한글인가 ──
q = core.SOP_QUERY.get('fall_detected','')
check('3. SOP 질의 한글', any('가' <= c <= '힣' for c in q), q[:30])
check('3-1. 낙상 SOP가 응급처치 원문 우선',
      '응급처치' in core.SOP_RESPONSE_SOURCE['fall_detected']['03_낙상_응급처치'],
      core.SOP_RESPONSE_SOURCE['fall_detected'])
check('3-2. 정지형 감전 SOP가 2021 응급처치 원문 우선',
      '산업재해 형태별 응급처치' in
      core.SOP_RESPONSE_SOURCE['stationary_anomaly']['01_감전_LOTO'],
      core.SOP_RESPONSE_SOURCE['stationary_anomaly'])

# ── 4. LLM 마크다운 별표 제거 ──
h = core.md_to_html('**굵게** 그리고 *기울임*\n- 불릿')
check('4. 마크다운 → HTML', '*' not in h and '<b>' in h, h[:50])

# ── 5. 미설치 구역을 '투입'으로 칠하지 않는가 ──
pump(20)
w.dash.refresh(); app.processEvents()
plan = w.dash.plan
import facility as fac
check('5. 미설치 구역을 감시중으로 칠하지 않음',
      all(not plan.state[z]['live'] for z in ('B', 'C')),
      {z: plan.state[z]['live'] for z in ('B', 'C')})
check('6. 감지영역은 레이더 있는 구역만',
      fac.coverage(ui.RADAR_ZONE) is not None
      and all(fac.coverage(z) is None for z in ('B', 'C')))
check('7. 작업자 점은 레이더 설치 구역에만',
      plan.worker is not None and plan.worker[0] == ui.RADAR_ZONE,
      f'{plan.worker[0] if plan.worker else None}')

# ── 8. 링크 끊김인데 '이상 없음' 이라고 하지 않는가 (v1 최대 결함) ──
w2 = ui.ConsoleV2(core.RadarLink('127.0.0.1'), demo=False)
w2.resize(1440,900); w2.show()
w2._navigate(ui.PG_MON); w2.tick_ui(); app.processEvents()
t = w2.monitor.h_t.text()
check('8. 미연결 시 "이상 없음" 금지', t != '이상 없음', f'표시="{t}"')
check('9. 미연결 시 무경보 시간 숨김', w2.monitor.h_quiet.text() == '',
      w2.monitor.h_quiet.text())
check('10. 미연결 시 계측 —', w2.monitor.tiles['height'].val.text() == '—')
check('11. 미연결 시 장면 베일', w2.monitor.scene.veil.isVisible())
# 기준 미학습(READY) 인데 '이상 없음' 이라고 하지 않는가
w2.pkt = {'phase': ui.PH_READY}
w2.link.last_rx = time.time()          # 링크는 살아 있게
w2.tick_ui(); app.processEvents()
check('12. 기준 미학습 시 "이상 없음" 금지',
      w2.monitor.h_t.text() != '이상 없음', f'표시="{w2.monitor.h_t.text()}"')
w2.close()

# ── 13. 판정 임계값이 편집 불가(읽기 전용)인가 ──
ro = [not isinstance(r,(QtWidgets.QLineEdit,QtWidgets.QSpinBox,QtWidgets.QDoubleSpinBox)) for r in w.cfg.thr_rows.values()]
check('13. 판정 임계값 읽기 전용', all(ro), f'{ro}')

# ── 14. 경보 상태기계 ──
#   ⚠ 위젯 가시성을 보려면 감시 화면이 떠 있어야 한다. 자동 전환은 1.5초
#     지연이므로 여기서는 직접 이동해 둔다(전환 자체는 19b 에서 검사).
w.begin_session({'zone': ui.RADAR_ZONE, 'shift': '주간조', 'operator': '홍유빈'})
app.processEvents()
w.demo_src.t0 = time.time()-7; pump(20); w.tick_ui()
check('14. 경보 발생 → UNACK', w.alarm == ui.ST_UNACK, w.alarm)
check('14a. 경보 발생 시 챗봇 기록에 자동 브리핑·다음 조치 추가',
      '경보 진행 중' in w.assistant.log.toPlainText()
      and '다음 조치' in w.assistant.log.toPlainText())
check('14b. 다음 조치 질문은 LLM 없이 확정 즉시조치로 응답',
      w.assistant._local_answer('지금 뭐 해야 해?').startswith('현재 단계:'))
b = w.monitor.b_right.text()
w.do_ack()
check('15. 확인함 = ACK (경보 유지)',
      w.alarm == ui.ST_ACK and w.monitor.alert_box.isVisible())
pump(6)
check('16. 자동 해제 없음 (경보 지속)',
      w.alarm == ui.ST_ACK and w.monitor.banner.isVisible())

# ── 17. 젯슨 시계가 아니라 노트북 수신 시각 기준 경과 ──
check('17. 경과시간 = 노트북 기준', abs(w.alert_t0 - time.time()) < 30,
      f'alert_t0 diff={time.time()-w.alert_t0:.1f}s')

# 같은 사건 안에 낙상이 추가되면 기존 설비 사고를 지우지 않고 화면을 갱신한다.
_today, _t0, _eid = w.today, w.alert_t0, w.last_ev_id
_compound = dict(w.pkt.get('ev') or {})
_compound.update({'active': True, 'id': _eid, 'rev': w.last_ev_rev + 1,
                  'type': 'fall_detected',
                  'types': ['overcurrent', 'fall_detected'],
                  'items': {'overcurrent': {'sev': 'critical', 'conf': 1.0},
                            'fall_detected': {'sev': 'critical', 'conf': 0.98}},
                  'sev': 'critical', 'zone': ui.RADAR_ZONE})
w._pump_state({'ev': _compound}); app.processEvents()
check('17a. 설비 이상 + 낙상 복합 critical 동시 표시',
      '과전류' in w.monitor.a_kind.text() and '낙상' in w.monitor.a_kind.text(),
      w.monitor.a_kind.text())
check('17b. 복합 사건 revision은 새 사건 건수·경과시간을 만들지 않음',
      w.today == _today and w.alert_t0 == _t0,
      f'today={w.today} t0_same={w.alert_t0 == _t0}')
check('17c. 복합 사건 하위 등급을 텍스트로 함께 표시',
      w.monitor.a_kind.text().count('[위험]') == 2,
      w.monitor.a_kind.text())

# ── 18. 미확인 경보 중 화면 이탈 차단 ──
w.clear_alarm(); w.last_ev_id = 0; w.alarm = ui.ST_NORMAL
w.demo_src.t0 = time.time()-7; pump(20)
nav = [b.isEnabled() for b in w.nav.buttons]
check('18. UNACK 중 네비 잠금', nav == [False,True,False,False,False,False], f'{nav}')
w._navigate(ui.PG_DASH); pump(3)
check('19a. 자동 전환 전에는 평면도를 보여 준다',
      w.stack.currentIndex() == ui.PG_DASH, f'page={w.stack.currentIndex()}')
_t0 = time.time()
while time.time() - _t0 < (ui.AUTO_NAV_MS / 1000.0 + 1.0):
    w.on_packet(w.demo_src.read()); app.processEvents(); time.sleep(0.02)
check(f'19b. {ui.AUTO_NAV_MS}ms 뒤 감시 화면으로 자동 전환',
      w.stack.currentIndex() == ui.PG_MON, f'page={w.stack.currentIndex()}')

# ── 20. 전력 복구는 체크 3개 + 확인 ──
r = core.RestorePopup(w)
check('20. 전력복구 체크 3개 · 기본 비활성',
      len(r.checks) == 3 and not r.ok.isEnabled())
for c in r.checks: c.setChecked(True)
check('21. 체크 완료 시에만 활성', r.ok.isEnabled())

# ── 22. 폰트 8pt 금지 (radar_common 지침) ──
small = []
for wd in w.findChildren(QtWidgets.QWidget):
    if isinstance(wd,(QtWidgets.QLabel,QtWidgets.QPushButton)) and wd.isVisible():
        ps = wd.font().pointSize()
        if 0 < ps < 9: small.append((type(wd).__name__, wd.text()[:16], ps))
check('22. 8pt 미만 텍스트 없음', not small, f'{small[:4]}')

# ── 23. full 패킷 히스토리 이어붙이기 ──
w.pkt = dict(w.pkt); w.pkt['cz'] = [1,2,3]
p = w.demo_src.read(); p.pop('cz', None); p['full'] = False
w.on_packet(p)
check('23. full 아닌 패킷의 히스토리 승계', w.pkt.get('cz') == [1,2,3], w.pkt.get('cz'))

# ── 24. 구역 일관성 ──
d = w.demo_src.read()
alert_z = [z for z,v in d['zone_state'].items() if v=='ALERT']
trip_z  = [z for z,v in d['breaker']['state'].items() if v!='ON']
check('24. 데모 구역 = RADAR_ZONE',
      d['ev']['zone']==ui.RADAR_ZONE and alert_z==[ui.RADAR_ZONE] and trip_z==[ui.RADAR_ZONE],
      f"ev={d['ev']['zone']} alert={alert_z} trip={trip_z}")

# ── 25. 자동조치 문구가 본 패널/드로어에서 일치 ──
check('25. 자동조치 문구 일치',
      w.monitor.auto_lb.text() == w.drawer.sop.done.text())

# ── 26. 조치 가이드가 별도 OS 창이 아닌가 ──
check('26. 조치 가이드 = 인앱 위젯', not w.drawer.isWindow() and w.drawer.parent() is not None)

# ── 27. 빨강 사용 제한 (경보 중에도 계측은 흰색) ──
w.demo_src.t0 = time.time()-7; pump(10); w.tick_ui()
col = w.monitor.tiles['height'].val.styleSheet()
check('27. 경보 중 계측 수치는 빨강 아님', ui.RED not in col, col)

# ── 28. 경보 첫 패킷부터 사람 형상을 숨기는가 ──
w.clear_alarm(); w.last_ev_id = 0; w._navigate(ui.PG_MON)
# 100ms 데모 타이머가 검사 패킷 사이에 끼면 push 호출 수가 2회가 되어
# 첫 패킷 검사가 비결정적이므로, 이 한 호출만 직접 주입한다.
w.demo_timer.stop()
first = w.demo_src.read()
first['ev'] = dict(first.get('ev') or {}, active=True, id=999999,
                   type='fall_detected', sev='critical', zone=ui.RADAR_ZONE,
                   conf=0.9)
hide_args = []
orig_push = w.scene.push
def capture_push(pkt, sev='normal', hide_shape=False):
    hide_args.append(hide_shape)
    return orig_push(pkt, sev, hide_shape)
w.scene.push = capture_push
w.on_packet(first); app.processEvents()
w.scene.push = orig_push
w.demo_timer.start(100)
check('28. 경보 첫 패킷부터 사람 형상 숨김', hide_args == [True], hide_args)

# ══════════════════════════════════════════════════════════════════════
# 인체 도식 (8/01 추가) — 도식이 '측정한 것' 과 어긋나지 않는지
# ══════════════════════════════════════════════════════════════════════
import numpy as _np
w.clear_alarm(); w.last_ev_id = 0
w._navigate(ui.PG_MON)
w.demo_src.t0 = time.time()          # 서 있는 구간
w.scene.track.pose.clear()
pump(40)
ps = w.scene.track.pose.estimate()
assert ps is not None, '자세 추정 실패 — 데모 데이터 확인'
seg = core.Track3D.stick2d(ps)
cl = w.scene.track.pose.cloud()
check('29. 서 있는 도식의 발이 바닥에 닿음',
      abs(seg[:, 1].min()) < 0.12, f'발 높이 {seg[:,1].min():+.2f} m')
check('30. 머리 추정점이 몸 중심보다 위',
      ps['head'][1] < ps['center'][1],
      f"머리 {core.CEILING_H-ps['head'][1]:.2f} m / 중심 {core.CEILING_H-ps['center'][1]:.2f} m")
ov = min(seg[:,0].max(), cl[:,0].max()) - max(seg[:,0].min(), cl[:,0].min())
check('31. 도식이 점군 위에 얹힘(서 있음)', ov > 0.15, f'겹침 {ov:.2f} m')

w.demo_src.t0 = time.time()-7        # 누운 구간
pump(40)
pl = w.scene.track.pose.estimate()
segl = core.Track3D.stick2d(pl)
cll = w.scene.track.pose.cloud()
ovl = min(segl[:,0].max(), cll[:,0].max()) - max(segl[:,0].min(), cll[:,0].min())
check('32. 도식이 점군 위에 얹힘(누움)', ovl > 0.5, f'겹침 {ovl:.2f} m')
check('33. 누운 도식이 바닥 근처', segl[:,1].max() < 0.9,
      f'최고 {segl[:,1].max():.2f} m')
prev, flips = None, 0
for _ in range(120):
    w.on_packet(w.demo_src.read()); app.processEvents()
    r = w.scene.track.pose.estimate()
    if r:
        if prev is not None and float(_np.dot(r['axis'], prev)) < 0:
            flips += 1
        prev = _np.array(r['axis'])
check('34. 누운 상태 축 부호 반전 없음', flips == 0, f'{flips}회/120프레임')
check('35. 도식에 관절 자유도가 없음(고정 비율)',
      set(core.STICK) == {'head_t','head_r','neck','shoulder','hip',
                          'sh_w','hand_t','hand_w','foot_t','foot_w'})
check('35a. 정상 누움도 3D 뼈대 대신 표면 마네킹 표시',
      len(getattr(w.scene.track.cap, 'pos', ())) == 0
      and w.scene.track.body.visible())

# ── 36~38. 실제 경보 사고 포즈·설비 위험 표시 ──
event_pose = {}
for et, want in (('fall_detected', 'fall'),
                 ('electric_shock_risk', 'electric'),
                 ('pinching', 'pinching')):
    w.alert = {'type': et, 'types': [et], 'evidence': {}}
    event_pose[et] = (w._incident_visual() or {}).get('kind')
check('36. 실제 사고 경보 3종이 OBJ 포즈로 연결',
      event_pose == {'fall_detected': 'fall',
                     'electric_shock_risk': 'electric',
                     'pinching': 'pinching'}, event_pose)

meshes = [core._incident_mannequin_mesh(k)[0]
          for k in ('fall', 'electric', 'pinching')]
check('37. 사고 포즈 3종의 관절 형상이 서로 다름',
      all(meshes[i].shape != meshes[j].shape
          or not _np.allclose(meshes[i], meshes[j])
          for i in range(3) for j in range(i + 1, 3)))

geometry = core._facility_scene_geometry()
check('38. 폐기한 주황·보라 소영역이 설비 형상에서 제거',
      len(geometry) == 5, f'형상 그룹 {len(geometry)}개')

roi_normal = core.Track3D.roi_color('normal')
roi_warning = core.Track3D.roi_color('warning')
roi_critical = core.Track3D.roi_color('critical')
check('39. ROI가 3D 장면에 복원되고 정상은 파란 경계로 표시',
      (w.scene.track.gl is None or hasattr(w.scene.track, 'roi'))
      and roi_normal[2] > roi_normal[0] and roi_normal[2] > roi_normal[1],
      f'normal={roi_normal}')
check('40. ROI가 기존 경보 등급의 주황·빨강을 그대로 사용',
      roi_warning[:3] == core.pg.glColor(core.sev_color('warning'))[:3]
      and roi_critical[:3] == core.pg.glColor(core.sev_color('critical'))[:3],
      f'warning={roi_warning} critical={roi_critical}')

print('\n' + '='*62)
print(f'통과 {len(OK)} / 실패 {len(NG)}')
print('='*62)
for x in OK: print('  OK  ', x)
if NG:
    print()
    for x in NG: print('  NG  ', x)
