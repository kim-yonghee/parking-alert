import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import holidays
import requests


# ===== 설정 =====

KST = timezone(timedelta(hours=9))

# 조회 차량번호 고정
CAR_NUMBER = "1989"

API_URL = (
    os.environ.get("API_URL") or ""
).strip() or "http://yongparking.co.kr/include/DST/inc_find_car.dst"

TELEGRAM_BOT_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN") or ""
).strip()

TELEGRAM_CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID") or ""
).strip()

PARKING_NAME = "용산구청 부설주차장"
STATE_FILE = Path("parking_state.json")

# 정상 미조회가 연속 2회일 때 출차 확정
CONFIRM_COUNT = 2
MAX_GAP_SECONDS = 20 * 60

# 기관 자체 휴일 등 추가 가능
# 예: EXTRA_HOLIDAYS = {"2026-12-31"}
EXTRA_HOLIDAYS = set()


# ===== 날짜 및 상태 =====

def is_holiday(now):
    calendar = holidays.KR(
        years=now.year,
        observed=True,
    )
    return (
        now.weekday() >= 5
        or now.date() in calendar
        or now.date().isoformat() in EXTRA_HOLIDAYS
    )


def save_state(state):
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(STATE_FILE)


def load_state(today):
    if STATE_FILE.exists():
        state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
        if state["date"] == today:
            return state

    return {
        "date": today,
        "phase": "WAIT_IN",
        "pending": 0,
        "pending_at": None,
        "last_in": "",
        "notifications": [],
        "morning_checks": 0,
        "morning_errors": 0,
    }


# ===== 텔레그램 =====

def send_telegram(message):
    missing = []

    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        raise RuntimeError(
            "텔레그램 Secrets 누락: "
            + ", ".join(missing)
        )

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=10,
        )
    except requests.RequestException:
        raise RuntimeError(
            "텔레그램 연결 실패: 다음 실행에서 재시도"
        ) from None

    if not response.ok:
        raise RuntimeError(
            f"텔레그램 발송 실패: HTTP {response.status_code}"
        )

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(
            "텔레그램 응답 해석 실패"
        ) from None

    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
    ):
        raise RuntimeError("텔레그램 발송 실패")

    print("텔레그램 발송 성공")


def flush_notifications(state):
    # 성공한 알림만 제거하여 실패 시 다음 실행에서 재시도
    while state["notifications"]:
        send_telegram(state["notifications"][0])
        state["notifications"].pop(0)
        save_state(state)


# ===== 주차 조회 =====

def check_parking():
    try:
        response = requests.get(
            API_URL,
            params={
                "PROC_CMD": "FIND_00",
                "CAR_TYPE": "00",
                "CAR_NO": CAR_NUMBER,
            },
            timeout=10,
        )
        response.raise_for_status()

    except requests.RequestException:
        print("주차 API 연결 또는 HTTP 오류: 판단 보류")
        return None

    result = response.text.strip()
    parts = [part.strip() for part in result.split("|")]
    status = parts[0]

    if status == "OK":
        print("차량 주차 확인")
        return True

    # 직접 미조회 시 관찰된 응답: FAIL|NO CARINFO
    # 다른 FAIL 응답은 차량 없음으로 처리하지 않음
    if (
        len(parts) == 2
        and status == "FAIL"
        and parts[1] == "NO CARINFO"
    ):
        print("정상 차량 미조회: FAIL|NO CARINFO")
        return False

    # 기존 NO 응답 처리 유지
    if status == "NO":
        print("정상 차량 미조회: NO")
        return False

    print(
        "알 수 없는 주차 API 응답: "
        "출차로 판단하지 않습니다."
    )
    return None


# ===== 메인 =====

