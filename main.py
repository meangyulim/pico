# =================================================================
# 🛡️ main.py : 부팅 · 네트워크 연결 · 메인 루프
# =================================================================
# 이 파일은 얇게 유지합니다. HTTP 처리는 httpd.py, 화면 HTML은 web_ui.py,
# 주기 작업(OTA 등)은 bg_thread.py가 담당합니다.
#
# 센서별 로직은 app_*.py 중 하나가 담당하며 app_manager가 예외를 격리해
# 로드합니다 — 앱이 깨져도 웹 에디터와 시스템 코어는 살아있습니다.
# =================================================================

# console_log를 가장 먼저 import해야 이후 모든 print()가 원격 콘솔(/logs)
# 버퍼에 잡힙니다.
from console_log import log_error, flush_log_to_file

import gc
import machine
import network
import utime

import httpd
from bg_thread import register_periodic_task, start_background_worker
from lcd_driver import I2cLcd
from wifi_manager import (
    load_wifi_networks, scan_nearby_wifis, connect_sta_wifi, start_ap_mode,
    AP_RETRY_INTERVAL_MS,
)
from app_manager import load_active_app
import ota

try:
    import watchdog
except ImportError:
    # OTA로 아직 전달되지 않은 새 모듈일 수 있습니다. 여기서 죽으면 boot.py가
    # 핵심 모듈을 전부 .bak으로 되돌려버려, 정작 그 모듈을 받아올 기회조차
    # 사라집니다. 없으면 없는 대로 부팅하고 다음 업데이트에서 받아옵니다.
    class watchdog:
        @staticmethod
        def start():
            print("⚠️ watchdog.py가 없습니다 — 워치독 없이 실행합니다")

        @staticmethod
        def feed():
            pass

        @staticmethod
        def is_active():
            return False

try:
    import cpu_config
except ImportError:
    class cpu_config:
        DEFAULT_FREQ_MHZ = 150

        @staticmethod
        def load_freq_mhz():
            return 150


HEARTBEAT_MS = 60 * 1000        # 하트비트 로그 주기
LOG_FLUSH_EVERY = 5             # 하트비트 5번마다 파일로 flush (플래시 수명 배려)
OTA_POLL_MS = 2 * 1000          # "지금 업데이트 확인" 요청을 집어가는 주기
LCD_I2C_SDA = 8
LCD_I2C_SCL = 9


class State:
    """웹 핸들러와 메인 루프가 공유하는 상태."""

    def __init__(self):
        self.mode = "OFFLINE_AP"
        self.current_ip = None
        self.wifi_list = []
        self.app_mod = None
        self.app_err = None
        self.value = 0.0
        self.avg_v = 0.0
        self.status_eng = "INIT"
        self.status_kor = "준비"
        self.color_hex = "#38bdf8"
        self.power_mode = "ON"          # "ON" | "SLEEP"
        # 웹 핸들러가 요청한 동작. 응답을 끝까지 보내고 소켓을 닫은 뒤
        # 메인 루프가 실행해야 브라우저가 결과 화면을 받습니다.
        self.pending_action = None
        self._boot_ms = utime.ticks_ms()

    def uptime_sec(self):
        return utime.ticks_diff(utime.ticks_ms(), self._boot_ms) // 1000

    def uptime_str(self):
        s = self.uptime_sec()
        h, m = s // 3600, (s % 3600) // 60
        if h:
            return "{}시간 {}분".format(h, m)
        if m:
            return "{}분".format(m)
        return "{}초".format(s)


# -----------------------------------------------------------------
# 초기화
# -----------------------------------------------------------------
def init_lcd():
    try:
        i2c = machine.I2C(0, sda=machine.Pin(LCD_I2C_SDA),
                          scl=machine.Pin(LCD_I2C_SCL), freq=100000)
        addrs = i2c.scan()
        if addrs:
            lcd = I2cLcd(i2c, addrs[0])
            lcd.display_2lines("Pico Core", "Starting...")
            print("LCD 연결 완료 (I2C 주소: {})".format(hex(addrs[0])))
            return lcd
        print("경고: I2C0에서 LCD를 찾지 못했습니다")
    except Exception as e:
        log_error("LCD 초기화", e)
    return None


