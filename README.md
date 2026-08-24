# Pico 2 W IoT 플랫폼

Raspberry Pi Pico 2 W(RP2350) 기반의 모듈형 IoT 시스템입니다. 시스템 코어(Wi-Fi/웹서버/LCD/OTA/웹 에디터)는 붙어있는 센서와 무관하게 항상 동일하게 동작하고, 실제 센서 로직은 `app_*.py` 형태의 "앱"으로 분리되어 있어 웹 UI(`/apps`)에서 원하는 앱을 골라 활성화할 수 있습니다.

## 설계 원칙: 버퍼링하지 말고 스트리밍한다

MicroPython 힙은 조각화되면 **여유 메모리가 충분해도 큰 연속 블록 할당에 실패**합니다. 실제로 `mem_free()`가 370KB인 상태에서 35KB 파일을 받다가 `ENOMEM`이 났습니다. 그래서 코드 전반이 "한 번에 큰 덩어리를 만들지 않는다"는 원칙을 따릅니다.

| 경로 | 방식 | 한 번에 필요한 연속 블록 |
|---|---|---|
| 페이지 렌더링 | 제너레이터가 조각을 yield → 즉시 소켓 전송 | 약 1.5KB (이전: 14KB) |
| OTA 다운로드 | 512바이트씩 임시 파일로 흘리며 해시 계산 | 512B (이전: 파일 전체) |
| 에디터 파일 열기 | 512자씩 읽어 이스케이프하며 전송 | 512B (이전: 파일 전체) |
| 에디터 저장 | POST 본문을 조각 단위로 디코딩하며 기록 | 512B |

## 구성

### 시스템 코어 (센서와 무관, 항상 동작)

- `boot.py` — 부팅 안전망. **웹 에디터로 절대 수정할 수 없는 유일한 파일**입니다. `main.py`보다 항상 먼저 실행되며, `import main`이 실패하면(문법 오류, 없는 모듈 등) `CORE_FILES`의 `.bak`으로 일괄 복구를 시도합니다. 재부팅 시 `debug.log`를 `debug_prev.log`로 옮겨 직전 세션 기록도 보존합니다.
- `main.py` — 부팅·네트워크 연결·메인 루프만 담당하는 얇은 파일. HTTP는 `httpd.py`, 화면은 `web_ui.py`, 주기 작업은 `bg_thread.py`가 맡습니다.
- `httpd.py` — 웹서버. 요청 파싱, **경로 정확 일치 라우팅**(`ROUTES` 표), 응답 스트리밍.
- `web_ui.py` — 모든 화면의 HTML. 각 페이지 함수는 완성된 문자열이 아니라 조각을 yield하는 **제너레이터**이고, CSS는 `BASE_CSS` 하나를 공유합니다.
- `console_log.py` — `print()` 후킹 → 원격 콘솔(`/logs`) 버퍼 + `debug.log` 파일 기록. `main.py`가 가장 먼저 import합니다.
- `bg_thread.py` — 보조 코어(core1)에서 도는 **영구 워커** 하나가 등록된 주기 작업을 순차 실행합니다.
- `watchdog.py` — 하드웨어 워치독(8초). 원인 불명의 먹통에서 자동 복구합니다.
- `cpu_config.py` — CPU 클럭(오버클럭) 선택 저장/불러오기. `/power`에서 고른 값을 재부팅 후 적용합니다.
- `lcd_driver.py` — I2C 1602 LCD 드라이버 (백라이트 제어 포함).
- `wifi_manager.py` — Wi-Fi 연결(3회 재시도)/AP 모드/설정 저장/NTP 시각 동기화.
- `file_editor.py` — 웹 에디터의 저장/백업/되돌리기/목록 로직.
- `ota.py` — GitHub `manifest.json` 기반 **수동** 업데이트.
- `app_manager.py` — 어떤 `app_*.py`를 활성화할지 관리.
- `netutil.py` — 하드웨어 의존성 없는 순수 유틸리티(URL 디코딩, HTML 이스케이프, 요청 파싱 등). 데스크톱에서 그대로 테스트합니다.
- `manifest.json` — OTA용 파일별 sha256 + 버전 해시. 커밋 전 `python3 scripts/gen_manifest.py`로 재생성합니다 (CI가 확인).

### 앱 (센서별 로직, 하나만 활성화됨)

