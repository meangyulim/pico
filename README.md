# Pico 2 W IoT 플랫폼

Raspberry Pi Pico 2 W(RP2350) 기반의 모듈형 IoT 시스템입니다. 시스템 코어(Wi-Fi/웹서버/LCD/OTA/웹 에디터)는 붙어있는 센서와 무관하게 항상 동일하게 동작하고, 실제 센서 로직은 `app_*.py` 형태의 "앱"으로 분리되어 있어 웹 UI(`/apps`)에서 원하는 앱을 골라 활성화할 수 있습니다.

## 구성

### 시스템 코어 (센서와 무관, 항상 동작)

- `boot.py` — 부팅 안전망. **웹 에디터로 절대 수정할 수 없는 유일한 파일**입니다. `main.py`보다 항상 먼저 실행되며, 핵심 모듈이 깨졌을 때(문법 오류, 임포트 중 예외 등) 각 모듈의 `.bak`으로 자동 복구를 시도합니다 (`CORE_FILES` 목록 참고).
- `main.py` — 시스템 코어의 글루 코드. Wi-Fi 연결/AP 모드, 웹서버, LCD, 앱 로딩을 서로 연결하고 HTTP 라우팅을 담당합니다. 실제 기능 구현은 아래 모듈들에 있습니다.
- `console_log.py` — 원격 콘솔(`/logs`)용 `print()` 후킹 및 오류 로그 헬퍼. 다른 모든 모듈이 이 모듈을 통해 로그를 남기므로 `main.py`가 가장 먼저 import합니다.
- `bg_thread.py` — RP2040/RP2350의 보조 코어(core1) 하나를 클라우드 동기화/OTA 확인 등 여러 백그라운드 작업이 충돌 없이 공유해서 쓰도록 조율합니다.
- `lcd_driver.py` — I2C 1602 LCD 드라이버 (`I2cLcd` 클래스).
- `wifi_manager.py` — Wi-Fi 연결/AP 모드 전환/설정 저장 로직.
- `web_ui.py` — 대시보드/파일 브라우저/에디터/로그/앱 전환 화면의 HTML 생성 함수.
- `file_editor.py` — 웹 에디터의 파일 저장/백업/되돌리기/목록 로직.
- `ota.py` — GitHub `manifest.json` 폴링 기반 자동 업데이트.
- `app_manager.py` — 어떤 `app_*.py`를 활성화할지 관리 (아래 "앱 시스템" 참고).
- `netutil.py` — 하드웨어 의존성 없는 순수 유틸리티 (`url_decode` 등). 데스크톱 파이썬에서 그대로 테스트할 수 있습니다.
- `manifest.json` — OTA 자동 업데이트용 파일별 sha256 해시 목록. 커밋 전 `python3 scripts/gen_manifest.py`로 재생성합니다 (CI가 최신 상태인지 확인).

### 앱 (센서별 로직, 하나만 활성화됨)

- `app_reaction_game.py` — Grove 옐로우 LED 버튼을 이용한 반응속도 게임.
- `app_dust_monitor.py` — 샤프 계열 미세먼지 센서 + 버저 모니터링, 구글 시트 연동.

## 앱 시스템

피코에 어떤 센서가 연결되어 있는지에 따라 다른 로직을 실행할 수 있도록, `app_` 접두사가 붙은 `.py` 파일 중 하나를 "활성 앱"으로 선택해서 실행합니다.

- `http://<피코IP>/apps`에서 설치된 앱 목록을 보고 전환할 수 있습니다 (전환 시 즉시 재부팅).
- 현재는 **수동 선택** 방식입니다 — 센서를 자동으로 감지하지 않고, 사용자가 붙인 센서에 맞는 앱을 직접 고릅니다.
- 활성 앱 이름은 `active_app.json`에 기기별로 저장됩니다. `wifi_config.json`과 마찬가지로 기기마다 다를 수 있는 로컬 런타임 설정이라 **git/OTA 동기화 대상이 아닙니다** — 리포를 그대로 받아써도 각 기기가 이미 고른 앱 선택이 유지됩니다.
- 새 앱을 추가하려면 웹 에디터(`/edit`)에서 `app_새앱이름.py`처럼 파일을 만들면 `/apps` 목록에 자동으로 나타납니다.
- `main.py`는 활성 앱을 `app_manager.load_active_app()`으로 예외 격리해서 로드합니다 — 앱에 오타나 오류가 있어도 시스템 코어(웹서버/LCD)는 절대 다운되지 않습니다.

