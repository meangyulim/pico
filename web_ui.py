# =================================================================
# web_ui.py : 웹 대시보드/파일 브라우저/에디터/로그/앱 전환 화면 HTML 생성
# =================================================================
# main.py는 이 함수들을 호출해서 응답 본문을 만들기만 하고, 실제 소켓
# 전송/라우팅은 main.py의 handle_client()가 담당합니다.


def generate_main_html(mode, current_ip, wifi_list, app_err, value, volt_val, status_eng, status_kor, color_hex, cloud_msg, is_muted_val, thresh_val, ota_status, active_app):
    is_offline = (mode == "OFFLINE_AP")
    mode_badge_text = "📡 오프라인 단독 AP 모드" if is_offline else "🌐 온라인 모드"
    mode_badge_color = "#38bdf8" if is_offline else "#10b981"

    wifi_options = ""
    for w in wifi_list:
        wifi_options += f'<option value="{w}">{w}</option>'

    error_banner = ""
    if app_err:
        error_banner = f"""<div style="background:#ef444422; border:1px solid #ef4444; border-radius:16px; padding:14px; margin-bottom:16px; color:#fca5a5; font-size:13px; text-align:left; line-height:1.5;">
            <b>⚠️ 활성 앱 실행 오류 ({active_app})</b><br>
            <code style="color:#fff; word-break:break-all; display:block; margin:6px 0; background:#0f172a; padding:6px 8px; border-radius:6px;">{app_err}</code>
            👉 아래 <b>[📝 웹 에디터]</b>로 코드를 고치거나 <b>[🔌 앱 전환]</b>에서 다른 앱을 선택하세요.
        </div>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pico 대시보드</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; text-align: center; padding: 20px 16px; -webkit-text-size-adjust: 100%; }}
        .card {{ background: #1e293b; border-radius: 24px; padding: 28px 20px; max-width: 380px; margin: 0 auto 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }}
        .mode-badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px; background: {mode_badge_color}; color: #000; font-size: 12px; font-weight: bold; margin-bottom: 12px; }}
        .status-badge {{ display: inline-block; padding: 8px 22px; border-radius: 50px; background: {color_hex}; color: #000; font-weight: 800; font-size: 15px; margin-bottom: 12px; transition: background 0.3s; }}
        .value {{ font-size: 52px; font-weight: 800; margin: 8px 0; color: #fff; }}
        .sub-info {{ font-size: 13px; color: #94a3b8; margin-top: 16px; border-top: 1px solid #334155; padding-top: 14px; line-height: 1.8; text-align: left; }}
        .sub-info b {{ color: #f1f5f9; }}
        .live-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #22c55e; margin-right: 6px; animation: pulse 1.5s infinite; }}
        @keyframes pulse {{ 0% {{ opacity: 1; transform: scale(1); }} 50% {{ opacity: 0.3; transform: scale(0.8); }} 100% {{ opacity: 1; transform: scale(1); }} }}

        details {{ background: #1e293b; border-radius: 16px; padding: 14px 18px; max-width: 380px; margin: 0 auto; border: 1px solid #334155; text-align: left; }}
        summary {{ font-size: 14px; font-weight: bold; color: #38bdf8; cursor: pointer; list-style: none; display: flex; justify-content: space-between; align-items: center; }}
        summary::-webkit-details-marker {{ display: none; }}
        summary::after {{ content: '⚙️'; font-size: 14px; }}
        .wifi-form {{ margin-top: 14px; border-top: 1px solid #334155; padding-top: 12px; }}
        label {{ display: block; font-size: 12px; font-weight: 600; margin-bottom: 4px; color: #cbd5e1; }}
        select, input[type="text"], input[type="password"] {{ width: 100%; padding: 10px 12px; margin-bottom: 12px; border-radius: 8px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 16px; }}
        select:focus, input:focus {{ outline: none; border-color: #38bdf8; }}
        .btn {{ width: 100%; padding: 12px; background: #0284c7; color: #fff; border: none; border-radius: 10px; font-size: 14px; font-weight: bold; cursor: pointer; }}
    </style>
</head>
<body>
    <div class="card">
        {error_banner}
        <div class="mode-badge">{mode_badge_text}</div><br>
        <div class="status-badge" id="statusBadge">{status_kor} ({status_eng})</div>
        <div class="value" id="dustVal">{value:.0f}</div>
        <div class="sub-info">
            • <span class="live-dot"></span>실시간 로컬 연결: <b>정상</b><br>
            • 🔌 활성 앱: <b>{active_app}</b><br>
            • ☁️ 클라우드 동기화: <b id="cloudVal">{cloud_msg}</b><br>
            • 🔔 알림 제어 상태: <b id="controlVal">Mute: {is_muted_val} / 기준: {thresh_val:.0f}</b><br>
            • 🛰️ OTA 마지막 확인: <b id="otaVal">{ota_status}</b><br>
            • 기기 IP 주소: <b>{current_ip}</b>
        </div>
    </div>

    <details>
        <summary>📶 Wi-Fi 공유기 연결 설정</summary>
        <form action="/save" method="GET" class="wifi-form">
            <label>주변 Wi-Fi 선택</label>
            <select name="ssid_select" onchange="document.getElementById('ssid_in').value = this.value;">
                <option value="">-- 검색된 Wi-Fi 목록 --</option>
                {wifi_options}
            </select>
            <label>Wi-Fi 이름 (SSID)</label>
            <input type="text" name="ssid" id="ssid_in" placeholder="Wi-Fi 이름 직접 입력 가능" required>
            <label>Wi-Fi 비밀번호</label>
            <input type="password" name="password" placeholder="비밀번호 (공개 Wi-Fi는 빈칸)">
            <button type="submit" class="btn">저장 및 공유기 연결</button>
        </form>
    </details>

    <div style="margin-top: 14px; max-width: 380px; margin-left: auto; margin-right: auto; display: flex; flex-direction: column; gap: 10px;">
        <a href="/apps" style="display: block; text-decoration: none; padding: 12px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; color: #38bdf8; font-size: 14px; font-weight: bold; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">🔌 앱 전환</a>
        <a href="/edit" style="display: block; text-decoration: none; padding: 12px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; color: #38bdf8; font-size: 14px; font-weight: bold; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">📝 웹 에디터 열기</a>
        <a href="/logs" style="display: block; text-decoration: none; padding: 12px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; color: #38bdf8; font-size: 14px; font-weight: bold; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">📜 실시간 로그 보기</a>
    </div>

    <script>
        async function updateData() {{
            try {{
                const res = await fetch('/data?t=' + Date.now());
                if(res.ok) {{
                    const d = await res.json();
                    document.getElementById('dustVal').innerText = d.value.toFixed(0);
                    document.getElementById('cloudVal').innerText = d.cloud;
                    document.getElementById('controlVal').innerText = 'Mute: ' + d.mute + ' / 기준: ' + d.thresh;
                    document.getElementById('otaVal').innerText = d.ota;
                    const badge = document.getElementById('statusBadge');
                    badge.innerText = d.kor + ' (' + d.eng + ')';
                    badge.style.background = d.color;
                }}
            }} catch(e) {{}}
        }}
        setInterval(updateData, 2000);
        updateData();
    </script>
</body>
</html>"""
    return html


