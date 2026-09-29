"""Entry point.

    python -m openmontage_mcp                                  # serve over stdio
    python /path/to/OpenMontage/openmontage_mcp/__main__.py    # same, from any directory
    python -m openmontage_mcp approve-stage <project_id> <stage>
    python -m openmontage_mcp approve-tool <project_id> <tool_name>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    # Tools resolve pipeline/, styles/ and .env relative to the repository.
    os.chdir(REPO_ROOT)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    parser = argparse.ArgumentParser(prog="python -m openmontage_mcp")
    sub = parser.add_subparsers(dest="command")
    stage = sub.add_parser("approve-stage", help="approve a stage awaiting the human")
    stage.add_argument("project_id")
    stage.add_argument("stage")
    tool = sub.add_parser("approve-tool", help="allow a paid tool to spend on a project")
    tool.add_argument("project_id")
    tool.add_argument("tool_name")
    args = parser.parse_args(argv)

    if args.command in ("approve-stage", "approve-tool"):
        from openmontage_mcp import approvals

        try:
            if args.command == "approve-stage":
                result = approvals.approve_stage(args.project_id, args.stage)
            else:
                result = approvals.approve_paid_tool(args.project_id, args.tool_name)
        except Exception as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    from openmontage_mcp.server import StartupRefused, build_server, enforce_budget_env

    try:
        enforce_budget_env()
    except StartupRefused as exc:
        print(f"openmontage_mcp: {exc}", file=sys.stderr)
        return 2
    asyncio.run(build_server().run_stdio_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
