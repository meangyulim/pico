# =================================================================
# ota.py : GitHub manifest.json 기반 수동 업데이트
# =================================================================
# 아주 작은 manifest.json(파일별 sha256만 담음)을 받아 로컬 해시와 비교한
# 뒤, 달라진 파일만 내려받습니다.
#
# 이전 구조에서 고친 것들:
#  * 파일을 res.content로 통째로 메모리에 올렸습니다. main.py는 35KB라
#    그만한 연속 블록이 필요한데, 힙이 조각나면 여유 메모리가 370KB여도
#    실패합니다 (실제로 ENOMEM 발생). 이제 512바이트씩 임시 파일로
#    흘려보내며 해시를 같이 계산합니다 — 메모리에 남는 건 조각 하나뿐.
#  * 받을 파일 목록을 이름으로 하드코딩해서, 새로 추가된 모듈은 영영
#    전달되지 못했습니다(판정 주체가 기기의 구버전 ota.py라서). 이제
#    구조로 판정합니다 (_is_ota_target).
#  * 중간에 실패해도 재부팅해버려서 버전이 뒤섞인 채 부팅 -> ImportError
#    -> boot.py 전체 롤백이 반복됐습니다. 이제 완주했을 때만 재부팅합니다.
#  * 자동 주기 확인을 끄고 대시보드 버튼으로만 실행합니다.
# =================================================================
import gc
import json
import machine
import network
import os
import urequests
import utime

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

from console_log import log_error
from file_editor import file_hash, backup_file, _remove_quiet

try:
    import watchdog
except ImportError:
    class watchdog:
        @staticmethod
        def feed():
            pass

OTA_ENABLED = True
OTA_AUTO_CHECK = False          # 대시보드의 "지금 업데이트 확인" 버튼으로만 실행
OTA_CHECK_INTERVAL_MS = 5 * 60 * 1000   # OTA_AUTO_CHECK를 켤 때만 쓰이는 주기
OTA_REPO_RAW_BASE = "https://raw.githubusercontent.com/meangyulim/pico/main"
OTA_MANIFEST_URL = OTA_REPO_RAW_BASE + "/manifest.json"
OTA_MAX_FILE_SIZE = 128 * 1024
OTA_REQUEST_TIMEOUT_SEC = 10    # urequests는 기본 타임아웃이 없어 무한정 멈출 수 있음
DL_CHUNK = 512

OTA_STATE_FILE = "ota_state.json"

# 기기별 로컬 설정/로그는 리포 내용으로 덮어쓰면 안 됩니다.
OTA_PROTECTED_FILES = {
    "wifi_config.json", "active_app.json", "ota_state.json",
    "debug.log", "debug_prev.log",
}

_last_check_ms = None
_last_result = "확인 전"
_manual_requested = False
_in_progress = False


def _is_ota_target(name):
    """manifest 항목이 실제로 받아올 대상인지 구조로 판정합니다.
    이름을 나열하지 않으므로 새로 추가된 모듈도 그대로 전달됩니다."""
    if not name or name.startswith("_"):
        return False                      # _version 같은 메타데이터
    if "/" in name or "\\" in name or ".." in name:
        return False                      # 경로 탈출 방지
    if name in OTA_PROTECTED_FILES:
        return False
    return name.endswith(".py")


# -----------------------------------------------------------------
# 상태 표시
# -----------------------------------------------------------------
def get_ota_status_text():
    if _in_progress:
        return "확인 중..."
    if _last_check_ms is None:
        return "수동 확인 대기 중" if not OTA_AUTO_CHECK else "확인 전"
    ago = utime.ticks_diff(utime.ticks_ms(), _last_check_ms) // 1000
    ago_s = "{}초 전".format(ago) if ago < 60 else "{}분 전".format(ago // 60)
    return ago_s + " - " + _last_result


def _save_state(version, names):
    try:
        with open(OTA_STATE_FILE, "w") as f:
            json.dump({"version": version or "?", "applied_at": utime.time(),
                       "files": names}, f)
    except Exception as e:
        log_error("OTA 상태 저장", e)


def get_last_update_text():
    """마지막으로 실제 적용된 시각(KST)과 버전. NTP 동기화 전이면 시각 불명."""
    try:
        with open(OTA_STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        return "아직 없음"
    ver = st.get("version", "?")
    at = st.get("applied_at", 0)
    if not at:
        return "버전 {} (시각 불명)".format(ver)
    # NTP 동기화 전이면 utime.time()은 부팅 후 경과 초에 불과해 엉뚱한
    # 날짜가 나옵니다. 2024년 이후 값만 진짜 시각으로 취급합니다.
    if at < 780000000:
        return "버전 {} (NTP 미동기화)".format(ver)
    t = utime.localtime(at + 9 * 3600)   # UTC -> KST
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d} (버전 {})".format(
        t[0], t[1], t[2], t[3], t[4], ver)


