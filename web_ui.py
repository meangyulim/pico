# =================================================================
# web_ui.py : 웹 화면 HTML 생성 (조각 단위 스트리밍)
# =================================================================
# 모든 페이지 함수는 완성된 HTML 문자열을 반환하지 않고, 작은 조각을
# 차례로 yield하는 제너레이터입니다. httpd가 그 조각을 받는 즉시 소켓으로
# 흘려보내므로, 페이지가 아무리 커도 메모리에는 조각 하나만 남습니다.
#
# 예전에는 페이지 전체를 f-string으로 한 번에 만들었는데, 대시보드 기준
# 문자열 7KB + 인코딩된 bytes 7.4KB = 요청당 약 14KB를 동시에 들고
# 있었습니다. MicroPython 힙은 조각화되면 이런 큰 연속 블록을 잡지
# 못해서, 여유 메모리가 370KB인데도 할당이 실패하는 일이 생깁니다
# (실제로 OTA 중 ENOMEM으로 겪음).
#
# CSS도 페이지마다 통째로 중복돼 있던 것을 BASE_CSS 하나로 합쳤습니다
# (예전엔 <style> 블록 6개가 web_ui.py의 39%를 차지했습니다).
#
# 이 파일은 하드웨어 모듈을 import하지 않는 순수 문자열 생성이라
# 데스크톱에서 그대로 테스트할 수 있습니다 (tests/test_web_ui.py).
# =================================================================
from netutil import esc

BASE_CSS = (
    "*{box-sizing:border-box;margin:0;padding:0}"
    "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
    "background:#0f172a;color:#f8fafc;padding:16px;-webkit-text-size-adjust:100%}"
    "a{color:#38bdf8}"
    "h3{font-size:16px;color:#38bdf8}"
    ".hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}"
    ".back{color:#94a3b8;text-decoration:none;font-size:12px;padding:6px 10px;"
    "background:#1e293b;border-radius:6px;border:1px solid #334155}"
    ".card{background:#1e293b;border:1px solid #334155;border-radius:16px;"
    "padding:16px;margin-bottom:12px}"
    ".note{font-size:12px;color:#94a3b8;line-height:1.5;margin-bottom:12px}"
    ".row{display:block;padding:12px 14px;background:#1e293b;border:1px solid #334155;"
    "border-radius:10px;color:#f1f5f9;text-decoration:none;font-size:14px;margin-bottom:8px}"
    ".row:active{background:#334155}"
    ".btn{width:100%;padding:12px;background:#0284c7;color:#fff;border:none;"
    "border-radius:10px;font-size:14px;font-weight:bold}"
    "label{display:block;font-size:12px;font-weight:600;margin-bottom:4px;color:#cbd5e1}"
    "input,select,textarea{width:100%;padding:10px 12px;margin-bottom:12px;border-radius:8px;"
    "border:1px solid #475569;background:#0f172a;color:#fff;font-size:16px}"
    ".info{font-size:13px;color:#94a3b8;line-height:1.9}"
    ".info b{color:#f1f5f9}"
    ".tag{float:right;font-size:11px;background:#065f46;color:#34d399;"
    "padding:3px 8px;border-radius:6px;font-weight:bold}"
    ".t{font-size:15px;font-weight:bold;margin-bottom:4px}"
    ".d{font-size:12px;color:#94a3b8;line-height:1.5}"
)


def _head(title, extra_css=None):
    yield ('<!DOCTYPE html><html><head><meta charset="UTF-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1.0,'
           'maximum-scale=1.0,user-scalable=no"><title>')
    yield esc(title)
    yield '</title><style>'
    yield BASE_CSS
    if extra_css:
        yield extra_css
    yield '</style></head><body>'


def _hdr(title, back_href="/", back_text="⬅ 메인으로"):
    yield '<div class="hdr"><h3>'
    yield esc(title)
    yield '</h3><a href="'
    yield back_href
    yield '" class="back">'
    yield back_text
    yield '</a></div>'


def _end():
    yield '</body></html>'


