import sys, os, time, traceback
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
if sys.platform == 'win32':
    os.environ.setdefault(
        'QT_QPA_FONTDIR',
        os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'))
sys.argv=['console_ui.py']
import console_ui as v2
from PyQt5 import QtCore, QtGui, QtWidgets

errors=[]
sys.excepthook=lambda t,v,tb: errors.append(''.join(traceback.format_exception(t,v,tb)))
app,w,link = v2.build_app([])

FAIL=[]
def check_clipping(root, label):
    """QLabel/QPushButton 의 실제 폭이 텍스트 폭보다 좁으면 잘린 것."""
    bad=[]
    for wid in root.findChildren(QtWidgets.QWidget):
        if not wid.isVisible() or wid.width()<=1: continue
        # 닫힌/애니메이션 중인 드로어 안은 폭이 0 이라 측정 대상이 아니다
        anc=wid.parentWidget(); skip=False
        while anc is not None and anc is not root:
            if anc.width()<=1 or isinstance(anc,v2.ActionDrawer) and anc.maximumWidth()<v2.ActionDrawer.WIDTH:
                skip=True; break
            anc=anc.parentWidget()
        if skip: continue
        txt=None
        if isinstance(wid, QtWidgets.QLabel):
            if wid.wordWrap(): continue
            txt=wid.text()
        elif isinstance(wid, QtWidgets.QPushButton):
            txt=wid.text()
        if not txt or '<' in txt: continue
        need=QtGui.QFontMetrics(wid.font()).width(txt)
        have=wid.width()
        if isinstance(wid, QtWidgets.QPushButton): have-=28
        if need>have+1:
            bad.append((txt[:28], need, have))
    if bad:
        FAIL.append(f'[잘림] {label}: '+' | '.join(f'"{t}" {n}>{h}px' for t,n,h in bad))

def check_overlap(root,label):
    """같은 부모 안 형제 위젯의 사각형이 겹치면 레이아웃 붕괴."""
    bad=[]
    for parent in root.findChildren(QtWidgets.QWidget):
        kids=[c for c in parent.children()
              if isinstance(c,QtWidgets.QWidget) and c.isVisible()
              and not isinstance(c,(QtWidgets.QScrollBar,))
              and c.width()>1 and c.height()>1]
        for i in range(len(kids)):
            for j in range(i+1,len(kids)):
                a,b=kids[i].geometry(),kids[j].geometry()
                inter=a.intersected(b)
                if inter.width()>4 and inter.height()>4:
                    # 오버레이(veil, 드로어)는 의도된 것
                    if 'veil' in (kids[i].objectName()+kids[j].objectName()): continue
                    if isinstance(kids[i],QtWidgets.QLabel) or isinstance(kids[j],QtWidgets.QLabel):
                        bad.append((type(kids[i]).__name__+':'+(kids[i].text()[:14] if hasattr(kids[i],'text') else ''),
                                    type(kids[j]).__name__+':'+(kids[j].text()[:14] if hasattr(kids[j],'text') else '')))
    if bad:
        FAIL.append(f'[겹침] {label}: '+' | '.join(f'{a} ↔ {b}' for a,b in bad[:6]))

def settle(n=8):
    for _ in range(n):
        w.on_packet(w.demo_src.read()); app.processEvents()

for size in ((1440,900),(1366,900),(1280,800),(1200,800)):
    w.resize(*size); w.show(); app.processEvents()
    # 대시보드
    settle(); w.dash.refresh(); app.processEvents()
    check_clipping(w.dash, f'대시보드 {size}'); check_overlap(w.dash, f'대시보드 {size}')
    # 감시 (정상)
    w.demo_src.t0=time.time(); settle(20)
    w.begin_session({'zone':'A','shift':'주간조','operator':'홍유빈'})
    settle(10); w.tick_ui(); app.processEvents()
    check_clipping(w.monitor, f'감시-정상 {size}'); check_overlap(w.monitor, f'감시-정상 {size}')
    # 감시 (경보 + 드로어)
    w.demo_src.t0=time.time()-7.0; settle(20); w.tick_ui()
    w.drawer.open_at(0); app.processEvents(); settle(4)
    check_clipping(w.monitor, f'감시-경보 {size}'); check_overlap(w.monitor, f'감시-경보 {size}')
    assert w.alarm==v2.ST_UNACK, w.alarm
    locked=[b.isEnabled() for b in w.nav.buttons]
    if locked!=[False,True,False,False,False,False]:
        FAIL.append(f'[네비잠금] UNACK 중 잠금 상태 이상: {locked}')
    w.do_ack()
    if not all(b.isEnabled() for b in w.nav.buttons):
        FAIL.append('[네비잠금] ACK 후에도 잠김')
    w.clear_alarm(); app.processEvents()
    # 나머지 페이지
    for idx,name in ((v2.PG_LOG,'이벤트로그'),(v2.PG_SOP,'SOP'),(v2.PG_PREP,'현장준비'),(v2.PG_DASH,'대시보드복귀')):
        w._navigate(idx); app.processEvents()
        pg=w.stack.currentWidget()
        check_clipping(pg,f'{name} {size}'); check_overlap(pg,f'{name} {size}')
    w._navigate(v2.PG_MON)

# 상태기계 순서 검증
w.clear_alarm(); w.alarm=v2.ST_NORMAL; w.last_ev_id=0
seq=[]
w.demo_src.t0=time.time()-7.0
for _ in range(10):
    w.on_packet(w.demo_src.read()); seq.append(w.alarm)
assert seq[0]==v2.ST_UNACK and all(s==v2.ST_UNACK for s in seq), seq[:3]
w.do_ack(); assert w.alarm==v2.ST_ACK
w.demo_src.t0=time.time()          # 낙상 해제 → 젯슨이 먼저 해소
for _ in range(5): w.on_packet(w.demo_src.read())
assert w.alarm==v2.ST_NORMAL, w.alarm
print('상태기계 NORMAL→UNACK→ACK→(원격)NORMAL  OK')

# 데모 소스 구역 일관성
d=w.demo_src.read()
zs=set(k for k,vv in d['zone_state'].items() if vv=='ALERT')
bs=set(k for k,vv in d['breaker']['state'].items() if vv!='ON')
print('데모 구역 일관성: ev.zone=%s zone_state=%s breaker=%s' % (d['ev'].get('zone'), zs or '-', bs or '-'))

# 링크 없음(--live) 경로: stale 표시
w2=v2.ConsoleV2(v2.RadarLink('127.0.0.1'), demo=False)
w2.resize(1440,900); w2.show(); app.processEvents()
w2._navigate(v2.PG_MON); w2.tick_ui(); app.processEvents()
assert w2.monitor.scene.veil.isVisible(), 'stale 베일이 안 뜸'
print('링크 대기 stale 베일 OK')
w2.close()

print('\n=== 결과 ===')
print('예외:', len(errors))
for e in errors[:3]: print(e)
if FAIL:
    print('발견:', len(FAIL))
    for x in FAIL: print(' -', x)
else:
    print('잘림/겹침 0건')
