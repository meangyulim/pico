# =================================================================
# 💤 app_idle.py : 센서/모듈 없음 (디스플레이 전용 대기 모드)
# =================================================================
# 현재 연결된 센서나 게임 모듈이 없을 때 쓰는 최소 앱입니다. 하드웨어
# 의존성이 전혀 없어서 안전하게 로드됩니다. 나중에 새 센서/모듈을
# 연결하면 그에 맞는 app_*.py를 새로 만들어 /apps에서 전환하세요.
# =================================================================

DISPLAY_UPDATE_INTERVAL_MS = 2000
CLOUD_SYNC_INTERVAL_MS = 60000
alert_threshold = 0
is_muted = True
cloud_sync_status = "대기 모드 (연결된 센서 없음)"


def read_dust_sensor():
    return 0.0, 0.0


def get_status_info(_value):
    return "IDLE", "대기 중 (연결된 센서 없음)", "#64748b"


def play_alert_beep():
    pass
