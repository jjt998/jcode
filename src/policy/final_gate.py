from __future__ import annotations

import hashlib

from src.runtime.final_readiness import build_final_readiness


class FinalGate:
    """对 Agent 与 Harness 分别评分，只对可纠正的 Agent 问题发起有限重跑。"""

    def check(self, final_text: str, task_state, working_memory, *, session=None, workspace=None, context=None) -> dict:
        readiness = build_final_readiness(task_state, session, workspace, context=context)
        reasons = list(readiness.reasons)
        if not final_text.strip():
            reasons.insert(0, _reason("empty_final", "hard", "模型没有生成最终内容。", {}))

        agent_reasons = [item for item in reasons if item.get("owner", "agent") == "agent"]
        harness_reasons = [item for item in reasons if item.get("owner") == "harness"]
        agent_quality = _agent_quality(agent_reasons, task_state)
        harness_quality = _harness_quality(harness_reasons, task_state)
        assurance = _assurance(final_text, task_state, harness_reasons)
        rerun_reason = _next_rerun_reason(agent_reasons, task_state)

        if rerun_reason:
            fingerprint = _fingerprint(rerun_reason)
            task_state.agent_rerun_count += 1
            intervention = {
                "intervention_id": f"intervention-{len(task_state.interventions) + 1}",
                "kind": "agent_rerun",
                "actor": "harness",
                "reason_code": rerun_reason["code"],
                "evidence_fingerprint": fingerprint,
                "attempt": task_state.agent_rerun_count,
                "budget_remaining": max(0, task_state.agent_rerun_budget - task_state.agent_rerun_count),
                "instruction": _correction_instruction(rerun_reason),
            }
            task_state.record_intervention(intervention)
            decision = {"allowed": False, "action": "rerun_agent", "reason": rerun_reason["code"], "reasons": reasons, "message": intervention["instruction"], "correction_packet": intervention, "agent_quality": agent_quality, "harness_quality": harness_quality, "assurance": assurance}
            task_state.record_gate_decision(decision)
            return decision

        finalization = {"status": "committed", "final_content_status": "present" if final_text.strip() else "empty", "finalization_id": _finalization_id(task_state.run_id, final_text, reasons)}
        task_state.agent_quality = agent_quality
        task_state.harness_quality = harness_quality
        task_state.assurance = assurance
        task_state.finalization = finalization
        task_state.record_intervention({"intervention_id": f"intervention-{len(task_state.interventions) + 1}", "kind": "safe_finalize", "actor": "harness", "result": "completed", "final_content_status": finalization["final_content_status"]})
        decision = {"allowed": True, "action": "safe_finalize", "reason": "finalized", "reasons": reasons, "message": "", "agent_quality": agent_quality, "harness_quality": harness_quality, "assurance": assurance, "finalization": finalization}
        task_state.record_gate_decision(decision)
        return decision


def _next_rerun_reason(reasons: list[dict], task_state) -> dict | None:
    if task_state.agent_rerun_count >= task_state.agent_rerun_budget:
        return None
    rerunnable = {"empty_final", "delivery_action_failure", "failed_verification", "missing_required_artifact", "plan_artifact_not_ready", "unresolved_tool_failure", "changed_paths_without_verification", "unresolved_todo"}
    attempted = {item.get("evidence_fingerprint") for item in task_state.interventions if item.get("kind") == "agent_rerun"}
    for reason in reasons:
        if reason.get("owner", "agent") != "agent" or reason.get("code") not in rerunnable:
            continue
        if _fingerprint(reason) not in attempted:
            return reason
    return None


def _agent_quality(reasons: list[dict], task_state) -> dict:
    hard = [item for item in reasons if item.get("severity") == "hard"]
    soft = [item for item in reasons if item.get("severity") == "soft"]
    historical_rerun = any(item.get("kind") == "agent_rerun" for item in task_state.interventions)
    level = "red" if hard else "yellow" if soft or historical_rerun else "green"
    return {"level": level, "reasons": reasons}


def _harness_quality(reasons: list[dict], task_state) -> dict:
    red = [item for item in reasons if item.get("severity") == "hard"]
    yellow = [item for item in reasons if item.get("severity") == "soft"]
    level = "red" if red else "yellow" if yellow else "green"
    return {"level": level, "reasons": reasons}


def _assurance(final_text: str, task_state, harness_reasons: list[dict]) -> dict:
    runtime_untrusted = any(item.get("code") == "final_context_exceeds_window" for item in harness_reasons)
    evidence_untrusted = bool(harness_reasons)
    return {"final_text": "verified" if final_text.strip() else "unverified", "execution_evidence": "unverified" if evidence_untrusted else "verified", "runtime_state": "unverified" if runtime_untrusted else "verified"}


def _fingerprint(reason: dict) -> str:
    payload = f"{reason.get('code', '')}|{sorted(reason.get('evidence', {}).items())}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _finalization_id(run_id: str, final_text: str, reasons: list[dict]) -> str:
    payload = f"{run_id}|{final_text}|{[_fingerprint(reason) for reason in reasons]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _correction_instruction(reason: dict) -> str:
    instructions = {
        "empty_final": "请生成非空最终内容；不得只声明任务已完成。",
        "failed_verification": "请修复失败验证，或提供可信的同类验证证据。",
        "missing_required_artifact": "请完成用户明确要求的产物，并验证其存在与可读性。",
        "changed_paths_without_verification": "请为本次文件变更补充必要验证，或记录无需验证的明确证据。",
        "unresolved_todo": "请完成或取消未完成 todo，并记录原因。",
    }
    return instructions.get(reason["code"], "请根据已记录的运行证据完成必要补救；不得通过最终文本声明关闭失败。")


def _reason(code: str, severity: str, message: str, evidence: dict, *, owner: str = "agent") -> dict:
    return {"code": code, "severity": severity, "message": message, "evidence": evidence, "owner": owner}