def connect_network(lcd, state):
    networks = load_wifi_networks()
    connected, ip = connect_sta_wifi(networks, lcd_ref=lcd) if networks else (False, None)
    if connected:
        state.mode, state.current_ip = "ONLINE_STA", ip
    else:
        state.current_ip = start_ap_mode(lcd_ref=lcd)
        state.mode = "OFFLINE_AP"
    state.wifi_list = scan_nearby_wifis()


# -----------------------------------------------------------------
# 센서 측정 + LCD
# -----------------------------------------------------------------
def measure_and_update_lcd(lcd, state, toggle):
    if state.power_mode == "SLEEP":
        return toggle

    app = state.app_mod
    if app and hasattr(app, 'read_dust_sensor'):
        try:
            state.avg_v, state.value = app.read_dust_sensor()
            state.status_eng, state.status_kor, state.color_hex = \
                app.get_status_info(state.value)
            muted = getattr(app, "is_muted", False)
            thresh = getattr(app, "alert_threshold", 0.0)
            if (not muted) and thresh and state.value >= thresh:
                if hasattr(app, 'play_alert_beep'):
                    app.play_alert_beep()
            elif hasattr(app, 'buzzer'):
                app.buzzer.duty_u16(0)
        except Exception as e:
            state.app_err = "측정 오류: {}".format(e)
            state.status_eng, state.status_kor, state.color_hex = "ERR", "오류", "#ef4444"
            log_error("센서 측정", e)

    if lcd:
        lcd.move_to(0, 0)
        lcd.putstr("App Error       " if state.app_err
                   else "Val:{:6.0f}     ".format(state.value))
        lcd.move_to(0, 1)
        # 반응속도 게임처럼 실시간 상태를 계속 봐야 하는 앱은 IP와 번갈아
        # 보여주면 정작 중요한 순간(예: "GO" 신호)을 놓칠 수 있습니다.
        # 그런 앱은 SHOW_IP_ON_LCD = False로 IP 토글을 끄고 상태만 고정
        # 표시합니다 (기본값 True — 미세먼지 앱 등 기존 동작 유지).
        show_ip = getattr(app, "SHOW_IP_ON_LCD", True) if app else True
        if show_ip and toggle % 2 == 0:
            ip = str(state.current_ip) if state.current_ip else "No IP"
            tag = "IP:" if state.mode == "ONLINE_STA" else "AP:"
            disp = (tag + ip) if len(ip) <= 13 else ip
            lcd.putstr("{:<16}".format(disp[:16]))
        elif state.app_err:
            lcd.putstr("Check /edit Web ")
        else:
            lcd.putstr("{:<16}".format(state.status_eng[:16]))
    return toggle + 1


# -----------------------------------------------------------------
# 전원 관리 동작
# -----------------------------------------------------------------
def _apply_action(action, lcd, state, server):
    """웹에서 요청한 동작을 실행합니다. 서버 소켓을 닫아야 하면 True."""
    if action == "reboot":
        print("🔄 재부팅합니다...")
        utime.sleep(1)
        machine.reset()

    elif action == "reconnect":
        print("🔁 Wi-Fi 재연결을 시작합니다...")
        return True

    elif action == "sleep":
        state.power_mode = "SLEEP"
        print("🌙 [전원] 절전 모드 (LCD·측정 정지)")
        if lcd:
            lcd.clear()
            lcd.set_backlight(False)

    elif action == "wake":
        state.power_mode = "ON"
        print("☀️ [전원] 절전 해제")
        if lcd:
            lcd.set_backlight(True)
            lcd.display_2lines("Waking up...", "")

    elif action == "halt":
        if lcd:
            lcd.display_2lines("System halted.", "Unplug is safe.")
            utime.sleep(2)
            lcd.clear()
            lcd.set_backlight(False)
        try:
            server.close()
        except Exception:
            pass
        print("⏻ [전원] 정지됨 — 전원을 다시 인가해야 복구됩니다")
        # 워치독은 끌 수 없으므로 feed만 하며 머무릅니다.
        while True:
            watchdog.feed()
            utime.sleep_ms(200)
    return False


