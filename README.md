# Pico 2 W Dust Monitor

Raspberry Pi Pico 2 W(RP2350) + 미세먼지 센서 + 1602 LCD 기반 IoT 모니터링 시스템입니다.

## 구성

- `boot.py` — 부팅 안전망. **웹 에디터로 절대 수정할 수 없는 유일한 파일**입니다. `main.py`보다 항상 먼저 실행되며, `main.py`가 깨졌을 때(문법 오류, 임포트 중 예외 등) `main.py.bak`으로 자동 복구를 시도합니다.
- `main.py` — 시스템 코어 (Wi-Fi 연결/AP 모드, 웹서버, 1602 LCD, 웹 기반 파일 브라우저/에디터). 웹 에디터로 다른 모든 파일과 함께 수정할 수 있습니다.
- `netutil.py` — 하드웨어 의존성 없는 순수 유틸리티 (`url_decode` 등). `main.py`가 이 파일을 import하므로 보드에 같이 올려야 하고, 데스크톱 파이썬에서 그대로 테스트할 수 있습니다.
- `user_code.default.py` — `user_code.py`가 없을 때 자동 생성되는 기본 템플릿. 실제 로직과는 별도 파일로 관리되어 서로 내용이 어긋나지 않습니다.
- `user_code.py` — 사용자 커스텀 로직 (센서 측정, 상태 판정, 구글 시트 연동). 이 파일에 오류가 있어도 `main.py`의 웹서버와 웹 에디터는 계속 동작합니다.
- `manifest.json` — OTA 자동 업데이트용 파일별 sha256 해시 목록. 커밋 전 `python3 scripts/gen_manifest.py`로 재생성합니다 (CI가 최신 상태인지 확인).

## `user_code.py` 인터페이스 계약

`main.py`는 `user_code.py`에서 아래 항목을 찾아서 있으면 사용하고, 없으면 해당 기능만 조용히 건너뜁니다 (부팅 시 콘솔에 누락 항목을 경고로 출력). 전체 목록은 `main.py`의 `REQUIRED_USER_ATTRS`를 참고하세요.

| 이름 | 형태 | 설명 |
|---|---|---|
| `read_dust_sensor()` | `() -> (avg_voltage: float, density: float)` | 센서 측정 |
| `get_status_info(density)` | `(float) -> (eng: str, kor: str, color_hex: str)` | 농도 → 상태 판정 |
| `sync_with_google_sheets(density, voltage, status_eng)` | `(...) -> bool` | 클라우드 동기화 (별도 코어 스레드에서 호출됨) |
| `play_alert_beep()` | `() -> None` | 경보음 |
| `is_muted` | `bool` | 음소거 여부 |
| `alert_threshold` | `float` | 경보 임계치 |
| `cloud_sync_status` | `str` | 동기화 상태 표시 문자열 |

## 웹 파일 브라우저 / 에디터

`http://<피코IP>/edit`에 접속하면 보드 루트의 `.py` 파일 목록이 뜹니다 (`boot.py`와 `.bak`/`.json` 등 상태 파일은 제외). 파일을 눌러 편집·저장하거나, 새 파일 이름을 입력해 만들 수 있습니다. `main.py`도 여기서 직접 수정할 수 있습니다.

- **저장**: 내용이 실제로 바뀐 경우에만 저장 전 자동으로 `<파일명>.bak`을 만들고, 저장 후 재부팅합니다.
- **되돌리기**: 백업이 있는 파일은 에디터 화면에 "↩️ 이전 버전" 버튼이 뜨고, 누르면 `.bak`을 복원하고 재부팅합니다.
- **`main.py` 안전망(`boot.py`)**: 저장한 `main.py`가 문법 오류나 즉시 예외로 부팅에 실패하면, `boot.py`가 자동으로 `main.py.bak`을 복원합니다. **다만 `main.py`가 예외 없이 무한루프에 빠지는 경우는 이 자동 복구 대상이 아닙니다** — 이런 드문 경우엔 USB로 재업로드해야 합니다. (하드웨어 워치독으로 이 경우까지 잡는 것도 시도해봤지만, RP2040/RP2350의 워치독은 한번 켜면 재부팅 전까지 끌 수 없어서 Thonny 등으로 개발 중 스크립트를 정지시킬 때마다 강제 재부팅되어 시리얼 연결이 끊기는 문제가 있어 제거했습니다.)
- `user_code.py`는 기존과 동일하게 `main.py`가 예외를 격리해서 로드하므로, 이 파일이 깨져도 웹서버 자체는 절대 죽지 않습니다.

## 동작 방식

