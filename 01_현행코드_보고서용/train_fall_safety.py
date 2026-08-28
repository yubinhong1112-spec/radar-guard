# -*- coding: utf-8 -*-
"""사람 단위 LOSO로 낙상 RandomForest를 학습한다.

실행 환경: 내 PC PowerShell 또는 젯슨 터미널
  python train_fall_safety.py --data <8/13.jsonl> <8/14.jsonl> --output fall_classifier.joblib
  python train_fall_safety.py --data <3초.jsonl> --empty <빈방.jsonl> --window 30 \
      --exclude A:crouch:2 --exclude E:fall:10 --output fall_classifier_hybrid30.joblib

운영점은 LOSO 예측에서 wave 오탐이 0건인 가장 낮은 임계값이다.
fast_sit은 실제 표본이 없고 사용자 결정으로 다음 지시까지 제외한다.
"""
import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import LeaveOneGroupOut

FEATURES = ['ds_max', 'ds_mean', 'ds_first', 'ds_last', 'impulse', 'ds_broad',
            'settle_ratio', 'dop_peaks', 'zvel_sign_changes', 'h_drop',
            'end_low_ratio', 'net_drop_ratio', 'max_drop_ratio', 'horiz_range',
            'horiz_disp', 'n_peak_ratio', 'n_cv', 'n_trend', 'mean_dop_abs']
USED_LABELS = {'fall', 'crouch', 'wave', 'walk', 'normal'}
HEIGHT_BG_GRID = 0.10
HEIGHT_BG_OCCUPANCY = 0.80
HEIGHT_Y_PERCENTILE = 70.0  # 천장 좌표 y의 상위 30% = 바닥에서 낮은 쪽 30%
FRAME_HALF = 0.72
ROI_Y = (0.50, 2.55)


def _roi_points(points):
    return [p for p in points
            if (-FRAME_HALF <= p['x'] <= FRAME_HALF
                and ROI_Y[0] <= p['y'] <= ROI_Y[1]
                and -FRAME_HALF <= p['z'] <= FRAME_HALF)]


def _voxel(point):
    return (round(point['x'] / HEIGHT_BG_GRID),
            round(point['y'] / HEIGHT_BG_GRID),
            round(point['z'] / HEIGHT_BG_GRID))


def build_height_background(path):
    """빈방 프레임 80% 이상에서 반복된 10 cm 복셀만 높이용 배경으로 고정한다."""
    with open(path, encoding='utf-8') as fh:
        row = json.loads(next(fh))
    frames = row.get('frames') or []
    if not frames:
        raise ValueError('빈방 파일에 frames가 없습니다.')
    counts = Counter()
    for frame in frames:
        counts.update({_voxel(p) for p in _roi_points(frame.get('points') or [])})
    return {voxel for voxel, hits in counts.items()
            if hits / len(frames) >= HEIGHT_BG_OCCUPANCY}


def apply_height_background(sample, background):
    """도플러·점수는 원본을 두고 위치만 10 cm 배경 제거 후 낮은 분포로 바꾼다."""
    frames = []
    for original in sample['frames']:
        frame = dict(original)
        points = [p for p in _roi_points(original.get('raw_pts') or [])
                  if _voxel(p) not in background]
        if points:
            frame['cx'] = float(np.median([p['x'] for p in points]))
            frame['cy'] = float(np.percentile([p['y'] for p in points],
                                               HEIGHT_Y_PERCENTILE))
            frame['cz'] = float(np.median([p['z'] for p in points]))
        # 전부 제거된 프레임은 실시간에서도 가능한 원본 centroid로 즉시 대체한다.
        frames.append(frame)
    return frames


