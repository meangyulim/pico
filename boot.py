# =================================================================
# 🔒 boot.py : 부팅 안전망 (절대 웹 에디터로 수정하지 않는 유일한 파일)
# =================================================================
# MicroPython은 이 파일을 main.py보다 먼저, 항상 실행합니다.
# main.py 및 그 하위 모듈들은 이제 웹 에디터로 자유롭게 수정할 수 있는데,
# 만약 그 수정으로 main.py가 깨지면(문법 오류, 임포트 중 예외 등) 이
# 파일이 핵심 모듈들의 .bak으로 자동 복구를 시도합니다.
#
# 이 파일은 최대한 단순하게 유지하세요. 이 파일 자체가 깨지면 안전망이
# 통째로 무력화되어 USB 재업로드로만 복구할 수 있습니다.
# =================================================================
import os

# main.py가 import하는 핵심 시스템 모듈 전부. 이 중 무엇이 깨져도 main.py
# import가 실패할 수 있으므로, 실패 시 전부 백업에서 일괄 복원을 시도합니다.
# 개별 센서 앱(app_*.py)은 main.py가 예외 격리로 로드하므로 여기 넣지
# 않습니다 (앱이 깨져도 시스템 코어는 안 죽음 — app_manager.py 참고).
CORE_FILES = (
    "main.py", "console_log.py", "bg_thread.py", "lcd_driver.py",
    "wifi_manager.py", "web_ui.py", "file_editor.py", "ota.py",
    "app_manager.py", "netutil.py", "watchdog.py",
)


def _restore_backups():
    restored_any = False
    for name in CORE_FILES:
        backup_path = name + ".bak"
        try:
            os.stat(backup_path)
        except OSError:
            continue
        try:
            try:
                os.remove(name)
            except OSError:
                pass
            os.rename(backup_path, name)
            print(f"🛟 [boot] {backup_path}을(를) {name}로 복원했습니다.")
            restored_any = True
        except Exception as e:
            print(f"🛟 [boot] {name} 복원 실패: {type(e).__name__}: {e}")
    return restored_any


def _try_import_main():
    try:
        import main
        return True
    except Exception as e:
        print(f"🛟 [boot] main.py 로드 실패: {type(e).__name__}: {e}")
        return False


def _preserve_debug_log():
    # main.py는 30초마다 debug.log를 덮어씁니다. 재부팅 직후 바로 새
    # 내용으로 덮이기 전에, 지난 세션의 마지막 상태(먹통 직전 상태)를
    # debug_prev.log로 옮겨서 /edit?file=debug_prev.log로 계속 볼 수
    # 있게 합니다.
    try:
        os.stat("debug.log")
    except OSError:
        return
    try:
        try:
            os.remove("debug_prev.log")
        except OSError:
            pass
        os.rename("debug.log", "debug_prev.log")
    except Exception:
        pass


_preserve_debug_log()

if not _try_import_main():
    if _restore_backups():
        if not _try_import_main():
            print("🛟 [boot] 백업 복원 후에도 main.py 로드 실패. USB로 재업로드가 필요합니다.")
    else:
        print("🛟 [boot] 복원할 백업이 없습니다. USB로 재업로드가 필요합니다.")
