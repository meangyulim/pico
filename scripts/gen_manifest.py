#!/usr/bin/env python3
"""OTA용 manifest.json을 재생성합니다.

TRACKED_FILES 중 하나라도 내용이 바뀌면, 커밋 전에 이 스크립트를 실행해서
manifest.json을 최신 상태로 맞춰야 피코의 OTA 업데이트가 정확히 동작합니다
(CI가 manifest.json이 최신인지 확인합니다: .github/workflows/test.yml 참고).

    python3 scripts/gen_manifest.py
"""
import hashlib
import json
import pathlib

TRACKED_FILES = [
    "boot.py",
    "main.py",
    "httpd.py",
    "console_log.py",
    "bg_thread.py",
    "lcd_driver.py",
    "wifi_manager.py",
    "web_ui.py",
    "file_editor.py",
    "ota.py",
    "app_manager.py",
    "netutil.py",
    "watchdog.py",
    "app_reaction_game.py",
    "app_dust_monitor.py",
    "app_idle.py",
]

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    manifest = {}
    file_hashes = {}
    for name in TRACKED_FILES:
        data = (ROOT / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        manifest[name] = {"sha256": digest}
        file_hashes[name] = digest

    # 추적 파일들의 해시를 합쳐서 만든 결정론적 "버전" 식별자. git 커밋
    # SHA를 쓰면 매니페스트를 생성하는 시점엔 아직 이 커밋이 존재하지
    # 않아 한 커밋 어긋나는 문제가 있고, 생성 시각(now)을 쓰면 CI가
    # 재생성했을 때 항상 다른 값이 나와 "manifest.json이 최신인지"
    # 검사(diff)가 매번 실패합니다. 파일 내용만의 함수라 둘 다 안전합니다.
    combined = json.dumps(file_hashes, sort_keys=True).encode()
    manifest["_version"] = hashlib.sha256(combined).hexdigest()[:12]

    out_path = ROOT / "manifest.json"
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {out_path} ({len(TRACKED_FILES)} files, version {manifest['_version']})")


if __name__ == "__main__":
    main()
