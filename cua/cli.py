"""Command line: discover a capability, probe it for outcomes, approve it, replay it."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .agent import discover, probe, verify
from .console import serve as serve_console
from .handoff import ConsoleOperator, FileOperator
from .llm import ClaudeLLM
from .replay import replay
from .runtime import ConfigError
from .schema import AppProfile, Capability, TenantBinding, dump, load


def pairs(items: list[str]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in items)


def context(args) -> tuple[AppProfile, TenantBinding]:
    tenant = load(TenantBinding, f"tenants/{args.tenant}.yaml")
    return load(AppProfile, f"apps/{tenant.product}.yaml"), tenant


def operator(args):
    if args.operator == "console":
        return ConsoleOperator(timeout_s=args.operator_timeout)
    if args.operator == "web":
        return FileOperator(timeout_s=args.operator_timeout)
    return None


def bump(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def cmd_discover(args) -> int:
    profile, tenant = context(args)
    params = pairs(args.param)
    cap, run_id, stopped = discover(args.goal, args.name, params, profile, tenant, ClaudeLLM("discovery"),
                                    operator=operator(args), headed=args.headed, max_steps=args.max_steps,
                                    runs_dir=args.runs_dir)
    if cap is None:
        print(f"discovery failed: {stopped}\nevidence: {args.runs_dir}/{run_id}/", file=sys.stderr)
        return 1
    verify(cap, profile, tenant, [params, *([pairs(args.verify_param)] if args.verify_param else [])], args.runs_dir)
    path = Path("capabilities") / f"{args.name}.yaml"
    dump(cap, path)
    print(f"saved {path} (status: draft, verified: {cap.provenance.verified})\nevidence: {args.runs_dir}/{run_id}/")
    return 0 if cap.provenance.verified else 1


def cmd_probe(args) -> int:
    profile, tenant = context(args)
    cap = load(Capability, args.capability)
    result, outcome = probe(cap, profile, tenant, pairs(args.param), ClaudeLLM("probe"), headed=args.headed,
                            runs_dir=args.runs_dir)
    if outcome:
        cap.version = bump(cap.version)
        dump(cap, args.capability)
        print(f"learned outcome {outcome.name}; saved {args.capability} as {cap.version}")
    else:
        print(f"no new outcome learned (replay status: {result.status})")
    print(f"evidence: {args.runs_dir}/{result.run_id}/")
    return 0


def cmd_approve(args) -> int:
    cap = load(Capability, args.capability)
    if not cap.provenance.verified:
        print("refusing: this capability has not passed verification", file=sys.stderr)
        return 1
    cap.status = "approved"
    cap.provenance.notes.append(f"approved by {args.by}")
    dump(cap, args.capability)
    print(f"{cap.capability}@{cap.version} approved")
    return 0


def cmd_replay(args) -> int:
    profile, tenant = context(args)
    result = replay(load(Capability, args.capability), profile, tenant, pairs(args.param), confirm=args.confirm,
                    operator=operator(args), headed=args.headed, dry_run=args.dry_run, runs_dir=args.runs_dir)
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    print(f"evidence: {args.runs_dir}/{result.run_id}/", file=sys.stderr)
    return {"success": 0, "business_outcome": 0, "failed": 1, "needs_human": 2}[result.status]


def load_dotenv(path: str = ".env") -> None:
    """Read KEY=VALUE lines from .env; variables already in the environment win."""
    if not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and value and not key.startswith("#"):
            os.environ.setdefault(key, value)


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="cua", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, human: bool = True) -> None:
        p.add_argument("--tenant", default="harbor", help="name of a file in tenants/")
        p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
        p.add_argument("--headed", action="store_true", help="show the browser window")
        p.add_argument("--runs-dir", default="runs", help="where run evidence is written")
        if human:
            p.add_argument("--operator", choices=["none", "console", "web"], default="none",
                           help="where intervention requests go: the terminal, or the web console")
            p.add_argument("--operator-timeout", type=float, default=300, metavar="SECONDS")

    d = sub.add_parser("discover", help="LLM-driven run that records a new capability")
    d.add_argument("--name", required=True, help="capability name, e.g. member.read_savings_balance")
    d.add_argument("--goal", required=True)
    d.add_argument("--verify-param", action="append", default=[], metavar="NAME=VALUE",
                   help="a second input set, replayed to prove the artifact is not tied to the example")
    d.add_argument("--max-steps", type=int, default=25)
    common(d)
    d.set_defaults(fn=cmd_discover)

    p = sub.add_parser("probe", help="replay with a bad input and let the model name the resulting outcome")
    p.add_argument("capability")
    common(p, human=False)
    p.set_defaults(fn=cmd_probe)

    a = sub.add_parser("approve", help="mark a verified capability as approved for irreversible steps")
    a.add_argument("capability")
    a.add_argument("--by", required=True, help="reviewer identity")
    a.set_defaults(fn=cmd_approve)

    r = sub.add_parser("replay", help="deterministic execution; no model involved")
    r.add_argument("capability")
    r.add_argument("--confirm", action="store_true", help="caller's explicit consent to irreversible steps")
    r.add_argument("--dry-run", action="store_true", help="stop before the first irreversible step")
    common(r)
    r.set_defaults(fn=cmd_replay)

    c = sub.add_parser("console", help="web console: capabilities, run evidence, live operator handoff")
    c.add_argument("--port", type=int, default=8770)
    c.set_defaults(fn=lambda a: serve_console(a.port))

    args = ap.parse_args()
    try:
        sys.exit(args.fn(args))
    except ConfigError as e:
        sys.exit(f"configuration error: {e}")


if __name__ == "__main__":
    main()
