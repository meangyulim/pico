# =================================================================
# 🎮 user_code.py : 반응속도 게임 (Grove Yellow LED Button)
# =================================================================
# 이 파일의 코드를 실수로 지우거나 문법 오타를 내더라도,
# main.py(시스템 코어)의 보호 기능 덕분에 웹서버와 Wi-Fi는 항상 살아있어
# 스마트폰 웹 에디터(/edit)로 언제든지 다시 수정할 수 있습니다.
#
# 규칙: 대기하다가 LED가 켜지는 순간 최대한 빨리 버튼을 누르세요.
#       LED가 켜지기 전에 누르면 "반칙(FOUL)"으로 처리됩니다.
# =================================================================
import machine
import utime

try:
    import urandom as _random
except ImportError:
    import random as _random

# -----------------------------------------------------------------
# [1] 핀 설정 — Grove Shield D16 포트 (SIG1=GP16, SIG2=GP17)
# -----------------------------------------------------------------
# LED가 안 켜지거나 버튼이 반대로 동작하면 이 두 핀 번호를 서로 바꿔보세요.
BUTTON_PIN = 16
LED_PIN = 17

# main.py가 요구하는 인터페이스 값들 (게임에서는 대부분 미사용/의미 재활용)
DISPLAY_UPDATE_INTERVAL_MS = 300   # 대시보드 갱신 주기 (게임 판정 자체는 타이머/인터럽트라 이 값과 무관하게 정확함)
CLOUD_SYNC_INTERVAL_MS = 60000
alert_threshold = 0                # 미사용
is_muted = True                    # 버저가 없으므로 항상 음소거
cloud_sync_status = "게임 모드 (클라우드 동기화 없음)"

led = machine.Pin(LED_PIN, machine.Pin.OUT)
led.value(0)

# Grove 버튼 모듈은 대부분 "누르면 HIGH"인 능동-HIGH 방식입니다. 반대로 동작하면
# PULL_DOWN -> PULL_UP, IRQ_RISING -> IRQ_FALLING으로 같이 바꿔주세요.
button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_DOWN)

# -----------------------------------------------------------------
# [2] 게임 상태 (모두 폴링 없이 인터럽트/타이머로만 갱신되어 정확함)
# -----------------------------------------------------------------
STATE_WAITING = "waiting"  # LED 켜지기 전, 무작위 대기 중
STATE_LIT = "lit"          # LED 켜짐, 누르길 기다리는 중
STATE_RESULT = "result"    # 성공! 결과 잠깐 보여주는 중
STATE_FOUL = "foul"        # 반칙(LED 켜지기 전에 누름), 결과 잠깐 보여주는 중

_state = STATE_WAITING
_lit_at = 0
_result_until = 0
_last_reaction_ms = None
_best_reaction_ms = None
_press_flag = False
_last_press_ms = 0
_DEBOUNCE_MS = 30
_RESULT_DISPLAY_MS = 2000

_led_timer = machine.Timer(-1)


def _on_button_press(pin):
    # 인터럽트 핸들러는 최대한 가볍게: 디바운스 후 플래그만 세팅
    global _press_flag, _last_press_ms
    now = utime.ticks_ms()
    if utime.ticks_diff(now, _last_press_ms) < _DEBOUNCE_MS:
        return
    _last_press_ms = now
    _press_flag = True


button.irq(trigger=machine.Pin.IRQ_RISING, handler=_on_button_press)


def _on_light_up(timer):
    # machine.Timer 콜백: 폴링 주기와 무관하게 정확한 순간에 LED를 켭니다.
    global _state, _lit_at
    if _state != STATE_WAITING:
        return  # 그 사이 반칙 등으로 이미 라운드가 취소됨
    led.value(1)
    _lit_at = utime.ticks_ms()
    _state = STATE_LIT


def _start_round():
    global _state
    led.value(0)
    _state = STATE_WAITING
    delay_ms = 1000 + _random.getrandbits(12) % 3000  # 1~4초 무작위 대기
    _led_timer.init(mode=machine.Timer.ONE_SHOT, period=delay_ms, callback=_on_light_up)


def _update_game():
    """매 폴링 주기(read_dust_sensor 호출 시)마다 상태를 갱신합니다. 블로킹 없음."""
    global _state, _last_reaction_ms, _best_reaction_ms, _press_flag, _result_until

    now = utime.ticks_ms()

    if _state == STATE_WAITING:
        if _press_flag:
            _press_flag = False
            _led_timer.deinit()  # 아직 안 울린 점등 타이머 취소
            led.value(0)
            _state = STATE_FOUL
            _result_until = utime.ticks_add(now, _RESULT_DISPLAY_MS)
        return

    if _state == STATE_LIT:
        if _press_flag:
            _press_flag = False
            reaction = utime.ticks_diff(now, _lit_at)
            _last_reaction_ms = reaction
            if _best_reaction_ms is None or reaction < _best_reaction_ms:
                _best_reaction_ms = reaction
            led.value(0)
            _state = STATE_RESULT
            _result_until = utime.ticks_add(now, _RESULT_DISPLAY_MS)
        return

    if _state in (STATE_RESULT, STATE_FOUL):
        _press_flag = False  # 결과 보여주는 동안 누르는 건 무시
        if utime.ticks_diff(now, _result_until) >= 0:
            _start_round()
        return


# -----------------------------------------------------------------
# [3] main.py 인터페이스 (REQUIRED_USER_ATTRS)
# -----------------------------------------------------------------
def read_dust_sensor():
    """
    main.py는 원래 미세먼지 센서 값을 읽는 용도로 이 함수를 부르지만, 이
    프로젝트는 반응속도 게임이라 게임 상태를 갱신하는 용도로 재활용합니다.
    반환 형식(voltage, density)은 그대로 두고, density 자리에 최근
    반응속도(ms)를 넣어 대시보드 숫자 표시를 그대로 활용합니다.
    """
    _update_game()
    shown_ms = _last_reaction_ms if _last_reaction_ms is not None else 0
    return 0.0, shown_ms


def get_status_info(_reaction_ms):
    if _state == STATE_WAITING:
        return "WAIT", "대기 (아직 누르지 마세요)", "#eab308"
    if _state == STATE_LIT:
        return "GO", "지금 누르세요!", "#22c55e"
    if _state == STATE_FOUL:
        return "FOUL", "너무 빨랐어요", "#ef4444"
    if _state == STATE_RESULT and _last_reaction_ms is not None:
        best = _best_reaction_ms if _best_reaction_ms is not None else _last_reaction_ms
        return "RESULT", f"{_last_reaction_ms}ms (최고 {best}ms)", "#38bdf8"
    return "READY", "준비 중", "#94a3b8"


def play_alert_beep():
    pass  # 버저 없음


def sync_with_google_sheets(density, voltage, status_str):
    # 게임 모드에서는 클라우드 동기화를 쓰지 않습니다 (미세먼지용 시트에
    # 반응속도 데이터를 섞지 않기 위함). 필요하면 여기서 별도 GAS_URL로
    # 라운드 결과를 전송하도록 채워 넣으세요.
    return True
