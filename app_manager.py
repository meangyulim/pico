# =================================================================
# app_manager.py : 센서별 "앱" 전환 관리
# =================================================================
# 이 보드에 어떤 센서가 연결되어 있는지에 따라 다른 로직(반응속도 게임,
# 미세먼지 모니터 등)을 실행할 수 있도록, app_*.py 파일 중 하나를 골라
# 활성화합니다. 각 앱은 REQUIRED_APP_ATTRS를 구현해야 합니다.
#
# 활성 앱 이름은 active_app.json에 로컬로 저장됩니다. 이 파일은 기기마다
# 다를 수 있는 "런타임 설정"이라(wifi_config.json과 동일한 취급) 깃/OTA
# 동기화 대상이 아닙니다 — 리포를 그대로 받아써도 각자 연결한 센서에 맞는
# 앱을 각자 고를 수 있습니다.
# =================================================================
import json
import os

from console_log import log_error

ACTIVE_APP_FILE = "active_app.json"
DEFAULT_APP = "app_idle"
APP_PREFIX = "app_"

# app_manager.py 자기 자신도 이름이 APP_PREFIX로 시작하지만 센서 앱이
# 아니므로, /apps 목록에서 제외합니다.
_NOT_AN_APP = {"app_manager"}

REQUIRED_APP_ATTRS = [
    "read_dust_sensor",         # () -> (avg_voltage: float, value: float)
    "get_status_info",          # (value: float) -> (eng: str, kor: str, color_hex: str)
    "sync_with_google_sheets",  # (value, voltage, status_eng) -> bool
    "play_alert_beep",          # () -> None
    "is_muted",                 # bool
    "alert_threshold",          # float
    "cloud_sync_status",        # str
]


def list_available_apps():
    try:
        names = os.listdir()
    except Exception:
        return []
    apps = [
        n[:-3] for n in names
        if n.startswith(APP_PREFIX) and n.endswith(".py") and n[:-3] not in _NOT_AN_APP
    ]
    apps.sort()
    return apps


def get_active_app_name():
    try:
        with open(ACTIVE_APP_FILE) as f:
            data = json.load(f)
        name = data.get("app", "")
        if name and (name + ".py") in os.listdir():
            return name
    except Exception:
        pass
    return DEFAULT_APP


def set_active_app_name(name):
    try:
        with open(ACTIVE_APP_FILE, "w") as f:
            json.dump({"app": name}, f)
        return True
    except Exception as e:
        log_error("앱 설정 저장", e)
        return False


def validate_app_module(app_mod):
    missing = [name for name in REQUIRED_APP_ATTRS if not hasattr(app_mod, name)]
    if missing:
        print(f"⚠️ 활성 앱에 다음 항목이 없습니다 (해당 기능만 비활성화됩니다): {', '.join(missing)}")


def load_active_app():
    """활성 앱을 안전하게 로드. 실패해도 예외를 격리해서 시스템은 계속 삽니다."""
    name = get_active_app_name()
    try:
        app_mod = __import__(name)
        validate_app_module(app_mod)
        return app_mod, None
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        print(f"❌ [{name} 로드 오류] {err_msg}")
        return None, err_msg