- `app_idle.py` — 연결된 센서가 없을 때 쓰는 최소 앱 (하드웨어 의존성 없음). **기본값**입니다.
- `app_reaction_game.py` — Grove 옐로우 LED 버튼 반응속도 게임.
- `app_dust_monitor.py` — 샤프 계열 미세먼지 센서 + 버저, 구글 시트 연동.

## 앱 시스템

`app_` 접두사가 붙은 `.py` 파일 중 하나를 "활성 앱"으로 선택해 실행합니다.

- `http://<피코IP>/apps`에서 목록을 보고 전환합니다 (전환 시 재부팅).
- **수동 선택** 방식입니다 — 센서를 자동 감지하지 않고 직접 고릅니다.
- 활성 앱 이름은 `active_app.json`에 기기별로 저장되며 **git/OTA 동기화 대상이 아닙니다**.
- 웹 에디터(`/edit`)에서 `app_이름.py`를 만들면 `/apps`에 자동으로 나타납니다.
- `app_manager.load_active_app()`이 예외를 격리하므로, 앱이 깨져도 웹서버/LCD는 죽지 않습니다.

### 앱 인터페이스 계약

없는 항목은 해당 기능만 조용히 건너뜁니다 (부팅 시 누락 경고 출력). 전체 목록은 `app_manager.REQUIRED_APP_ATTRS` 참고.

| 이름 | 형태 | 설명 |
|---|---|---|
| `read_dust_sensor()` | `() -> (voltage: float, value: float)` | 센서/상태 측정 |
| `get_status_info(value)` | `(float) -> (eng, kor, color_hex)` | 측정값 → 상태 판정 |
| `sync_with_google_sheets(value, voltage, status)` | `(...) -> bool` | 클라우드 동기화 (core1 워커에서 호출. 정의하지 않으면 아예 호출되지 않음) |
| `play_alert_beep()` | `() -> None` | 경보음 |
| `is_muted` / `alert_threshold` / `cloud_sync_status` | `bool` / `float` / `str` | 알림·표시용 상태 |

## 웹 화면

| 경로 | 설명 |
|---|---|
| `/` | 대시보드 — 측정값, 활성 앱, OTA 상태, 여유 메모리, 가동 시간, Wi-Fi 설정 |
| `/edit` | 파일 브라우저 / 에디터 |
| `/apps` | 앱 전환 |
| `/logs` | 원격 콘솔 (최근 로그, 2초마다 갱신) |
| `/power` | 전원 관리 — 다시 시작 / 절전 / 시스템 종료 |

### 웹 에디터

보드 루트의 `.py` 파일과 `debug.log`/`debug_prev.log`를 열 수 있습니다 (`boot.py` 제외). `main.py`를 포함한 코어 모듈도 직접 수정할 수 있습니다.

- **저장**: 내용이 실제로 바뀐 경우에만 `<파일명>.bak`을 만들고 재부팅합니다. 임시 파일에 다 쓴 뒤 `os.rename`으로 갈아끼우므로 중간에 전원이 끊겨도 반쪽 파일이 남지 않습니다.
- **되돌리기**: 백업이 있으면 "↩️ 되돌리기" 버튼으로 `.bak`을 복원하고 재부팅합니다.
- **안전망**: 저장한 코어 모듈 때문에 `import main`이 실패하면 `boot.py`가 `.bak`을 복원합니다.

### 전원 관리 (`/power`)

- **다시 시작** — 즉시 재부팅
- **절전** — LCD와 센서 측정을 끄고 웹서버는 유지 (같은 화면에서 해제 가능)
- **시스템 종료** — 전원을 뽑아도 안전한 상태로 정지. 복구하려면 전원 재인가 필요
- **CPU 클럭 (오버클럭)** — 150/200/225/250/270/300MHz 중 선택. 전압은 건드리지 않는
  선의 값들이며, 선택 즉시가 아니라 재부팅 후 적용됩니다 (`cpu_config.json`에 저장).
  문제가 보이면 기본값(150MHz)으로 되돌리면 됩니다.

## 하드웨어 워치독

메인 루프가 8초 이상 멈추면 자동으로 재부팅합니다. 부팅과 Wi-Fi 연결(최대 24초+)이 모두 끝난 뒤에 켜지므로 부팅 중 리셋 루프에 빠지지 않습니다. Wi-Fi 재접속, NTP, 파일 저장, `/edit` 전송, OTA 다운로드 등 오래 걸릴 수 있는 모든 구간에서 `feed()`를 호출합니다.

