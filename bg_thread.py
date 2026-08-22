# =================================================================
# bg_thread.py : RP2040/RP2350 보조 코어(core1) 하나를 여러 백그라운드
# 작업(클라우드 동기화, OTA 확인 등)이 공유해서 쓰기 위한 조율 도구
# =================================================================
import utime

from console_log import log_error

try:
    import _thread
    THREADING_AVAILABLE = True
except ImportError:
    THREADING_AVAILABLE = False

_lock = _thread.allocate_lock() if THREADING_AVAILABLE else None
_busy = False


def _try_acquire():
    global _busy
    if not THREADING_AVAILABLE:
        return True
    _lock.acquire()
    already = _busy
    if not already:
        _busy = True
    _lock.release()
    return not already


def _release():
    global _busy
    if _lock:
        _lock.acquire()
    _busy = False
    if _lock:
        _lock.release()


def _start(fn, args):
    """
    _thread.start_new_thread()을 시작합니다. RP2040/RP2350은 이전 스레드가
    끝나서 busy 플래그가 지워진 직후에도, 실제 core1이 완전히 해제되기까지
    아주 짧은 지연이 있어 곧바로 새 스레드를 시작하면 "OSError: core1 in use"가
    날 수 있습니다. 그래서 실패하면 잠깐 쉬었다가 몇 번 더 시도합니다.
    """
    delays_ms = (0, 50, 150, 400)
    last_err = None
    for delay_ms in delays_ms:
        if delay_ms:
            utime.sleep_ms(delay_ms)
        try:
            _thread.start_new_thread(fn, args)
            return True
        except OSError as e:
            last_err = e
    log_error("백그라운드 스레드 시작", last_err)
    return False


def run_exclusive(fn, args, busy_message):
    """
    core1을 다른 작업이 안 쓰고 있으면 fn(*args)를 스레드로 실행하고, 끝나면
    자동으로 점유를 해제합니다. 이미 다른 작업이 쓰고 있으면 busy_message를
    출력하고 조용히 건너뜁니다. _thread를 쓸 수 없는 빌드에서는 동기 호출로
    자동 폴백합니다.
    """
    if not THREADING_AVAILABLE:
        fn(*args)
        return

    if not _try_acquire():
        print(busy_message)
        return

    def _wrapped(*a):
        try:
            fn(*a)
        finally:
            _release()

    if not _start(_wrapped, args):
        _release()
