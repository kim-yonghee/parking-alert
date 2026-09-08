import os
import requests
from datetime import datetime, timezone, timedelta

# 설정
CAR_NUMBER = os.environ.get('CAR_NUMBER', '1989')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# API
API_URL = "http://yongparking.co.kr/include/DST/inc_find_car.dst"
STATE_FILE = "last_state.txt"


def check_parking():
    params = {
        "PROC_CMD": "FIND_00",
        "CAR_TYPE": "00",
        "CAR_NO": CAR_NUMBER
    }
    
    try:
        response = requests.get(API_URL, params=params, timeout=10)
        result = response.text.strip()
        print(f"API 응답: {result}")
        
        if result.startswith("OK"):
            parts = result.split("|")
            car_info = parts[1] if len(parts) > 1 else ""
            return True, car_info
        return False, ""
        
    except Exception as e:
        print(f"조회 오류: {e}")
        return None, str(e)


def get_last_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return f.read().strip()
    except:
        pass
    return "UNKNOWN"


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        f.write(state)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 설정 없음")
        print(message)
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    try:
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        })
        if response.ok:
            print("텔레그램 전송 성공!")
        else:
            print(f"텔레그램 오류: {response.text}")
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")


def main():
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"[{now}] 차량 {CAR_NUMBER} 조회 중...")
    
    found, car_info = check_parking()
    
    if found is None:
        print("API 오류, 상태 변경 없음")
        return
    
    current_state = "IN" if found else "OUT"
    last_state = get_last_state()
    
    print(f"이전 상태: {last_state}, 현재 상태: {current_state}")
    
    # 상태 변경 감지
    if last_state != current_state:
        if current_state == "IN":
            message = f"""🚗 <b>입차 알림</b>

차량번호: {car_info}
확인시간: {now}
주차장: 용산구청 부설주차장"""
        else:
            message = f"""🚙 <b>출차 알림</b>

차량번호: {CAR_NUMBER}
확인시간: {now}
주차장: 용산구청 부설주차장"""
        
        send_telegram(message)
        print(f"✅ 상태 변경! 알림 전송")
    else:
        print(f"상태 변경 없음")
    
    # 상태 저장
    save_state(current_state)


if __name__ == "__main__":
    main()
