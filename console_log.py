# =================================================================
# console_log.py : 원격 콘솔용 print 후킹 + 오류 로그 헬퍼
# =================================================================
# builtins.print를 감싸서 시리얼 출력은 그대로 두고, 최근 N줄을 메모리에도
# 저장합니다. main.py가 이 모듈을 가장 먼저 import해야 이후의 모든
# print() 호출(다른 모듈 것까지)이 잡힙니다.
# =================================================================
import builtins

LOG_BUFFER_MAX_LINES = 200
DEBUG_LOG_FILE = "debug.log"
DEBUG_LOG_TAIL_LINES = 60  # 플래시에 남기는 최근 줄 수 (전체를 매번 쓰면 느려짐)
log_buffer = []
_real_print = builtins.print


def _tee_print(*args, **kwargs):
    _real_print(*args, **kwargs)
    try:
        line = " ".join(str(a) for a in args)
        log_buffer.append(line)
        if len(log_buffer) > LOG_BUFFER_MAX_LINES:
            del log_buffer[0]
    except Exception:
        pass


builtins.print = _tee_print


def log_error(context, exc):
    print(f"⚠️ [{context}] {type(exc).__name__}: {exc}")


def flush_log_to_file():
    """
    /logs는 웹서버를 통해서만 보이므로, 기기가 완전히 먹통이 되면 정작
    그 직전 상황을 확인할 방법이 없습니다. 최근 로그를 주기적으로 파일에
    남겨서, 재부팅 후 웹 에디터(/edit?file=debug.log)로 먹통 직전 상태를
    사후에 확인할 수 있게 합니다. (매번 전체 버퍼를 쓰면 느리므로 최근
    DEBUG_LOG_TAIL_LINES줄만 저장 — 매번 덮어쓰기라 파일 크기는 고정)
    """
    try:
        with open(DEBUG_LOG_FILE, "w") as f:
            f.write("\n".join(log_buffer[-DEBUG_LOG_TAIL_LINES:]))
    except Exception:
        pass
