#!/usr/bin/env python3
"""AI 导航本地服务器 - 访问页面时自动后台刷新数据"""

import os
import subprocess
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from datetime import datetime

BASE     = Path(__file__).parent
OUTPUT   = BASE / "output"
SCRIPT   = BASE / "fetch_news.py"
PORT     = 8765
COOLDOWN = 1800  # 两次刷新最短间隔（秒），30 分钟

_state = {
    "running":  False,
    "last_run": 0.0,
    "lock":     threading.Lock(),
    "last_msg": "等待首次访问...",
}

# ── 注入每个 HTML 页面的刷新状态角标 ──
_INJECTED_JS = """
<script>
(function(){
  var b=document.createElement('div');
  b.id='srv-badge';
  b.style.cssText='position:fixed;bottom:1.2rem;right:1.2rem;padding:.45rem 1rem;'
    +'background:#161b22;border:1px solid #30363d;border-radius:8px;'
    +'font-size:.76rem;color:#8b949e;z-index:9999;display:none;'
    +'font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;'
    +'box-shadow:0 4px 16px rgba(0,0,0,.4);transition:opacity .3s';
  document.body.appendChild(b);
  function poll(){
    fetch('/api/status').then(function(r){return r.json();}).then(function(d){
      if(d.running){
        b.innerHTML='<span style="color:#58a6ff">⟳</span> 正在后台刷新数据...';
        b.style.display='block';
      } else if(d.just_finished){
        b.innerHTML='<span style="color:#3fb950">✓</span> 刷新完成，请刷新页面';
        b.style.display='block';
        setTimeout(function(){b.style.display='none';},6000);
      } else {
        b.style.display='none';
      }
    }).catch(function(){});
  }
  poll();
  setInterval(poll, 4000);
})();
</script>
</body>"""


def _do_refresh():
    _state["last_msg"] = "刷新中..."
    try:
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 后台刷新启动")
        subprocess.run(
            ["python3", str(SCRIPT)],
            cwd=str(BASE),
            env=os.environ.copy(),
        )
        _state["last_msg"] = f"上次刷新：{datetime.now().strftime('%H:%M:%S')}"
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 后台刷新完成")
    except Exception as e:
        _state["last_msg"] = f"刷新失败：{e}"
        print(f"  [ERROR] {e}")
    finally:
        _state["running"]      = False
        _state["just_finished"] = True
        # 6 秒后清除"刚完成"标志
        def clear():
            time.sleep(6)
            _state["just_finished"] = False
        threading.Thread(target=clear, daemon=True).start()


def _script_already_running() -> bool:
    """检测系统中是否已有 fetch_news.py 进程在运行（跨服务器重启也有效）。"""
    import subprocess as sp
    result = sp.run(["pgrep", "-f", "fetch_news.py"], capture_output=True)
    return result.returncode == 0


def trigger_refresh(force: bool = False) -> str:
    with _state["lock"]:
        # 系统级检测：防止多进程并发
        if _script_already_running():
            _state["running"] = True   # 同步内存状态
            return "running"
        if _state["running"]:
            return "running"
        if not force and time.time() - _state["last_run"] < COOLDOWN:
            remaining = int(COOLDOWN - (time.time() - _state["last_run"]))
            return f"cooldown:{remaining}"
        _state["running"]       = True
        _state["just_finished"] = False
        _state["last_run"]      = time.time()
    threading.Thread(target=_do_refresh, daemon=True).start()
    return "started"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT), **kwargs)

    # ── API 端点 ──
    def _api_status(self):
        running       = _state["running"]
        just_finished = _state.get("just_finished", False)
        elapsed       = round(time.time() - _state["last_run"])
        body = (
            f'{{"running":{str(running).lower()},'
            f'"just_finished":{str(just_finished).lower()},'
            f'"elapsed":{elapsed},'
            f'"msg":"{_state["last_msg"]}"}}'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _api_refresh(self):
        force  = b"force" in (self.headers.get("X-Force", b""))
        result = trigger_refresh(force=True)
        body   = f'{{"status":"{result}"}}'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            self._api_status()
            return
        if self.path == "/api/refresh":
            self._api_refresh()
            return

        # 忽略 favicon
        if self.path == "/favicon.ico":
            self.send_error(404)
            return

        # 普通页面访问：自动触发刷新（有冷却）
        trigger_refresh()

        # 对 HTML 文件注入状态角标 JS
        if self.path.endswith(".html") or self.path in ("/", ""):
            file_name = "index.html" if self.path in ("/", "") else self.path.lstrip("/")
            file_path = OUTPUT / file_name
            if file_path.exists():
                content = file_path.read_bytes()
                # 注入角标脚本（替换 </body>）
                content = content.replace(b"</body>", _INJECTED_JS.encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
                return

        super().do_GET()

    def log_message(self, fmt, *args):
        try:
            if args and isinstance(args[0], str) and "/api/" not in args[0]:
                print(f"  {datetime.now().strftime('%H:%M:%S')} {args[0]} {args[1]}")
        except Exception:
            pass


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (BASE / "logs").mkdir(exist_ok=True)
    _state["just_finished"] = False

    url = f"http://localhost:{PORT}"
    print(f"🌐 AI 导航中心已启动：{url}")
    print(f"   访问页面时自动后台刷新（冷却 {COOLDOWN // 60} 分钟）")
    print(f"   手动强制刷新：curl {url}/api/refresh")
    print(f"   按 Ctrl+C 停止\n")

    # 延迟 1 秒后打开浏览器
    threading.Timer(1, lambda: webbrowser.open(url)).start()

    HTTPServer(("localhost", PORT), Handler).serve_forever()
