"""Double-click FIN Desktop starter, using the bundled official Python runtime.

This file never downloads Python or changes Windows security settings. An
optional, separately signed Microsoft WebView2 bootstrapper is prompted only
when its runtime is missing. Local market execution remains research-only.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
FINLAB = BASE / "finlab_v2"
RUNTIME = BASE / "Runtime"
WEBVIEW_BOOTSTRAP = BASE / "MicrosoftEdgeWebview2Setup.exe"
APPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "TRIAID_FIN_Local"

MB_YESNO = 0x00000004
MB_ICONQUESTION = 0x00000020
MB_ICONERROR = 0x00000010
IDYES = 6

def message(body: str, flags: int = 0) -> int:
    return ctypes.windll.user32.MessageBoxW(None, body, "TRIAID FIN Desktop", flags)

def installed_webview2() -> bool:
    if os.name != "nt":
        return False
    import winreg
    key = r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as reg:
                    value, _ = winreg.QueryValueEx(reg, "pv")
                    if value and value != "0.0.0.0":
                        return True
            except OSError:
                pass
    return False

def ensure_webview2() -> None:
    if installed_webview2():
        return
    if not WEBVIEW_BOOTSTRAP.is_file():
        raise RuntimeError(
            "未找到微软 WebView2 安装组件。请从微软官网下载并安装 WebView2 Runtime。"
        )
    answer = message(
        "电脑未检测到 Microsoft Edge WebView2。\n"
        "是否使用压缩包内微软签名的官方安装组件安装？\n"
        "如需下载运行环境，需要联网。\n\n"
        "请选择“是”安装，或“否”取消。",
        MB_YESNO | MB_ICONQUESTION,
    )
    if answer != IDYES:
        raise RuntimeError("未安装 WebView2，已取消启动。")
    result = subprocess.run(
        [str(WEBVIEW_BOOTSTRAP), "/silent", "/install"],
        timeout=240, check=False,
    )
    if result.returncode != 0 or not installed_webview2():
        raise RuntimeError(
            "WebView2 安装未完成。请从微软官网下载正式版本后重新启动。"
        )

def ensure_portable_layout() -> None:
    expected = (
        RUNTIME / "pythonw.exe",
        RUNTIME / "python312.dll",
        RUNTIME / "python312.zip",
        FINLAB / "app.py",
        FINLAB / "local_desktop" / "client.py",
        FINLAB / "local_desktop" / "server.py",
    )
    missing = [str(p.relative_to(BASE)) for p in expected if not p.is_file()]
    if missing:
        raise RuntimeError("压缩包不完整，请重新解压：\n" + "\n".join(missing))
    if not str(Path(sys.executable).resolve()).lower().startswith(str(RUNTIME.resolve()).lower()):
        raise RuntimeError("请双击“启动 FIN Desktop.cmd”，使用内置 Python 启动。")

def start_supervisor() -> None:
    from local_desktop.client import is_our_server
    if is_our_server():
        return
    from local_desktop.config import log_dir
    logfile = log_dir() / "portable-supervisor.log"
    with logfile.open("ab") as stream:
        kwargs = {
            "cwd": str(FINLAB), "stdin": subprocess.DEVNULL,
            "stdout": stream, "stderr": subprocess.STDOUT,
            "close_fds": True,
            "creationflags": subprocess.CREATE_NO_WINDOW,
        }
        subprocess.Popen(
            [str(RUNTIME / "pythonw.exe"), "-m", "local_desktop.supervisor"],
            **kwargs,
        )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if is_our_server():
            return
        time.sleep(0.4)
    raise RuntimeError(
        "后台启动失败，请查看日志：\n" +
        str(logfile) + "\n以及 server.log。"
    )

def maybe_enable_startup() -> None:
    APPDATA.mkdir(parents=True, exist_ok=True)
    configured = APPDATA / ".portable-autostart-choice"
    if configured.exists():
        return
    answer = message(
        "是否设置 Windows 登录后自动运行研究后台？\n"
        "电脑持续开机并登录时，关闭桌面窗口后实验仍可继续。\n"
        "不会开放公网，也不会自动交易。\n\n"
        "选择“否”时，今后仍可以双击启动文件。",
        MB_YESNO | MB_ICONQUESTION,
    )
    if answer == IDYES:
        userprofile = os.environ.get("APPDATA")
        if not userprofile:
            raise RuntimeError("无法找到 Windows 当前用户的启动目录。")
        startup = (
            Path(userprofile) / "Microsoft" / "Windows" /
            "Start Menu" / "Programs" / "Startup"
        )
        startup.mkdir(parents=True, exist_ok=True)
        target = startup / "TRIAID FIN Background.cmd"
        # cmd special characters in extracted paths are not safe to interpolate.
        paths = (str(RUNTIME / "pythonw.exe"), str(FINLAB))
        if any(ch in p for p in paths for ch in '&|<>!^%"'):
            message(
                "当前解压路径含特殊字符，未配置自动启动；"
                "可以继续双击启动。请解压到简单英文路径后再设置。"
            )
        else:
            target.write_text(
                "@echo off\r\n"
                'cd /d "' + paths[1] + '"\r\n'
                'start "" "' + paths[0] +
                '" -m local_desktop.supervisor\r\n',
                encoding="ascii",
            )
    configured.write_text("yes" if answer == IDYES else "no", encoding="ascii")

def self_test() -> int:
    ensure_portable_layout()
    import fastapi
    import webview
    import tzdata
    from zoneinfo import ZoneInfo
    assert ZoneInfo("America/New_York")
    from local_desktop.config import HOST
    assert HOST == "127.0.0.1"
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(
            json.dumps({"python": sys.version, "root": str(BASE), "status": "PASS"}),
            encoding="utf-8",
        )
    return 0

def main() -> int:
    os.chdir(FINLAB)
    if "--self-test" in sys.argv:
        return self_test()
    try:
        ensure_portable_layout()
        ensure_webview2()
        start_supervisor()
        maybe_enable_startup()
        from local_desktop.client import main as open_window
        return open_window()
    except Exception as exc:
        APPDATA.mkdir(parents=True, exist_ok=True)
        log = APPDATA / "portable-start-failed.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        message(
            "启动遇到问题：\n" + str(exc) +
            "\n\n诊断记录：\n" + str(log) +
            "\n\n请不要关闭杀毒软件。",
            MB_ICONERROR,
        )
        return 1

if __name__ == "__main__":
    sys.exit(main())
