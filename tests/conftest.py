"""데스크톱에서 MicroPython 전용 모듈을 흉내 내는 스텁.

ota.py / httpd.py 등은 machine·network·urequests를 import하므로,
그대로는 데스크톱 테스트에서 import되지 않습니다. 여기서 최소한의
가짜 모듈을 sys.modules에 심어 두면 순수 로직 부분을 테스트할 수
있습니다 (실제 하드웨어 동작이 아니라 "판정/파싱 로직"만 검증하는
용도입니다).
"""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _stub(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _WLAN:
    def __init__(self, *a):
        pass

    def isconnected(self):
        return False

    def active(self, *a):
        return True

    def config(self, *a, **k):
        return None

    def ifconfig(self, *a):
        return ("0.0.0.0", "", "", "")

    def scan(self):
        return []

    def connect(self, *a):
        return None


_stub("machine",
      WDT=lambda **k: types.SimpleNamespace(feed=lambda: None),
      reset=lambda: None,
      freq=lambda: 150_000_000,
      Pin=lambda *a, **k: None,
      I2C=lambda *a, **k: None,
      ADC=lambda *a, **k: None,
      PWM=lambda *a, **k: None,
      Timer=lambda *a, **k: None)

_stub("network", WLAN=_WLAN, STA_IF=0, AP_IF=1)

_stub("urequests", get=lambda *a, **k: None, post=lambda *a, **k: None)

import time as _time  # noqa: E402

_stub("utime",
      ticks_ms=lambda: int(_time.monotonic() * 1000),
      ticks_diff=lambda a, b: a - b,
      ticks_add=lambda a, b: a + b,
      time=lambda: int(_time.time()),
      localtime=_time.localtime,
      sleep=lambda s: None,
      sleep_ms=lambda ms: None,
      sleep_us=lambda us: None)

_stub("uhashlib")  # 비어 있으면 file_editor/ota가 표준 hashlib으로 폴백

# MicroPython의 gc.mem_free()는 CPython에 없으므로 테스트용으로 채웁니다.
import gc as _gc  # noqa: E402
if not hasattr(_gc, "mem_free"):
    _gc.mem_free = lambda: 123456