def main():
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    now_text = now.strftime("%Y-%m-%d %H:%M:%S")

    holiday = is_holiday(now)

    # 일반 목요일은 제외, 목요일 공휴일은 조회
    if now.weekday() == 3 and not holiday:
        print("평일 목요일: 조회 제외")
        return

    if holiday:
        entry_start = 9 * 60
        entry_end = 18 * 60
        window_text = "09:00~18:00"
    else:
        entry_start = 7 * 60
        entry_end = 9 * 60
        window_text = "07:00~09:00"

    day_type = "휴일" if holiday else "평일"
    print(
        f"{day_type} 규칙 적용: 입차 확인 {window_text}"
    )

    state = load_state(today)

    # 미발송 알림 먼저 재시도
    flush_notifications(state)

    if state["phase"] == "DONE":
        print("당일 조회 종료: 주차 API 호출 안 함")
        return

    minutes = now.hour * 60 + now.minute

    # ===== 입차 대기 =====

    if state["phase"] == "WAIT_IN":
        if minutes >= entry_end:
            checks = state.get("morning_checks", 0)
            errors = state.get("morning_errors", 0)

            if checks == 0:
                detail = (
                    f"{window_text} 정상 조회 기록이 없어 "
                    "입차 여부를 확인하지 못했습니다."
                )
            else:
                detail = (
                    f"{window_text} 동안 "
                    "입차가 확인되지 않았습니다."
                )

            if errors:
                detail += f"\n조회 오류: {errors}회"

            state["phase"] = "DONE"
            state["notifications"].append(
                "🚫 차량 입차 미확인 알림\n\n"
                f"차량번호: {CAR_NUMBER}\n"
                f"주차장: {PARKING_NAME}\n"
                f"{detail}\n\n"
                "오늘 조회를 종료합니다."
            )

            save_state(state)
            flush_notifications(state)
            return

        if minutes < entry_start:
            print("입차 조회 시작 전")
            return

        found = check_parking()

        counter = (
            "morning_errors"
            if found is None
            else "morning_checks"
        )
        state[counter] = state.get(counter, 0) + 1

        if found is True:
            state["phase"] = "IN"
            state["last_in"] = now_text
            state["pending"] = 0
            state["pending_at"] = None

            next_step = (
                "18:00까지 출차를 확인합니다."
                if holiday
                else "18:30부터 출차를 확인합니다."
            )

            state["notifications"].append(
                "🚗 입차 확인 알림\n\n"
                f"차량번호: {CAR_NUMBER}\n"
                f"확인시간: {now_text}\n"
                f"주차장: {PARKING_NAME}\n\n"
                f"{next_step}"
            )

        save_state(state)
        flush_notifications(state)
        return

    # ===== 입차 확인 후 출차 대기 =====

    if holiday and minutes >= 18 * 60:
        state["phase"] = "DONE"
        state["pending"] = 0
        state["pending_at"] = None
        save_state(state)

        print(
            "휴일 조회 시간 종료: "
            "출차 미확정 상태로 조회만 종료"
        )
        return

    if holiday and minutes < 9 * 60:
        print("휴일 조회 시작 전")
        return

    if not holiday and minutes < 18 * 60 + 30:
        print("입차 확인 완료: 18:30까지 조회 중단")
        return

    found = check_parking()

    if found is True:
        state["last_in"] = now_text
        state["pending"] = 0
        state["pending_at"] = None
        print("계속 주차 중: 추가 알림 없음")

    elif found is None:
        state["pending"] = 0
        state["pending_at"] = None
        print("조회 오류: 출차 판정 보류")

    else:
        previous = state.get("pending_at")
        gap = (
            now.timestamp() - previous
            if previous is not None
            else None
        )

        consecutive = (
            gap is not None
            and 0 < gap <= MAX_GAP_SECONDS
        )

        state["pending"] = (
            state.get("pending", 0) + 1
            if consecutive
            else 1
        )
        state["pending_at"] = now.timestamp()

        print(
            "출차 확인 대기: "
            f"{state['pending']}/{CONFIRM_COUNT}"
        )

        if state["pending"] >= CONFIRM_COUNT:
            state["phase"] = "DONE"
            state["pending"] = 0
            state["pending_at"] = None

            state["notifications"].append(
                "🚙 출차 확인 알림\n\n"
                f"차량번호: {CAR_NUMBER}\n"
                f"마지막 주차 확인: {state['last_in']}\n"
                f"출차 확인: {now_text}\n"
                f"주차장: {PARKING_NAME}\n\n"
                "오늘 조회를 종료합니다."
            )

    save_state(state)
    flush_notifications(state)


if __name__ == "__main__":
    main()