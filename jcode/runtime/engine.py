from __future__ import annotations

from jcode.runtime.actions import parse_model_action
from jcode.runtime.completion import finish_run
from jcode.runtime.transitions import ABORTED, MODEL_ERROR, STEP_LIMIT_REACHED, VALID_FINAL
from jcode.state.checkpoint import CheckpointManager
from jcode.state.history import append_history
from jcode.state.task import TaskState


class Engine:
    def __init__(self, agent):
        self.agent = agent

    def ask(self, user_message: str) -> str:
        agent = self.agent
        task_state = TaskState.create(user_message)
        run_dir = agent.run_store.start_run(task_state)
        checkpoint = CheckpointManager(run_dir, agent.workspace)
        agent.working_memory.task_goal = user_message
        append_history(agent.session, "user", user_message)
        agent.run_store.append_trace(run_dir, "run_started", task_state.run_id, task_id=task_state.task_id, user_request=user_message[:500])
        final_text = ""

        for step in range(agent.config.max_steps):
            if agent.abort_requested:
                return finish_run(agent, task_state, run_dir, "Stopped after abort request.", ABORTED)
            task_state.step_index = step + 1
            task_state.attempts += 1

            prompt_result = agent.prompt_builder.build(agent.session, agent.working_memory, user_message)
            agent.run_store.append_trace(run_dir, "prompt_built", task_state.run_id, **prompt_result.metadata)
            try:
                response = agent.model_router.complete(
                    prompt_result.prompt,
                    max_tokens=agent.config.max_new_tokens,
                    temperature=agent.config.temperature,
                )
            except Exception as exc:
                agent.run_store.append_trace(run_dir, "run_failed", task_state.run_id, error_type=type(exc).__name__, message=str(exc)[:500])
                return finish_run(agent, task_state, run_dir, f"Model error: {exc}", MODEL_ERROR)

            agent.run_store.append_trace(
                run_dir,
                "model_requested",
                task_state.run_id,
                estimated_input_tokens=response.input_tokens,
                estimated_output_tokens=response.output_tokens,
            )
            action = parse_model_action(response.text)
            task_state.last_action = {"kind": action.kind, "tool_name": action.tool_name, "content": action.content[:200]}
            agent.run_store.append_trace(run_dir, "model_parsed", task_state.run_id, action=task_state.last_action)

            if action.kind == "final":
                decision = agent.final_gate.check(action.content, task_state, agent.working_memory)
                agent.run_store.append_trace(run_dir, "final_readiness_decision", task_state.run_id, **decision)
                if decision["allowed"]:
                    append_history(agent.session, "assistant", action.content)
                    checkpoint.create(agent.session, task_state, agent.working_memory, agent.worker_manager.worker_refs())
                    agent.run_store.append_trace(run_dir, "checkpoint_created", task_state.run_id, trigger="final")
                    final_text = action.content
                    return finish_run(agent, task_state, run_dir, final_text, VALID_FINAL)
                append_history(agent.session, "tool", decision["message"], name="final_gate")
                agent.working_memory.observe_tool(decision["message"])
                continue

            if action.kind != "tool":
                append_history(agent.session, "tool", action.content, name="parser")
                agent.working_memory.observe_tool(action.content)
                continue

            agent.run_store.append_trace(run_dir, "tool_requested", task_state.run_id, name=action.tool_name, args=action.tool_args or {})
            if action.tool_name in {"spawn_subagent", "send_subagent_message", "wait_subagent"}:
                result = agent.worker_manager.run_tool(action.tool_name, action.tool_args or {})
            else:
                result = agent.tool_executor.execute(action.tool_name, action.tool_args or {})

            task_state.tool_steps += 1
            append_history(agent.session, "tool", result.text, name=action.tool_name, tool_status=result.status)
            agent.working_memory.observe_tool(f"{action.tool_name}: {result.status}: {result.text[:500]}")
            if action.tool_name == "wait_subagent" and result.status == "success":
                agent.working_memory.subagent_results.append(result.text[:1000])

            event_name = "subagent_completed" if action.tool_name == "wait_subagent" and result.status == "success" else "tool_executed"
            agent.run_store.append_trace(
                run_dir,
                event_name,
                task_state.run_id,
                name=action.tool_name,
                status=result.status,
                error_type=result.error_type,
                changed_files=result.changed_files,
                result=agent.redactor.redact(result.text[:1000]),
            )
            checkpoint.create(agent.session, task_state, agent.working_memory, agent.worker_manager.worker_refs())
            agent.run_store.append_trace(run_dir, "checkpoint_created", task_state.run_id, trigger="tool_executed")
            agent.run_store.write_task_state(run_dir, task_state)

        return finish_run(agent, task_state, run_dir, final_text or "Stopped after reaching max steps.", STEP_LIMIT_REACHED)