### 앱 인터페이스 계약

`main.py`는 활성 앱에서 아래 항목을 찾아서 있으면 사용하고, 없으면 해당 기능만 조용히 건너뜁니다 (부팅 시 콘솔에 누락 항목을 경고로 출력). 전체 목록은 `app_manager.py`의 `REQUIRED_APP_ATTRS`를 참고하세요.

| 이름 | 형태 | 설명 |
|---|---|---|
| `read_dust_sensor()` | `() -> (avg_voltage: float, value: float)` | 센서/상태 측정 |
| `get_status_info(value)` | `(float) -> (eng: str, kor: str, color_hex: str)` | 측정값 → 상태 판정 |
| `sync_with_google_sheets(value, voltage, status_eng)` | `(...) -> bool` | 클라우드 동기화 (별도 코어 스레드에서 호출됨, 정의하지 않으면 스레드 자체가 안 뜸) |
| `play_alert_beep()` | `() -> None` | 경보음 |
| `is_muted` | `bool` | 음소거 여부 |
| `alert_threshold` | `float` | 경보 임계치 |
| `cloud_sync_status` | `str` | 동기화 상태 표시 문자열 |

## 웹 파일 브라우저 / 에디터

`http://<피코IP>/edit`에 접속하면 보드 루트의 `.py` 파일 목록이 뜹니다 (`boot.py`와 `.bak`/`.json` 등 상태 파일은 제외). 파일을 눌러 편집·저장하거나, 새 파일 이름을 입력해 만들 수 있습니다. `main.py`를 포함한 시스템 코어 모듈도 여기서 직접 수정할 수 있습니다.

- **저장**: 내용이 실제로 바뀐 경우에만 저장 전 자동으로 `<파일명>.bak`을 만들고, 저장 후 재부팅합니다.
- **되돌리기**: 백업이 있는 파일은 에디터 화면에 "↩️ 이전 버전" 버튼이 뜨고, 누르면 `.bak`을 복원하고 재부팅합니다.
- **안전망(`boot.py`)**: 저장한 코어 모듈이 문법 오류나 즉시 예외로 `main.py` import에 실패하면, `boot.py`가 자동으로 해당 모듈들의 `.bak`을 복원합니다 (`CORE_FILES` 목록 참고). **다만 예외 없이 무한루프에 빠지는 경우는 이 자동 복구 대상이 아닙니다** — 이런 드문 경우엔 USB로 재업로드해야 합니다. (하드웨어 워치독으로 이 경우까지 잡는 것도 시도해봤지만, RP2040/RP2350의 워치독은 한번 켜면 재부팅 전까지 끌 수 없어서 Thonny 등으로 개발 중 스크립트를 정지시킬 때마다 강제 재부팅되어 시리얼 연결이 끊기는 문제가 있어 제거했습니다.)
- 활성 앱(`app_*.py`)은 `main.py`가 예외를 격리해서 로드하므로, 이 파일이 깨져도 웹서버 자체는 절대 죽지 않습니다.

## 원격 콘솔 (`/logs`)

USB로 Thonny에 물려있지 않아도, Wi-Fi로만 연결돼 있으면 `http://<피코IP>/logs`에서 `print()` 출력을 실시간으로 볼 수 있습니다 (최근 200줄, 2초마다 자동 갱신). 시리얼 출력은 그대로 유지되고, 별도로 메모리 버퍼에도 저장해서 웹으로 보여주는 방식입니다 (`console_log.py`). `main.py`가 이 훅을 설치하기 전(부팅 아주 초반, `boot.py` 단계)의 로그는 여기 안 남습니다.

