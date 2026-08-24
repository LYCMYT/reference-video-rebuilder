#!/usr/bin/env python3
"""CLI for the optional, explicitly networked v0.7 OpenAI image controller."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

try:
    import rrv_openai_controller
    import rrv_runtime
except ImportError:  # pragma: no cover - package-style import support.
    from . import rrv_openai_controller, rrv_runtime  # type: ignore[no-redef]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or run the separately authorized GPT Image 2 controller. "
            "The run command can upload images and incur API charges."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("plan", help="Project-root-relative approved generation-plan.json")
        subparser.add_argument("plan_review", help="Project-root-relative approved plan review JSON")
        subparser.add_argument("--project-root", required=True)
        subparser.add_argument("--generation-rights-confirmed", action="store_true")
        subparser.add_argument("--ffprobe", default="ffprobe")
        subparser.add_argument(
            "--timeout-seconds",
            type=float,
            default=rrv_openai_controller.DEFAULT_TIMEOUT_SECONDS,
        )
        subparser.add_argument("--json", action="store_true")

    preflight = subparsers.add_parser(
        "preflight", help="Validate approvals and immutable local inputs; no SDK, network, env, or writes"
    )
    common(preflight)

    run = subparsers.add_parser(
        "run", help="Issue sequential, billable GPT Image requests and atomically publish a PNG pack"
    )
    common(run)
    run.add_argument("--output-dir", default="openai-generation-result-pack")
    run.add_argument("--cloud-upload-confirmed", action="store_true")
    run.add_argument("--billable-requests-confirmed", action="store_true")
    run.add_argument("--max-billable-requests", type=int, required=True)
    return parser


def _safe_failure(exc: BaseException) -> Mapping[str, Any]:
    if isinstance(exc, rrv_runtime.RRVError):
        return {
            "schema_version": rrv_runtime.JSON_SCHEMA_VERSION,
            "status": "fail",
            "error": {"code": exc.code, "message": exc.message},
        }
    return {
        "schema_version": rrv_runtime.JSON_SCHEMA_VERSION,
        "status": "fail",
        "error": {
            "code": rrv_runtime.ERR_TOOL_EXECUTION,
            "message": "OpenAI image controller failed",
        },
    }


def _emit(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(rrv_runtime.stable_json_dumps(payload, indent=None))
        return
    if payload.get("status") == "ok":
        result = payload.get("result")
        print("OpenAI image controller: ok")
        if isinstance(result, Mapping):
            counts = result.get("counts")
            if isinstance(counts, Mapping):
                if "billable_requests" in counts:
                    print(f"billable requests: {counts.get('billable_requests')}")
                elif "generation_tasks" in counts:
                    print(f"generation tasks: {counts.get('generation_tasks')}")
            if isinstance(result.get("output_dir"), str):
                print(f"output: {result['output_dir']}")
    else:
        error = payload.get("error")
        message = error.get("message") if isinstance(error, Mapping) else "operation failed"
        print(f"OpenAI image controller: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = rrv_openai_controller.preflight_openai_generation(
                args.plan,
                args.plan_review,
                project_root=args.project_root,
                generation_rights_confirmed=args.generation_rights_confirmed,
                ffprobe=args.ffprobe,
                timeout_seconds=args.timeout_seconds,
            )
        else:
            result = rrv_openai_controller.run_openai_generation(
                args.plan,
                args.plan_review,
                project_root=args.project_root,
                output_dir=args.output_dir,
                generation_rights_confirmed=args.generation_rights_confirmed,
                cloud_upload_confirmed=args.cloud_upload_confirmed,
                billable_requests_confirmed=args.billable_requests_confirmed,
                max_billable_requests=args.max_billable_requests,
                ffprobe=args.ffprobe,
                timeout_seconds=args.timeout_seconds,
            )
        payload = rrv_runtime.success_payload(result)
        _emit(payload, as_json=args.json)
        return 0
    except BaseException as exc:
        payload = _safe_failure(exc)
        _emit(payload, as_json=getattr(args, "json", False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
