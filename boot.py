# =================================================================
# 🔒 boot.py : 부팅 안전망 (절대 웹 에디터로 수정하지 않는 유일한 파일)
# =================================================================
# MicroPython은 이 파일을 main.py보다 먼저, 항상 실행합니다.
# main.py는 이제 웹 에디터로 자유롭게 수정할 수 있는데, 만약 그 수정으로
# main.py가 깨지면(문법 오류, 임포트 중 예외 등) 이 파일이 main.py.bak으로
# 자동 복구를 시도합니다.
#
# 이 파일은 최대한 단순하게 유지하세요. 이 파일 자체가 깨지면 안전망이
# 통째로 무력화되어 USB 재업로드로만 복구할 수 있습니다.
# =================================================================
import os


def _restore_backup():
    try:
        os.stat("main.py.bak")
    except OSError:
        return False
    try:
        try:
            os.remove("main.py")
        except OSError:
            pass
        os.rename("main.py.bak", "main.py")
        print("🛟 [boot] main.py.bak을 main.py로 복원했습니다.")
        return True
    except Exception as e:
        print(f"🛟 [boot] 백업 복원 실패: {type(e).__name__}: {e}")
        return False


def _try_import_main():
    try:
        import main
        return True
    except Exception as e:
        print(f"🛟 [boot] main.py 로드 실패: {type(e).__name__}: {e}")
        return False


if not _try_import_main():
    if _restore_backup():
        if not _try_import_main():
            print("🛟 [boot] 백업 복원 후에도 main.py 로드 실패. USB로 재업로드가 필요합니다.")
    else:
        print("🛟 [boot] main.py.bak이 없어 자동 복구할 수 없습니다. USB로 재업로드가 필요합니다.")
