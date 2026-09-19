import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import holidays
import requests


KST = timezone(timedelta(hours=9))
STATE = Path("parking_state.json")

CONFIRM_COUNT = 2
MAX_GAP_SECONDS = 20 * 60

# 기관 자체 휴일 등을 추가할 수 있습니다.
# 예: EXTRA_HOLIDAYS = {"2026-12-31"}
EXTRA_HOLIDAYS = set()


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


def save(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(STATE)


def load(today):
    if STATE.exists():
        state = json.loads(
            STATE.read_text(encoding="utf-8")
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


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat:
        raise RuntimeError(
            "텔레그램 Secrets 설정이 필요합니다."
        )

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": message,
            },
            timeout=10,
        )

        if (
            not response.ok
            or response.json().get("ok") is not True
        ):
            raise RuntimeError("텔레그램 발송 실패")

    except (requests.RequestException, ValueError):
        raise RuntimeError(
            "텔레그램 연결 또는 응답 오류"
        ) from None


def flush_notifications(state):
    while state["notifications"]:
        send_telegram(state["notifications"][0])
        state["notifications"].pop(0)
        save(state)


def check_parking():
    car = os.environ.get("CAR_NUMBER")
    api = os.environ.get("API_URL")

    if not car or not api:
        raise RuntimeError(
            "CAR_NUMBER와 API_URL Secrets가 필요합니다."
        )

    try:
        response = requests.get(
            api,
            params={
                "PROC_CMD": "FIND_00",
                "CAR_TYPE": "00",
                "CAR_NO": car,
            },
            timeout=10,
        )
        response.raise_for_status()

    except requests.RequestException:
        print(
            "주차 API 연결 실패: "
            "이번 조회로 입출차를 판단하지 않습니다."
        )
        return None

    # 실제 API 응답 규격 확인 필요:
    # OK = 차량 있음 / NO = 정상 차량 미조회로 가정
    status = (
        response.text.strip()
        .split("|", 1)[0]
        .strip()
    )

    if status == "OK":
        return True

    if status == "NO":
        return False

    # 오류, 빈 응답, HTML 등을 출차로 오인하지 않음
    print("알 수 없는 주차 API 응답: 입출차 판단 보류")
    return None


def main(now=None):
    now = now or datetime.now(KST)
    holiday = is_holiday(now)

    # 평일 목요일만 제외. 목요일이 휴일이면 조회.
    if now.weekday() == 3 and not holiday:
        print("평일 목요일: 조회하지 않습니다.")
        return

    start = 9 * 60 if holiday else 7 * 60
    entry_end = 18 * 60 if holiday else 9 * 60
    window = (
        "09:00~18:00"
        if holiday
        else "07:00~09:00"
    )

    day_type = "휴일" if holiday else "평일"
    print(
        f"{day_type} 규칙 적용: "
        f"입차 확인 {window}"
    )

    state = load(now.strftime("%Y-%m-%d"))

    # 실패한 알림 재시도. 주차 API는 호출하지 않음.
    flush_notifications(state)

    if state["phase"] == "DONE":
        print("당일 조회 종료")
        return

    minutes = now.hour * 60 + now.minute

    # 입차 확인 단계
    if state["phase"] == "WAIT_IN":
        if minutes >= entry_end:
            state["phase"] = "DONE"

            checks = state.get("morning_checks", 0)
            errors = state.get("morning_errors", 0)

            if checks == 0:
                detail = (
                    f"{window} 정상 조회 기록이 없어 "
                    "입차 여부를 확인하지 못했습니다."
                )
            else:
                detail = (
                    f"{window} 동안 "
                    "입차가 확인되지 않았습니다."
                )

            if errors:
                detail += (
                    f"\n조회 오류 {errors}회가 "
                    "포함되어 있습니다."
                )

            state["notifications"].append(
                "🚫 차량 입차 미확인 알림\n"
                f"차량번호: {os.environ.get('CAR_NUMBER', '')}\n"
                f"{detail}\n"
                "오늘 조회를 종료합니다."
            )

            save(state)
            flush_notifications(state)
            print("입차 확인 시간 종료: 당일 조회 종료")
            return

        if minutes < start:
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
            state["last_in"] = now.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            next_check = (
                "18:00까지 출차를 확인합니다."
                if holiday
                else "오후 6시 30분부터 출차를 확인합니다."
            )

            state["notifications"].append(
                "🚗 입차 확인 알림\n"
                f"차량번호: {os.environ.get('CAR_NUMBER', '')}\n"
                f"확인시간: {state['last_in']}\n"
                "주차장: 용산구청 부설주차장\n"
                f"{next_check}"
            )

            save(state)
            flush_notifications(state)

        else:
            save(state)

        return

    # 입차 확인 후 출차 확인 단계
    if holiday and minutes >= 18 * 60:
        state["phase"] = "DONE"
        state["pending"] = 0
        state["pending_at"] = None
        save(state)
        print(
            "휴일 조회 시간 종료: "
            "출차를 추정하지 않고 당일 종료"
        )
        return

    if holiday and minutes < start:
        return

    if not holiday and minutes < 18 * 60 + 30:
        print(
            "오전 입차 확인 완료: "
            "18:30까지 조회하지 않습니다."
        )
        return

    found = check_parking()

    if found is True:
        state.update(
            pending=0,
            pending_at=None,
            last_in=now.strftime("%Y-%m-%d %H:%M:%S"),
        )

    elif found is None:
        # 오류가 끼면 연속 미조회 횟수 초기화
        state.update(
            pending=0,
            pending_at=None,
        )

    else:
        previous = state["pending_at"]

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
            state["pending"] + 1
            if consecutive
            else 1
        )
        state["pending_at"] = now.timestamp()

        if state["pending"] >= CONFIRM_COUNT:
            state["phase"] = "DONE"

            state["notifications"].append(
                "🚙 출차 확인 알림\n"
                f"차량번호: {os.environ.get('CAR_NUMBER', '')}\n"
                f"마지막 주차 확인: {state['last_in']}\n"
                f"출차 확인: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                "주차장: 용산구청 부설주차장\n"
                "오늘 조회를 종료합니다."
            )

    save(state)
    flush_notifications(state)


if __name__ == "__main__":
    main()
