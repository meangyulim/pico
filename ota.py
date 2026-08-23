# =================================================================
# ota.py : GitHub manifest.json 폴링 기반 자동 업데이트
# =================================================================
# 매번 전체 파일을 받으면 느린 Wi-Fi에서 부담이 크므로, 아주 작은
# manifest.json(파일별 sha256 해시만 담음)만 주기적으로 확인하고,
# 실제로 해시가 달라진 파일만 통째로 받아옵니다. main.py를 포함한 핵심
# 모듈이 바뀌면 재부팅해서 boot.py의 안전망을 그대로 거칩니다.
# =================================================================
import gc
import json
import machine
import network
import urequests
import utime

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

from console_log import log_error
from file_editor import file_hash, backup_file

try:
    import watchdog
except ImportError:  # OTA로 아직 전달되지 않은 새 모듈 — 없어도 동작해야 함
    class watchdog:
        @staticmethod
        def feed():
            pass

OTA_ENABLED = True
# 자동 주기 확인을 끄고, 대시보드의 "지금 업데이트 확인" 버튼을 눌렀을 때만
# 확인/적용합니다. 47초마다 GitHub에 HTTPS로 붙던 걸 없애서 네트워크 활동이
# 크게 줄고, 원인 미상의 먹통을 좁히는 데도 유리합니다.
OTA_AUTO_CHECK = False
OTA_REPO_RAW_BASE = "https://raw.githubusercontent.com/meangyulim/pico/main"
OTA_MANIFEST_URL = OTA_REPO_RAW_BASE + "/manifest.json"
OTA_CHECK_INTERVAL_MS = 47 * 1000  # 클라우드 동기화(60초)와 안 겹치게 60의 배수가 아닌 값을 씀
OTA_MAX_FILE_SIZE = 128 * 1024
OTA_REQUEST_TIMEOUT_SEC = 10  # urequests는 기본 타임아웃이 없어서, 네트워크가
# 응답을 영영 안 주면 이 스레드가 무한정 멈춰버릴 수 있음 (심하면 GC의
# "두 코어 동시 정지"에 걸려 메인 루프까지 같이 얼어붙을 수 있음)

# 기기별 로컬 설정/로그는 리포 상태로 덮어쓰면 안 됩니다 (각 기기가 고른
# 앱/Wi-Fi가 매번 초기화되므로). 애초에 manifest에 실리지도 않지만,
# 이중 안전장치로 여기서도 막습니다.
OTA_PROTECTED_FILES = {
    "wifi_config.json", "active_app.json", "ota_state.json",
    "debug.log", "debug_prev.log",
}


def _is_ota_target(name):
    """
    manifest에 실린 이름이 실제로 받아올 대상인지 판정합니다.

    예전에는 허용 파일명을 집합(OTA_ALLOWED_TARGETS)에 하드코딩했는데,
    그러면 "새로 추가된 모듈"은 영영 전달되지 못하는 부트스트랩 문제가
    있었습니다 — 판정을 하는 주체가 기기에 이미 깔려 있는 '구버전'
    ota.py라서, 새 파일 이름을 알 리가 없기 때문입니다. 실제로
    watchdog.py를 도입할 때 이 문제가 터졌습니다: main.py 등은 새
    버전으로 갱신됐는데 watchdog.py만 목록에 없어 안 와서, 재부팅 후
    ImportError -> boot.py가 핵심 모듈을 전부 .bak으로 롤백 -> 구버전
    복귀가 반복됐습니다.

    그래서 이제는 이름을 나열하는 대신 구조로 판정합니다. manifest 자체가
    우리 리포에서 HTTPS로 받아온 것이고 파일마다 sha256을 재검증하므로,
    목록을 따로 유지해서 얻는 실익도 크지 않았습니다.
    """
    if not name or name.startswith("_"):
        return False  # manifest의 메타데이터(_version 등)
    if "/" in name or "\\" in name or ".." in name:
        return False  # 경로 탈출 방지 — 항상 최상위 파일명만 허용
    if name in OTA_PROTECTED_FILES:
        return False
    return name.endswith(".py")

