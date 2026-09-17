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
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        return res.ok
    except Exception:
        return False

def main():
    now = get_kst_now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute
    
    # 1. 목요일은 무조건 조회 스킵
    if weekday == 3:
        return
        
    # 2. 지정된 시간대(07:00~09:59 / 18:30~23:59)인지 확인
    is_morning = (7 <= hour <= 9)
    is_evening = (hour == 18 and minute >= 30) or (19 <= hour <= 23)
    
    if not (is_morning or is_evening):
        return

    # 3. 오늘 이미 최종 알림을 보냈다면 실행 안 함
    if is_exit_done_today():
        return

    found, info = check_parking()
    if found is None:
        return

    last_state = read_file(STATE_FILE, "UNKNOWN")

    # 4. [18:30 이후 첫 조회] 차량이 없는 경우 (입차 안 함)
    if is_evening and not found and last_state != "IN":
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

    # 5. 주차 중 (IN)
    if found:
        write_file(LAST_IN_FILE, now_str)
        write_file(PENDING_FILE, "0")
        
        if last_state != "IN":
            print("새로운 입차 확인 -> 알림 발송")
            send_telegram(
                f"🚗 <b>입차 알림</b>\n\n"
                f"차량번호: {info}\n"
                f"확인시간: {now_str}\n"
                f"주차장: {PARKING_NAME}"
            )
        else:
            print("상태 변경 없음 (주차 중)")
                
        write_file(STATE_FILE, "IN")
        return

    # 6. 차량 미발견 (OUT) 상태 처리
    if last_state != "IN":
        write_file(STATE_FILE, "OUT")
        write_file(PENDING_FILE, "0")
        return

    # 7. 주차 중이었다가 안 보임 (출차 대기)
    pending = int(read_file(PENDING_FILE, "0") or 0) + 1
    write_file(PENDING_FILE, pending)

    if pending < CONFIRM_COUNT:
        return

    # 8. 완전 출차 확정
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
    
    # 오후 12시 이후 출차 시 오늘은 완벽히 조회를 중단하도록 기록
    if hour >= 12:
        write_file(EXIT_DONE_FILE, now.strftime("%Y-%m-%d"))

if __name__ == "__main__":
    main()
