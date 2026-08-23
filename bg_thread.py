# =================================================================
# bg_thread.py : RP2040/RP2350 보조 코어(core1)에서 도는 영구 백그라운드
# 워커 — 클라우드 동기화, OTA 확인 등 주기적 작업을 전담
# =================================================================
# 예전에는 작업 주기(예: OTA 확인 47초)마다 매번 새 스레드를 만들고
# 버리는 방식이었는데, 이게 core1 상태를 조금씩 갉아먹는 것으로 보였습니다
# (관찰된 증상: 시간이 지날수록 여유 메모리가 꾸준히 줄어들다가, 드물게는
# 새 스레드 생성 자체가 응답 없이 멈춰버림 — 메인 루프가 스레드 생성을
# 동기적으로 기다리는 구조라 이 경우 메인 루프까지 통째로 멈춥니다).
#
# 그래서 지금은 부팅 시 딱 한 번만 백그라운드 스레드를 띄우고, 그 안에서
# 영구히 도는 루프가 등록된 작업들의 타이밍을 자체적으로 체크해서
# 순서대로 실행합니다. 스레드 생성/해제 자체가 아예 없어지고, 작업들이
# 같은 스레드 안에서 순차 실행되니 서로 겹칠 일도 없어(잠금 장치 불필요)
# 예전에 있었던 "OSError: core1 in use" 문제도 원천적으로 사라집니다.
import utime

from console_log import log_error

try:
    import _thread
    THREADING_AVAILABLE = True
except ImportError:
    THREADING_AVAILABLE = False

_WORKER_POLL_MS = 500
_tasks = []
_worker_started = False


def register_periodic_task(name, fn, interval_ms):
    """
    core1 워커가 주기적으로 실행할 작업을 등록합니다. main()에서 부팅 시
    한 번만 등록하세요. fn은 인자 없이 호출되며, 필요한 상태는 클로저로
    캡처하고 (예: Wi-Fi 연결 여부 등) 스스로 판단해서 조용히 건너뛰어야
    합니다. fn에서 발생한 예외는 여기서 잡아 로그만 남기고 워커는 계속
    돕니다 (한 작업의 오류가 다른 작업/워커 자체를 죽이지 않음).
    """
    _tasks.append({
        "name": name, "fn": fn, "interval_ms": interval_ms,
        "last_run": utime.ticks_ms(),
    })


def _worker_loop():
    # 이 루프가 예외로 빠져나가면 주기 작업(OTA 등)이 영영 멈춥니다.
    # 그런데 부팅 이후에는 스레드를 다시 띄우지 않는 설계라(그게 예전
    # 불안정의 원인이었음) 여기서 절대 밖으로 나가지 않게 이중으로 감쌉니다.
    while True:
        try:
            now = utime.ticks_ms()
            for task in _tasks:
                if utime.ticks_diff(now, task["last_run"]) >= task["interval_ms"]:
                    task["last_run"] = now
                    try:
                        task["fn"]()
                    except Exception as e:
                        log_error("백그라운드 작업({})".format(task["name"]), e)
        except Exception as e:
            log_error("백그라운드 워커", e)
        utime.sleep_ms(_WORKER_POLL_MS)


def start_background_worker():
    """
    main()에서 부팅 시 딱 한 번만 호출하세요 (등록된 작업이 다 끝난 뒤).
    _thread를 쓸 수 없는 빌드에서는 아무것도 하지 않습니다 — 이 경우
    등록된 작업들은 실행되지 않습니다 (메인 루프를 막지 않으려고 애초에
    별도 코어로 뺀 것이므로, 동기 폴백은 지원하지 않음).

    스레드 시작 실패(core1을 아직 다른 쪽이 잡고 있는 등)는 삼켜야 합니다.
    여기서 예외가 그대로 올라가면 main()이 죽고, boot.py가 핵심 모듈을
    전부 .bak으로 되돌려버립니다 — 주기 작업 하나 못 띄운 대가로는
    지나치게 큽니다. Wi-Fi·웹서버·LCD는 워커 없이도 정상 동작합니다.
    """
    global _worker_started
    if _worker_started or not THREADING_AVAILABLE:
        return
    try:
        _thread.start_new_thread(_worker_loop, ())
        _worker_started = True
    except Exception as e:
        log_error("백그라운드 워커 시작", e)
        print("⚠️ 백그라운드 작업 없이 계속 진행합니다 (OTA 확인 불가)")
