import time

import network
from machine import Pin

try:
    from wifi_config import SSID, PASSWORD
except ImportError:
    SSID, PASSWORD = None, None

led = Pin("LED", Pin.OUT)


def connect_wifi():
    if not SSID:
        print("wifi_config.py 가 없습니다. wifi_config.example.py 를 참고해 만들어주세요.")
        return None

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)

    for _ in range(20):
        if wlan.isconnected():
            print("Wi-Fi 연결됨:", wlan.ifconfig())
            return wlan
        led.toggle()
        time.sleep(0.5)

    print("Wi-Fi 연결 실패")
    return None


def blink_forever():
    while True:
        led.toggle()
        time.sleep(1)


if __name__ == "__main__":
    connect_wifi()
    blink_forever()
