import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
STATE = Path('parking_state.json')
CONFIRM_COUNT = 2
MAX_GAP_SECONDS = 20 * 60


def save(state):
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(STATE)


def load(today):
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding='utf-8'))
        if state['date'] == today:
            return state
    return {'date': today, 'phase': 'WAIT_IN', 'pending': 0,
            'pending_at': None, 'last_in': '', 'notifications': [],
            'morning_checks': 0, 'morning_errors': 0}


def send_telegram(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        raise RuntimeError('텔레그램 Secrets 설정이 필요합니다.')
    try:
        response = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat, 'text': message}, timeout=10)
        if not response.ok or response.json().get('ok') is not True:
            raise RuntimeError('텔레그램 발송 실패')
    except (requests.RequestException, ValueError):
        raise RuntimeError('텔레그램 연결 또는 응답 오류') from None


def flush_notifications(state):
    while state['notifications']:
        send_telegram(state['notifications'][0])
        state['notifications'].pop(0)
        save(state)


def check_parking():
    car = os.environ.get('CAR_NUMBER')
    api = os.environ.get('API_URL')
    if not car or not api:
        raise RuntimeError('CAR_NUMBER와 API_URL Secrets가 필요합니다.')
    try:
        response = requests.get(api, params={
            'PROC_CMD': 'FIND_00', 'CAR_TYPE': '00', 'CAR_NO': car
        }, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        print('주차 API 연결 실패: 이번 조회로 입출차를 판단하지 않습니다.')
        return None
    # 실제 API 규격을 확인해야 합니다. NO를 정상 미조회 코드로 가정합니다.
    status = response.text.strip().split('|', 1)[0].strip()
    if status == 'OK':
        return True
    if status == 'NO':
        return False
    print('알 수 없는 주차 API 응답: 입출차 판단 보류')
    return None


def main(now=None):
    now = now or datetime.now(KST)
    if now.weekday() == 3:
        print('목요일: 조회하지 않습니다.')
        return
    state = load(now.strftime('%Y-%m-%d'))
    # 발송 실패한 알림만 재시도하며, 이 과정에서 주차 API는 호출하지 않습니다.
    flush_notifications(state)
    if state['phase'] == 'DONE':
        print('당일 조회 종료')
        return

    minutes = now.hour * 60 + now.minute
    if state['phase'] == 'WAIT_IN':
        if minutes >= 9 * 60:
            state['phase'] = 'DONE'
            checks = state.get('morning_checks', 0)
            errors = state.get('morning_errors', 0)
            if checks == 0:
                detail = '오전 정상 조회 기록이 없어 입차 여부를 확인하지 못했습니다.'
            else:
                detail = '오전 07:00~09:00 동안 입차가 확인되지 않았습니다.'
            if errors:
                detail += f'\n조회 오류 {errors}회가 포함되어 있습니다.'
            state['notifications'].append(
                '🚫 차량 입차 미확인 알림\n'
                f"차량번호: {os.environ.get('CAR_NUMBER', '')}\n"
                f'{detail}\n오늘 조회를 종료합니다.')
            save(state)
            flush_notifications(state)
            print('오전 입차 확인 없음: 당일 조회 종료')
            return
        if minutes < 7 * 60:
            return
        found = check_parking()
        counter = 'morning_errors' if found is None else 'morning_checks'
        state[counter] = state.get(counter, 0) + 1
        if found is True:
            state['phase'] = 'IN'
            state['last_in'] = now.strftime('%Y-%m-%d %H:%M:%S')
            state['notifications'].append(
                f"🚗 입차 확인 알림\n차량번호: {os.environ.get('CAR_NUMBER', '')}"
                f"\n확인시간: {state['last_in']}\n주차장: 용산구청 부설주차장"
                '\n오후 6시 30분부터 출차를 확인합니다.')
            save(state)
            flush_notifications(state)
        else:
            save(state)
        return

    if minutes < 18 * 60 + 30:
        print('오전 입차 확인 완료: 18:30까지 조회하지 않습니다.')
        return

    found = check_parking()
    if found is True:
        state.update(pending=0, pending_at=None,
                     last_in=now.strftime('%Y-%m-%d %H:%M:%S'))
    elif found is None:
        state.update(pending=0, pending_at=None)
    else:
        previous = state['pending_at']
        gap = now.timestamp() - previous if previous is not None else None
        consecutive = gap is not None and 0 < gap <= MAX_GAP_SECONDS
        state['pending'] = state['pending'] + 1 if consecutive else 1
        state['pending_at'] = now.timestamp()
        if state['pending'] >= CONFIRM_COUNT:
            state['phase'] = 'DONE'
            state['notifications'].append(
                f"🚙 출차 확인 알림\n차량번호: {os.environ.get('CAR_NUMBER', '')}"
                f"\n마지막 주차 확인: {state['last_in']}"
                f"\n출차 확인: {now.strftime('%Y-%m-%d %H:%M:%S')}"
                '\n주차장: 용산구청 부설주차장\n오늘 조회를 종료합니다.')
    save(state)
    flush_notifications(state)


if __name__ == '__main__':
    main()