# -----------------------------------------------------------------
# 범용 안내 화면 — 저장 완료/실패, 재부팅 안내 등에 씁니다.
# 예전에는 이런 화면들이 main.py 안에 인라인 HTML로 6군데 흩어져
# 중복돼 있었습니다.
# -----------------------------------------------------------------
def message(title, heading, body="", color="#38bdf8", redirect=None, delay_ms=5000):
    yield from _head(title)
    yield '<div class="card" style="text-align:center;padding:40px 20px">'
    yield '<h3 style="color:' + color + ';font-size:19px;margin-bottom:12px">'
    yield esc(heading)
    yield '</h3>'
    if body:
        yield '<p class="d">' + body + '</p>'
    yield '</div>'
    if redirect:
        yield ('<script>setTimeout(function(){location.href="'
               + redirect + '"},' + str(delay_ms) + ')</script>')
    else:
        yield '<a href="/" class="row" style="text-align:center">메인으로</a>'
    yield from _end()


# -----------------------------------------------------------------
# 대시보드 (/)
# -----------------------------------------------------------------
DASH_CSS = (
    ".badge{display:inline-block;padding:4px 12px;border-radius:20px;color:#000;"
    "font-size:12px;font-weight:bold;margin-bottom:12px}"
    ".sbadge{display:inline-block;padding:8px 22px;border-radius:50px;color:#000;"
    "font-weight:800;font-size:15px;margin-bottom:12px;transition:background .3s}"
    ".val{font-size:52px;font-weight:800;margin:8px 0;color:#fff}"
    ".mid{text-align:center}"
    ".dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;"
    "margin-right:6px;animation:p 1.5s infinite}"
    "@keyframes p{0%{opacity:1}50%{opacity:.3}100%{opacity:1}}"
    "details{background:#1e293b;border-radius:16px;padding:14px 18px;"
    "border:1px solid #334155;margin-bottom:12px}"
    "summary{font-size:14px;font-weight:bold;color:#38bdf8;list-style:none}"
)


