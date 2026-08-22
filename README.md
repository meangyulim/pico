# Pico 2 W Dev

Raspberry Pi Pico 2 W (RP2350) 개발용 MicroPython 프로젝트입니다.

## 준비물

- Raspberry Pi Pico 2 W
- USB 케이블
- [MicroPython 펌웨어](https://micropython.org/download/RPI_PICO2_W/)
- [Thonny IDE](https://thonny.org/) 또는 `mpremote`, `rshell` 등

## 시작하기

1. Pico 2 W에 MicroPython 펌웨어를 설치합니다 (BOOTSEL 버튼을 누른 채로 USB 연결 → `.uf2` 파일 드래그).
2. `src/` 아래 파일들을 보드에 업로드합니다.
   ```bash
   mpremote cp src/main.py :main.py
   mpremote cp src/wifi_config.py :wifi_config.py
   ```
3. `src/wifi_config.py`에 Wi-Fi SSID/비밀번호를 입력합니다 (`wifi_config.example.py` 참고).
4. 보드를 리셋하면 `main.py`가 자동 실행됩니다.

## 구조

```
src/
  main.py              # 부팅 시 실행되는 메인 스크립트 (LED 블링크 + Wi-Fi 연결 예제)
  wifi_config.example.py  # Wi-Fi 설정 예제 (실제 값은 wifi_config.py로 복사해서 사용)
```

## 참고

- Pico 2 W는 무선 칩(CYW43439)을 통해 온보드 LED를 제어하므로 `machine.Pin("LED")` 대신 `network` 모듈 초기화가 필요합니다.