1. 부팅 시 `boot.py`가 `main.py`를 안전하게 로드합니다 (실패 시 백업 복원).
2. `main.py`가 `user_code.py`를 안전하게(예외 격리) 로드합니다. 없으면 `user_code.default.py`를 복사해 생성합니다.
3. `wifi_config.json`에 저장된 Wi-Fi 정보로 접속을 시도합니다 (신호 불안정으로 인한 일시적 실패에 대비해 최대 3회 재시도). 그래도 실패하면 `Pico-Dust-Setup`이라는 이름의 오프라인 핫스팟(AP)을 엽니다. AP 모드로 넘어간 뒤에도 3분마다 저장된 정보로 백그라운드 재접속을 시도하므로, 비밀번호가 맞다면 굳이 다시 입력하지 않아도 신호가 돌아오면 자동으로 복구됩니다.
4. 웹 대시보드(`/`)에서 실시간 미세먼지 농도·전압·상태를 확인하고, Wi-Fi를 설정할 수 있습니다.
5. 웹 에디터(`/edit`)에서 원하는 파일을 스마트폰으로 직접 수정·생성·복원할 수 있습니다.
6. `user_code.py`는 1분 주기로 Google Apps Script(GAS) 웹앱과 데이터를 주고받아 구글 시트에 기록하고, 원격으로 음소거/경보 임계치를 제어할 수 있습니다. 이 동기화는 RP2350의 두 번째 코어(`_thread`)에서 실행되어, 네트워크가 느리거나 응답이 없어도 웹서버·LCD·센서 측정은 멈추지 않습니다. (`_thread`를 지원하지 않는 빌드에서는 자동으로 기존 방식인 동기 호출로 폴백합니다.)
7. Wi-Fi에 연결된 동안 3분마다 GitHub의 `manifest.json`을 확인해서, 바뀐 파일이 있으면 자동으로 받아 적용하고 재부팅합니다 (아래 OTA 자동 업데이트 참고).

## OTA 자동 업데이트 (GitHub 자동 반영)

Wi-Fi에 연결되어 있으면, 피코가 3분마다 이 저장소의 `main` 브랜치에 있는 `manifest.json`(파일별 sha256 해시만 담은 아주 작은 파일)을 확인합니다. 해시가 실제로 다른 파일만 골라 통째로 받아서 적용하므로, 평소엔 몇십 바이트만 오가고 진짜 코드가 바뀐 순간에만 무거운 다운로드가 일어납니다.

- 추적 대상: `boot.py`, `main.py`, `netutil.py`, `user_code.py`, `user_code.default.py` (`main.py`의 `OTA_ALLOWED_TARGETS`)
- 적용 전 받아온 내용의 해시를 다시 검증하고, 기존 내용은 웹 에디터와 동일하게 `.bak`으로 백업합니다.
- 적용된 파일이 하나라도 있으면 재부팅합니다 — `main.py`가 바뀌었어도 `boot.py`의 롤백 안전망을 그대로 거칩니다.
- **다른 리포로 포크했다면** `main.py`의 `OTA_REPO_RAW_BASE`를 본인 리포 주소로 바꾸세요. 끄고 싶으면 `OTA_ENABLED = False`로 설정하면 됩니다.
- 리포를 고칠 때(특히 `main.py`, `user_code.py` 등) 커밋 전에 반드시 `python3 scripts/gen_manifest.py`를 실행해 `manifest.json`을 최신 상태로 맞춰야 합니다. CI가 이걸 확인합니다.

## 준비물

- Raspberry Pi Pico 2 W
- 샤프 계열 미세먼지 센서 (LED: GPIO27, ADC: GPIO26)
- PWM 버저 (GPIO20)
- 1602 LCD (PCF8574 I2C 어댑터, I2C0: SDA=GPIO8, SCL=GPIO9)
- [MicroPython 펌웨어](https://micropython.org/download/RPI_PICO2_W/)
- Thonny, `mpremote`, `rshell` 등 업로드 도구

## 시작하기

1. Pico 2 W에 MicroPython 펌웨어를 설치합니다 (BOOTSEL 버튼을 누른 채로 USB 연결 → `.uf2` 파일 드래그).
2. 파일을 보드에 업로드합니다 (`boot.py`, `netutil.py`, `user_code.default.py`도 함께 올려야 합니다).
   ```bash
   mpremote cp boot.py :boot.py
   mpremote cp main.py :main.py
   mpremote cp netutil.py :netutil.py
   mpremote cp user_code.default.py :user_code.default.py
   mpremote cp user_code.py :user_code.py
   ```
3. 보드를 리셋하면 웹서버가 자동 시작됩니다. 처음에는 `Pico-Dust-Setup` 핫스팟에 접속해 `192.168.4.1`에서 Wi-Fi를 설정하세요.
4. `user_code.py`의 `GAS_URL`을 본인의 Google Apps Script 배포 URL로 교체하세요.

## 테스트

하드웨어 의존성이 없는 로직(`netutil.py`)은 데스크톱 파이썬으로 테스트합니다.

```bash
pip install pytest
pytest tests/ -v
```

PR을 올리면 GitHub Actions(`.github/workflows/test.yml`)가 자동으로 같은 테스트를 실행합니다.

## 참고

- `wifi_config.json`은 런타임에 보드에 저장되는 파일이라 저장소에는 포함하지 않습니다 (`.gitignore` 처리).
- `user_code.py`를 완전히 지우거나 문법 오류를 내도, `main.py`가 기본 템플릿(`user_code.default.py`)을 자동 생성하고 웹 에디터로 복구할 수 있습니다.
- `/edit`, `/save_code`, `/revert`에는 별도 인증이 없어 같은 네트워크에 있는 누구나 코드를 수정할 수 있습니다. 필요하면 추후 보완하세요.
- OTA는 리포지토리가 public이라 별도 인증 없이 raw 파일을 받아옵니다. 인증서 체인 검증은 하지 않는 연결이라(기존 GAS 연동과 동일한 신뢰 수준) 완벽한 보안은 아니지만, 받아온 내용이 `manifest.json`의 해시와 일치하는지는 항상 확인합니다.