def dashboard(d):
    """d: mode/ip/wifis/saved_wifis/app_err/value/status_eng/status_kor/color/
    cloud/mute/thresh/ota/active_app/last_update 를 담은 dict."""
    offline = (d["mode"] == "OFFLINE_AP")
    yield from _head("Pico 대시보드", DASH_CSS)

    yield '<div class="card mid">'
    if d.get("app_err"):
        yield ('<div style="background:#ef444422;border:1px solid #ef4444;border-radius:12px;'
               'padding:12px;margin-bottom:14px;color:#fca5a5;font-size:13px;text-align:left">'
               '<b>⚠️ 앱 실행 오류 (')
        yield esc(d["active_app"])
        yield ')</b><br><code style="color:#fff;word-break:break-all;display:block;margin:6px 0;'
        yield 'background:#0f172a;padding:6px 8px;border-radius:6px">'
        yield esc(d["app_err"])
        yield '</code>👉 <b>웹 에디터</b>로 고치거나 <b>앱 전환</b>에서 다른 앱을 고르세요.</div>'

    yield '<div class="badge" style="background:'
    yield "#38bdf8" if offline else "#10b981"
    yield '">'
    yield "📡 오프라인 AP 모드" if offline else "🌐 온라인 모드"
    yield '</div><br><div class="sbadge" id="sb" style="background:'
    yield d["color"]
    yield '">'
    yield esc(d["status_kor"]) + ' (' + esc(d["status_eng"]) + ')'
    yield '</div><div class="val" id="v">'
    yield "{:.0f}".format(d["value"])
    yield '</div>'

    yield '<div class="info" style="text-align:left;border-top:1px solid #334155;padding-top:12px">'
    yield '• <span class="dot"></span>로컬 연결: <b>정상</b><br>'
    yield '• 🔌 활성 앱: <b>' + esc(d["active_app"]) + '</b><br>'
    yield '• ☁️ 클라우드: <b id="c">' + esc(d["cloud"]) + '</b><br>'
    yield '• 🔔 알림: <b id="m">Mute: ' + str(d["mute"]) + ' / 기준: ' + "{:.0f}".format(d["thresh"]) + '</b><br>'
    yield '• 🛰️ OTA 확인: <b id="o">' + esc(d["ota"]) + '</b><br>'
    yield '• 📦 마지막 업데이트: <b id="u">' + esc(d["last_update"]) + '</b><br>'
    yield '• 🧠 여유 메모리: <b id="f">-</b><br>'
    yield '• ⏱️ 가동 시간: <b id="t">-</b><br>'
    yield '• IP: <b>' + esc(str(d["ip"])) + '</b></div></div>'

    yield '<details><summary>📶 Wi-Fi 연결 설정</summary>'
    saved = d.get("saved_wifis") or []
    if saved:
        yield ('<div class="note" style="margin-top:12px">저장된 Wi-Fi — 이 중 신호가 '
               '잡히는 곳에 자동으로 연결됩니다 (휴대폰처럼 여러 개 저장 가능).</div>')
        for ssid in saved:
            yield '<div class="row" style="display:flex;justify-content:space-between;align-items:center">'
            yield '<span>📶 ' + esc(ssid) + '</span>'
            yield ('<a href="/wifi/forget?ssid=' + esc(ssid) + '" style="color:#fca5a5;'
                   'font-size:12px" onclick="return confirm(\'' + esc(ssid) +
                   '을(를) 잊어버릴까요?\')">삭제</a></div>')
    yield '<form action="/save" method="GET" style="margin-top:12px">'
    yield '<label>주변 Wi-Fi 추가</label><select onchange="document.getElementById(\'s\').value=this.value">'
    yield '<option value="">-- 검색된 목록 --</option>'
    for w in d["wifis"]:
        yield '<option value="' + esc(w) + '">' + esc(w) + '</option>'
    yield '</select><label>SSID</label><input type="text" name="ssid" id="s" required>'
    yield '<label>비밀번호</label><input type="password" name="password">'
    yield '<button type="submit" class="btn">저장 및 연결</button></form></details>'

    for href, label in (("/ota/check", "🛰️ 지금 업데이트 확인"), ("/apps", "🔌 앱 전환"),
                        ("/edit", "📝 웹 에디터"), ("/logs", "📜 실시간 로그"),
                        ("/power", "⚡ 전원 관리")):
        yield '<a href="' + href + '" class="row" style="text-align:center;color:#38bdf8;font-weight:bold">'
        yield label + '</a>'

    yield ("<script>function g(i){return document.getElementById(i)}"
           "async function u(){try{var r=await fetch('/data?t='+Date.now());"
           "if(!r.ok)return;var d=await r.json();"
           "g('v').innerText=d.value.toFixed(0);g('c').innerText=d.cloud;"
           "g('m').innerText='Mute: '+d.mute+' / 기준: '+d.thresh;"
           "g('o').innerText=d.ota;g('u').innerText=d.last_update;"
           "g('f').innerText=(d.mem_free/1024).toFixed(1)+' KB';"
           "g('t').innerText=d.uptime;"
           "var b=g('sb');b.innerText=d.kor+' ('+d.eng+')';b.style.background=d.color;"
           "}catch(e){}}setInterval(u,2000);u()</script>")
    yield from _end()


# -----------------------------------------------------------------
# 원격 콘솔 (/logs)
# -----------------------------------------------------------------
def logs_page():
    yield from _head("Pico 원격 콘솔")
    yield from _hdr("📜 원격 콘솔")
    yield '<div class="note">Wi-Fi만 연결돼 있어도 print() 출력을 볼 수 있습니다. 2초마다 갱신.</div>'
    yield ('<div id="b" style="width:100%;height:72vh;background:#000;color:#4ade80;'
           'font-family:Consolas,monospace;font-size:12px;line-height:1.5;'
           'border:1px solid #334155;border-radius:10px;padding:10px;overflow-y:auto;'
           'white-space:pre-wrap;word-break:break-all">불러오는 중...</div>')
    yield ("<script>var b=document.getElementById('b');"
           "async function r(){try{var x=await fetch('/logs.txt?t='+Date.now());"
           "if(!x.ok)return;var t=await x.text();"
           "var at=b.scrollTop+b.clientHeight>=b.scrollHeight-20;"
           "b.textContent=t;if(at)b.scrollTop=b.scrollHeight;}catch(e){}}"
           "setInterval(r,2000);r()</script>")
    yield from _end()


