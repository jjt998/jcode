from __future__ import annotations

import hashlib


INLINE_LIMITS = {"run_shell": 6000}
DEFAULT_INLINE_LIMIT = 4000
SUMMARY_LIMIT = 1500


def is_tool_result_artifact(path: str) -> bool:
    """判断路径是否为当前 run 生成的工具结果 artifact。"""
    normalized = str(path or "").replace("\\", "/")
    return normalized.startswith(".jcode/runs/") and "/artifacts/" in normalized


def prepare_tool_result_observation(run_store, run_dir, tool_name: str, full_result: str, artifacts: list[str] | None = None) -> tuple[str, dict, list[str]]:
    """按工具类别外置超长结果，并生成可继续操作的短观察。"""
    full_result = str(full_result)
    artifact_list = list(artifacts or [])
    # 文件读取由 start/end/max_chars 主动分段，不能再被外置成另一层 artifact。
    if tool_name == "read_file":
        return full_result, {
            "original_chars": len(full_result),
            "content_sha256": hashlib.sha256(full_result.encode("utf-8")).hexdigest(),
            "full_output_artifact": "",
            "observation_policy": "read_file_direct",
            "observation_summary": full_result,
        }, artifact_list
    limit = INLINE_LIMITS.get(tool_name, DEFAULT_INLINE_LIMIT)
    metadata = {
        "original_chars": len(full_result),
        "content_sha256": hashlib.sha256(full_result.encode("utf-8")).hexdigest(),
        "full_output_artifact": "",
        "observation_policy": {"run_shell": "run_shell_6000"}.get(tool_name, "generic_4000"),
        "observation_summary": "",
    }
    if len(full_result) <= limit:
        return full_result, metadata, artifact_list

    artifact_name = f"{tool_name}-output-{metadata['content_sha256'][:12]}.txt"
    artifact_path = run_store.write_artifact(run_dir, artifact_name, full_result)
    if artifact_path not in artifact_list:
        artifact_list.append(artifact_path)
    metadata["full_output_artifact"] = artifact_path
    if tool_name == "run_shell":
        lines = [line.strip() for line in full_result.splitlines() if line.strip() and line.strip() not in {"stdout:", "stderr:"}]
        summary = "\n".join(lines[:30])[:SUMMARY_LIMIT] or full_result[:SUMMARY_LIMIT]
    else:
        head, tail = full_result[:750], full_result[-750:]
        summary = (head + ("\n...\n" + tail if tail and tail != head else ""))[:SUMMARY_LIMIT]
    metadata["observation_summary"] = summary
    return f"tool output stored at: {artifact_path}\nsummary:\n{summary}", metadata, artifact_list