# -----------------------------------------------------------------
# 다운로드
# -----------------------------------------------------------------
def _download_verified(name, expected_hex):
    """파일을 조각 단위로 임시 파일에 받으면서 해시를 계산합니다.
    해시가 맞으면 임시 파일 경로를, 아니면 None을 돌려줍니다."""
    tmp = name + ".tmp"
    _remove_quiet(tmp)          # 이전 실패로 남은 조각이 있으면 버리고 시작
    h = hashlib.sha256()
    total = 0
    res = None
    try:
        res = urequests.get(OTA_REPO_RAW_BASE + "/" + name,
                            timeout=OTA_REQUEST_TIMEOUT_SEC)
        with open(tmp, "wb") as f:      # 조각마다 열고 닫지 않고 한 번만 엽니다
            while True:
                chunk = res.raw.read(DL_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > OTA_MAX_FILE_SIZE:
                    print("⚠️ [OTA] {} 크기 초과로 건너뜁니다".format(name))
                    _remove_quiet(tmp)
                    return None
                f.write(chunk)
                h.update(chunk)
                watchdog.feed()
    except Exception as e:
        log_error("OTA 다운로드({})".format(name), e)
        _remove_quiet(tmp)
        return None
    finally:
        if res is not None:
            try:
                res.close()
            except Exception:
                pass
        gc.collect()

    if h.digest().hex() != expected_hex:
        # 잘렸거나 손상된 다운로드 — 해시 검증이 이런 경우를 걸러줍니다.
        print("⚠️ [OTA] {} 해시 불일치로 적용하지 않습니다".format(name))
        _remove_quiet(tmp)
        return None
    return tmp


def _run_check():
    global _last_check_ms, _last_result, _in_progress
    applied = []
    completed = False
    version = None
    _in_progress = True
    try:
        watchdog.feed()
        res = urequests.get(OTA_MANIFEST_URL, timeout=OTA_REQUEST_TIMEOUT_SEC)
        try:
            manifest = res.json()
        finally:
            res.close()
        version = manifest.get("_version")

        for name in manifest:
            watchdog.feed()
            gc.collect()          # 파일마다 회수해서 조각화 압박을 줄임
            if not _is_ota_target(name):
                continue
            remote_hex = manifest[name].get("sha256", "")
            if not remote_hex:
                continue
            local = file_hash(name)
            if local is not None and local.hex() == remote_hex:
                continue

            tmp = _download_verified(name, remote_hex)
            if tmp is None:
                continue          # 이 파일만 건너뛰고 나머지는 계속
            backup_file(name)
            _remove_quiet(name)
            os.rename(tmp, name)  # rename은 원자적 — 반쪽 파일이 남지 않음
            applied.append(name)
            print("⬇️ [OTA] {} 적용".format(name))

        completed = True
        _last_result = ("적용됨: " + ", ".join(applied)) if applied else "변경 없음"
    except Exception as e:
        log_error("OTA 확인", e)
        _last_result = "오류: " + type(e).__name__
    finally:
        _last_check_ms = utime.ticks_ms()
        _in_progress = False
        gc.collect()
        watchdog.feed()

    if applied and not completed:
        # 중간에 끊긴 채 재부팅하면 버전이 뒤섞인 상태로 부팅합니다
        # (manifest는 이름순이라 main.py가 web_ui.py보다 먼저 적용됨).
        # 그러면 ImportError -> boot.py 전체 롤백 -> 다음 주기에 같은 실패,
        # 이 악순환으로 기기가 구버전에 갇힙니다. 남은 파일은 해시가 여전히
        # 다르므로 다음 확인 때 이어받습니다.
        print("⚠️ [OTA] 중간 실패로 재부팅하지 않습니다 (적용: {}). "
              "다음 확인 때 이어서 받습니다.".format(", ".join(applied)))
        return

    if applied:
        _save_state(version, applied)
        print("🔄 [OTA] 적용 완료, 3초 후 재부팅합니다...")
        utime.sleep(3)
        machine.reset()


# -----------------------------------------------------------------
# 외부 진입점
# -----------------------------------------------------------------
def request_manual_check():
    """웹 요청 스레드에서 호출 — 플래그만 세웁니다. 여기서 네트워크를
    타면 응답이 수 초간 막히므로, 실제 확인은 core1 워커가 합니다."""
    global _manual_requested
    _manual_requested = True


def poll():
    """bg_thread 워커가 짧은 주기로 호출합니다."""
    global _manual_requested, _last_result, _last_check_ms
    if not OTA_ENABLED:
        return
    if OTA_AUTO_CHECK:
        if (_last_check_ms is not None and
                utime.ticks_diff(utime.ticks_ms(), _last_check_ms) < OTA_CHECK_INTERVAL_MS):
            return
    elif not _manual_requested:
        return

    _manual_requested = False
    if not network.WLAN(network.STA_IF).isconnected():
        _last_result = "Wi-Fi 미연결로 건너뜀"
        _last_check_ms = utime.ticks_ms()
        return
    print("🛰️ [OTA] 업데이트 확인 시작")
    _run_check()
