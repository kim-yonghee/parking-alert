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
EXIT_DONE_FILE = "exit_done.txt"


def get_kst_now():
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst)


def should_run():
    """현재 시간이 조회 시간대인지 확인"""
    now = get_kst_now()
    hour = now.hour
    weekday = now.weekday()  # 0=월요일, 6=일요일
    
    is_weekday = weekday < 5
    
    if is_weekday:
        # 평일: 아침 7~9시, 저녁 18~23시
        if 7 <= hour < 9:
            return True, "morning"
        elif 18 <= hour < 23:
            return True, "evening"
    else:
        # 주말: 9~23시
        if 9 <= hour < 23:
            return True, "weekend"
    
    return False, None


def is_exit_done_today():
    """오늘 이미 출차 완료했는지 확인"""
    try:
        if os.path.exists(EXIT_DONE_FILE):
            with open(EXIT_DONE_FILE, 'r') as f:
                done_date = f.read().strip()
                today = get_kst_now().strftime("%Y-%m-%d")
                return done_date == today
    except:
        pass
    return False


def save_exit_done():
    """출차 완료 기록"""
    today = get_kst_now().strftime("%Y-%m-%d")
    with open(EXIT_DONE_FILE, 'w') as f:
        f.write(today)


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
    now = get_kst_now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    weekday = now.weekday()
    is_weekday = weekday < 5
    
    print(f"[{now_str}] 실행 시작")
    
    # 1. 조회 시간대 확인
    should, time_slot = should_run()
    if not should:
        print("조회 시간대 아님, 종료")
        return
    
    print(f"시간대: {time_slot}")
    
    # 2. 저녁/주말에 이미 출차했으면 스킵
    if time_slot in ["evening", "weekend"]:
        if is_exit_done_today():
            print("오늘 이미 출차 완료, 종료")
            return
    
    # 3. 주차 상태 조회
    print(f"차량 {CAR_NUMBER} 조회 중...")
    found, car_info = check_parking()
    
    if found is None:
        print("API 오류, 종료")
        return
    
    current_state = "IN" if found else "OUT"
    last_state = get_last_state()
    
    print(f"이전 상태: {last_state}, 현재 상태: {current_state}")
    
    # 4. 상태 변경 감지
    if last_state != current_state:
        if current_state == "IN":
            message = f"""🚗 <b>입차 알림</b>

차량번호: {car_info}
확인시간: {now_str}
주차장: 용산구청 부설주차장"""
            send_telegram(message)
            print("✅ 입차 알림 전송")
            
        else:
            message = f"""🚙 <b>출차 알림</b>

차량번호: {CAR_NUMBER}
확인시간: {now_str}
주차장: 용산구청 부설주차장"""
            send_telegram(message)
            print("✅ 출차 알림 전송")
            
            # 출차 완료 기록 (저녁/주말만)
            if time_slot in ["evening", "weekend"]:
                save_exit_done()
                print("오늘 출차 완료 기록")
    else:
        print("상태 변경 없음")
    
    # 5. 상태 저장
    save_state(current_state)


if __name__ == "__main__":
    main()
