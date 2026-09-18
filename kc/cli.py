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
import dataclasses
import json
import sys
from pathlib import Path
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


def _result_row(outcome: Any) -> dict[str, Any]:
    """An IngestResult as a dict, dropping the empty fields so a run of fifty
    objects stays readable."""
    row = dataclasses.asdict(outcome)
    return {k: v for k, v in row.items() if v not in (None, [], 0, False)}


def cmd_ingest(args: argparse.Namespace) -> int:
    """Drive the fast path over discover() -> fetch() -> ingest.

    `--dry-run` reports what would be written and appends nothing, so real
    tickets can be inspected before any of it reaches the log.
    """
    from kc.connectors import registry as connector_registry
    from kc.ingest import FastPath

    store = Store(args.db)
    connector = connector_registry.get(args.connector)
    if connector is None:
        _print({"error": "unknown connector", "known": sorted(connector_registry.all_connectors())})
        return 2

    fast = FastPath(store)

    # --from-file needs no credentials: save a real API response to disk and
    # dry-run the extraction before wiring fetch at all. This is the fastest
    # way to see what a real ticket produces.
    if args.from_file:
        from kc.connectors.base import SourceObject

        results: list[dict[str, Any]] = []
        for path in args.from_file:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            obj = SourceObject(
                source_system=args.connector,
                source_instance=payload.get("source_instance", "manual"),
                source_id=payload.get("source_id") or payload.get("key") or Path(path).stem,
                container=payload.get("container")
                or str(payload.get("source_id", payload.get("key", ""))).split("-")[0],
                content=payload.get("content", payload),
                raw=payload.get("raw", json.dumps(payload.get("content", payload))),
                deleted=bool(payload.get("deleted")),
            )
            if args.dry_run:
                results.append(fast.plan_object(obj))
            else:
                try:
                    results.append(_result_row(fast.ingest_object(obj)))
                except KCError as exc:
                    results.append({"file": path, "rejected": type(exc).__name__,
                                    "detail": str(exc)})
        summary: dict[str, Any] = {
            "connector": args.connector,
            "source": "files",
            "mode": "dry-run (nothing appended)" if args.dry_run else "ingested",
            "results": results,
        }
        if not args.dry_run:
            summary["projected"] = GraphProjector(store).run_once()
        _print(summary)
        return 0

    source_ids: list[str] = list(args.source_id or [])
    if not source_ids:
        try:
            source_ids = connector.discover(args.since)
        except NotImplementedError as exc:
            _print(
                {
                    "connector": args.connector,
                    "stage": "discover",
                    "wired": False,
                    "detail": str(exc),
                    "next": (
                        f"implement {args.connector}.discover in "
                        f"kc/connectors/{args.connector}.py, or pass --source-id KEY to "
                        "ingest specific objects"
                    ),
                }
            )
            return 2
    source_ids = source_ids[: args.limit]

    results: list[dict[str, Any]] = []
    for source_id in source_ids:
        try:
            obj = connector.fetch(source_id)
        except NotImplementedError as exc:
            _print(
                {
                    "connector": args.connector,
                    "stage": "fetch",
                    "wired": False,
                    "source_id": source_id,
                    "detail": str(exc),
                    "next": f"implement {args.connector}.fetch; see the class docstring",
                }
            )
            return 2
        except Exception as exc:  # noqa: BLE001 - a wired connector failing on one object
            results.append({"source_id": source_id, "error": f"{type(exc).__name__}: {exc}"})
            continue

        if args.dry_run:
            results.append(fast.plan_object(obj))
            continue
        try:
            results.append(_result_row(fast.ingest_object(obj)))
        except KCError as exc:
            results.append(
                {"source_id": source_id, "rejected": type(exc).__name__, "detail": str(exc)}
            )

    summary = {
        "connector": args.connector,
        "since": args.since,
        "objects": len(source_ids),
        "mode": "dry-run (nothing appended)" if args.dry_run else "ingested",
        "results": results,
    }
    if not args.dry_run:
        summary["projected"] = GraphProjector(store).run_once()
    _print(summary)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from kc import doctor

    report = doctor.run(args.db, probe_network=not args.no_network, timeout=args.timeout)
    _print(report)
    return {"pass": 0, "warn": 0, "fail": 1}[report["verdict"]]


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
    if args.distribution:
        # The first week's distribution is a finding in its own right: if
        # mention_candidate dominates, the Resolver's thresholds are wrong
        # rather than the Reconciler being needed; if staleness dominates, the
        # TTLs are wrong. Both are cheaper than building a spoke.
        by_kind: dict[str, dict[str, int]] = {}
        for task in tasks:
            row = by_kind.setdefault(task["kind"], {})
            row[task["state"]] = row.get(task["state"], 0) + 1
            row["total"] = row.get("total", 0) + 1
        ranked = sorted(by_kind.items(), key=lambda kv: -kv[1]["total"])
        _print(
            {
                "total": len(tasks),
                "by_kind": dict(ranked),
                "dominant": ranked[0][0] if ranked else None,
                "reading": {
                    "mention_candidate": "Resolver thresholds are wrong, not a missing spoke",
                    "staleness": "TTL defaults are wrong (FR-14.1 re-baseline)",
                    "contradiction": "authority matrix needs per-property overrides (FR-6.13)",
                    "bad_answer": "retrieval shape, not knowledge coverage",
                },
            }
        )
        return 0
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
    tasks.add_argument("--distribution", action="store_true",
                       help="counts by kind and state, with how to read them")
    tasks.set_defaults(func=cmd_tasks)

    ingest = sub.add_parser("ingest", help="drive the fast path over a connector")
    ingest.add_argument("--connector", required=True, help="jira|confluence|git|ado")
    ingest.add_argument("--since", default=None, help="watermark passed to discover()")
    ingest.add_argument("--source-id", action="append", default=None,
                        help="ingest specific objects instead of calling discover()")
    ingest.add_argument("--from-file", action="append", default=None,
                        help="a saved API response (JSON) to ingest without wiring fetch")
    ingest.add_argument("--limit", type=int, default=50)
    ingest.add_argument("--dry-run", action="store_true",
                        help="report what would be written; append nothing")
    ingest.set_defaults(func=cmd_ingest)

    doctor = sub.add_parser("doctor", help="pre-flight checks for a locked-down machine")
    doctor.add_argument("--no-network", action="store_true",
                        help="skip connector reachability probes")
    doctor.add_argument("--timeout", type=float, default=2.0)
    doctor.set_defaults(func=cmd_doctor)

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