# -----------------------------------------------------------------
# 파일 브라우저 (/edit)
# -----------------------------------------------------------------
def file_list(files):
    yield from _head("Pico 파일 브라우저")
    yield from _hdr("📁 파일 브라우저")
    yield ('<div class="note">boot.py는 부팅 안전망이라 목록에서 제외됩니다. '
           '저장할 때마다 이전 버전이 자동 백업됩니다.</div>')
    if files:
        for n in files:
            yield '<a href="/edit?file=' + esc(n) + '" class="row">📄 ' + esc(n) + '</a>'
    else:
        yield '<p class="note">편집 가능한 파일이 없습니다.</p>'
    yield ('<div class="card"><form action="/edit" method="GET">'
           '<label>새 파일 이름 (.py)</label>'
           '<input type="text" name="file" placeholder="예: app_sensor.py" required>'
           '<button type="submit" class="btn">만들기 / 열기</button></form></div>')
    yield from _end()


# -----------------------------------------------------------------
# 에디터 (/edit?file=..) — 코드 본문은 httpd가 파일에서 조각내어 흘려보냅니다
# -----------------------------------------------------------------
EDITOR_CSS = (
    "textarea{height:62vh;font-family:Consolas,monospace;line-height:1.4;"
    "resize:none;white-space:pre;tab-size:4}"
    ".tb{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px}"
    ".tbtn{padding:10px 4px;background:#1e293b;color:#cbd5e1;border:1px solid #334155;"
    "border-radius:8px;font-size:13px;font-weight:600;text-align:center;text-decoration:none}"
)


def editor_head(name, has_backup):
    safe = esc(name)
    yield from _head("편집: " + name, EDITOR_CSS)
    yield from _hdr("📝 " + name, "/edit", "⬅ 파일 목록")
    yield '<div class="note">'
    if name == "main.py":
        yield '🛡️ 이 파일이 깨져도 boot.py가 이전 버전으로 자동 복구합니다.'
    else:
        yield '🛡️ 실수해도 시스템 코어는 죽지 않습니다.'
    yield '</div><div class="tb">'
    yield '<button type="button" class="tbtn" onclick="cp()">📋 복사</button>'
    yield '<button type="button" class="tbtn" onclick="cl()">🗑️ 비우기</button>'
    if has_backup:
        yield ('<a href="/revert?file=' + safe + '" class="tbtn" '
               'onclick="return confirm(\'이전 저장본으로 되돌리고 재부팅할까요?\')">↩️ 되돌리기</a>')
    else:
        yield '<span class="tbtn" style="opacity:.4">↩️ 백업 없음</span>'
    yield '</div><form action="/save_code?file=' + safe + '" method="POST">'
    yield '<textarea name="code" id="c" spellcheck="false" required>'


def editor_tail(name):
    yield '</textarea>'
    yield ('<button type="submit" class="btn" onclick="return confirm(\''
           + esc(name) + ' 저장하고 재부팅할까요?\')">💾 저장 및 재부팅</button></form>')
    yield '<div class="note" style="margin-top:10px;text-align:center">'
    yield '※ 내용이 바뀐 경우에만 재부팅합니다.</div>'
    yield ("<script>var t=document.getElementById('c');"
           "function cp(){t.select();try{document.execCommand('copy');alert('복사됨')}"
           "catch(e){alert('직접 길게 눌러 복사해주세요')}}"
           "function cl(){if(confirm('내용을 모두 지울까요?')){t.value='';t.focus()}}</script>")
    yield from _end()


