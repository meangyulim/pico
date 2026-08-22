# =================================================================
# lcd_driver.py : I2C 1602 LCD 드라이버 (PCF8574 I2C 어댑터용)
# =================================================================
import utime

from console_log import log_error


class I2cLcd:
    def __init__(self, i2c, i2c_addr):
        self.i2c = i2c
        self.addr = i2c_addr
        self._error_logged = False
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

    def _send_nibble(self, nibble):
        b = (nibble & 0xF0) | 0x08  # 백라이트 ON
        try:
            self.i2c.writeto(self.addr, bytes([b | 0x04]))
            utime.sleep_us(50)
            self.i2c.writeto(self.addr, bytes([b]))
            utime.sleep_us(50)
        except Exception as e:
            self._log_once(e)

    def _write_cmd(self, cmd):
        buf = [
            (cmd & 0xF0) | 0x0C,
            (cmd & 0xF0) | 0x08,
            ((cmd << 4) & 0xF0) | 0x0C,
            ((cmd << 4) & 0xF0) | 0x08
        ]
        for b in buf:
            try:
                self.i2c.writeto(self.addr, bytes([b]))
            except Exception as e:
                self._log_once(e)
            utime.sleep_us(50)

    def _write_data(self, data):
        buf = [
            (data & 0xF0) | 0x0D,  # RS=1, 백라이트 ON
            (data & 0xF0) | 0x09,
            ((data << 4) & 0xF0) | 0x0D,
            ((data << 4) & 0xF0) | 0x09
        ]
        for b in buf:
            try:
                self.i2c.writeto(self.addr, bytes([b]))
            except Exception as e:
                self._log_once(e)
            utime.sleep_us(50)

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
