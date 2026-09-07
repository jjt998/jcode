from __future__ import annotations

import os
import signal
import subprocess
import time

from src.tools.base import ToolResult


def run_shell(workspace, args, *, abort_requested=None) -> ToolResult:
    """执行命令时轮询 abort 请求，避免用户中止后 shell 仍在后台继续运行。"""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        args.command,
        cwd=str(workspace.root),
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    deadline = time.monotonic() + args.timeout
    while proc.poll() is None:
        if callable(abort_requested) and abort_requested():
            _terminate_process_tree(proc)
            stdout, stderr = proc.communicate()
            return ToolResult(
                "interrupted",
                "[JCode tool execution interrupted]\nstatus: interrupted\nreason: user_abort\nmessage: 该工具执行期间被用户终止，可能已经产生文件、命令、进程或其他运行时副作用；必须先检查工作区和运行状态，不得将其视为完成。",
                error_type="user_abort",
                metadata={"return_code": proc.returncode, "interrupted": True, "stdout": stdout, "stderr": stderr},
            )
        if time.monotonic() >= deadline:
            _terminate_process_tree(proc)
            raise subprocess.TimeoutExpired(args.command, args.timeout)
        time.sleep(0.05)
    stdout, stderr = proc.communicate()
    text = f"stdout:\n{stdout}\nstderr:\n{stderr}\nexit_code: {proc.returncode}"
    status = "success" if proc.returncode == 0 else "error"
    return ToolResult(status, text, error_type=None if proc.returncode == 0 else "tool_failed", metadata={"return_code": proc.returncode, "signal": -proc.returncode if proc.returncode < 0 else None})


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """终止 shell 及其子进程，防止 cmd 或脚本在 abort 后继续占用工作区。"""
    if os.name == "nt":
        # taskkill 输出只用于清理，统一按 UTF-8 容错解码，避免 Windows 默认 GBK 警告。
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    else:
        os.killpg(proc.pid, signal.SIGTERM)
