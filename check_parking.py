import os
import time
import requests
from datetime import datetime, timezone, timedelta

# ===== 설정 =====
CAR_NUMBER = os.environ.get('CAR_NUMBER') or '1989'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
API_URL = os.environ.get('API_URL') or 'http://yongparking.co.kr/include/DST/inc_find_car.dst'
PARKING_NAME = "용산구청 부설주차장"

CONFIRM_COUNT = 2

STATE_FILE = "last_state.txt"
EXIT_DONE_FILE = "exit_done.txt"
LAST_IN_FILE = "last_in.txt"
PENDING_FILE = "pending_out.txt"

def get_kst_now():
    return datetime.now(timezone(timedelta(hours=9)))

def read_file(path, default=""):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return default

def write_file(path, value):
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value))

def is_exit_done_today():
    return read_file(EXIT_DONE_FILE) == get_kst_now().strftime("%Y-%m-%d")

def check_parking():
    params = {"PROC_CMD": "FIND_00", "CAR_TYPE": "00", "CAR_NO": CAR_NUMBER}
    for attempt in range(1, 4):
        try:
            res = requests.get(API_URL, params=params, timeout=10)
            res.raise_for_status()
            result = res.text.strip()
            if result.startswith("OK"):
                parts = result.split("|")
                return True, (parts[1] if len(parts) > 1 else CAR_NUMBER)
            if result.startswith(("NO", "FAIL", "ERR")) or result == "":
                return False, ""
            if "<" in result[:20].lower():
                return None, "html_response"
            return False, ""
        except Exception as e:
            print(f"[API 조회 오류] {e}")
            time.sleep(3)
    return None, "request_failed"

def send_telegram(message):
    print("▶️ 텔레그램 발송 시도 중...")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ 오류: 텔레그램 토큰 또는 챗아이디가 누락되었습니다.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        if res.ok:
            print("✅ 텔레그램 발송 성공!")
            return True
        else:
            print(f"❌ 텔레그램 발송 실패 (상태코드 {res.status_code}): {res.text}")
            return False
    except Exception as e:
        print(f"❌ 텔레그램 네트워크 오류: {e}")
        return False

def main():
    now = get_kst_now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    weekday = now.weekday()
    
    print(f"[{now_str}] 파이썬 스크립트 실행 시작 (요일: {weekday})")
    
    # 1. 목요일은 무조건 조회 스킵
    if weekday == 3:
        print("목요일이므로 조회를 스킵합니다.")
        return
        
    # 2. 오늘 이미 최종 알림을 보냈다면 실행 안 함
    if is_exit_done_today():
        print("오늘 이미 최종 알림이 완료되어 스킵합니다.")
        return

    print(f"주차장 API 조회 중 (차량번호: {CAR_NUMBER})...")
    found, info = check_parking()
    
    if found is None:
        print("API 판정 불가로 종료합니다.")
        return

    print(f"조회 결과: {'주차 중' if found else '미발견'}")
    last_state = read_file(STATE_FILE, "UNKNOWN")
    print(f"이전 기록: {last_state}")

    # 3. [18:30 이후 첫 조회] 차량이 없는 경우 (GitHub 지연 고려 & 출차 로직과 충돌 방지)
    if (now.hour == 18 and now.minute >= 30) or (now.hour >= 19):
        if not found and last_state != "IN":
            print("18:30 이후 미조회 확인 -> 중단 알림 발송")
            send_telegram(
                f"🚫 <b>차량 미조회 알림</b>\n\n"
                f"차량번호: {CAR_NUMBER}\n"
                f"18:30 이후 주차장에 차량이 없습니다.\n"
                f"금일 조회를 중단합니다."
            )
            write_file(STATE_FILE, "OUT")
            write_file(PENDING_FILE, "0")
            write_file(EXIT_DONE_FILE, now.strftime("%Y-%m-%d"))
            return

    # 4. 주차 중 (IN)
    if found:
        write_file(LAST_IN_FILE, now_str)
        write_file(PENDING_FILE, "0")
        if last_state != "IN":
            if last_state != "UNKNOWN":
                print("새로운 입차 확인 -> 알림 발송")
                send_telegram(
                    f"🚗 <b>입차 알림</b>\n\n"
                    f"차량번호: {info}\n"
                    f"확인시간: {now_str}\n"
                    f"주차장: {PARKING_NAME}"
                )
            else:
                print("첫 확인 -> 알림 없이 IN 기록")
        else:
            print("상태 변경 없음 (주차 중)")
            
        write_file(STATE_FILE, "IN")
        return

    # 5. 차량 미발견 (OUT)
    if last_state != "IN":
        print("주차 이력 없음 -> OUT 기록 유지")
        write_file(STATE_FILE, "OUT")
        write_file(PENDING_FILE, "0")
        return

    # 주차 중이었다가 안 보임 (출차 대기)
    pending = int(read_file(PENDING_FILE, "0") or 0) + 1
    write_file(PENDING_FILE, pending)
    print(f"출차 의심: {pending}/{CONFIRM_COUNT}회")

    if pending < CONFIRM_COUNT:
        return

    # 완전 출차 확정
    print("연속 미발견으로 출차 확정 -> 알림 발송")
    last_in = read_file(LAST_IN_FILE, "기록 없음")
    send_telegram(
        f"🚙 <b>출차 알림</b>\n\n"
        f"차량번호: {CAR_NUMBER}\n"
        f"마지막 주차 확인: {last_in}\n"
        f"출차 확인: {now_str}\n"
        f"주차장: {PARKING_NAME}"
    )

    write_file(STATE_FILE, "OUT")
    write_file(PENDING_FILE, "0")
    
    if now.hour >= 12:
        write_file(EXIT_DONE_FILE, now.strftime("%Y-%m-%d"))

if __name__ == "__main__":
    main()
