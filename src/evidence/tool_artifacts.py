from __future__ import annotations

import hashlib


INLINE_TOOL_OUTPUT_LIMIT = 1000


def prepare_tool_result_observation(run_store, run_dir, tool_name: str, full_result: str, artifacts: list[str] | None = None) -> tuple[str, dict, list[str]]:
    """把超长工具结果写入 artifact，并返回给模型可读的短预览。"""
    full_result = str(full_result)
    artifact_list = list(artifacts or [])
    metadata = {
        "original_chars": len(full_result),
        "content_sha256": hashlib.sha256(full_result.encode("utf-8")).hexdigest(),
        "full_output_artifact": "",
    }
    if len(full_result) <= INLINE_TOOL_OUTPUT_LIMIT:
        return full_result, metadata, artifact_list

    artifact_name = f"{tool_name}-output-{metadata['content_sha256'][:12]}.txt"
    artifact_path = run_store.write_artifact(run_dir, artifact_name, full_result)
    if artifact_path not in artifact_list:
        artifact_list.append(artifact_path)
    metadata["full_output_artifact"] = artifact_path
    # 首行保留 artifact 路径，方便旧历史压缩逻辑直接识别并替换。
    return f"{artifact_path}\n{full_result[:INLINE_TOOL_OUTPUT_LIMIT]}", metadata, artifact_list
