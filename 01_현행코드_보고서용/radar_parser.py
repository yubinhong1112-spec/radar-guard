"""
radar_parser.py — IWR6843ISK-ODS 실시간 데이터 파싱
=====================================================
흐름:
  1. radar_guard.cfg → ttyUSB0 (config port) 전송
  2. ttyUSB1 (data port) 에서 TLV 바이너리 수신
  3. Point Cloud (x, y, z, doppler, intensity) 파싱
  4. stage1_filtered.json 저장 (파이프라인 입력 포맷)

실행 (젯슨 터미널):
    python3 ~/radar_parser.py

의존 패키지:
    python3 -m pip install pyserial --break-system-packages

포트 확인 (2026-06-29):
    ttyUSB0 = CLI/Config 포트 (115200)
    ttyUSB1 = Data 포트 (921600)
"""

import serial
import struct
import time
import json

# ── 포트 / 파일 경로 ────────────────────────────────────
CONFIG_PORT = '/dev/ttyUSB0'   # cfg 전송 (CLI)
DATA_PORT   = '/dev/ttyUSB1'   # Point Cloud 수신
CONFIG_BAUD = 115200
DATA_BAUD   = 921600

CFG_FILE    = '/home/project/radar_guard.cfg'
OUTPUT_FILE = '/home/project/stage1_filtered.json'

# ── TLV 타입 상수 ───────────────────────────────────────
# SDK 3.6.x xwr68xx_mmw_demo 기준:
TLV_DETECTED_POINTS    = 1   # Detected Points (float x,y,z,doppler × 16 bytes)
TLV_STATS              = 6   # Processing Stats — 무시
TLV_POINT_CLOUD_SIDE   = 7   # Side Info (uint16 snr, noise × 4 bytes)

MAGIC_WORD      = b'\x02\x01\x04\x03\x06\x05\x08\x07'
FRAME_HDR_SIZE  = 40   # magic(8) + 8×uint32