## 동작 방식

1. 부팅 시 `boot.py`가 `main.py`를 안전하게 로드합니다 (실패 시 핵심 모듈들을 백업에서 일괄 복원).
2. `main.py`가 `app_manager.load_active_app()`으로 활성 앱을 안전하게(예외 격리) 로드합니다.
3. `wifi_config.json`에 저장된 Wi-Fi 정보로 접속을 시도합니다 (신호 불안정으로 인한 일시적 실패에 대비해 최대 3회 재시도). 그래도 실패하면 `Pico-Dust-Setup`이라는 이름의 오프라인 핫스팟(AP)을 엽니다. AP 모드로 넘어간 뒤에도 3분마다 저장된 정보로 백그라운드 재접속을 시도하므로, 비밀번호가 맞다면 굳이 다시 입력하지 않아도 신호가 돌아오면 자동으로 복구됩니다.
4. 웹 대시보드(`/`)에서 실시간 측정값·상태를 확인하고, Wi-Fi를 설정할 수 있습니다.
5. 웹 에디터(`/edit`)에서 원하는 파일을 스마트폰으로 직접 수정·생성·복원할 수 있습니다.
6. `/apps`에서 연결된 센서에 맞는 앱을 선택할 수 있습니다.
7. 활성 앱이 `sync_with_google_sheets`를 구현했다면, `CLOUD_SYNC_INTERVAL_MS`(기본 1분) 주기로 클라우드와 데이터를 주고받습니다. 이 동기화는 RP2350의 두 번째 코어(`_thread`)에서 실행되어, 네트워크가 느리거나 응답이 없어도 웹서버·LCD·센서 측정은 멈추지 않습니다. (`_thread`를 지원하지 않는 빌드에서는 자동으로 기존 방식인 동기 호출로 폴백합니다.)
8. Wi-Fi에 연결된 동안 약 47초마다 GitHub의 `manifest.json`을 확인해서, 바뀐 파일이 있으면 자동으로 받아 적용하고 재부팅합니다 (아래 OTA 자동 업데이트 참고).

## OTA 자동 업데이트 (GitHub 자동 반영)

Wi-Fi에 연결되어 있으면, 피코가 약 47초마다(`ota.OTA_CHECK_INTERVAL_MS`) 이 저장소의 `main` 브랜치에 있는 `manifest.json`(파일별 sha256 해시만 담은 아주 작은 파일)을 확인합니다. 클라우드 동기화 주기(60초)와 딱 겹치지 않도록 일부러 60의 배수가 아닌 값을 썼습니다. 해시가 실제로 다른 파일만 골라 통째로 받아서 적용하므로, 평소엔 몇십 바이트만 오가고 진짜 코드가 바뀐 순간에만 무거운 다운로드가 일어납니다.

- 추적 대상: 시스템 코어 모듈 전체 + 모든 `app_*.py` (`ota.py`의 `OTA_ALLOWED_TARGETS`). `wifi_config.json`/`active_app.json`처럼 기기별 로컬 설정은 일부러 제외됩니다 (동기화하면 각 기기가 고른 Wi-Fi/앱이 매번 초기화되므로).
- 적용 전 받아온 내용의 해시를 다시 검증하고, 기존 내용은 웹 에디터와 동일하게 `.bak`으로 백업합니다.
- 적용된 파일이 하나라도 있으면 재부팅합니다 — 코어 모듈이 바뀌었어도 `boot.py`의 롤백 안전망을 그대로 거칩니다.
- **다른 리포로 포크했다면** `ota.py`의 `OTA_REPO_RAW_BASE`를 본인 리포 주소로 바꾸세요. 끄고 싶으면 `OTA_ENABLED = False`로 설정하면 됩니다.
- **리포지토리가 public이어야 합니다.** GitHub은 인증 없는 요청이 private 리포의 raw 파일에 접근하면 404를 돌려줘서(파일이 없는 것과 구분이 안 됨), OTA가 조용히 계속 실패합니다.
- 리포를 고칠 때 커밋 전에 반드시 `python3 scripts/gen_manifest.py`를 실행해 `manifest.json`을 최신 상태로 맞춰야 합니다 (`scripts/gen_manifest.py`의 `TRACKED_FILES` 목록). CI가 이걸 확인합니다.

