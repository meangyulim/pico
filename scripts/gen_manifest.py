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
    "console_log.py",
    "bg_thread.py",
    "lcd_driver.py",
    "wifi_manager.py",
    "web_ui.py",
    "file_editor.py",
    "ota.py",
    "app_manager.py",
    "netutil.py",
    "app_reaction_game.py",
    "app_dust_monitor.py",
    "app_idle.py",
]

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    manifest = {}
    for name in TRACKED_FILES:
        data = (ROOT / name).read_bytes()
        manifest[name] = {"sha256": hashlib.sha256(data).hexdigest()}
    out_path = ROOT / "manifest.json"
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {out_path} ({len(manifest)} files)")


if __name__ == "__main__":
    main()