# ════════════════════════════════════════════════════════
# 1. cfg 전송
# ════════════════════════════════════════════════════════
def send_config(cfg_path: str, port: serial.Serial):
    print(f"\n[CFG] {cfg_path} 전송 중...")
    port.reset_input_buffer()
    with open(cfg_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('%'):
                continue
            port.write((line + '\n').encode())
            # sensorStop / flushCfg 는 더 긴 대기
            if line.startswith('sensorStop') or line.startswith('flushCfg'):
                time.sleep(0.5)
            else:
                time.sleep(0.1)
            resp = port.read(port.in_waiting or 64).decode(errors='ignore').strip()
            print(f"  {line:<45}  ← {resp}")
    print("[CFG] 전송 완료\n")


# ════════════════════════════════════════════════════════
# 2. TLV 프레임 파싱
# ════════════════════════════════════════════════════════
def parse_frame(data: bytes):
    """
    반환값: {"frame_num": int, "points": [ {x,y,z,doppler,intensity}, ... ]}
    파싱 실패 시 None 반환
    """
    if len(data) < FRAME_HDR_SIZE:
        return None

    # 헤더 (magic 8바이트 이후 8×uint32)
    hdr = struct.unpack('<8I', data[8:40])
    # version, total_len, platform, frame_num, time_cpu, num_obj, num_tlv, sub_frame
    frame_num = hdr[3]
    num_tlv   = hdr[6]

    offset = FRAME_HDR_SIZE
    points    = []
    side_info = []

    for _ in range(num_tlv):
        if offset + 8 > len(data):
            break
        tlv_type, tlv_len = struct.unpack('<2I', data[offset:offset + 8])
        offset  += 8
        tlv_data = data[offset:offset + tlv_len]
        offset  += tlv_len

        # ── SDK 3.6.x : Type 1 (x, y, z, doppler float) ──
        if tlv_type == TLV_DETECTED_POINTS:
            n = tlv_len // 16          # 4 floats × 4 bytes
            for i in range(n):
                chunk = tlv_data[i*16:(i+1)*16]
                if len(chunk) < 16:
                    break              # 잘린 패킷 무시
                x, y, z, dop = struct.unpack('<4f', chunk)
                points.append({
                    "x":         round(float(x),   3),
                    "y":         round(float(y),   3),
                    "z":         round(float(z),   3),
                    "doppler":   round(float(dop), 4),
                    "intensity": 400.0,            # Type 7에서 갱신
                })

        # ── SDK 3.6.x : Type 7 (snr → intensity) ─────────
        elif tlv_type == TLV_POINT_CLOUD_SIDE:
            n = tlv_len // 4           # 2 shorts × 2 bytes
            for i in range(n):
                chunk4 = tlv_data[i*4:(i+1)*4]
                if len(chunk4) < 4:
                    break              # 잘린 패킷 무시
                snr, _ = struct.unpack('<2H', chunk4)
                side_info.append(snr * 0.1)        # 0.1 dB 단위

        # ── Type 6 = Stats, 무시 ───────────────────────────
        elif tlv_type == TLV_STATS:
            pass

    # SNR → intensity 반영 (Type 6 + Type 7 함께 온 경우)
    for i, snr in enumerate(side_info):
        if i < len(points):
            points[i]["intensity"] = round(snr * 10 + 300, 1)  # 300~500 범위 근사

    return {"frame_num": frame_num, "points": points}


# ════════════════════════════════════════════════════════
# 3. 메인 수신 루프
# ════════════════════════════════════════════════════════
def main():
    # 두 포트를 동시에 열어야 데이터가 끊기지 않음
    cfg_port  = serial.Serial(CONFIG_PORT, CONFIG_BAUD, timeout=2)
    data_port = serial.Serial(DATA_PORT,   DATA_BAUD,   timeout=0.1)

    send_config(CFG_FILE, cfg_port)

    print(f"[DATA] {DATA_PORT} 수신 시작... (Ctrl+C 로 종료 및 저장)")
    buf     = b''
    n_saved = 0

    # JSONL 스트림: 시작 시 파일 초기화(w) 후, 프레임마다 한 줄씩 append.
    # 전체 파일을 다시 쓰지 않으므로 파일이 커져도 저장 비용이 일정하다.
    out_f = open(OUTPUT_FILE, 'w', encoding='utf-8')

    port = data_port
    try:
        try:
            total_raw = 0
            last_report = time.time()
            while True:
                chunk = port.read(4096)
                if chunk:
                    buf += chunk
                    total_raw += len(chunk)

                # 2초마다 raw 수신량 출력 (디버그)
                now = time.time()
                if now - last_report >= 2.0:
                    print(f"  [RAW] 누적 {total_raw}바이트 수신, buf={len(buf)}바이트")
                    last_report = now

                # magic word 기준으로 프레임 분리
                while True:
                    idx = buf.find(MAGIC_WORD)
                    if idx == -1:
                        buf = buf[-8:] if len(buf) >= 8 else buf
                        break
                    buf = buf[idx:]                         # magic word 앞 쓰레기 제거

                    if len(buf) < FRAME_HDR_SIZE:
                        break

                    total_len = struct.unpack('<I', buf[12:16])[0]
                    if len(buf) < total_len:
                        break                               # 아직 전체 프레임 미수신

                    frame_data = buf[:total_len]
                    buf        = buf[total_len:]

                    try:
                        result = parse_frame(frame_data)
                    except Exception as e:
                        print(f"  [SKIP] 파싱 오류 무시: {e}")
                        result = None

                    if result is not None:
                        n = len(result["points"])
                        print(f"  Frame #{result['frame_num']:4d} | {n:2d} points")
                        # 한 프레임 = 한 줄 (JSONL). 전체 재작성 안 함 → UI freeze 방지.
                        rec = {"frame_num": result["frame_num"],
                               "t":         time.time(),
                               "points":    result["points"]}
                        out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        out_f.flush()
                        n_saved += 1

        except KeyboardInterrupt:
            print("\n[STOP] 종료 중...")

    finally:
        out_f.close()
        cfg_port.close()
        data_port.close()

    print(f"\n✅ 저장 완료: {OUTPUT_FILE}  ({n_saved} frames, JSONL)")


if __name__ == '__main__':
    main()