## 준비물

- Raspberry Pi Pico 2 W
- Grove Shield for Pi Pico
- 1602 LCD (PCF8574 I2C 어댑터, I2C0: SDA=GPIO8, SCL=GPIO9)
- (반응속도 게임 앱) Grove 옐로우 LED 버튼 — Grove Shield D16 포트 (SIG1=GP16, SIG2=GP17)
- (미세먼지 앱) 샤프 계열 미세먼지 센서 (LED: GPIO27, ADC: GPIO26), PWM 버저 (GPIO20)
- [MicroPython 펌웨어](https://micropython.org/download/RPI_PICO2_W/)
- Thonny, `mpremote`, `rshell` 등 업로드 도구

## 시작하기

1. Pico 2 W에 MicroPython 펌웨어를 설치합니다 (BOOTSEL 버튼을 누른 채로 USB 연결 → `.uf2` 파일 드래그).
2. 시스템 코어 파일 전부와, 사용할 앱 파일을 보드에 업로드합니다.
   ```bash
   mpremote cp boot.py :boot.py
   mpremote cp main.py :main.py
   mpremote cp console_log.py :console_log.py
   mpremote cp bg_thread.py :bg_thread.py
   mpremote cp lcd_driver.py :lcd_driver.py
   mpremote cp wifi_manager.py :wifi_manager.py
   mpremote cp web_ui.py :web_ui.py
   mpremote cp file_editor.py :file_editor.py
   mpremote cp ota.py :ota.py
   mpremote cp app_manager.py :app_manager.py
   mpremote cp netutil.py :netutil.py
   mpremote cp app_reaction_game.py :app_reaction_game.py
   mpremote cp app_dust_monitor.py :app_dust_monitor.py
   ```
3. 보드를 리셋하면 웹서버가 자동 시작됩니다. 처음에는 `Pico-Dust-Setup` 핫스팟에 접속해 `192.168.4.1`에서 Wi-Fi를 설정하세요.
4. `http://<피코IP>/apps`에서 실제로 연결한 센서에 맞는 앱을 선택하세요 (기본값은 `app_reaction_game`).
5. 미세먼지 앱을 쓴다면 `app_dust_monitor.py`의 `GAS_URL`을 본인의 Google Apps Script 배포 URL로 교체하세요.

## 테스트

하드웨어 의존성이 없는 로직(`netutil.py`)은 데스크톱 파이썬으로 테스트합니다.

```bash
pip install pytest
pytest tests/ -v
```

PR을 올리면 GitHub Actions(`.github/workflows/test.yml`)가 자동으로 같은 테스트를 실행합니다.

## 참고

- `wifi_config.json`, `active_app.json`은 런타임에 보드에 저장되는 기기별 파일이라 저장소에는 포함하지 않습니다 (`.gitignore` 처리).
- 활성 앱을 완전히 지우거나 문법 오류를 내도, 시스템 코어(웹서버/LCD)는 죽지 않고 웹 에디터/`.bak` 되돌리기로 복구할 수 있습니다.
- `/edit`, `/save_code`, `/revert`, `/apps/set`에는 별도 인증이 없어 같은 네트워크에 있는 누구나 코드를 수정하거나 앱을 전환할 수 있습니다. 필요하면 추후 보완하세요.
- OTA는 리포지토리가 public이라 별도 인증 없이 raw 파일을 받아옵니다. 인증서 체인 검증은 하지 않는 연결이라(기존 GAS 연동과 동일한 신뢰 수준) 완벽한 보안은 아니지만, 받아온 내용이 `manifest.json`의 해시와 일치하는지는 항상 확인합니다.
