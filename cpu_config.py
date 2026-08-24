# =================================================================
# cpu_config.py : CPU 클럭(오버클럭) 선택 저장/불러오기
# =================================================================
# 전압은 건드리지 않는 선의 값들만 고릅니다. 기기별 개체차가 있으니
# 문제가 보이면 웹에서 다시 기본값(150MHz)으로 되돌릴 수 있습니다.
import json

CONFIG_FILE = "cpu_config.json"
DEFAULT_FREQ_MHZ = 150
FREQ_OPTIONS_MHZ = (150, 200, 225, 250, 270, 300)


def load_freq_mhz():
    try:
        with open(CONFIG_FILE, "r") as f:
            mhz = json.load(f).get("freq_mhz", DEFAULT_FREQ_MHZ)
    except Exception:
        return DEFAULT_FREQ_MHZ
    return mhz if mhz in FREQ_OPTIONS_MHZ else DEFAULT_FREQ_MHZ


def save_freq_mhz(mhz):
    with open(CONFIG_FILE, "w") as f:
        json.dump({"freq_mhz": mhz}, f)
