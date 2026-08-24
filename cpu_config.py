# =================================================================
# cpu_config.py : CPU 클럭(오버클럭) 선택 저장/불러오기
# =================================================================
# 전압은 건드리지 않는 선의 값들만 고릅니다. 기기별 개체차가 있으니
# 문제가 보이면 웹에서 다시 기본값(150MHz)으로 되돌릴 수 있습니다.
#
# 300MHz는 실제 기기에서 부팅 자체가 안 되는 것이 확인돼 목록에서 뺐습니다.
# httpd._wifi_survives_freq의 검증은 0.3초짜리 짧은 라이브 테스트라 부팅
# 전체 과정(LCD/플래시 접근 등)에서만 드러나는 불안정까지는 못 잡아냅니다.
# 이미 저장돼 있던 값이 여기서 빠지면 load_freq_mhz()가 자동으로 기본값
# (150MHz)으로 되돌리므로, 다음 부팅부터 저절로 복구됩니다.
import json

CONFIG_FILE = "cpu_config.json"
DEFAULT_FREQ_MHZ = 150
FREQ_OPTIONS_MHZ = (150, 200, 225, 250, 270)


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
