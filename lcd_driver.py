# =================================================================
# lcd_driver.py : I2C 1602 LCD 드라이버 (PCF8574 I2C 어댑터용)
# =================================================================
import utime

from console_log import log_error

_BL_ON = 0x08   # PCF8574의 백라이트 비트
_BL_OFF = 0x00
_E = 0x04       # Enable 스트로브 비트
_RS = 0x01      # 데이터/명령 선택 비트


class I2cLcd:
    def __init__(self, i2c, i2c_addr):
        self.i2c = i2c
        self.addr = i2c_addr
        self._error_logged = False
        self._bl = _BL_ON  # 백라이트 상태 (전원 관리 화면에서 끌 수 있음)
        utime.sleep_ms(200)
        for _ in range(3):
            self._send_nibble(0x30)
            utime.sleep_ms(10)
        self._send_nibble(0x20)
        utime.sleep_ms(10)

        for cmd in [0x28, 0x0C, 0x06, 0x01]:
            self._write_cmd(cmd)
            utime.sleep_ms(5)

    def _log_once(self, e):
        # LCD는 주기적으로 계속 쓰기 때문에, 연결이 끊기면 매번 로그를 남기지 않고
        # 부팅 후 최초 1회만 남깁니다 (콘솔 스팸 방지).
        if not self._error_logged:
            log_error("LCD I2C", e)
            self._error_logged = True

    def _write_byte(self, b):
        try:
            self.i2c.writeto(self.addr, bytes([b]))
        except Exception as e:
            self._log_once(e)
        utime.sleep_us(50)

    def _send_nibble(self, nibble):
        b = (nibble & 0xF0) | self._bl
        self._write_byte(b | _E)
        self._write_byte(b)

    def _write_4bits(self, value, rs_bit):
        base = self._bl | rs_bit
        for nibble in ((value & 0xF0), ((value << 4) & 0xF0)):
            self._write_byte(nibble | base | _E)
            self._write_byte(nibble | base)

    def _write_cmd(self, cmd):
        self._write_4bits(cmd, 0)

    def _write_data(self, data):
        self._write_4bits(data, _RS)

    def set_backlight(self, on):
        """백라이트를 켜고 끕니다 (전원 관리의 절전/종료 모드에서 사용).
        다음 전송부터 반영되므로, 즉시 반영하려면 아무 명령이나 한 번 더
        보내야 합니다 — 여기서는 커서 위치 지정으로 대신합니다."""
        self._bl = _BL_ON if on else _BL_OFF
        self._write_cmd(0x80)

    def clear(self):
        self._write_cmd(0x01)
        utime.sleep_ms(2)

    def move_to(self, col, row):
        addr = col + (0x40 if row == 1 else 0x00)
        self._write_cmd(0x80 | addr)

    def putstr(self, string):
        for char in string:
            self._write_data(ord(char))

    def display_2lines(self, line1, line2=""):
        self.move_to(0, 0)
        self.putstr("{:<16}".format(line1[:16]))
        self.move_to(0, 1)
        self.putstr("{:<16}".format(line2[:16]))