> ⚠️ RP2040/RP2350의 워치독은 **한번 켜면 재부팅 전까지 끌 수 없습니다.** Thonny에서 스크립트를 정지하면 feed가 끊겨 8초마다 강제 재부팅되어 개발이 불가능해집니다. Thonny로 개발할 일이 생기면 `watchdog.py`의 `WDT_ENABLED = False`로 바꾸세요.

## OTA 업데이트 (수동)

대시보드의 **"🛰️ 지금 업데이트 확인"** 버튼을 눌렀을 때만 이 저장소 `main` 브랜치의 `manifest.json`을 확인하고, 해시가 다른 파일만 받아 적용한 뒤 재부팅합니다.

자동 주기 폴링은 껐습니다(`ota.OTA_AUTO_CHECK = False`). 47초마다 HTTPS로 GitHub에 붙는 것이 불안정 요인이었고, 실패한 업데이트가 연쇄적으로 기기를 망가뜨린 전례가 있어서입니다.

**받을 파일은 이름 목록이 아니라 구조로 판정합니다** (`_is_ota_target`): 최상위 `.py`만 허용하고, 경로 탈출(`../`)·기기 로컬 파일(`wifi_config.json` 등)·매니페스트 메타데이터(`_version`)는 거부합니다. 예전처럼 허용 파일명을 하드코딩하면, 판정 주체가 기기에 이미 깔린 **구버전** `ota.py`라서 **새로 추가된 모듈이 영영 전달되지 못합니다**.

안전장치:

- 받는 즉시 sha256을 계산해 매니페스트와 대조하고, 다르면 적용하지 않습니다 (잘린 다운로드가 걸러집니다).
- 임시 파일에 받은 뒤 `os.rename`으로 원자적으로 갈아끼웁니다.
- 기존 내용은 `.bak`으로 백업합니다.
- **중간에 실패하면 재부팅하지 않습니다.** 매니페스트는 이름순으로 처리되므로(`main.py`가 `web_ui.py`보다 먼저) 도중에 끊기면 버전이 뒤섞인 상태가 되고, 그대로 부팅하면 `ImportError` → 전체 롤백 → 다음 시도에 같은 실패가 반복됩니다. 남은 파일은 해시가 여전히 다르므로 다음 확인 때 이어받습니다.

기타:

- **다른 리포로 포크했다면** `ota.py`의 `OTA_REPO_RAW_BASE`를 바꾸세요. 끄려면 `OTA_ENABLED = False`.
- **리포지토리가 public이어야 합니다.** private면 raw 파일 요청에 404가 와서(파일 없음과 구분 불가) 조용히 실패합니다.
- 커밋 전 반드시 `python3 scripts/gen_manifest.py`를 실행하세요 (CI가 확인).
- 새 모듈을 추가할 때는 `main.py`처럼 `try: import X / except ImportError:` 스텁 폴백을 두세요. 아직 그 파일을 받지 못한 기기에서 부팅이 죽으면 `boot.py`가 전체를 롤백해버려, 정작 그 파일을 받아올 기회가 사라집니다.

## 문제 진단

기기가 응답하지 않게 되면:

1. 전원을 뽑았다 다시 꽂습니다.
2. `http://<피코IP>/edit?file=debug_prev.log`를 엽니다 — **직전 세션의 마지막 기록**입니다. `debug.log`는 이번 세션 것으로 덮이므로 사후 확인에는 `debug_prev.log`를 봅니다.
3. 하트비트(`💓 mem=... wifi=... up=...`)의 마지막 값으로 메모리 고갈인지, Wi-Fi 문제인지, 아니면 여유가 충분한데 멈춘 것인지 구분합니다.

오류가 났을 때는 `log_error()`가 즉시 파일로 flush하므로, 오류 직후 멈춰도 그 기록은 남습니다. 평소 하트비트는 플래시 수명을 아끼려고 5분에 한 번만 파일에 씁니다.

## 동작 방식