def generate_logs_html():
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pico 원격 콘솔</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 12px; -webkit-text-size-adjust: 100%; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        h3 { font-size: 15px; color: #38bdf8; }
        .back-btn { color: #94a3b8; text-decoration: none; font-size: 12px; padding: 6px 10px; background: #1e293b; border-radius: 6px; border: 1px solid #334155; }
        .note { font-size: 11px; color: #94a3b8; margin-bottom: 8px; }
        #logBox { width: 100%; height: 75vh; background: #000; color: #4ade80; font-family: Consolas, "Courier New", monospace; font-size: 12px; line-height: 1.5; border: 1px solid #334155; border-radius: 10px; padding: 10px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; }
    </style>
</head>
<body>
    <div class="header">
        <h3>📜 원격 콘솔 (최근 200줄)</h3>
        <a href="/" class="back-btn">⬅ 메인으로</a>
    </div>
    <div class="note">Wi-Fi로만 연결돼 있어도 Thonny 시리얼 콘솔과 비슷하게 print() 출력을 볼 수 있습니다. 2초마다 자동 갱신됩니다.</div>
    <div id="logBox">불러오는 중...</div>

    <script>
        const box = document.getElementById('logBox');
        async function refreshLogs() {
            try {
                const res = await fetch('/logs.txt?t=' + Date.now());
                if (res.ok) {
                    const text = await res.text();
                    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
                    box.textContent = text;
                    if (atBottom) box.scrollTop = box.scrollHeight;
                }
            } catch (e) {}
        }
        setInterval(refreshLogs, 2000);
        refreshLogs();
    </script>
</body>
</html>"""
    return html


def generate_file_list_html(files):
    rows = ""
    for name in files:
        rows += f'<a href="/edit?file={name}" class="file-row">📄 {name}</a>'
    if not rows:
        rows = '<p style="color:#94a3b8;font-size:13px;">편집 가능한 파일이 없습니다.</p>'

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pico 파일 브라우저</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 16px; -webkit-text-size-adjust: 100%; }}
        h3 {{ font-size: 16px; color: #38bdf8; margin-bottom: 4px; }}
        .back-btn {{ color: #94a3b8; text-decoration: none; font-size: 12px; padding: 6px 10px; background: #1e293b; border-radius: 6px; border: 1px solid #334155; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }}
        .note {{ font-size: 12px; color: #94a3b8; margin-bottom: 14px; line-height: 1.5; }}
        .file-list {{ display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px; }}
        .file-row {{ display: block; padding: 12px 14px; background: #1e293b; border: 1px solid #334155; border-radius: 10px; color: #f1f5f9; text-decoration: none; font-size: 14px; }}
        .file-row:active {{ background: #334155; }}
        .new-file {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 14px; }}
        label {{ display: block; font-size: 12px; font-weight: 600; margin-bottom: 6px; color: #cbd5e1; }}
        input[type="text"] {{ width: 100%; padding: 10px 12px; margin-bottom: 12px; border-radius: 8px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 16px; }}
        .btn {{ width: 100%; padding: 12px; background: #0284c7; color: #fff; border: none; border-radius: 10px; font-size: 14px; font-weight: bold; cursor: pointer; }}
    </style>
</head>
<body>
    <div class="header">
        <h3>📁 파일 브라우저</h3>
        <a href="/" class="back-btn">⬅ 메인으로</a>
    </div>
    <div class="note">boot.py는 부팅 안전망이라 목록에서 제외됩니다. main.py를 포함한 다른 모든 .py 파일을 수정할 수 있으며, 저장할 때마다 이전 버전이 자동 백업됩니다.</div>

    <div class="file-list">
        {rows}
    </div>

    <div class="new-file">
        <form action="/edit" method="GET">
            <label>새 파일 이름 (.py)</label>
            <input type="text" name="file" placeholder="예: sensor2.py" required>
            <button type="submit" class="btn">파일 만들기 / 열기</button>
        </form>
    </div>
</body>
</html>"""
    return html


def generate_editor_html_head(target_file, has_backup):
    """
    에디터 페이지를 <textarea> 여는 태그까지만 만듭니다. 코드 본문은
    handle_client가 따로 조각내어 이스케이프하며 스트리밍합니다 — main.py처럼
    큰 파일(수십 KB)을 한 번에 문자열로 합치면 메모리 부담이 크고, 첫 바이트가
    나가기까지 오래 걸려 느린 Wi-Fi에서 타임아웃에 걸리기 쉽기 때문입니다.
    """
    if target_file == "main.py":
        safe_note = "🛡️ 이 파일이 깨지면 boot.py가 자동으로 이전 버전으로 복구합니다"
    else:
        safe_note = "🛡️ 실수해도 시스템은 안 죽습니다 (main.py가 오류를 격리합니다)"
    revert_btn = f'<a href="/revert?file={target_file}" class="tool-btn" style="text-decoration:none;display:block;" onclick="return confirm(\'{target_file}을(를) 이전 저장본으로 되돌리고 재부팅할까요?\');">↩️ 이전 버전</a>' if has_backup else ""

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pico {target_file} 웹 에디터</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 12px; -webkit-text-size-adjust: 100%; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
        h3 {{ font-size: 15px; color: #38bdf8; }}
        .safe-tag {{ display: inline-block; font-size: 11px; background: #065f46; color: #34d399; padding: 3px 8px; border-radius: 6px; font-weight: bold; margin-bottom: 8px; }}
        .back-btn {{ color: #94a3b8; text-decoration: none; font-size: 12px; padding: 6px 10px; background: #1e293b; border-radius: 6px; border: 1px solid #334155; }}

        .toolbar {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px; margin-bottom: 10px; }}
        .tool-btn {{ padding: 10px 4px; background: #1e293b; color: #cbd5e1; border: 1px solid #334155; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; text-align: center; }}
        .tool-btn:active {{ background: #334155; }}

        textarea {{ width: 100%; height: 65vh; background: #1e293b; color: #f1f5f9; font-family: Consolas, "Courier New", monospace; font-size: 16px; line-height: 1.4; border: 1px solid #334155; border-radius: 10px; padding: 12px; outline: none; resize: none; white-space: pre; tab-size: 4; -webkit-overflow-scrolling: touch; touch-action: pan-x pan-y; }}
        textarea:focus {{ border-color: #38bdf8; }}

        .btn-save {{ width: 100%; padding: 14px; margin-top: 10px; background: #0284c7; color: #fff; border: none; border-radius: 10px; font-size: 15px; font-weight: bold; cursor: pointer; }}
        .btn-save:active {{ background: #0369a1; }}

        #toast {{ display: none; position: fixed; top: 20px; left: 50%; transform: translateX(-50%); background: #22c55e; color: #000; padding: 10px 20px; border-radius: 25px; font-weight: bold; font-size: 13px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); z-index: 999; }}
        .note {{ font-size: 11px; color: #94a3b8; margin-top: 8px; text-align: center; line-height: 1.4; }}
    </style>
</head>
<body>
    <div id="toast"></div>
    <div class="header">
        <h3>📝 {target_file} 편집</h3>
        <a href="/edit" class="back-btn">⬅ 파일 목록</a>
    </div>
    <div class="safe-tag">{safe_note}</div>

    <div class="toolbar">
        <button type="button" class="tool-btn" onclick="copyAllCode()">📋 전체 복사</button>
        <button type="button" class="tool-btn" onclick="pasteFromClipboard()">📄 붙여넣기</button>
        <button type="button" class="tool-btn" style="color:#ef4444;" onclick="clearAllCode()">🗑️ 전체 지우기</button>
        {revert_btn}
    </div>

    <form action="/save_code?file={target_file}" method="POST" id="codeForm">
        <textarea name="code" id="codeArea" spellcheck="false" required>"""
    return html


def generate_editor_html_tail(target_file):
    """generate_editor_html_head()로 시작한 페이지를 </textarea>부터 마무리합니다."""
    html = f"""</textarea>
        <button type="submit" class="btn-save" onclick="return confirm('{target_file}을(를) 저장하고 피코를 재부팅하시겠습니까?');">💾 저장 및 피코 재부팅</button>
    </form>

    <div class="note">
        ※ 16px 폰트 고정으로 아이폰 자동 확대가 방지됩니다.<br>
        ※ 저장 시 {target_file} 파일로 덮어쓰고(이전 내용은 자동 백업), 내용이 바뀐 경우에만 피코가 자동 재부팅됩니다.
    </div>

    <script>
        function showToast(msg, isErr) {{
            const t = document.getElementById('toast');
            t.innerText = msg;
            t.style.background = isErr ? '#ef4444' : '#22c55e';
            t.style.color = isErr ? '#fff' : '#000';
            t.style.display = 'block';
            setTimeout(() => {{ t.style.display = 'none'; }}, 2200);
        }}

        function copyAllCode() {{
            const ta = document.getElementById('codeArea');
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(ta.value)
                    .then(() => showToast('✅ 전체 코드가 복사되었습니다!'))
                    .catch(() => fallbackCopy(ta));
            }} else {{
                fallbackCopy(ta);
            }}
        }}

        function fallbackCopy(ta) {{
            ta.focus();
            ta.select();
            ta.setSelectionRange(0, 99999);
            try {{
                document.execCommand('copy');
                showToast('✅ 전체 코드가 복사되었습니다!');
            }} catch(e) {{
                showToast('❌ 복사 실패: 직접 길게 눌러 복사해주세요.', true);
            }}
        }}

        async function pasteFromClipboard() {{
            try {{
                if (navigator.clipboard && navigator.clipboard.readText) {{
                    const text = await navigator.clipboard.readText();
                    if (text) {{
                        document.getElementById('codeArea').value = text;
                        showToast('✅ 클립보드 내용을 붙여넣었습니다!');
                        return;
                    }}
                }}
            }} catch(e) {{}}
            const ta = document.getElementById('codeArea');
            ta.focus();
            showToast('💡 입력창을 길게 눌러 [붙여넣기]를 해주세요.');
        }}

        function clearAllCode() {{
            if (confirm('에디터 내용을 모두 지우시겠습니까?\\n(다른 앱에서 수정한 코드를 붙여넣기 편리합니다)')) {{
                const ta = document.getElementById('codeArea');
                ta.value = '';
                ta.focus();
                showToast('🗑️ 내용이 모두 지워졌습니다.');
            }}
        }}
    </script>
</body>
</html>"""
    return html


def generate_app_list_html(apps, active_name):
    rows = ""
    for name in apps:
        is_active = (name == active_name)
        if is_active:
            rows += f'<div class="app-row active">✅ {name} <span class="tag">현재 사용 중</span></div>'
        else:
            rows += (
                f'<a href="/apps/set?name={name}" class="app-row" '
                f'onclick="return confirm(\'{name}(으)로 전환하고 재부팅할까요?\');">🔌 {name}</a>'
            )
    if not rows:
        rows = '<p style="color:#94a3b8;font-size:13px;">app_*.py 형식의 앱 파일이 없습니다.</p>'

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pico 앱 전환</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 16px; -webkit-text-size-adjust: 100%; }}
        h3 {{ font-size: 16px; color: #38bdf8; margin-bottom: 4px; }}
        .back-btn {{ color: #94a3b8; text-decoration: none; font-size: 12px; padding: 6px 10px; background: #1e293b; border-radius: 6px; border: 1px solid #334155; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }}
        .note {{ font-size: 12px; color: #94a3b8; margin-bottom: 14px; line-height: 1.5; }}
        .app-list {{ display: flex; flex-direction: column; gap: 8px; }}
        .app-row {{ display: block; padding: 12px 14px; background: #1e293b; border: 1px solid #334155; border-radius: 10px; color: #f1f5f9; text-decoration: none; font-size: 14px; }}
        .app-row:active {{ background: #334155; }}
        .app-row.active {{ border-color: #22c55e; color: #86efac; }}
        .tag {{ float: right; font-size: 11px; background: #065f46; color: #34d399; padding: 3px 8px; border-radius: 6px; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="header">
        <h3>🔌 앱 전환</h3>
        <a href="/" class="back-btn">⬅ 메인으로</a>
    </div>
    <div class="note">연결된 센서/모듈에 맞는 앱을 골라 활성화하세요. 전환하면 즉시 재부팅됩니다. 새 앱은 파일 브라우저(/edit)에서 app_로 시작하는 이름의 .py 파일을 만들면 여기 자동으로 나타납니다.</div>

    <div class="app-list">
        {rows}
    </div>
</body>
</html>"""
    return html