# -----------------------------------------------------------------
# 메인 루프
# -----------------------------------------------------------------
def serve(lcd, state):
    server = httpd.make_server()
    print("🚀 웹 서버 가동! 접속: http://{}".format(state.current_ip))

    # 부팅과 Wi-Fi 연결(최대 24초+)이 끝난 지금 워치독을 켭니다.
    # 이보다 먼저 켜면 부팅 도중 리셋 루프에 빠집니다.
    watchdog.start()

    wlan = network.WLAN(network.STA_IF)
    app = state.app_mod
    meas_ms = getattr(app, "DISPLAY_UPDATE_INTERVAL_MS", 2000) if app else 2000

    toggle = 0
    beats = 0
    now = utime.ticks_ms()
    t_meas = t_beat = t_retry = now

    try:
        while True:
            watchdog.feed()
            now = utime.ticks_ms()

            if state.pending_action:
                action, state.pending_action = state.pending_action, None
                if _apply_action(action, lcd, state, server):
                    server.close()
                    return

            # Wi-Fi 끊김 -> AP 모드로 복귀
            if state.mode == "ONLINE_STA" and not wlan.isconnected():
                print("⚠️ Wi-Fi 끊김 감지 -> 오프라인 AP 모드로 전환")
                server.close()
                return

            # AP 모드에서도 저장된 Wi-Fi를 주기적으로 재시도
            if state.mode == "OFFLINE_AP" and \
                    utime.ticks_diff(now, t_retry) >= AP_RETRY_INTERVAL_MS:
                t_retry = now
                if load_wifi_networks():
                    print("🔁 저장된 Wi-Fi 재접속 시도...")
                    server.close()
                    return

            if utime.ticks_diff(now, t_meas) >= meas_ms:
                t_meas = now
                toggle = measure_and_update_lcd(lcd, state, toggle)

            # 하트비트 — 기기가 먹통이 됐을 때 재부팅 후 debug_prev.log로
            # 직전 상태(여유 메모리, Wi-Fi)를 확인하기 위한 기록.
            if utime.ticks_diff(now, t_beat) >= HEARTBEAT_MS:
                t_beat = now
                beats += 1
                print("💓 mem={} wifi={} up={}".format(
                    gc.mem_free(), wlan.isconnected(), state.uptime_str()))
                # 매번 쓰면 플래시 쓰기가 잦아 수명에 불리하므로 주기적으로만.
                if beats % LOG_FLUSH_EVERY == 0:
                    flush_log_to_file()

            httpd.poll(server, state)
            gc.collect()
            utime.sleep_ms(10)

    except Exception as e:
        log_error("서빙 루프", e)
        try:
            server.close()
        except Exception:
            pass
        utime.sleep(1)


def main():
    print("==========================================")
    print(" 🛡️ Pico Core System 가동")
    print("==========================================")

    try:
        machine.freq(cpu_config.load_freq_mhz() * 1_000_000)
        print("⚡ CPU 클럭: {}MHz".format(machine.freq() // 1_000_000))
    except Exception as e:
        log_error("CPU 클럭 설정", e)

    lcd = init_lcd()
    state = State()
    state.app_mod, state.app_err = load_active_app()

    app = state.app_mod
    sync_ms = getattr(app, "CLOUD_SYNC_INTERVAL_MS", 60000) if app else 60000
    register_periodic_task("cloud_sync", _cloud_sync_task(state), sync_ms)
    register_periodic_task("ota", ota.poll, OTA_POLL_MS)
    start_background_worker()

    # boot.py의 롤백 안전망은 "import 시점" 오류(문법 오류, 없는 모듈)만
    # 잡습니다. main()이 도는 도중 난 예외는 잡아주지 않으므로, 여기서
    # 직접 감싸 다시 시도합니다. 안 그러면 일시적인 네트워크 오류 하나에
    # 기기가 그대로 죽어 전원을 뽑기 전까지 돌아오지 못합니다.
    while True:
        try:
            connect_network(lcd, state)
            serve(lcd, state)
        except Exception as e:
            log_error("메인 루프", e)
            flush_log_to_file()
            utime.sleep(2)


def _cloud_sync_task(state):
    """state를 클로저로 잡아, 호출 시점의 최신 측정값으로 동기화하는
    무인자 함수를 만듭니다 (register_periodic_task는 무인자만 받음)."""
    def task():
        app = state.app_mod
        if not (app and hasattr(app, 'sync_with_google_sheets')):
            return
        if state.mode != "ONLINE_STA" or \
                not network.WLAN(network.STA_IF).isconnected():
            return
        try:
            app.sync_with_google_sheets(state.value, state.avg_v, state.status_eng)
        except Exception as e:
            log_error("클라우드 동기화", e)
    return task


if __name__ == "__main__":
    main()
