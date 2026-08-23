# =================================================================
# file_editor.py : 웹 에디터의 파일 저장/백업/되돌리기/목록 로직
# =================================================================
import os

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

from console_log import log_error
from netutil import is_safe_filename

try:
    import watchdog
except ImportError:  # OTA로 아직 전달되지 않은 새 모듈 — 없어도 동작해야 함
    class watchdog:
        @staticmethod
        def feed():
            pass

MAX_EDIT_FILE_SIZE = 64 * 1024  # 웹 에디터로 저장 가능한 파일 최대 크기
IO_CHUNK = 512

# boot.py는 부팅 안전망이라 절대 웹으로 수정하지 않습니다.
EDITOR_EXCLUDED_FILES = {"boot.py"}
EDITOR_EXCLUDED_SUFFIXES = (".bak", ".json", ".tmp")

# .py는 아니지만 열어볼 수 있게 허용하는 파일. debug.log는 이번 세션,
# debug_prev.log는 boot.py가 재부팅 때 옮겨둔 "지난 세션 마지막 상태"로,
# 먹통 원인을 사후에 확인할 때 씁니다.
EDITOR_EXTRA_VIEWABLE_FILES = {"debug.log", "debug_prev.log"}


def file_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def file_size(path):
    try:
        return os.stat(path)[6]
    except OSError:
        return -1


def file_hash(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                buf = f.read(IO_CHUNK)
                if not buf:
                    break
                h.update(buf)
                watchdog.feed()
        return h.digest()
    except Exception:
        return None


def backup_file(path):
    """path가 존재하면 path+'.bak'으로 복사합니다 (덮어쓰기 전 안전망)."""
    if not file_exists(path):
        return
    try:
        with open(path, "rb") as src, open(path + ".bak", "wb") as dst:
            while True:
                buf = src.read(IO_CHUNK)
                if not buf:
                    break
                dst.write(buf)
                watchdog.feed()
    except Exception as e:
        log_error("파일 백업", e)


def revert_file(target_file):
    """target_file.bak이 있으면 target_file로 복원합니다.
    (rename이므로 복원과 동시에 백업은 사라집니다.)"""
    backup_path = target_file + ".bak"
    if not file_exists(backup_path):
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
    if not is_safe_filename(name):
        return False
    if name in EDITOR_EXCLUDED_FILES:
        return False
    for suf in EDITOR_EXCLUDED_SUFFIXES:
        if name.endswith(suf):
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


# -----------------------------------------------------------------
# POST 본문(폼 인코딩된 코드) 수신 → 파일 저장
# -----------------------------------------------------------------
class _FormDecoder:
    """application/x-www-form-urlencoded 본문을 조각 단위로 디코딩합니다.

    조각 경계가 '%' 나 '%4' 처럼 이스케이프 중간을 자를 수 있으므로
    상태를 유지해야 합니다. 이 클래스가 그 상태를 들고 있습니다.
    """

    PREFIX = b"code="

    def __init__(self):
        self.state = 0      # 0=일반, 1=% 뒤 16진수 수집 중
        self.hex = b""
        self._pre = b""     # 'code=' 판별이 끝나기 전까지 모아두는 선행 바이트
        self._checked = False

    def _take_prefix(self, chunk):
        """첫 조각이 5바이트보다 짧게 잘려 들어와도 'code=' 를 제대로
        떼어내기 위해, 판별에 충분한 길이가 모일 때까지 기다립니다."""
        self._pre += chunk
        if len(self._pre) < len(self.PREFIX):
            return None                     # 아직 판단 불가
        self._checked = True
        body = self._pre
        self._pre = b""
        if body.startswith(self.PREFIX):
            return body[len(self.PREFIX):]
        return body

    def feed(self, chunk):
        if not self._checked:
            chunk = self._take_prefix(chunk)
            if chunk is None:
                return bytearray()
        out = bytearray()
        for b in chunk:
            if self.state == 0:
                if b == 0x2B:      # '+'
                    out.append(0x20)
                elif b == 0x25:    # '%'
                    self.state = 1
                    self.hex = b""
                else:
                    out.append(b)
            else:
                self.hex += bytes([b])
                if len(self.hex) == 2:
                    try:
                        out.append(int(self.hex, 16))
                    except Exception:
                        out.append(0x25)
                        out.extend(self.hex)
                    self.state = 0
                    self.hex = b""
        return out

    def finish(self):
        """본문 끝 처리.

        - 'code=' 판별이 끝나기 전에 본문이 끝난 경우(아주 짧은 본문)
        - '%4' 나 '%' 처럼 이스케이프 중간에 끝난 경우
        둘 다 남은 바이트를 잃지 않고 그대로 내보냅니다.
        """
        out = bytearray()
        if not self._checked and self._pre:
            leftover = self._pre
            self._pre = b""
            self._checked = True
            if leftover.startswith(self.PREFIX):
                leftover = leftover[len(self.PREFIX):]
            out.extend(self.feed(leftover))
        if self.state == 1:
            # hex가 비어 있어도('%' 로 끝남) '%' 자체는 살려야 합니다.
            out.extend(b'%' + self.hex)
            self.state = 0
            self.hex = b""
        return bytes(out)


def handle_save_code(conn, initial_body, content_length, target_file):
    """
    POST 본문을 스트리밍으로 받아 디코딩하면서 곧바로 target_file에
    씁니다. 반환값: (성공 여부, 메시지, 내용이 실제로 바뀌었는지)

    예전에는 임시 파일에 쓴 뒤 target_file로 다시 통째로 복사했습니다.
    쓰기가 두 배였고, 복사 도중 전원이 끊기면 오히려 반쪽 파일이 남을
    수 있었습니다. 지금은 임시 파일에 다 쓰고 os.rename으로 한 번에
    갈아끼웁니다 (rename은 원자적이라 중간 상태가 남지 않습니다).
    """
    if content_length > MAX_EDIT_FILE_SIZE:
        return False, "코드 크기가 너무 큽니다 ({} > {} bytes)".format(
            content_length, MAX_EDIT_FILE_SIZE), False

    old_hash = file_hash(target_file)
    tmp = target_file + ".tmp"
    dec = _FormDecoder()
    new_hash = hashlib.sha256()
    written = 0

    try:
        with open(tmp, "wb") as f:
            pending = initial_body
            read_total = len(initial_body)
            while True:
                if pending:
                    out = dec.feed(pending)
                    pending = b""
                    if out:
                        f.write(out)
                        new_hash.update(out)
                        written += len(out)
                    watchdog.feed()
                    continue
                if read_total >= content_length:
                    break
                chunk = conn.recv(min(IO_CHUNK, content_length - read_total))
                if not chunk:
                    break
                read_total += len(chunk)
                pending = chunk

            tail = dec.finish()
            if tail:
                f.write(tail)
                new_hash.update(tail)
                written += len(tail)

        if written <= 10:
            _remove_quiet(tmp)
            return False, "저장된 내용이 비어있습니다.", False

        changed = (old_hash != new_hash.digest())
        if changed:
            backup_file(target_file)
        _remove_quiet(target_file)
        os.rename(tmp, target_file)
        return True, "저장 성공 ({} bytes)".format(written), changed

    except Exception as e:
        _remove_quiet(tmp)
        return False, "파일 저장 오류: {}".format(e), False


def _remove_quiet(path):
    try:
        os.remove(path)
    except OSError:
        pass
