"""Pre-flight checks for a locked-down machine.

Everything here is a thing that has gone wrong on an enterprise laptop and
produced a confusing failure later: a database file on a path the process
cannot write, WAL silently unavailable on a network share, a clock that has
drifted or moved backwards, a config file edited into invalid JSON, a proxy
that swallows connections so a connector looks hung rather than blocked.

Each check returns pass, warn or fail with a one-line remedy. `fail` means the
skeleton will not work as-is; `warn` means it works but something needs
attention before real ingestion.

The skeleton needs no network, and a connector base that is unreachable is a
warning, not a failure — that is the deferred-ingestion design.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kc.config import loader as cfg
from kc.connectors import registry
from kc.core.projector import GRAPH
from kc.stores.sqlite_store import Store

PASS, WARN, FAIL = "pass", "warn", "fail"

# Third-party imports are a deployment risk here: pip may not reach an index.
STDLIB = set(sys.stdlib_module_names)
BUILD_FLOOR = datetime(2026, 1, 1, tzinfo=UTC)


def _result(name: str, status: str, detail: str, remedy: str = "") -> dict[str, Any]:
    return {"check": name, "status": status, "detail": detail, "remedy": remedy}


def run(db_path: str, *, probe_network: bool = True, timeout: float = 2.0) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_python())
    checks.append(_dependencies())
    checks.extend(_filesystem(db_path))
    checks.extend(_sqlite(db_path))
    checks.append(_config_parses())
    checks.extend(_config_sanity())
    checks.extend(_clock(db_path))
    checks.append(_projector(db_path))
    checks.extend(_connectors(probe_network=probe_network, timeout=timeout))
    checks.append(_offline_extraction())

    worst = FAIL if any(c["status"] == FAIL for c in checks) else (
        WARN if any(c["status"] == WARN for c in checks) else PASS
    )
    return {
        "verdict": worst,
        "counts": {
            status: sum(1 for c in checks if c["status"] == status)
            for status in (PASS, WARN, FAIL)
        },
        "checks": checks,
    }


# --- runtime -----------------------------------------------------------------


def _python() -> dict[str, Any]:
    major, minor = sys.version_info[:2]
    detail = f"{platform.python_version()} on {platform.system()} {platform.release()}"
    if (major, minor) < (3, 12):
        return _result("python version", FAIL, detail, "Python 3.12 or newer is required")
    return _result("python version", PASS, detail)


def _dependencies() -> dict[str, Any]:
    """The skeleton must run with nothing installed.

    This reads what the code imports rather than what the process happens to
    have loaded: under a test runner the interpreter is full of other people's
    packages, and a check that measured `sys.modules` would report them.
    """
    import ast

    package_root = Path(__file__).parent
    external: dict[str, str] = {}
    for source in sorted(package_root.rglob("*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            return _result("dependencies", FAIL, f"{source.name}: {exc}",
                           "the file does not parse; the package will not import")
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root not in STDLIB and root != "kc":
                    external.setdefault(root, source.relative_to(package_root).as_posix())
    if external:
        detail = ", ".join(f"{name} ({where})" for name, where in sorted(external.items()))
        return _result(
            "dependencies", FAIL, f"non-stdlib imports: {detail}",
            "the skeleton must run with nothing installed; remove the import",
        )
    return _result(
        "dependencies", PASS,
        f"standard library only across {len(list(package_root.rglob('*.py')))} modules",
    )


# --- filesystem and SQLite ---------------------------------------------------


def _filesystem(db_path: str) -> list[dict[str, Any]]:
    out = []
    target = Path(db_path).resolve()
    parent = target.parent
    if not parent.exists():
        out.append(_result("database directory", FAIL, f"{parent} does not exist",
                           "create it or pass --db elsewhere"))
        return out
    probe = parent / f".kc-write-probe-{os.getpid()}"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        out.append(_result("database directory writable", PASS, str(parent)))
    except OSError as exc:
        out.append(_result("database directory writable", FAIL, f"{parent}: {exc}",
                           "choose a writable path, e.g. --db %LOCALAPPDATA%\\kc\\kc.db"))
    try:
        with tempfile.NamedTemporaryFile(prefix="kc-doctor-", delete=True):
            pass
        out.append(_result("temp directory writable", PASS, tempfile.gettempdir()))
    except OSError as exc:
        out.append(_result("temp directory writable", WARN, str(exc),
                           "set TMPDIR/TEMP to a writable location"))
    if target.exists():
        out.append(_result("database file", PASS, f"{target} ({target.stat().st_size} bytes)"))
    else:
        out.append(_result("database file", WARN, f"{target} does not exist yet",
                           "run: python -m kc.cli init"))
    return out


def _sqlite(db_path: str) -> list[dict[str, Any]]:
    out = [_result("sqlite version", PASS, sqlite3.sqlite_version)]
    store = Store(db_path)
    try:
        conn = store.connect()
    except sqlite3.Error as exc:
        out.append(_result("sqlite connection", FAIL, str(exc),
                           "check the path and file permissions"))
        return out
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() == "wal":
            out.append(_result("write-ahead log", PASS, f"journal_mode={mode}"))
        else:
            out.append(_result(
                "write-ahead log", WARN, f"journal_mode={mode}",
                "WAL is unavailable on some network shares; keep the database on a local disk",
            ))
        conn.execute("CREATE TABLE IF NOT EXISTS kc_doctor_probe (x INTEGER)")
        conn.execute("INSERT INTO kc_doctor_probe (x) VALUES (1)")
        value = conn.execute("SELECT MAX(x) FROM kc_doctor_probe").fetchone()[0]
        conn.execute("DROP TABLE kc_doctor_probe")
        out.append(
            _result("read and write", PASS if value == 1 else FAIL, f"probe returned {value}")
        )
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        out.append(_result("busy timeout", PASS, f"{timeout} ms"))
    except sqlite3.Error as exc:
        out.append(_result("read and write", FAIL, str(exc),
                           "the file may be read-only or locked by another process"))
    finally:
        conn.close()
    return out


# --- config ------------------------------------------------------------------


def _config_parses() -> dict[str, Any]:
    cfg.reload()
    broken = []
    for name in ("schema", "identities", "retrieval", "entitlements"):
        path = Path(cfg.CONFIG_DIR) / f"{name}.json"
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            broken.append(f"{name}.json: {exc}")
    if broken:
        return _result("config files parse", FAIL, "; ".join(broken),
                       "fix the JSON; the validator reads these at every write")
    return _result("config files parse", PASS, "schema, identities, retrieval, entitlements")


def _config_sanity() -> list[dict[str, Any]]:
    out = []
    missing = [
        node_type for node_type in cfg.node_types()
        if not cfg.default_visibility(node_type) or not cfg.ttl_days(node_type)
    ]
    out.append(
        _result("visibility and TTL defaults", FAIL if missing else PASS,
                f"missing for {missing}" if missing else f"{len(cfg.node_types())} node types",
                "every node type needs a default (FR-5.2, FR-14.1)" if missing else "")
    )

    declared = set(cfg.known_operations())
    undeclared: dict[str, list[str]] = {}
    for identity in ("kc-ingest", "kc-curator", "kc-maintenance", "kc-review", "kc-operator"):
        extra = [op for op in declared if cfg.may(identity, op)]
        if not extra:
            undeclared[identity] = ["no permitted operations"]
    out.append(
        _result("write identities", FAIL if undeclared else PASS,
                str(undeclared) if undeclared else "five identities with operations",
                "check identities.json" if undeclared else "")
    )

    bad_edges = [
        edge for edge in cfg.edge_types()
        if not any(
            cfg.edge_permitted(edge, a, b)
            for a in cfg.node_types() for b in cfg.node_types()
        )
    ]
    out.append(
        _result("edge domain and range", FAIL if bad_edges else PASS,
                f"unsatisfiable: {bad_edges}" if bad_edges else f"{len(cfg.edge_types())} edges",
                "an edge with no valid node pair can never be written" if bad_edges else "")
    )

    unmapped = [
        system for system in ("jira", "confluence", "git")
        if not any(
            cfg.bu_for_source(system, container)
            for container in _containers(system)
        )
    ]
    out.append(
        _result("BU mappings", WARN if unmapped else PASS,
                f"no containers mapped for {unmapped}" if unmapped
                else "jira, confluence and git containers mapped",
                "add project keys, spaces and repos to entitlements.json before ingesting"
                if unmapped else "")
    )
    return out


def _containers(system: str) -> list[str]:
    table = json.loads((Path(cfg.CONFIG_DIR) / "entitlements.json").read_text(encoding="utf-8"))
    return list(table["source_containers"].get(system, {}))


# --- clock -------------------------------------------------------------------


def _clock(db_path: str) -> list[dict[str, Any]]:
    out = []
    now = datetime.now(UTC)
    if now.tzinfo is None:
        out.append(_result("clock timezone", FAIL, "naive datetime",
                           "the code requires timezone-aware UTC timestamps"))
    else:
        out.append(_result("clock timezone", PASS, f"UTC now = {now.isoformat(timespec='seconds')}"))
    if now < BUILD_FLOOR:
        out.append(_result(
            "clock plausible", FAIL, f"system clock reads {now.date()}",
            "a clock behind the build date breaks valid_from ordering; fix time sync",
        ))
    else:
        out.append(_result("clock plausible", PASS, str(now.date())))

    store = Store(db_path)
    try:
        events = store.read_events(max(0, store.last_seq() - 5), limit=5)
    except sqlite3.Error:
        return out
    if events:
        latest = max(str(e["recorded_at"]) for e in events)
        if latest > now.isoformat():
            out.append(_result(
                "clock moved backwards", FAIL,
                f"newest log entry {latest} is in the future",
                "the clock went back; new claims would sort before existing ones",
            ))
        else:
            out.append(_result("clock versus log", PASS, f"newest log entry {latest}"))
        ordered = [str(e["recorded_at"]) for e in events]
        if ordered != sorted(ordered):
            out.append(_result(
                "log timestamp order", WARN, "recorded_at is not monotonic with seq",
                "sequence order still governs replay, but investigate the clock",
            ))
    return out


def _projector(db_path: str) -> dict[str, Any]:
    store = Store(db_path)
    try:
        state = store.projector_state(GRAPH)
        lag = store.last_seq() - int(state["high_water"])
    except sqlite3.Error as exc:
        return _result("projector", WARN, str(exc), "run: python -m kc.cli init")
    if state["halted"]:
        return _result("projector", FAIL,
                       f"halted at {state['halt_seq']}: {state['halt_error']}",
                       "inspect the dead letter, fix the cause, then rebuild")
    if lag > 0:
        return _result("projector", WARN, f"{lag} events not yet applied",
                       "run: python -m kc.cli project")
    return _result("projector", PASS, f"up to date at seq {state['high_water']}")


# --- connectors --------------------------------------------------------------


def _connectors(*, probe_network: bool, timeout: float) -> list[dict[str, Any]]:
    out = []
    for name, connector in sorted(registry.all_connectors().items()):
        env = connector.api_base_env
        base = os.environ.get(env, "") if env else ""
        if not base:
            out.append(_result(
                f"connector {name}", WARN, f"{env or 'api base'} is unset",
                f"set {env} when you wire {name}; the skeleton itself needs no network",
            ))
            continue
        if not probe_network:
            out.append(_result(f"connector {name}", PASS, f"{env}={base} (not probed)"))
            continue
        out.append(_reachable(name, base, timeout))
    return out


def _reachable(name: str, base: str, timeout: float) -> dict[str, Any]:
    parsed = urlparse(base if "//" in base else f"https://{base}")
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return _result(f"connector {name}", FAIL, f"cannot parse {base}",
                       "use a full URL, e.g. https://company.atlassian.net")
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return _result(f"connector {name}", PASS, f"{host}:{port} reachable")
    except socket.gaierror as exc:
        return _result(f"connector {name}", WARN, f"{host} does not resolve ({exc.strerror})",
                       "on a locked-down machine this usually means DNS or a proxy; "
                       "set HTTPS_PROXY or use the internal hostname")
    except TimeoutError:
        return _result(f"connector {name}", WARN, f"{host}:{port} timed out after {timeout}s",
                       "likely a firewall dropping the connection rather than refusing it")
    except OSError as exc:
        return _result(f"connector {name}", WARN, f"{host}:{port} unreachable: {exc}",
                       "check VPN, proxy and whether the host is internal-only")


def _offline_extraction() -> dict[str, Any]:
    """Prove the parsers work with no credentials, using each connector's own
    self-check samples. This is what makes a dry run meaningful offline."""
    results = {}
    for name, connector in sorted(registry.all_connectors().items()):
        try:
            samples = connector.self_check_samples()
        except NotImplementedError:
            results[name] = "no samples declared"
            continue
        claims = 0
        for sample in samples:
            extraction = (
                connector.extract(sample, "commons", client=None)
                if name == "confluence"
                else connector.extract(sample, "commons")
            )
            claims += len(extraction.claims)
        results[name] = f"{len(samples)} samples, {claims} claims"
    broken = [name for name, value in results.items() if "no samples" in value or " 0 claims" in value]
    return _result(
        "offline extraction", FAIL if broken else PASS, json.dumps(results),
        "a connector whose parser produces nothing offline cannot be dry-run" if broken else "",
    )
