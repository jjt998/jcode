from __future__ import annotations

import argparse

import uvicorn

from src.app.cli import build_parser as build_cli_parser
from src.app.config import load_config
from src.app.web_runs import WebRunManager
from src.app.web_server import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = build_cli_parser()
    parser.description = "JCode local web console"
    parser.add_argument("--host", default="127.0.0.1", help="Web server host")
    parser.add_argument("--port", type=int, default=8765, help="Web server port")
    parser.set_defaults(temperature=0.2)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.prompt = []
    config = load_config(args)
    manager = WebRunManager(config)
    app = create_app(manager)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
