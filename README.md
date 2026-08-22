# Pico 2 W Dust Monitor

Raspberry Pi Pico 2 W(RP2350) + 미세먼지 센서 + 1602 LCD 기반 IoT 모니터링 시스템입니다.

## 구성

- `main.py` — 시스템 코어 (Wi-Fi 연결/AP 모드, 웹서버, 1602 LCD, 웹 기반 코드 에디터). 변경 없이 그대로 유지되는 인프라 파일입니다.
- `user_code.py` — 사용자 커스텀 로직 (센서 측정, 상태 판정, 구글 시트 연동). 이 파일에 오류가 있어도 `main.py`의 웹서버와 웹 에디터는 계속 동작합니다.

## 동작 방식

1. 부팅 시 `main.py`가 `user_code.py`를 안전하게(예외 격리) 로드합니다.
2. `wifi_config.json`에 저장된 Wi-Fi 정보로 접속을 시도하고, 실패하면 `Pico-Dust-Setup`이라는 이름의 오프라인 핫스팟(AP)을 엽니다.
3. 웹 대시보드(`/`)에서 실시간 미세먼지 농도·전압·상태를 확인하고, Wi-Fi를 설정할 수 있습니다.
4. 웹 에디터(`/edit`)에서 `user_code.py`를 스마트폰으로 직접 수정하고 저장하면 자동 재부팅됩니다.
5. `user_code.py`는 1분 주기로 Google Apps Script(GAS) 웹앱과 데이터를 주고받아 구글 시트에 기록하고, 원격으로 음소거/경보 임계치를 제어할 수 있습니다.

## 준비물

- Raspberry Pi Pico 2 W
- 샤프 계열 미세먼지 센서 (LED: GPIO27, ADC: GPIO26)
- PWM 버저 (GPIO20)
- 1602 LCD (PCF8574 I2C 어댑터, I2C0: SDA=GPIO8, SCL=GPIO9)
- [MicroPython 펌웨어](https://micropython.org/download/RPI_PICO2_W/)
- Thonny, `mpremote`, `rshell` 등 업로드 도구

## 시작하기

1. Pico 2 W에 MicroPython 펌웨어를 설치합니다 (BOOTSEL 버튼을 누른 채로 USB 연결 → `.uf2` 파일 드래그).
2. 파일을 보드에 업로드합니다.
   ```bash
   mpremote cp main.py :main.py
   mpremote cp user_code.py :user_code.py
   ```
3. 보드를 리셋하면 웹서버가 자동 시작됩니다. 처음에는 `Pico-Dust-Setup` 핫스팟에 접속해 `192.168.4.1`에서 Wi-Fi를 설정하세요.
4. `user_code.py`의 `GAS_URL`을 본인의 Google Apps Script 배포 URL로 교체하세요.

## 참고

- `wifi_config.json`은 런타임에 보드에 저장되는 파일이라 저장소에는 포함하지 않습니다 (`.gitignore` 처리).
- `user_code.py`를 완전히 지우거나 문법 오류를 내도, `main.py`가 기본 템플릿을 자동 생성하고 웹 에디터로 복구할 수 있습니다.
