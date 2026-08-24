from __future__ import annotations

import argparse

from src.app.bootstrap import build_agent
from src.app.config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JCode compact local coding agent")
    parser.add_argument("prompt", nargs="*", help="Optional one-shot prompt")
    parser.add_argument("--cwd", default=".", help="Workspace directory")
    parser.add_argument("--config", default=None, help="Path to .jcode.toml")
    parser.add_argument("--provider", default=None, help="Provider profile name")
    parser.add_argument("--api-key", default=None, help="Provider API key override")
    parser.add_argument("--base-url", default=None, help="Provider base URL override")
    parser.add_argument("--model", default=None, help="Provider model override")
    parser.add_argument("--resume", default=None, help="Session id to resume or latest")
    parser.add_argument("--session-id", default=None, help="Create or resume a fixed session id")
    parser.add_argument("--plan-topic", default=None, help="Enter plan mode with the given topic")
    parser.add_argument("--plan-path", default=None, help="Override the active plan artifact path")
    parser.add_argument("--approval", choices=("ask", "auto", "never"), default=None)
    parser.add_argument("--sandbox", choices=("off", "best_effort", "required"), default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--max-new-tokens", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args)
    agent = build_agent(config)
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        parser.print_help()
        return 0
    print(agent.ask(prompt))
    return 0
