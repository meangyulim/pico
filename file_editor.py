# =================================================================
# file_editor.py : 웹 에디터가 쓰는 파일 저장/백업/되돌리기/목록 로직
# =================================================================
import os

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

from console_log import log_error

MAX_EDIT_FILE_SIZE = 64 * 1024  # 웹 에디터로 저장 가능한 파일 최대 크기 (64KB)

# 웹 에디터의 파일 목록/편집 대상에서 제외하는 이름들.
# boot.py는 부팅 안전망이라 절대 웹으로 수정하지 않습니다.
EDITOR_EXCLUDED_FILES = {"boot.py"}
EDITOR_EXCLUDED_SUFFIXES = (".bak", ".json", ".tmp")

# .py는 아니지만 웹 에디터로 보고 싶은 파일 (console_log.flush_log_to_file()가
# 남기는 하트비트/최근 로그 — 기기가 먹통이 됐을 때 재부팅 후 확인용).
EDITOR_EXTRA_VIEWABLE_FILES = {"debug.log"}


def file_hash(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                buf = f.read(512)
                if not buf:
                    break
                h.update(buf)
        return h.digest()
    except Exception:
        return None


def backup_file(path):
    """path가 존재하면 path+'.bak'으로 복사합니다 (덮어쓰기 전 안전망)."""
    try:
        os.stat(path)
    except OSError:
        return
    try:
        with open(path, "rb") as src, open(path + ".bak", "wb") as dst:
            while True:
                buf = src.read(512)
                if not buf:
                    break
                dst.write(buf)
    except Exception as e:
        log_error("파일 백업", e)


def revert_file(target_file):
    """target_file.bak이 있으면 target_file로 복원합니다."""
    backup_path = target_file + ".bak"
    try:
        os.stat(backup_path)
    except OSError:
        return False
    try:
        try:
            os.remove(target_file)
        except OSError:
            pass
        os.rename(backup_path, target_file)
        return True
    except Exception as e:
        log_error("파일 복원", e)
        return False


def is_valid_editable_filename(name):
    if not name or '/' in name or '\\' in name or '..' in name:
        return False
    if name in EDITOR_EXCLUDED_FILES:
        return False
    if any(name.endswith(suf) for suf in EDITOR_EXCLUDED_SUFFIXES):
        return False
    return name.endswith(".py") or name in EDITOR_EXTRA_VIEWABLE_FILES


def list_editable_files():
    try:
        names = os.listdir()
    except Exception:
        return []
    files = [n for n in names if is_valid_editable_filename(n)]
    files.sort()
    return files


def handle_save_code(conn, initial_body, content_length, target_file):
    """
    POST로 전송된 대용량 폼 데이터를 스트리밍 방식으로 수신 및 URL 디코딩하여
    target_file에 안전하게 저장합니다 (내용이 바뀐 경우 저장 전 자동 백업).
    반환값: (성공 여부, 메시지, 기존 파일과 내용이 달라졌는지)
    """
    if content_length > MAX_EDIT_FILE_SIZE:
        return False, f"코드 크기가 너무 큽니다 ({content_length} > {MAX_EDIT_FILE_SIZE} bytes)", False

    old_hash = file_hash(target_file)

    body_stream = [initial_body]
    bytes_read = len(initial_body)

    state = 0
    hex_chars = b""
    is_first = True
    temp_file = "user_code_tmp.py"
    new_hash_ctx = hashlib.sha256()

    try:
        with open(temp_file, "wb") as f_out:
            while True:
                if body_stream:
                    chunk = body_stream.pop(0)
                elif bytes_read < content_length:
                    to_read = min(1024, content_length - bytes_read)
                    chunk = conn.recv(to_read)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                else:
                    break

                if not chunk:
                    continue

                if is_first:
                    if chunk.startswith(b"code="):
                        chunk = chunk[5:]
                    is_first = False

                out_buf = bytearray()
                i = 0
                while i < len(chunk):
                    b = chunk[i]
                    if state == 0:
                        if b == ord(b'+'):
                            out_buf.append(ord(b' '))
                            i += 1
                        elif b == ord(b'%'):
                            state = 1
                            hex_chars = b""
                            i += 1
                        else:
                            out_buf.append(b)
                            i += 1
                    elif state == 1:
                        hex_chars += bytes([b])
                        i += 1
                        if len(hex_chars) == 2:
                            try:
                                val = int(hex_chars, 16)
                                out_buf.append(val)
                            except Exception:
                                out_buf.append(ord(b'%'))
                                out_buf.extend(hex_chars)
                            state = 0
                            hex_chars = b""

                if out_buf:
                    f_out.write(out_buf)
                    new_hash_ctx.update(out_buf)

            if state == 1 and hex_chars:
                tail = b'%' + hex_chars
                f_out.write(tail)
                new_hash_ctx.update(tail)

        # 파일 저장 검증
        stat = os.stat(temp_file)
        if stat[6] > 10:
            new_hash = new_hash_ctx.digest()
            changed = (old_hash != new_hash)
            if changed:
                backup_file(target_file)
            with open(temp_file, "rb") as f_src:
                with open(target_file, "wb") as f_dst:
                    while True:
                        buf = f_src.read(1024)
                        if not buf:
                            break
                        f_dst.write(buf)
            try:
                os.remove(temp_file)
            except Exception:
                pass
            return True, f"저장 성공 ({stat[6]} bytes)", changed
        else:
            return False, "저장된 파일 내용이 비어있습니다.", False
    except Exception as e:
        return False, f"파일 저장 오류: {e}", False
