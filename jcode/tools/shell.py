from __future__ import annotations

import subprocess

from jcode.tools.base import ToolResult


def run_shell(workspace, args) -> ToolResult:
    proc = subprocess.run(args.command, cwd=str(workspace.root), shell=True, text=True, capture_output=True, timeout=args.timeout)
    text = f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}\nexit_code: {proc.returncode}"
    status = "success" if proc.returncode == 0 else "error"
    return ToolResult(status, text, error_type=None if proc.returncode == 0 else "tool_failed")
