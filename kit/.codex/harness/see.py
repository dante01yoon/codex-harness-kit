#!/usr/bin/env python3
"""Let the agent see what it built: screenshots, console errors and the server log.

Settings live in `see` of harness.json:

  "see": {
    "url": "http://127.0.0.1:5173",       base URL of the running app
    "start": "npm run dev",                started (and stopped again) when the URL is not up
    "pages": [{"name": "home", "path": "/"}],
    "viewports": ["390x844", "1280x900"],
    "out": "artifacts/harness/see",        must be outside .codex/ (the Codex sandbox cannot write there)
    "wait_ms": 2500,                       time given to scripts before the screenshot
    "chrome": "",                          path to Chrome/Chromium; empty means auto-detect
    "log": ""                              the app's own log file, if it writes one (else server.log in out)
  }

For every page and viewport it saves `<name>-<viewport>.png`, collects console messages and
failed loads, and writes `summary.txt` with the last lines of the server log. Exit code 1 when a
page did not answer, a script threw, or a resource failed to load. Uses only the Python standard
library and an installed Chrome or Chromium in headless mode.

Starting a local server needs permission to listen on a port. Inside Codex's default sandbox
that is refused (EPERM), so run the agent with network/full access for UI work, or start the
app yourself and keep it running.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harnesslib as hl  # noqa: E402

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
CONSOLE = re.compile(r':CONSOLE(?::\d+|\(\d+\))?\]\s*"(.*)", source: (\S*) \((\d+)\)')
PROBLEM = re.compile(r"Uncaught|Failed to load resource|Error:|TypeError|ReferenceError|SyntaxError|Unhandled", re.I)
VIEWPORT = re.compile(r"^(\d{2,5})x(\d{2,5})$")

TEXT = {
    "ko": {"head": "화면 확인", "page": "페이지", "shot": "스크린샷", "console": "콘솔", "none": "없음",
           "server": "서버 로그 마지막 {n}줄", "pass": "see PASS: 화면 {n}개, 문제 0건. 스크린샷을 직접 열어 보고 판단하세요.",
           "fail": "see FAIL: 문제 {n}건. 위 오류와 스크린샷을 보고 구현을 고친 뒤 다시 실행하세요.",
           "nochrome": "Chrome이나 Chromium을 찾지 못했습니다. harness.json의 see.chrome에 경로를 적으세요.",
           "down": "{url}에 연결할 수 없습니다. see.start를 설정하거나 앱을 먼저 띄우세요.",
           "eperm": "로컬 서버를 띄울 권한이 없습니다(샌드박스). 사람에게 앱을 띄워 달라고 하거나 네트워크 권한을 요청하세요.",
           "eperm_hook": "코덱스 샌드박스 안이라 앱을 띄울 수 없습니다. 정상입니다. 이 화면 확인은 응답을 마치기 직전 Stop 훅이 샌드박스 밖에서 실행하고, 실패하면 결과를 돌려줍니다. BLOCKED로 보고하지 말고 작업을 마치세요. 지난 결과는 {out}/summary.txt와 스크린샷에 있습니다."},
    "en": {"head": "Screen check", "page": "page", "shot": "screenshot", "console": "console", "none": "none",
           "server": "last {n} lines of the server log", "pass": "see PASS: {n} screens, 0 problems. Open the screenshots and judge them yourself.",
           "fail": "see FAIL: {n} problems. Fix the implementation using the errors and screenshots above, then run it again.",
           "nochrome": "Could not find Chrome or Chromium. Put its path in see.chrome in harness.json.",
           "down": "Cannot reach {url}. Set see.start or start the app first.",
           "eperm": "No permission to start a local server (sandbox). Ask a human to start the app or to grant network access.",
           "eperm_hook": "The app cannot be started inside the Codex sandbox. That is expected: the Stop hook runs this screen check outside the sandbox right before you finish and sends back any failure. Do not report it as BLOCKED; finish your work. The last results are in {out}/summary.txt and the screenshots."},
}


def find_chrome(cfg: Dict[str, Any]) -> Optional[str]:
    explicit = cfg.get("see", {}).get("chrome") or os.environ.get("HARNESS_CHROME")
    for cand in ([explicit] if explicit else []) + CHROME_CANDIDATES:
        if os.path.isabs(cand) and os.path.exists(cand):
            return cand
        found = shutil.which(cand)
        if found:
            return found
    return None


def is_up(url: str, timeout: float = 2.0) -> Optional[int]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


def start_server(root: str, cmd: str, log_path: str) -> subprocess.Popen:
    log = open(log_path, "a", encoding="utf-8")
    kw: Dict[str, Any] = {"cwd": root, "stdout": log, "stderr": subprocess.STDOUT, "shell": True}
    if os.name == "posix":
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw)


def stop_server(proc: Optional[subprocess.Popen]) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def tail(path: str, n: int) -> List[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


class WebSocket:
    """The smallest WebSocket client that can talk to Chrome's DevTools endpoint (RFC 6455, text frames)."""

    def __init__(self, url: str, timeout: float) -> None:
        m = re.match(r"ws://([^/:]+):(\d+)(/.*)", url)
        if not m:
            raise OSError(f"bad DevTools URL {url}")
        self.sock = socket.create_connection((m.group(1), int(m.group(2))), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {m.group(3)} HTTP/1.1\r\nHost: {m.group(1)}:{m.group(2)}\r\n"
                           f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("DevTools closed the connection during the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError("DevTools refused the WebSocket: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise OSError("DevTools connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode())

    def recv(self) -> str:
        message = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else b""
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = b1 & 0x0F
            if opcode == 0x8:
                raise OSError("DevTools closed the connection")
            if opcode == 0x9:
                self._frame(0xA, data)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += data
                if b1 & 0x80:
                    return message.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class DevTools:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.next_id = 0
        self.events: List[Dict[str, Any]] = []

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.next_id += 1
        my_id = self.next_id
        self.ws.send(json.dumps({"id": my_id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my_id:
                if "error" in msg:
                    raise OSError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def pump(self, seconds: float, until: str = "") -> bool:
        end = time.time() + seconds
        while time.time() < end:
            self.ws.sock.settimeout(max(0.05, end - time.time()))
            try:
                msg = json.loads(self.ws.recv())
            except socket.timeout:
                break
            if "method" in msg:
                self.events.append(msg)
                if until and msg["method"] == until:
                    return True
        return False


def _remote_text(arg: Dict[str, Any]) -> str:
    if "value" in arg:
        return arg["value"] if isinstance(arg["value"], str) else json.dumps(arg["value"], ensure_ascii=False)
    return arg.get("description") or arg.get("unserializableValue") or arg.get("type", "")


def console_lines(events: List[Dict[str, Any]]) -> List[str]:
    """Turn DevTools events into the same `text  (source:line)` lines the CLI log used to give."""
    lines = []
    for e in events:
        p = e.get("params", {})
        if e["method"] == "Runtime.consoleAPICalled":
            text = " ".join(_remote_text(a) for a in p.get("args", []))
            frame = (p.get("stackTrace") or {}).get("callFrames") or [{}]
            if p.get("type") == "error" and not PROBLEM.search(text):
                text = "console.error: " + text
            lines.append(f"{text}  ({frame[0].get('url', '')}:{frame[0].get('lineNumber', -1) + 1})")
        elif e["method"] == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            text = ((d.get("exception") or {}).get("description") or d.get("text") or "").split("\n")[0]
            if not text.startswith("Uncaught"):
                text = "Uncaught " + text
            lines.append(f"{text}  ({d.get('url', '')}:{d.get('lineNumber', -1) + 1})")
        elif e["method"] == "Log.entryAdded":
            entry = p.get("entry", {})
            if entry.get("level") in ("error", "warning") and not entry.get("url", "").endswith("/favicon.ico"):
                lines.append(f"{entry.get('text', '')}  ({entry.get('url', '')}:{entry.get('lineNumber', -1) + 1})")
    return lines


def chrome_args(prof: str) -> List[str]:
    return ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--use-mock-keychain", "--password-store=basic", "--disable-extensions",
            "--disable-background-networking", "--disable-component-update", "--disable-sync",
            "--no-service-autorun", "--mute-audio", f"--user-data-dir={prof}"]


def shoot_devtools(chrome: str, url: str, w: int, h: int, png: str, wait_ms: int) -> Tuple[bool, List[str]]:
    """Open the page through the DevTools protocol so the viewport is exactly w x h.

    Headless Chrome will not make a window narrower than about 500px, so `--window-size=390,...`
    lays the page out at 500px and only crops the screenshot. Device metrics emulation fixes that.
    """
    prof = tempfile.mkdtemp(prefix="harness-see-")
    kw: Dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kw["start_new_session"] = True
    proc = subprocess.Popen([chrome, *chrome_args(prof), "--remote-debugging-port=0",
                             f"--window-size={max(w, 800)},{max(h, 600)}", "about:blank"], **kw)
    ws = None
    try:
        port_file = os.path.join(prof, "DevToolsActivePort")
        for _ in range(200):
            if os.path.exists(port_file) and os.path.getsize(port_file) > 0:
                break
            if proc.poll() is not None:
                raise OSError("Chrome exited before DevTools was ready")
            time.sleep(0.1)
        else:
            raise OSError("Chrome did not open DevTools in time")
        with open(port_file) as f:
            port = int(f.readline().strip())
        targets = []
        for _ in range(50):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                targets = [t for t in json.load(r) if t.get("type") == "page"]
            if targets:
                break
            time.sleep(0.1)
        if not targets:
            raise OSError("Chrome opened no page")
        ws = WebSocket(targets[0]["webSocketDebuggerUrl"], timeout=30)
        dt = DevTools(ws)
        for method in ("Runtime.enable", "Log.enable", "Page.enable"):
            dt.call(method)
        dt.call("Emulation.setDeviceMetricsOverride",
                {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": w < 768})
        if w < 768:
            dt.call("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        dt.call("Page.navigate", {"url": url})
        dt.pump(30, until="Page.loadEventFired")
        dt.pump(wait_ms / 1000.0)
        ws.sock.settimeout(60)
        shot = dt.call("Page.captureScreenshot", {"format": "png"})
        with open(png, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        return True, console_lines(dt.events)
    finally:
        if ws:
            ws.close()
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        shutil.rmtree(prof, ignore_errors=True)


def shoot(chrome: str, url: str, w: int, h: int, png: str, wait_ms: int) -> Tuple[bool, List[str]]:
    try:
        return shoot_devtools(chrome, url, w, h, png, wait_ms)
    except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
        ok, lines = shoot_cli(chrome, url, w, h, png, wait_ms)
        note = f"DevTools unavailable ({e}); used the plain screenshot"
        if w < 500:
            note += f", which lays the page out at 500px and crops it to {w}px"
        return ok, [note] + lines


def shoot_cli(chrome: str, url: str, w: int, h: int, png: str, wait_ms: int) -> Tuple[bool, List[str]]:
    prof = tempfile.mkdtemp(prefix="harness-see-")
    cmd = [chrome, *chrome_args(prof), f"--window-size={w},{h}",
           f"--virtual-time-budget={wait_ms}", f"--screenshot={png}", "--enable-logging=stderr", "--v=0", url]
    timed_out = False
    kw: Dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True, "errors": "replace"}
    if os.name == "posix":
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    try:
        _, err = proc.communicate(timeout=60 + wait_ms // 1000)
    except subprocess.TimeoutExpired:
        # Headless Chrome sometimes keeps running after it wrote the screenshot (slow CI machines).
        # Kill it and keep everything it already logged, so console errors are never lost.
        timed_out = True
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        _, err = proc.communicate()
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    lines = []
    for m in CONSOLE.finditer(err or ""):
        lines.append(f"{m.group(1)}  ({m.group(2)}:{m.group(3)})")
    shot = os.path.exists(png)
    if timed_out and not shot:
        # Console messages are logged while the page loads, before the screenshot. Without a
        # screenshot the page may not have finished, so this is a problem, not a pass.
        lines.append("browser did not finish before the timeout; no screenshot (Error: browser timeout)")
    return shot, lines


def main(argv: Optional[List[str]] = None) -> int:
    root = hl.project_root()
    cfg = hl.load_config()
    t = TEXT.get(cfg.get("lang", "ko"), TEXT["ko"])
    see = cfg.get("see", {})
    url = (see.get("url") or "").rstrip("/")
    out = os.path.join(root, see.get("out") or "artifacts/harness/see")
    os.makedirs(out, exist_ok=True)
    log_path = os.path.join(root, see["log"]) if see.get("log") else os.path.join(out, "server.log")
    chrome = find_chrome(cfg)
    if not chrome:
        print(t["nochrome"])
        return 2

    proc = None
    if is_up(url) is None:
        if not see.get("start"):
            print(t["down"].format(url=url))
            return 2
        open(log_path, "w").close()
        proc = start_server(root, see["start"], log_path)
        for _ in range(int(see.get("start_timeout", 60) * 2)):
            if is_up(url) is not None or proc.poll() is not None:
                break
            time.sleep(0.5)
        if is_up(url) is None:
            text = "\n".join(tail(log_path, 40))
            stop_server(proc)
            if "EPERM" in text or "EACCES" in text:
                in_hook = any("harness.py see" in c["run"] for c in cfg["verify"]["commands"])
                print(t["eperm_hook"].format(out=see.get("out") or "artifacts/harness/see") if in_hook else t["eperm"])
                stop_server(proc)
                return 3
            print(t["down"].format(url=url))
            print(text)
            return 2

    screens = 0
    problems = 0
    report: List[str] = [f"{t['head']} {url}"]
    try:
        for page in see.get("pages") or [{"name": "home", "path": "/"}]:
            target = url + "/" + page.get("path", "/").lstrip("/")
            status = is_up(target)
            for vp in see.get("viewports") or ["390x844", "1280x900"]:
                w, h = (int(x) for x in VIEWPORT.match(vp).groups())
                png = os.path.join(out, f"{page['name']}-{vp}.png")
                ok, console = shoot(chrome, target, w, h, png, int(see.get("wait_ms", 2500)))
                bad = [c for c in console if PROBLEM.search(c)]
                if status is None or status >= 400 or not ok:
                    bad.insert(0, f"HTTP {status}" if status else "no response")
                problems += len(bad)
                screens += 1
                report.append(f"[{'FAIL' if bad else 'PASS'}] {t['page']} {page['name']} {vp}  {t['shot']}: {os.path.relpath(png, root)}")
                for c in console:
                    report.append(f"    {t['console']}: {c}")
                for b in bad:
                    if not any(b in c for c in console):
                        report.append(f"    ! {b}")
    finally:
        stop_server(proc)

    n = int(see.get("log_tail", 30))
    report.append(f"--- {t['server'].format(n=n)} ---")
    report += tail(log_path, n) or [t["none"]]
    report.append(t["fail"].format(n=problems) if problems else t["pass"].format(n=screens))
    text = "\n".join(report)
    with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except hl.ConfigError as e:
        print("config error:", e, file=sys.stderr)
        sys.exit(2)
