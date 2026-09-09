import os
import time
import requests
from datetime import datetime, timezone, timedelta

# ===== 설정 =====
CAR_NUMBER = os.environ.get('CAR_NUMBER', '1989')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
API_URL = os.environ.get('API_URL', 'http://yongparking.co.kr/include/DST/inc_find_car.dst')
PARKING_NAME = "용산구청 부설주차장"

# 출차 알림 전 필요한 연속 'OUT' 확인 횟수 (1로 바꾸면 즉시 알림)
CONFIRM_COUNT = 2

STATE_FILE = "last_state.txt"       # IN / OUT / UNKNOWN
EXIT_DONE_FILE = "exit_done.txt"    # 출차/조회중단 완료한 날짜
LAST_IN_FILE = "last_in.txt"        # 마지막으로 '주차 중' 확인한 시각
PENDING_FILE = "pending_out.txt"    # 연속 OUT 카운트


def get_kst_now():
    return datetime.now(timezone(timedelta(hours=9)))


# ===== 파일 유틸 =====
def read_file(path, default=""):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception as e:
        print(f"파일 읽기 오류({path}): {e}")
    return default


def write_file(path, value):
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value))


# ===== 시간대 판정 =====
def should_run(now=None):
    """조회 시간대 여부 판정 (Actions 지연 0~4분 허용)"""
    now = now or get_kst_now()
    hour, minute = now.hour, now.minute
    weekday = now.weekday()  # 0=월 ... 6=일

    # 목요일(3)은 무조건 스킵
    if weekday == 3:
        return False, None

    if weekday < 5:  # 월, 화, 수, 금
        # 평일 아침 07:00~08:59, 30분 간격
        if 7 <= hour < 9:
            if minute % 30 < 5:
                return True, "morning"
            return False, None
        # 평일 저녁 18:30~22:59, 10분 간격
        if (hour == 18 and minute >= 30) or (19 <= hour < 23):
            if minute % 10 < 5:
                return True, "evening"
            return False, None
        return False, None

    # 주말(토, 일) 09:30~17:59, 15분 간격
    if (hour == 9 and minute >= 30) or (10 <= hour < 18):
        if minute % 15 < 5:
            return True, "weekend"
        return False, None
        
    # 주말(토, 일) 저녁 18:30 추가 (18:30 미조회 확인을 위해)
    if hour == 18 and 30 <= minute < 40:
        if minute % 10 < 5:
            return True, "weekend_evening"
        
    return False, None


def is_exit_done_today():
    return read_file(EXIT_DONE_FILE) == get_kst_now().strftime("%Y-%m-%d")


# ===== 주차 조회 =====
def check_parking():
    """
    반환: (True, 차량정보) 주차중 / (False, "") 출차 / (None, 사유) 판정불가
    """
    params = {"PROC_CMD": "FIND_00", "CAR_TYPE": "00", "CAR_NO": CAR_NUMBER}

    for attempt in range(1, 4):
        try:
            res = requests.get(API_URL, params=params, timeout=10)
            res.raise_for_status()
            result = res.text.strip()
            print(f"[시도 {attempt}] API 응답: {result[:200]}")

            if result.startswith("OK"):
                parts = result.split("|")
                return True, (parts[1] if len(parts) > 1 else CAR_NUMBER)

            # 정상 '미발견' 응답인지 검증 (HTML 오류 페이지 방어)
            if result.startswith(("NO", "FAIL", "ERR")) or result == "":
                return False, ""
            if "<" in result[:20].lower():
                print("HTML 응답 감지 → 판정 불가")
                return None, "html_response"

            return False, ""

        except Exception as e:
            print(f"[시도 {attempt}] 조회 오류: {e}")
            time.sleep(3)

    return None, "request_failed"


# ===== 텔레그램 =====
def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 설정 없음 (secrets 확인 필요)")
        print(message)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if res.ok:
            print("텔레그램 전송 성공")
            return True
        print(f"텔레그램 오류: {res.status_code} {res.text}")
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")
    return False


# ===== 메인 =====
def main():
    now = get_kst_now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 실행 시작")

    run, slot = should_run(now)
    if not run:
        print("조회 시간대 아님, 종료")
        return
    print(f"시간대: {slot}")

    if is_exit_done_today():
        print("오늘 이미 출차(또는 조회 중단) 완료, 종료")
        return

    print(f"차량 {CAR_NUMBER} 조회 중...")
    found, info = check_parking()

    if found is None:
        print(f"판정 불가({info}) → 상태 유지, 종료")
        return

    last_state = read_file(STATE_FILE, "UNKNOWN")
    print(f"이전 상태: {last_state}")

    # ---------- 18:30 첫 조회 미발견 시 중단 로직 ----------
    # 목요일은 이미 should_run에서 걸러졌으므로 제외됨
    if now.hour == 18 and 30 <= now.minute < 40:
        if not found:
            print("18:30 미조회 확인 → 금일 조회 중단")
            send_telegram(
                f"🚫 <b>차량 미조회 알림</b>\n\n"
                f"차량번호: {CAR_NUMBER}\n"
                f"18:30 기준 주차장에 차량이 없습니다.\n"
                f"금일 조회를 중단합니다."
            )
            write_file(STATE_FILE, "OUT")
            write_file(PENDING_FILE, "0")
            write_file(EXIT_DONE_FILE, now.strftime("%Y-%m-%d")) # 조회 중단 플래그
            return

    # ---------- 주차 중 ----------
    if found:
        write_file(LAST_IN_FILE, now_str)
        write_file(PENDING_FILE, "0")

        if last_state != "IN":
            if last_state == "UNKNOWN":
                print("첫 확인 → 알림 없이 IN 기록")
            else:
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

    # ---------- 미발견 (출차 대기) ----------
    if last_state != "IN":
        print("주차 이력 없음 → 알림 없이 OUT 기록")
        write_file(STATE_FILE, "OUT")
        write_file(PENDING_FILE, "0")
        return

    pending = int(read_file(PENDING_FILE, "0") or 0) + 1
    write_file(PENDING_FILE, pending)
    print(f"미발견 연속 {pending}/{CONFIRM_COUNT}회")

    if pending < CONFIRM_COUNT:
        print("확정 대기 → 상태 유지")
        return

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
    
    if slot in ("evening", "weekend_evening", "weekend"):
        write_file(EXIT_DONE_FILE, now.strftime("%Y-%m-%d"))
        print("오늘 출차 완료 기록")


if __name__ == "__main__":
    main()
