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
import urequests
import utime

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

from console_log import log_error
from bg_thread import run_exclusive
from file_editor import file_hash, backup_file

OTA_ENABLED = True
OTA_REPO_RAW_BASE = "https://raw.githubusercontent.com/meangyulim/pico/main"
OTA_MANIFEST_URL = OTA_REPO_RAW_BASE + "/manifest.json"
OTA_CHECK_INTERVAL_MS = 47 * 1000  # 클라우드 동기화(60초)와 안 겹치게 60의 배수가 아닌 값을 씀
OTA_MAX_FILE_SIZE = 128 * 1024

# active_app.json/wifi_config.json 같은 기기별 로컬 설정은 일부러 뺐습니다
# (리포 상태로 덮어쓰면 각 기기가 고른 앱/Wi-Fi가 매번 초기화되므로).
OTA_ALLOWED_TARGETS = {
    "boot.py", "main.py",
    "console_log.py", "bg_thread.py", "lcd_driver.py", "wifi_manager.py",
    "web_ui.py", "file_editor.py", "ota.py", "app_manager.py", "netutil.py",
    "app_reaction_game.py", "app_dust_monitor.py", "app_idle.py",
}

# 웹 대시보드에 "OTA 마지막 확인" 상태를 보여주기 위한 값. 콘솔(Thonny)을
# 안 보고 있어도 브라우저로 확인할 수 있게 함.
_last_check_ms = None
_last_result = "확인 전"

# 실제로 파일이 바뀌어 적용된 마지막 시각/버전은 재부팅 후에도 남아있어야
# 하므로(업데이트 적용 자체가 재부팅을 유발함) 파일에 저장합니다.
OTA_STATE_FILE = "ota_state.json"


def get_ota_status_text():
    if _last_check_ms is None:
        return "확인 전"
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
    global _last_check_ms, _last_result
    changed_any = False
    applied_names = []
    try:
        res = urequests.get(OTA_MANIFEST_URL)
        try:
            manifest = res.json()
        finally:
            res.close()

        for name, meta in manifest.items():
            if name not in OTA_ALLOWED_TARGETS:
                continue  # manifest에 엉뚱한 이름이 있어도 무시 (안전장치)
            remote_hash_hex = meta.get("sha256", "")
            if not remote_hash_hex:
                continue
            local_digest = file_hash(name)
            local_hash_hex = local_digest.hex() if local_digest else ""
            if remote_hash_hex == local_hash_hex:
                continue

            res2 = urequests.get(OTA_REPO_RAW_BASE + "/" + name)
            try:
                content = res2.content
            finally:
                res2.close()

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

        _last_result = f"적용됨: {', '.join(applied_names)}" if applied_names else "변경 없음"
    except Exception as e:
        log_error("OTA 확인", e)
        _last_result = f"오류: {type(e).__name__}"
    finally:
        _last_check_ms = utime.ticks_ms()
        gc.collect()

    if changed_any:
        _save_ota_state(manifest.get("_version"), applied_names)
        print("🔄 [OTA] 변경 사항 적용 완료, 3초 후 재부팅합니다...")
        utime.sleep(3)
        machine.reset()


def trigger_ota_check():
    if not OTA_ENABLED:
        return
    run_exclusive(
        _run_ota_check, (),
        "⏭️ 다른 백그라운드 작업(클라우드 동기화 등)이 core1에서 진행 중이라 이번 OTA 확인 주기는 건너뜁니다."
    )
