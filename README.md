# JCode

JCode is a compact local coding agent inspired by Pico. It keeps the core interview-facing engineering chain: CLI, runtime assembly, ReAct loop, prompt construction, provider calls, tool and subagent execution, policy governance, working memory, trace, checkpoint, and final response.

It intentionally does not include Pico's TUI, full evaluation suite, dream consolidation, complex multi-provider routing, large benchmark harnesses, or vision/media tooling.

## Install

```bash
pip install -e .
```

## Configure

Create `.jcode.toml` or set environment variables:

```bash
set JCODE_API_KEY=sk-...
set JCODE_BASE_URL=https://api.openai.com/v1
set JCODE_MODEL=gpt-5
```

## Run

```bash
jcode "hi"
python -m jcode --help
```

Every run writes auditable state to `.jcode/runs/<run_id>/` and session state to `.jcode/sessions/`.

## Prompt Shape

JCode always builds the same prompt structure, even for a one-token request like `hi`:

```text
prefix
skill
working_memory
history
current_request
```

`prefix` contains stable identity and protocol rules. Changing facts such as workspace state, resume context, file freshness, retrieved durable notes, and subagent results live in `working_memory`.

## Tool Safety Chain

Tool execution follows five gates: input validation, behavior constraints, permission authorization, sandbox boundary, and result closure. This covers parameter errors, path escape, read-before-write, repeated calls, shell side effects, secret redaction, and checkpoint evidence.

## Subagents

Workers are lightweight subagents with independent task state, working memory, trace, and result files. They inherit the same workspace root, tool policy, permission model, and sandbox settings. A subagent result is merged through the parent agent instead of directly becoming the parent final answer.