# -----------------------------------------------------------------
# 앱 전환 (/apps)
# -----------------------------------------------------------------
def app_list(apps, active):
    yield from _head("Pico 앱 전환")
    yield from _hdr("🔌 앱 전환")
    yield ('<div class="note">연결된 센서에 맞는 앱을 고르세요. 전환하면 재부팅됩니다. '
           '파일 브라우저에서 app_ 로 시작하는 .py를 만들면 여기 자동으로 나타납니다.</div>')
    if apps:
        for n in apps:
            if n == active:
                yield '<div class="row" style="border-color:#22c55e;color:#86efac">✅ '
                yield esc(n) + '<span class="tag">사용 중</span></div>'
            else:
                yield '<a href="/apps/set?name=' + esc(n) + '" class="row" '
                yield 'onclick="return confirm(\'' + esc(n) + '(으)로 전환하고 재부팅할까요?\')">🔌 '
                yield esc(n) + '</a>'
    else:
        yield '<p class="note">app_ 로 시작하는 앱 파일이 없습니다.</p>'
    yield from _end()


# -----------------------------------------------------------------
# 전원 관리 (/power)
# -----------------------------------------------------------------
def power_page(power_mode, uptime_str, cpu_mhz, wdt_active, freq_options, current_freq):
    sleeping = (power_mode == "SLEEP")
    yield from _head("Pico 전원 관리")
    yield from _hdr("⚡ 전원 관리")
    yield '<div class="card info">'
    yield '• 상태: <b>' + ("절전 중" if sleeping else "정상 작동") + '</b><br>'
    yield '• 가동 시간: <b>' + esc(uptime_str) + '</b><br>'
    yield '• CPU: <b>' + esc(str(cpu_mhz)) + ' MHz</b><br>'
    yield '• 워치독: <b>' + ("켜짐 (먹통 시 자동 재부팅)" if wdt_active else "꺼짐") + '</b>'
    yield '</div>'

    yield '<details><summary>🚀 CPU 클럭 (오버클럭)</summary>'
    yield ('<div class="note" style="margin-top:8px">전압은 건드리지 않는 선의 값들입니다. '
           '바꾸면 재부팅 후 적용됩니다. 문제가 보이면 150MHz(기본)로 되돌리세요.</div>')
    yield ('<form action="/power/freq" method="GET" style="margin-top:4px" '
           'onsubmit="return confirm(\'클럭을 바꾸고 재부팅할까요?\')">')
    yield '<label>클럭 선택</label><select name="mhz">'
    for f in freq_options:
        selected = ' selected' if f == current_freq else ''
        label = str(f) + 'MHz' + (' (기본)' if f == 150 else '')
        yield '<option value="' + str(f) + '"' + selected + '>' + label + '</option>'
    yield '</select><button type="submit" class="btn">적용 및 재부팅</button></form></details>'

    yield ('<a href="/power/reboot" class="row" style="border-color:#38bdf8" '
           'onclick="return confirm(\'지금 다시 시작할까요?\')">'
           '<div class="t" style="color:#7dd3fc">🔄 다시 시작</div>'
           '<div class="d">즉시 재부팅합니다. 20~30초 후 다시 접속할 수 있습니다.</div></a>')

    if sleeping:
        yield ('<a href="/power/wake" class="row" style="border-color:#eab308">'
               '<div class="t" style="color:#fde047">☀️ 절전 해제</div>'
               '<div class="d">화면과 센서 측정을 다시 켭니다.</div></a>')
    else:
        yield ('<a href="/power/sleep" class="row" '
               'onclick="return confirm(\'절전 모드로 전환할까요?\')">'
               '<div class="t">🌙 절전 모드</div>'
               '<div class="d">LCD와 센서 측정을 끕니다. 웹서버는 살아있어 여기서 다시 켤 수 있습니다.</div></a>')

    yield ('<a href="/power/halt" class="row" style="border-color:#ef4444" '
           'onclick="return confirm(\'시스템을 종료할까요?\\n\\n웹으로는 다시 켤 수 없고 '
           '전원을 다시 인가해야 합니다.\')">'
           '<div class="t" style="color:#fca5a5">⏻ 시스템 종료</div>'
           '<div class="d">전원을 뽑아도 안전한 상태로 정지시킵니다. '
           '<b style="color:#fca5a5">복구하려면 전원 재인가가 필요합니다.</b></div></a>')
    yield from _end()