def extract(frames):
    fr = [f for f in frames if f['n'] > 0]
    if len(fr) < 4:
        return None
    cx = np.array([f['cx'] for f in fr]); cy = np.array([f['cy'] for f in fr])
    cz = np.array([f['cz'] for f in fr]); ds = np.array([f['dop_std'] for f in fr])
    n = np.array([f['n'] for f in fr], dtype=float)
    dop = np.array([f.get('dop_mean', 0.0) for f in fr])
    half = max(1, len(ds) // 2); first = ds[:half].mean(); last = ds[half:].mean()
    zvel = np.zeros(len(fr)); zvel[1:] = cy[:-1] - cy[1:]
    valid = zvel[np.abs(zvel) > 0.05]
    zsc = int(np.sum(np.diff(np.sign(valid)) != 0)) if len(valid) > 2 else 0
    peaks = sum(ds[i] >= 0.6 and ds[i] >= ds[i-1] and ds[i] > ds[i+1]
                for i in range(1, len(ds) - 1))
    pk = int(np.argmax(ds)); span = float(cy.max() - cy.min()) + 1e-6
    start = float(cy[:3].mean()); end = float(cy[-3:].mean())
    nm = float(n.mean()) + 1e-6; nh = max(1, len(n) // 2)
    return [float(ds.max()), float(ds.mean()), float(first), float(last),
            float(ds.max() / max(0.15, first)), int((ds >= 0.8).sum()),
            float(last / (ds.max() + 1e-6)), peaks, zsc, float(cy.max()-cy.min()),
            (end-float(cy.min()))/span, (end-start)/span,
            (float(cy.max())-start)/span,
            float(np.hypot(cx.max()-cx.min(), cz.max()-cz.min())),
            float(np.hypot(cx[pk:].mean()-cx[:max(1, pk)].mean(),
                           cz[pk:].mean()-cz[:max(1, pk)].mean())),
            float(n.max())/nm, float(n.std())/nm,
            (float(n[nh:].mean())-float(n[:nh].mean()))/nm,
            float(np.abs(dop).mean())]


def _parse_exclusions(values):
    parsed = set()
    for value in values:
        try:
            person, label, occurrence = value.split(':')
            parsed.add((person.upper(), label, int(occurrence)))
        except ValueError as exc:
            raise ValueError(f'제외 형식은 PERSON:LABEL:COUNT 입니다: {value}') from exc
    return parsed


def load(paths, background=None, window=None, exclusions=()):
    out = []
    occurrences = defaultdict(int)
    for path in paths:
        source = os.path.basename(path)
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                row = json.loads(line)
                label = row['label']
                # 8/13 A의 fast_sit 6건은 현장 메모상 실제 crouch였다.
                if ('20260813' in source or '0813' in source) \
                        and row.get('person') == 'A' and label == 'fast_sit':
                    label = 'crouch'
                if label not in USED_LABELS:
                    continue
                frames = row.get('frames') or []
                key = (str(row.get('person', '')).upper(), label)
                occurrences[key] += 1
                if (key[0], key[1], occurrences[key]) in exclusions:
                    continue
                if window is not None:
                    if len(frames) < window:
                        continue
                    frames = frames[-window:]
                if len(frames) < 4:
                    continue
                if window is None and frames[-1]['t'] - frames[0]['t'] > 2.5:
                    continue
                if background is not None:
                    row = dict(row)
                    row['frames'] = frames
                    frames = apply_height_background(row, background)
                feat = extract(frames)
                if feat is not None:
                    out.append((feat, label == 'fall', row['person'], label, source))
    return out


def make_model():
    return RandomForestClassifier(n_estimators=300, min_samples_leaf=8,
                                  max_features='sqrt', class_weight='balanced',
                                  random_state=42, n_jobs=-1)


def _sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', nargs='+', required=True)
    ap.add_argument('--empty', help='10 cm 높이 전용 배경맵을 만들 빈방 jsonl')
    ap.add_argument('--window', type=int, help='각 표본에서 사용할 마지막 프레임 수')
    ap.add_argument('--exclude', action='append', default=[],
                    help='제외할 표본 PERSON:LABEL:발생순번 (여러 번 지정 가능)')
    ap.add_argument('--output', default='fall_classifier.joblib')
    args = ap.parse_args()
    if bool(args.empty) != bool(args.window):
        ap.error('--empty와 --window는 함께 지정해야 합니다.')
    if args.empty and args.window != 30:
        ap.error('용도 분리 RF의 운영 창은 30프레임이어야 합니다.')

    background = build_height_background(args.empty) if args.empty else None
    exclusions = _parse_exclusions(args.exclude)
    rows = load(args.data, background, args.window, exclusions)
    if not rows:
        ap.error('학습 가능한 표본이 없습니다.')
    x = np.asarray([r[0] for r in rows]); y = np.asarray([r[1] for r in rows])
    groups = np.asarray([r[2] for r in rows]); labels = np.asarray([r[3] for r in rows])
    scores = np.zeros(len(y))
    for train, test in LeaveOneGroupOut().split(x, y, groups):
        model = make_model().fit(x[train], y[train])
        fall_col = list(model.classes_).index(True)
        scores[test] = model.predict_proba(x[test])[:, fall_col]

    wave = (~y) & (labels == 'wave')
    if not wave.any():
        raise RuntimeError('wave 표본이 없어 오탐 0건 운영점을 선택할 수 없습니다.')
    threshold = float(np.nextafter(scores[wave].max(), 1.0))
    pred = scores >= threshold
    metrics = {
        'tp': int((pred & y).sum()), 'fn': int((~pred & y).sum()),
        'fp': int((pred & ~y).sum()), 'tn': int((~pred & ~y).sum()),
        'wave_fp': int((pred & wave).sum()),
        'per_label': {label: {'positive': int((pred & (labels == label)).sum()),
                              'total': int((labels == label).sum())}
                      for label in sorted(set(labels))},
        'per_person': {person: {'fall_hit': int((pred & y & (groups == person)).sum()),
                                'fall_total': int((y & (groups == person)).sum()),
                                'false_positive': int((pred & ~y & (groups == person)).sum())}
                       for person in sorted(set(groups))},
    }
    # 기존 sender의 RF veto 경로도 안전하게 읽을 수 있도록 최종 클래스명은 문자열로 저장한다.
    final_y = np.where(y, 'fall', 'normal')
    model = make_model().fit(x, final_y)
    hashes = {}
    for path in args.data:
        hashes[os.path.basename(path)] = _sha256(path)
    # ⚠ [8/19] 배치 학습은 n_jobs=-1 이 빠르지만, 이 값이 그대로 저장되면
    #   런타임(jetson_sender.py)의 샘플 1개 추론에서 디스패치 비용이 연산을 압도한다.
    #   저장 직전에만 1로 정규화한다 — 학습 자체는 계속 -1 로 돈다.
    model.n_jobs = 1
    joblib.dump({'model': model, 'features': FEATURES, 'threshold': threshold,
                 'threshold_policy': 'LOSO max recall with wave FP=0',
                 'excluded_labels': ['fast_sit'], 'metrics': metrics,
                 'samples': {'fall': int(y.sum()), 'negative': int((~y).sum())},
                 'source_sha256': hashes,
                 'window_frames': args.window,
                 'feature_mode': ('raw_motion_height_bg10_low30'
                                  if background is not None else 'raw'),
                 'height_background': sorted(background) if background is not None else [],
                 'height_background_config': {
                     'grid_m': HEIGHT_BG_GRID,
                     'min_occupancy': HEIGHT_BG_OCCUPANCY,
                     'y_percentile': HEIGHT_Y_PERCENTILE,
                     'empty_sha256': _sha256(args.empty) if args.empty else None,
                 },
                 'excluded_samples': sorted(args.exclude)}, args.output)
    print(f'threshold={threshold:.6f} TP={metrics["tp"]} FN={metrics["fn"]} '
          f'FP={metrics["fp"]} TN={metrics["tn"]} wave_FP={metrics["wave_fp"]}')
    print(json.dumps(metrics['per_person'], ensure_ascii=False))
    print(args.output)


if __name__ == '__main__':
    main()
