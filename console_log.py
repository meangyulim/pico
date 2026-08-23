# =================================================================
# console_log.py : print 후킹 + 원격 콘솔 버퍼 + 디버그 로그 파일
# =================================================================
# builtins.print를 감싸서 시리얼 출력은 그대로 두고 최근 N줄을 메모리에도
# 남깁니다. main.py가 이 모듈을 가장 먼저 import해야 이후 모든 모듈의
# print()가 잡힙니다.
#
# /logs는 웹서버를 통해서만 보이므로, 기기가 완전히 먹통이 되면 정작 그
# 직전 상황을 볼 방법이 없습니다. 그래서 주기적으로 debug.log에도
# 남깁니다. boot.py가 재부팅 때 이 파일을 debug_prev.log로 옮겨두므로,
# 재부팅 후 /edit?file=debug_prev.log 로 "먹통 직전 상태"를 확인할 수
# 있습니다.
#
# 플래시 쓰기는 수명이 있으므로 매번 쓰지 않습니다. 평소에는 main.py가
# 몇 분에 한 번 flush_log_to_file()을 부르고, 오류가 났을 때만
# log_error()가 즉시 flush합니다 (오류 직후 멈추면 그게 마지막 단서라서).
# =================================================================
import builtins

LOG_BUFFER_MAX_LINES = 120
DEBUG_LOG_FILE = "debug.log"
DEBUG_LOG_TAIL_LINES = 50

log_buffer = []
_real_print = builtins.print


def _tee_print(*args, **kwargs):
    _real_print(*args, **kwargs)
    try:
        log_buffer.append(" ".join(str(a) for a in args))
        if len(log_buffer) > LOG_BUFFER_MAX_LINES:
            del log_buffer[0]
    except Exception:
        pass


builtins.print = _tee_print


def flush_log_to_file():
    """최근 로그를 파일에 덮어씁니다 (덮어쓰기라 파일 크기는 고정)."""
    try:
        with open(DEBUG_LOG_FILE, "w") as f:
            f.write("\n".join(log_buffer[-DEBUG_LOG_TAIL_LINES:]))
    except Exception:
        pass


def log_error(context, exc):
    print("⚠️ [{}] {}: {}".format(context, type(exc).__name__, exc))
    # 오류 직후 기기가 멈추는 경우가 있어, 이 시점 기록은 바로 남깁니다.
    flush_log_to_file()