# 웹 대시보드에 "OTA 마지막 확인" 상태를 보여주기 위한 값. 콘솔(Thonny)을
# 안 보고 있어도 브라우저로 확인할 수 있게 함.
_last_check_ms = None
_last_result = "확인 전"
_manual_check_requested = False
_check_in_progress = False

# 실제로 파일이 바뀌어 적용된 마지막 시각/버전은 재부팅 후에도 남아있어야
# 하므로(업데이트 적용 자체가 재부팅을 유발함) 파일에 저장합니다.
OTA_STATE_FILE = "ota_state.json"


def get_ota_status_text():
    if _check_in_progress:
        return "확인 중..."
    if _last_check_ms is None:
        return "수동 확인 대기 중" if not OTA_AUTO_CHECK else "확인 전"
    ago_sec = utime.ticks_diff(utime.ticks_ms(), _last_check_ms) // 1000
    ago_str = f"{ago_sec}초 전" if ago_sec < 60 else f"{ago_sec // 60}분 전"
    return f"{ago_str} - {_last_result}"


def _save_ota_state(version, applied_names):
    try:
        state = {
            "version": version or "?",
            "applied_at": utime.time(),
            "files": applied_names,
        }
        with open(OTA_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log_error("OTA 상태 저장", e)


def get_last_update_text():
    """마지막으로 실제 업데이트가 적용된 시각(NTP 동기화 기준, KST)과 버전.
    아직 한 번도 적용된 적이 없으면(또는 NTP 동기화 전이면) 안내 문구를 반환."""
    try:
        with open(OTA_STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        return "아직 없음"

    version = state.get("version", "?")
    applied_at = state.get("applied_at", 0)
    if not applied_at:
        return f"버전 {version} (시각 불명)"

    # utime.time()은 NTP 동기화가 안 됐으면 부팅 이후 경과 초에 불과해
    # 말이 안 되는 날짜가 나올 수 있음 — 대략적인 판별로 2024년 이후만
    # "실제 날짜"로 취급 (동기화 안 됐으면 훨씬 작은 값이 나옴).
    if applied_at < 780000000:  # 2024-09-XX 근방 (MicroPython epoch 2000-01-01 기준)
        return f"버전 {version} (NTP 미동기화, 시각 불명)"

    t = utime.localtime(applied_at + 9 * 3600)  # UTC -> KST(+9h)
    date_str = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}".format(t[0], t[1], t[2], t[3], t[4])
    return f"{date_str} (버전 {version})"


def _run_ota_check():
    global _last_check_ms, _last_result, _check_in_progress
    changed_any = False
    completed = False
    applied_names = []
    _check_in_progress = True
    try:
        watchdog.feed()
        res = urequests.get(OTA_MANIFEST_URL, timeout=OTA_REQUEST_TIMEOUT_SEC)
        try:
            manifest = res.json()
        finally:
            res.close()

        for name, meta in manifest.items():
            watchdog.feed()  # 파일이 여러 개면 전체가 8초를 넘길 수 있음
            # 한 번에 여러 파일을 받으면 파일 내용 + TLS 버퍼가 겹쳐
            # ENOMEM이 날 수 있어, 파일마다 회수하고 시작합니다.
            gc.collect()
            if not _is_ota_target(name):
                continue  # manifest에 엉뚱한 이름이 있어도 무시 (안전장치)
            remote_hash_hex = meta.get("sha256", "")
            if not remote_hash_hex:
                continue
            local_digest = file_hash(name)
            local_hash_hex = local_digest.hex() if local_digest else ""
            if remote_hash_hex == local_hash_hex:
                continue

            res2 = urequests.get(OTA_REPO_RAW_BASE + "/" + name, timeout=OTA_REQUEST_TIMEOUT_SEC)
            try:
                content = res2.content
            finally:
                res2.close()
            watchdog.feed()

            if len(content) > OTA_MAX_FILE_SIZE:
                print(f"⚠️ [OTA] {name} 크기가 너무 커서 건너뜁니다 ({len(content)} bytes)")
                continue

            verify = hashlib.sha256()
            verify.update(content)
            if verify.digest().hex() != remote_hash_hex:
                print(f"⚠️ [OTA] {name} 다운로드 내용이 매니페스트 해시와 달라 적용하지 않습니다.")
                continue

            backup_file(name)
            with open(name, "wb") as f:
                f.write(content)
            changed_any = True
            applied_names.append(name)
            print(f"⬇️ [OTA] {name} 업데이트 적용")

        completed = True  # 모든 파일을 끝까지 처리했음 (중간에 끊기지 않음)
        _last_result = f"적용됨: {', '.join(applied_names)}" if applied_names else "변경 없음"
    except Exception as e:
        log_error("OTA 확인", e)
        _last_result = f"오류: {type(e).__name__}"
    finally:
        _last_check_ms = utime.ticks_ms()
        _check_in_progress = False
        gc.collect()
        watchdog.feed()

    if changed_any and not completed:
        # 중간에 끊긴 채로 재부팅하면 안 됩니다. manifest는 이름순으로
        # 처리되므로(main.py가 web_ui.py보다 먼저), 도중에 실패하면
        # "새 main.py + 구 web_ui.py"처럼 버전이 뒤섞인 상태가 됩니다.
        # 그대로 부팅하면 ImportError -> boot.py가 핵심 모듈을 전부
        # .bak으로 롤백 -> 다음 주기에 같은 실패 반복, 이 악순환으로
        # 기기가 계속 구버전에 갇힙니다. 그래서 재부팅하지 않고 이번
        # 주기를 포기합니다 — 남은 파일은 해시가 여전히 다르므로 다음
        # 확인 때 이어서 받아옵니다.
        print("⚠️ [OTA] 업데이트가 중간에 실패해 재부팅하지 않습니다 "
              f"(적용된 파일: {', '.join(applied_names)}). 다음 확인 때 이어서 받습니다.")
        return

    if changed_any:
        _save_ota_state(manifest.get("_version"), applied_names)
        print("🔄 [OTA] 변경 사항 적용 완료, 3초 후 재부팅합니다...")
        utime.sleep(3)
        machine.reset()


def trigger_ota_check():
    """Wi-Fi가 연결돼 있지 않으면 조용히 건너뜁니다."""
    global _last_result, _last_check_ms
    if not OTA_ENABLED:
        return
    if not network.WLAN(network.STA_IF).isconnected():
        _last_result = "Wi-Fi 미연결로 건너뜀"
        _last_check_ms = utime.ticks_ms()
        return
    _run_ota_check()


def request_manual_check():
    """웹에서 '지금 업데이트 확인'을 눌렀을 때 호출합니다. 여기서 바로
    네트워크를 타면 웹 요청 응답이 수 초간 막히므로, 플래그만 세워두고
    실제 확인은 core1의 백그라운드 워커가 집어가서 수행합니다."""
    global _manual_check_requested
    _manual_check_requested = True


def poll_manual_ota_request():
    """bg_thread 워커가 짧은 주기로 호출 — 요청이 들어와 있을 때만 실행."""
    global _manual_check_requested
    if OTA_AUTO_CHECK:
        # 자동 모드로 되돌린 경우: 이 함수는 짧은 주기로 불리므로, 여기서
        # OTA_CHECK_INTERVAL_MS를 직접 지켜야 매번 확인하지 않습니다.
        if _last_check_ms is not None and \
                utime.ticks_diff(utime.ticks_ms(), _last_check_ms) < OTA_CHECK_INTERVAL_MS:
            return
        trigger_ota_check()
        return
    if not _manual_check_requested:
        return
    _manual_check_requested = False
    print("🛰️ [OTA] 수동 업데이트 확인 요청됨")
    trigger_ota_check()
