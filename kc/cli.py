"""Command line: the whole loop without a server running.

    python -m kc.cli init
    python -m kc.cli fixture
    python -m kc.cli query --identity alice@bu-a --intent impact_analysis --anchor ledger-api
    python -m kc.cli health
    python -m kc.cli tasks
    python -m kc.cli work
    python -m kc.cli project
    python -m kc.cli rebuild
    python -m kc.cli serve

Every command takes `--db`, defaulting to kc.db in the working directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from kc.agents.curator import Curator
from kc.connectors import registry
from kc.core.changeapi import ChangeAPI
from kc.core.errors import Ambiguous, KCError
from kc.core.projector import GraphProjector
from kc.serve import okf, pipeline
from kc.stores.sqlite_store import Store

DEFAULT_DB = "kc.db"


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=False, default=str))


def cmd_init(args: argparse.Namespace) -> int:
    store = Store(args.db)
    store.init_schema()
    _print(
        {
            "db": args.db,
            "schema": "ready",
            "dry_run": store.runtime_get("dry_run"),
            "cognitive_layer": store.runtime_get("cognitive_layer"),
            "note": "dry-run is the MVP default: agents propose, humans apply (FR-7.7)",
        }
    )
    return 0


def cmd_fixture(args: argparse.Namespace) -> int:
    from kc import fixture

    store = Store(args.db)
    store.init_schema()
    _print(fixture.build(store))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    store = Store(args.db)
    try:
        response = pipeline.query(
            store,
            user=args.identity,
            application=args.application,
            intent=args.intent,
            anchor=args.anchor,
            query_text=args.query_text,
            include_artifacts=not args.no_artifacts,
            as_of=args.as_of,
        )
    except Ambiguous as exc:
        _print(
            okf.disambiguation(
                request_id="r_ambiguous",
                ambiguous_anchors=[
                    {"anchor_index": exc.anchor_index, "name": exc.name,
                     "candidates": exc.candidates}
                ],
            )
        )
        return 0
    except KCError as exc:
        _print(okf.error(exc.code))
        return 1

    if args.summary:
        _print(_summary(response))
    else:
        _print(response)
    return 0


def _summary(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("message_type") != okf.CONTEXT_RESPONSE:
        return response
    return {
        "message_type": response["message_type"],
        "request_id": response["request_id"],
        "intent": response["intent"],
        "entities": [
            {
                "id": e["id"],
                "type": e["type"],
                "hop": e["hop"],
                "visibility": e["visibility"],
                "name": (e["props"].get("name") or {}).get("value"),
            }
            for e in response["entities"]
        ],
        "relations": [
            f"{r['from']} -{r['type']}-> {r['to']} (hop {r['hop']}, "
            f"corroboration {r.get('corroboration', 1)})"
            for r in response["relations"]
        ],
        "artifacts": [f"{a['kind']}:{a['node_id']}" for a in response["artifacts"]],
        "gaps": response["gaps"],
        "budget": response["budget"],
    }


def cmd_health(args: argparse.Namespace) -> int:
    from kc import health

    store = Store(args.db)
    payload = {
        "metrics": health.metrics(store),
        "guardrails": health.guardrails(store),
        "connectors": registry.status(),
        "agents": Curator(store).declarations(),
        "allocation": Curator(store).allocate(),
    }
    _print(payload)
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    store = Store(args.db)
    tasks = store.tasks(state=args.state, kind=args.kind)
    _print(
        [
            {
                "task_id": t["task_id"],
                "kind": t["kind"],
                "state": t["state"],
                "scope": t["scope"],
                "reason": t["reason"],
                "candidates": len(t["payload"].get("candidates", [])),
            }
            for t in tasks
        ]
    )
    return 0


def cmd_work(args: argparse.Namespace) -> int:
    store = Store(args.db)
    results = Curator(store).work(limit=args.limit)
    _print(results)
    GraphProjector(store).run_once()
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    store = Store(args.db)
    _print(GraphProjector(store).run_once(limit=args.limit))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    """Rebuild every projection from the log alone (NFR-5, AT-28)."""
    store = Store(args.db)
    before = store.graph_fingerprint()
    result = GraphProjector(store).rebuild()
    _print(
        {
            **result,
            "fingerprint_before": before,
            "identical": before == result["fingerprint"],
        }
    )
    return 0 if before == result["fingerprint"] else 2


def cmd_serve(args: argparse.Namespace) -> int:
    from kc.api.server import serve

    serve(args.db, host=args.host, port=args.port)
    return 0


def cmd_flags(args: argparse.Namespace) -> int:
    store = Store(args.db)
    change = ChangeAPI(store)
    if args.dry_run is not None:
        change.set_dry_run(args.dry_run == "on")
    if args.cognitive_layer is not None:
        change.set_cognitive_layer(args.cognitive_layer == "on")
    _print({"dry_run": change.dry_run, "cognitive_layer": change.cognitive_layer_on})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kc", description="KC Agent skeleton")
    parser.add_argument("--db", default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the schema").set_defaults(func=cmd_init)
    sub.add_parser("fixture", help="load the BU-A / BU-B fixture").set_defaults(func=cmd_fixture)

    query = sub.add_parser("query", help="run one OKF context request")
    query.add_argument("--identity", required=True, help="end user, e.g. alice@bu-a")
    query.add_argument("--application", default="kc-cli")
    query.add_argument("--intent", default="lookup", help="lookup|test_planning|design|impact_analysis")
    query.add_argument("--anchor", required=True, help="node id or name")
    query.add_argument("--query-text", dest="query_text", default=None)
    query.add_argument("--as-of", dest="as_of", default=None)
    query.add_argument("--no-artifacts", action="store_true")
    query.add_argument("--summary", action="store_true", help="human-readable digest")
    query.set_defaults(func=cmd_query)

    health = sub.add_parser("health", help="metrics, guardrails, connector and agent status")
    health.set_defaults(func=cmd_health)

    tasks = sub.add_parser("tasks", help="the human queue")
    tasks.add_argument("--state", default=None)
    tasks.add_argument("--kind", default=None)
    tasks.set_defaults(func=cmd_tasks)

    work = sub.add_parser("work", help="Curator dispatches open tasks to spokes")
    work.add_argument("--limit", type=int, default=10)
    work.set_defaults(func=cmd_work)

    project = sub.add_parser("project", help="apply pending change-log events")
    project.add_argument("--limit", type=int, default=500)
    project.set_defaults(func=cmd_project)

    rebuild = sub.add_parser("rebuild", help="rebuild projections from the log")
    rebuild.set_defaults(func=cmd_rebuild)

    flags = sub.add_parser("flags", help="dry-run and kill switch")
    flags.add_argument("--dry-run", choices=("on", "off"), default=None)
    flags.add_argument("--cognitive-layer", choices=("on", "off"), default=None)
    flags.set_defaults(func=cmd_flags)

    serve = sub.add_parser("serve", help="A2A-shaped HTTP endpoint")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