1. `boot.py`가 `debug.log`를 보존하고 `import main`을 시도합니다 (실패 시 코어 모듈 일괄 롤백).
2. `main.py`가 LCD를 초기화하고 활성 앱을 예외 격리해서 로드합니다.
3. 주기 작업(클라우드 동기화, OTA 요청 확인)을 등록하고 core1 워커를 띄웁니다.
4. `wifi_config.json`으로 접속을 시도합니다(3회 재시도). 실패하면 `Pico-Dust-Setup` 핫스팟을 엽니다. AP 모드에서도 3분마다 저장된 Wi-Fi로 백그라운드 재접속을 시도합니다.
5. 웹서버를 열고 **워치독을 켭니다**.
6. 메인 루프가 요청 처리·측정·LCD 갱신·하트비트를 돌립니다.

## 준비물

- Raspberry Pi Pico 2 W + Grove Shield for Pi Pico
- 1602 LCD (PCF8574 I2C 어댑터, I2C0: SDA=GPIO8, SCL=GPIO9)
- (반응속도 게임) Grove 옐로우 LED 버튼 — D16 포트 (SIG1=GP16 버튼, SIG2=GP17 LED)
- (미세먼지) 샤프 계열 센서 (LED GPIO27, ADC GPIO26), PWM 버저 (GPIO20)
- [MicroPython 펌웨어](https://micropython.org/download/RPI_PICO2_W/)
- Thonny, `mpremote`, `rshell` 등 업로드 도구

## 시작하기

1. Pico 2 W에 MicroPython 펌웨어를 설치합니다 (BOOTSEL을 누른 채 USB 연결 → `.uf2` 드래그).
2. 전체 파일을 업로드합니다. `boot.py`를 **마지막에** 올리면 중간에 재부팅돼도 안전망이 어설프게 작동하지 않습니다.
   ```bash
   for f in main.py httpd.py web_ui.py console_log.py bg_thread.py watchdog.py \
            cpu_config.py lcd_driver.py wifi_manager.py file_editor.py ota.py \
            app_manager.py netutil.py app_idle.py app_reaction_game.py \
            app_dust_monitor.py boot.py; do
     mpremote cp "$f" ":$f"
   done
   ```
3. 보드를 리셋하면 웹서버가 시작됩니다. 처음에는 `Pico-Dust-Setup` 핫스팟에 접속해 `192.168.4.1`에서 Wi-Fi를 설정하세요.
4. `/apps`에서 실제로 연결한 센서에 맞는 앱을 고릅니다 (기본값 `app_idle`).
5. 미세먼지 앱을 쓴다면 `app_dust_monitor.py`의 `GAS_URL`을 본인 Google Apps Script 배포 URL로 바꾸세요.

## 테스트

하드웨어가 없어도 검증할 수 있는 부분은 데스크톱에서 테스트합니다.

```bash
pip install pytest
pytest tests/ -v
```

- `test_netutil.py` — URL 디코딩, HTML 이스케이프, 요청 파싱, 헤더 분리, 경로 안전성
- `test_web_ui.py` — 모든 페이지가 올바른 HTML 문서를 만드는지, **조각 크기가 작게 유지되는지**(스트리밍 설계가 되돌려지면 여기서 실패), 사용자 값이 이스케이프되는지
- `test_logic.py` — 폼 디코더의 조각 경계 처리, OTA 대상 판정, 라우팅 표
- `test_httpd_requests.py` — 가짜 소켓으로 실제 요청을 통과시키는 통합 테스트 (`/data`가 주는 필드와 대시보드 JS가 읽는 필드가 어긋나지 않는지도 검사)

`tests/conftest.py`가 `machine`·`network`·`urequests`·`utime` 스텁을 심어주므로 MicroPython 전용 모듈도 import됩니다.

PR을 올리면 GitHub Actions(`.github/workflows/test.yml`)가 같은 테스트와 `manifest.json` 최신 여부를 확인합니다.

## 참고

- `wifi_config.json`, `active_app.json`, `ota_state.json`, `debug*.log`는 기기별 런타임 파일이라 저장소에 포함하지 않습니다 (`.gitignore`).
- `/edit`, `/save_code`, `/revert`, `/apps/set`, `/power/*`에는 인증이 없어 같은 네트워크의 누구나 코드를 수정하거나 기기를 정지시킬 수 있습니다. **인터넷에 직접 노출(포트포워딩·DMZ)하지 마세요** — 노출한다면 VPN을 통해 접근하는 편이 안전합니다.
- OTA는 인증서 체인을 검증하지 않는 연결이지만, 받아온 내용이 `manifest.json`의 해시와 일치하는지는 항상 확인합니다.
