"""The only module that contains SQL (kc/stores is the swap seam, §21).

SQLite is the MVP implementation; PostgreSQL is the target. The change log is
identical either way. The graph, indexes and alias registry are projections and
are rebuilt by replay, so swapping this module out costs no re-ingestion.

Two things here are not portable and are marked TODO(postgres):
  * entitlement filtering is expressed as SQL fragments (kc/entitlements) and
    must become row-level security policies, so the planner applies them
    (FR-5.4, §21);
  * traversal is a recursive CTE, which stays behind neighbours() so a graph
    database can replace it without touching the serve pipeline (FR-11.2).

BM25 is hand-rolled over term-frequency tables: no search engine in the
skeleton. Vector search is absent by design — the MVP embeds Confluence prose
only (FR-6.8), which is deferred with the connectors.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from kc.config import loader as cfg
from kc.core import ids
from kc.core.errors import IdCollision
from kc.core.model import Claim, Edge, Node, corroborated_confidence, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS change_log (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id    TEXT NOT NULL UNIQUE,
  op          TEXT NOT NULL,
  identity    TEXT NOT NULL,
  tier        TEXT,
  payload     TEXT NOT NULL,
  run_id      TEXT,
  context_id  TEXT,
  recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
  claim_id           TEXT PRIMARY KEY,
  seq                INTEGER NOT NULL,
  kind               TEXT NOT NULL,
  subject_id         TEXT NOT NULL,
  property           TEXT,
  value              TEXT,
  from_id            TEXT,
  to_id              TEXT,
  edge_type          TEXT,
  node_type          TEXT,
  subtype            TEXT,
  bu_owner           TEXT NOT NULL,
  provenance_class   TEXT NOT NULL,
  extraction_method  TEXT NOT NULL,
  source_system      TEXT NOT NULL,
  source_instance    TEXT NOT NULL,
  source_id          TEXT NOT NULL,
  source_key         TEXT NOT NULL,
  valid_from         TEXT NOT NULL,
  valid_to           TEXT,
  recorded_at        TEXT NOT NULL,
  confidence         REAL NOT NULL,
  last_verified_at   TEXT NOT NULL,
  extraction_version TEXT NOT NULL,
  evidence           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_subject ON claims (subject_id, property, valid_to);
CREATE INDEX IF NOT EXISTS claims_source  ON claims (source_key, valid_to);

CREATE TABLE IF NOT EXISTS nodes (
  id               TEXT PRIMARY KEY,
  type             TEXT NOT NULL,
  subtype          TEXT,
  bu_owner         TEXT NOT NULL,
  visibility_class TEXT NOT NULL,
  live             INTEGER NOT NULL DEFAULT 1,
  source_key       TEXT NOT NULL,
  props            TEXT NOT NULL DEFAULT '{}',
  name_norm        TEXT,
  status           TEXT
);
CREATE INDEX IF NOT EXISTS nodes_scope ON nodes (bu_owner, visibility_class, live);
CREATE INDEX IF NOT EXISTS nodes_name  ON nodes (name_norm);

CREATE TABLE IF NOT EXISTS edges (
  id               TEXT PRIMARY KEY,
  edge_type        TEXT NOT NULL,
  from_id          TEXT NOT NULL,
  to_id            TEXT NOT NULL,
  bu_owner         TEXT NOT NULL,
  visibility_class TEXT NOT NULL,
  claim_id         TEXT NOT NULL,
  confidence       REAL NOT NULL,
  provenance_class TEXT NOT NULL,
  valid_from       TEXT NOT NULL,
  valid_to         TEXT,
  source_systems   TEXT NOT NULL DEFAULT '[]',
  corroboration    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS edges_from ON edges (from_id, valid_to, edge_type);
CREATE INDEX IF NOT EXISTS edges_to   ON edges (to_id, valid_to, edge_type);

CREATE TABLE IF NOT EXISTS aliases (
  alias_key        TEXT NOT NULL,
  kind             TEXT NOT NULL,
  node_id          TEXT NOT NULL,
  bu_owner         TEXT NOT NULL,
  visibility_class TEXT NOT NULL,
  PRIMARY KEY (alias_key, kind, node_id)
);
CREATE INDEX IF NOT EXISTS aliases_key ON aliases (alias_key, bu_owner);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id     TEXT PRIMARY KEY,
  node_id      TEXT NOT NULL,
  bu_owner     TEXT NOT NULL,
  namespace    TEXT NOT NULL,
  kind         TEXT NOT NULL,
  section_path TEXT NOT NULL,
  content      TEXT NOT NULL,
  live         INTEGER NOT NULL DEFAULT 1,
  length       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_node ON chunks (node_id, live);

CREATE TABLE IF NOT EXISTS chunk_terms (
  chunk_id TEXT NOT NULL,
  term     TEXT NOT NULL,
  tf       INTEGER NOT NULL,
  PRIMARY KEY (chunk_id, term)
);
CREATE INDEX IF NOT EXISTS chunk_terms_term ON chunk_terms (term);

CREATE TABLE IF NOT EXISTS projector_state (
  projector  TEXT PRIMARY KEY,
  high_water INTEGER NOT NULL DEFAULT 0,
  halted     INTEGER NOT NULL DEFAULT 0,
  halt_seq   INTEGER,
  halt_error TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dead_letters (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  projector  TEXT NOT NULL,
  seq        INTEGER NOT NULL,
  error      TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id    TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  state      TEXT NOT NULL,
  scope      TEXT NOT NULL,
  payload    TEXT NOT NULL,
  reason     TEXT,
  context_id TEXT,
  run_id     TEXT,
  assignee   TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks (state, kind);

CREATE TABLE IF NOT EXISTS audit (
  request_id  TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  application TEXT NOT NULL,
  scope       TEXT NOT NULL,
  request     TEXT NOT NULL,
  seq         INTEGER NOT NULL,
  cache_epoch INTEGER NOT NULL,
  node_ids    TEXT NOT NULL,
  chunk_ids   TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS serve_cache (
  cache_key   TEXT PRIMARY KEY,
  cache_epoch INTEGER NOT NULL,
  response    TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skips (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_system TEXT NOT NULL,
  container   TEXT,
  source_id   TEXT,
  reason      TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_stats (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key  TEXT NOT NULL,
  hash        TEXT,
  llm_used    INTEGER NOT NULL DEFAULT 0,
  dropped     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_memory (
  memory_id  TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  agent      TEXT NOT NULL,
  task_id    TEXT,
  bus        TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_agent ON agent_memory (agent, kind);

CREATE TABLE IF NOT EXISTS source_hashes (
  source_key         TEXT PRIMARY KEY,
  hash               TEXT NOT NULL,
  extraction_version TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
"""

_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the to with this these".split()
)


def tokenize(text: str) -> list[str]:
    """Lexical tokens. Keeps dots and dashes so `ledger-api` and `v1.2` survive."""
    return [t for t in _WORD.findall(text.lower()) if t not in _STOPWORDS]


def normalise_name(name: str) -> str:
    """Name form used for alias lookup: case- and separator-insensitive."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _similarity(left: str, right: str) -> float:
    """Token-set Jaccard on normalised names. No model, no embedding: step 2 of
    the resolution cascade must cost nothing (FR-6.9)."""
    a = set(left.split("-"))
    b = set(right.split("-"))
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 4)


class Store:
    """Owns the connection and every statement. Callers pass a connection into
    write methods so a Change API append and its claim write share one
    transaction (FR-7.1)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    # --- connections ---------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def transaction(self, conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        """One transaction. Nested use with an existing connection is a no-op
        so the Change API can wrap several writes in the caller's transaction."""
        if conn is not None:
            yield conn
            return
        own = self.connect()
        try:
            own.execute("BEGIN IMMEDIATE")
            yield own
            own.execute("COMMIT")
        except BaseException:
            own.execute("ROLLBACK")
            raise
        finally:
            own.close()

    def init_schema(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO runtime (key, value) VALUES ('cache_epoch', '1')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO runtime (key, value) VALUES ('dry_run', ?)",
                (json.dumps(cfg.tier_policy()["dry_run_default"]),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO runtime (key, value) VALUES ('cognitive_layer', ?)",
                (json.dumps("on"),),
            )
        finally:
            conn.close()

    # --- runtime flags -------------------------------------------------------

    def runtime_get(self, key: str, default: Any = None) -> Any:
        conn = self.connect()
        try:
            row = conn.execute("SELECT value FROM runtime WHERE key=?", (key,)).fetchone()
        finally:
            conn.close()
        return json.loads(row["value"]) if row else default

    def runtime_set(self, key: str, value: Any) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO runtime (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def cache_epoch(self, conn: sqlite3.Connection | None = None) -> int:
        use = conn or self.connect()
        try:
            row = use.execute("SELECT value FROM runtime WHERE key='cache_epoch'").fetchone()
            return int(json.loads(row["value"])) if row else 1
        finally:
            if conn is None:
                use.close()

    def bump_cache_epoch(self, conn: sqlite3.Connection) -> int:
        """Bumped when a projector applies an event, not when the log is written
        (FR-11.6): a request between append and apply must not cache stale data."""
        epoch = self.cache_epoch(conn) + 1
        conn.execute(
            "INSERT INTO runtime (key, value) VALUES ('cache_epoch', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(epoch),),
        )
        return epoch

    # --- change log ----------------------------------------------------------

    def append_event(
        self,
        conn: sqlite3.Connection,
        *,
        op: str,
        identity: str,
        payload: dict[str, Any],
        tier: str | None = None,
        run_id: str | None = None,
        context_id: str | None = None,
    ) -> int:
        body = json.dumps(payload, sort_keys=True)
        event = ids.event_id(identity, op, ids.content_hash(body + now()))
        cur = conn.execute(
            "INSERT INTO change_log (event_id, op, identity, tier, payload, run_id, context_id, recorded_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (event, op, identity, tier, body, run_id, context_id, now()),
        )
        return int(cur.lastrowid)

    def read_events(self, after_seq: int = 0, limit: int = 1000) -> list[sqlite3.Row]:
        conn = self.connect()
        try:
            return conn.execute(
                "SELECT * FROM change_log WHERE seq > ? ORDER BY seq LIMIT ?",
                (after_seq, limit),
            ).fetchall()
        finally:
            conn.close()

    def last_seq(self) -> int:
        conn = self.connect()
        try:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM change_log").fetchone()
            return int(row["s"])
        finally:
            conn.close()

    def events_for_run(self, run_id: str) -> list[sqlite3.Row]:
        conn = self.connect()
        try:
            return conn.execute(
                "SELECT * FROM change_log WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall()
        finally:
            conn.close()

    # --- claims --------------------------------------------------------------

    def insert_claim(self, conn: sqlite3.Connection, claim: Claim, seq: int) -> None:
        row = claim.as_row()
        row["seq"] = seq
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT OR REPLACE INTO claims ({cols}) VALUES ({marks})", tuple(row.values())
        )

    def close_claim(self, conn: sqlite3.Connection, claim_id: str, valid_to: str) -> None:
        conn.execute(
            "UPDATE claims SET valid_to=? WHERE claim_id=? AND valid_to IS NULL",
            (valid_to, claim_id),
        )

    def get_claim(self, claim_id: str) -> Claim | None:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
            return Claim.from_row(row) if row else None
        finally:
            conn.close()

    def open_claims(
        self,
        subject_id: str,
        prop: str | None = None,
        kind: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> list[Claim]:
        sql = "SELECT * FROM claims WHERE subject_id=? AND valid_to IS NULL"
        args: list[Any] = [subject_id]
        if prop is not None:
            sql += " AND property=?"
            args.append(prop)
        if kind is not None:
            sql += " AND kind=?"
            args.append(kind)
        use = conn or self.connect()
        try:
            return [Claim.from_row(r) for r in use.execute(sql, args).fetchall()]
        finally:
            if conn is None:
                use.close()

    def open_claims_for_source(
        self, source_key: str, conn: sqlite3.Connection | None = None
    ) -> list[Claim]:
        """Every open claim derived from one source object: the input to the
        claim-set diff that closes what disappeared (FR-6.18)."""
        use = conn or self.connect()
        try:
            return [
                Claim.from_row(r)
                for r in use.execute(
                    "SELECT * FROM claims WHERE source_key=? AND valid_to IS NULL", (source_key,)
                ).fetchall()
            ]
        finally:
            if conn is None:
                use.close()

    def claim_counts_by_provenance(self) -> dict[str, int]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT provenance_class AS pc, COUNT(*) AS n FROM claims "
                "WHERE valid_to IS NULL GROUP BY provenance_class"
            ).fetchall()
            return {r["pc"]: int(r["n"]) for r in rows}
        finally:
            conn.close()

    # --- node / edge projection ---------------------------------------------

    def get_node(self, node_id: str, conn: sqlite3.Connection | None = None) -> Node | None:
        use = conn or self.connect()
        try:
            row = use.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        finally:
            if conn is None:
                use.close()
        return self._node(row) if row else None

    @staticmethod
    def _node(row: sqlite3.Row) -> Node:
        return Node(
            id=row["id"],
            type=row["type"],
            subtype=row["subtype"],
            bu_owner=row["bu_owner"],
            visibility_class=row["visibility_class"],
            live=bool(row["live"]),
            source_key=row["source_key"],
            props=json.loads(row["props"]),
        )

    def put_node(self, conn: sqlite3.Connection, node: Node) -> None:
        """Upsert a projected node. A different source key for an existing ID is
        a collision and never a silent merge (FR-4.10, FR-6.10)."""
        existing = conn.execute(
            "SELECT source_key FROM nodes WHERE id=?", (node.id,)
        ).fetchone()
        if existing and node.source_key and existing["source_key"] != node.source_key:
            raise IdCollision(
                f"node {node.id} already holds source_key {existing['source_key']}, "
                f"refusing {node.source_key}"
            )
        name = node.value("name")
        conn.execute(
            "INSERT INTO nodes (id, type, subtype, bu_owner, visibility_class, live, source_key,"
            " props, name_norm, status) VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET type=excluded.type, subtype=excluded.subtype,"
            " bu_owner=excluded.bu_owner, visibility_class=excluded.visibility_class,"
            " live=excluded.live, props=excluded.props, name_norm=excluded.name_norm,"
            " status=excluded.status",
            (
                node.id,
                node.type,
                node.subtype,
                node.bu_owner,
                node.visibility_class,
                int(node.live),
                node.source_key,
                json.dumps(node.props, sort_keys=True),
                normalise_name(str(name)) if name else None,
                node.value("status"),
            ),
        )

    def set_node_live(self, conn: sqlite3.Connection, node_id: str, live: bool) -> None:
        conn.execute("UPDATE nodes SET live=? WHERE id=?", (int(live), node_id))

    def set_node_visibility(
        self, conn: sqlite3.Connection, node_id: str, visibility_class: str
    ) -> None:
        conn.execute(
            "UPDATE nodes SET visibility_class=? WHERE id=?", (visibility_class, node_id)
        )

    def put_edge(self, conn: sqlite3.Connection, edge: Edge) -> None:
        conn.execute(
            "INSERT INTO edges (id, edge_type, from_id, to_id, bu_owner, visibility_class,"
            " claim_id, confidence, provenance_class, valid_from, valid_to, source_systems,"
            " corroboration) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET bu_owner=excluded.bu_owner,"
            " visibility_class=excluded.visibility_class, claim_id=excluded.claim_id,"
            " confidence=excluded.confidence, provenance_class=excluded.provenance_class,"
            " valid_from=excluded.valid_from, valid_to=excluded.valid_to,"
            " source_systems=excluded.source_systems, corroboration=excluded.corroboration",
            (
                edge.id,
                edge.edge_type,
                edge.from_id,
                edge.to_id,
                edge.bu_owner,
                edge.visibility_class,
                edge.claim_id,
                edge.confidence,
                edge.provenance_class,
                edge.valid_from,
                edge.valid_to,
                json.dumps(sorted(edge.source_systems)),
                edge.corroboration,
            ),
        )

    def fold_edge(self, conn: sqlite3.Connection, edge_id: str) -> None:
        """Recompute one edge from its open relationship claims (FR-4.7 item 5).

        Corroboration is the number of distinct source systems asserting it;
        confidence rises with that count and never reaches 1.0.
        """
        claims = [
            Claim.from_row(r)
            for r in conn.execute(
                "SELECT * FROM claims WHERE kind='relationship' AND subject_id=?", (edge_id,)
            ).fetchall()
        ]
        if not claims:
            return
        open_claims = [c for c in claims if c.is_open]
        template = max(open_claims or claims, key=lambda c: (c.authority(), c.valid_from))
        systems = sorted({c.source_system for c in open_claims})
        confidence = corroborated_confidence([c.confidence for c in open_claims], len(systems))
        from_node = self.get_node(str(template.from_id), conn)
        to_node = self.get_node(str(template.to_id), conn)
        visibility = "restricted"
        if from_node and to_node:
            order = {"public": 0, "topology": 1, "restricted": 2}
            visibility = max(
                (from_node.visibility_class, to_node.visibility_class), key=lambda v: order[v]
            )
        edge = Edge(
            id=edge_id,
            edge_type=str(template.edge_type),
            from_id=str(template.from_id),
            to_id=str(template.to_id),
            bu_owner=from_node.bu_owner if from_node else template.bu_owner,
            visibility_class=visibility,
            claim_id=template.claim_id,
            confidence=confidence,
            provenance_class=template.provenance_class,
            valid_from=min(c.valid_from for c in (open_claims or claims)),
            valid_to=None if open_claims else max(str(c.valid_to) for c in claims),
            source_systems=tuple(systems),
            corroboration=len(systems),
        )
        self.put_edge(conn, edge)

    def get_edge(self, edge_id: str, conn: sqlite3.Connection | None = None) -> Edge | None:
        use = conn or self.connect()
        try:
            row = use.execute("SELECT * FROM edges WHERE id=?", (edge_id,)).fetchone()
        finally:
            if conn is None:
                use.close()
        if not row:
            return None
        return Edge(
            id=row["id"],
            edge_type=row["edge_type"],
            from_id=row["from_id"],
            to_id=row["to_id"],
            bu_owner=row["bu_owner"],
            visibility_class=row["visibility_class"],
            claim_id=row["claim_id"],
            confidence=row["confidence"],
            provenance_class=row["provenance_class"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            source_systems=tuple(json.loads(row["source_systems"])),
            corroboration=int(row["corroboration"]),
        )

    def clear_projection(self, conn: sqlite3.Connection) -> None:
        """Truncate every projected store. Only rebuild() calls this (NFR-5)."""
        for table in ("nodes", "edges", "aliases", "chunks", "chunk_terms", "serve_cache"):
            conn.execute(f"DELETE FROM {table}")

    # --- alias registry ------------------------------------------------------

    def put_alias(
        self,
        conn: sqlite3.Connection,
        *,
        alias_key: str,
        node_id: str,
        bu_owner: str,
        visibility_class: str,
        kind: str = "name",
    ) -> None:
        """Alias entries carry BU and visibility so lookup can be scoped
        in-query: resolving before scoping is how B2 leaked."""
        conn.execute(
            "INSERT OR REPLACE INTO aliases (alias_key, kind, node_id, bu_owner, visibility_class)"
            " VALUES (?,?,?,?,?)",
            (alias_key, kind, node_id, bu_owner, visibility_class),
        )

    def resolve_alias(self, name: str, scope_sql: str, scope_args: Iterable[Any]) -> list[dict]:
        """Candidates for a name, filtered inside the query (FR-5.4, FR-11.3)."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT a.node_id, n.type, n.subtype, n.bu_owner, n.props, n.live"
                " FROM aliases a JOIN nodes n ON n.id = a.node_id"
                f" WHERE a.alias_key = ? AND n.live = 1 AND {scope_sql}",
                (normalise_name(name), *tuple(scope_args)),
            ).fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            props = json.loads(row["props"])
            name_entry = props.get("name")
            out.append(
                {
                    "id": row["node_id"],
                    "type": row["type"],
                    "subtype": row["subtype"],
                    "bu": row["bu_owner"],
                    "name": name_entry["value"] if isinstance(name_entry, dict) else None,
                }
            )
        return out

    # --- traversal -----------------------------------------------------------

    def neighbours(
        self,
        node_ids: list[str],
        *,
        near_sql: str,
        far_sql: str,
        args_near: Iterable[Any],
        args_far: Iterable[Any],
        as_of: str | None = None,
        fanout: int = 24,
    ) -> list[dict]:
        """One hop out of `node_ids`, entitlement- and currency-filtered in-query.

        The seam a graph database would replace (§21). The current-claim filter
        (`valid_to IS NULL`) is applied here, on every hop, and is indexed
        (FR-11.9, AT-41).
        """
        if not node_ids:
            return []
        marks = ",".join("?" for _ in node_ids)
        if as_of is None:
            edge_time = "e.valid_to IS NULL"
            time_args: tuple[Any, ...] = ()
        else:
            edge_time = "e.valid_from <= ? AND (e.valid_to IS NULL OR e.valid_to > ?)"
            time_args = (as_of, as_of)
        sql = (
            "SELECT e.id AS edge_id, e.edge_type, e.from_id, e.to_id, e.confidence,"
            " e.corroboration, e.source_systems, e.claim_id, e.provenance_class,"
            " e.visibility_class AS edge_visibility,"
            " far.id AS far_id, far.type AS far_type, far.subtype AS far_subtype,"
            " far.bu_owner AS far_bu, far.visibility_class AS far_visibility,"
            " far.props AS far_props, far.live AS far_live,"
            " CASE WHEN e.from_id IN (" + marks + ") THEN 'out' ELSE 'in' END AS direction"
            " FROM edges e"
            " JOIN nodes near ON near.id = CASE WHEN e.from_id IN (" + marks + ")"
            "      THEN e.from_id ELSE e.to_id END"
            " JOIN nodes far  ON far.id  = CASE WHEN e.from_id IN (" + marks + ")"
            "      THEN e.to_id ELSE e.from_id END"
            f" WHERE (e.from_id IN ({marks}) OR e.to_id IN ({marks}))"
            f" AND {edge_time} AND near.live = 1 AND far.live = 1"
            f" AND {near_sql} AND {far_sql}"
            " ORDER BY e.confidence DESC, e.edge_type LIMIT ?"
        )
        params: list[Any] = []
        params += node_ids * 3  # three CASE expressions
        params += node_ids * 2  # the WHERE membership tests
        params += list(time_args)
        params += list(args_near)
        params += list(args_far)
        params.append(fanout)
        conn = self.connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def nodes_by_ids(self, node_ids: list[str]) -> dict[str, Node]:
        if not node_ids:
            return {}
        marks = ",".join("?" for _ in node_ids)
        conn = self.connect()
        try:
            rows = conn.execute(f"SELECT * FROM nodes WHERE id IN ({marks})", node_ids).fetchall()
        finally:
            conn.close()
        return {r["id"]: self._node(r) for r in rows}

    def edges_between(self, node_ids: list[str]) -> list[dict]:
        """Live edges whose both endpoints are already in the result set."""
        if not node_ids:
            return []
        marks = ",".join("?" for _ in node_ids)
        conn = self.connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM edges WHERE valid_to IS NULL AND from_id IN ({marks})"
                f" AND to_id IN ({marks})",
                node_ids * 2,
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def has_incident_edge(self, node_id: str, edge_type: str, direction: str = "out") -> bool:
        """Does a live edge of this type touch this node? Gap codes are computed
        from this, not from the edges that happened to survive traversal."""
        column = "from_id" if direction == "out" else "to_id"
        conn = self.connect()
        try:
            row = conn.execute(
                f"SELECT 1 FROM edges WHERE {column}=? AND edge_type=? AND valid_to IS NULL"
                " LIMIT 1",
                (node_id, edge_type),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def nodes_by_type(self, node_type: str, live_only: bool = True) -> list[Node]:
        sql = "SELECT * FROM nodes WHERE type=?"
        if live_only:
            sql += " AND live=1"
        conn = self.connect()
        try:
            return [self._node(r) for r in conn.execute(sql, (node_type,)).fetchall()]
        finally:
            conn.close()

    def similar_nodes(self, name: str, node_type: str | None, bus: list[str]) -> list[dict]:
        """Name-similarity candidates, restricted to the event's BUs and commons
        (FR-6.9, DR9): a cross-BU link at ingest is a false merge in effect."""
        if not bus:
            return []
        bu_marks = ",".join("?" for _ in bus)
        sql = f"SELECT id, type, bu_owner, name_norm, props FROM nodes WHERE live=1 AND bu_owner IN ({bu_marks})"
        args: list[Any] = list(bus)
        if node_type:
            sql += " AND type=?"
            args.append(node_type)
        conn = self.connect()
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
        target = normalise_name(name)
        out = []
        for row in rows:
            other = row["name_norm"] or ""
            if not other:
                continue
            out.append({"id": row["id"], "type": row["type"], "bu": row["bu_owner"],
                        "name_norm": other, "similarity": _similarity(target, other)})
        return sorted(out, key=lambda c: -c["similarity"])

    # --- chunks and BM25 -----------------------------------------------------

    def put_chunk(
        self,
        conn: sqlite3.Connection,
        *,
        chunk_id: str,
        node_id: str,
        bu_owner: str,
        kind: str,
        section_path: str,
        content: str,
    ) -> None:
        namespace = bu_owner  # per-BU namespaces (FR-5.6), kept alongside filtering
        terms: dict[str, int] = {}
        for token in tokenize(content):
            terms[token] = terms.get(token, 0) + 1
        conn.execute(
            "INSERT OR REPLACE INTO chunks (chunk_id, node_id, bu_owner, namespace, kind,"
            " section_path, content, live, length) VALUES (?,?,?,?,?,?,?,1,?)",
            (chunk_id, node_id, bu_owner, namespace, kind, section_path, content,
             sum(terms.values())),
        )
        conn.execute("DELETE FROM chunk_terms WHERE chunk_id=?", (chunk_id,))
        conn.executemany(
            "INSERT INTO chunk_terms (chunk_id, term, tf) VALUES (?,?,?)",
            [(chunk_id, term, tf) for term, tf in terms.items()],
        )

    def retract_chunks_for_node(self, conn: sqlite3.Connection, node_id: str) -> None:
        conn.execute("UPDATE chunks SET live=0 WHERE node_id=?", (node_id,))

    def set_chunk_liveness_from_nodes(self, conn: sqlite3.Connection) -> None:
        """Index entries carry their node's liveness (FR-11.10, AT-39).

        Downgrade only. Syncing in both directions would revive a chunk that
        was explicitly retracted — which is how content restricted at source
        (FR-5.9) would come back — so liveness only ever goes to 0 here. A
        chunk becomes live again only by being re-added through chunk_add.
        """
        conn.execute(
            "UPDATE chunks SET live = 0 WHERE live = 1 AND node_id IN"
            " (SELECT id FROM nodes WHERE live = 0)"
        )
        conn.execute(
            "UPDATE chunks SET live = 0 WHERE live = 1 AND node_id NOT IN (SELECT id FROM nodes)"
        )

    def bm25_search(
        self, query: str, namespaces: list[str], limit: int | None = None
    ) -> list[dict]:
        """Hand-rolled BM25 over chunk_terms. Namespace filtering is in-query,
        so a caller never scores a chunk from a BU they cannot see (FR-5.6)."""
        terms = tokenize(query)
        if not terms or not namespaces:
            return []
        k1 = float(cfg.retrieval_setting("bm25", "k1"))
        b = float(cfg.retrieval_setting("bm25", "b"))
        top_k = limit or int(cfg.retrieval_setting("bm25", "top_k"))
        ns_marks = ",".join("?" for _ in namespaces)
        term_marks = ",".join("?" for _ in terms)
        conn = self.connect()
        try:
            stats = conn.execute(
                f"SELECT COUNT(*) AS n, COALESCE(AVG(length), 0) AS avg_len FROM chunks"
                f" WHERE live = 1 AND namespace IN ({ns_marks})",
                namespaces,
            ).fetchone()
            total, avg_len = int(stats["n"]), float(stats["avg_len"]) or 1.0
            if not total:
                return []
            df_rows = conn.execute(
                f"SELECT ct.term AS term, COUNT(*) AS df FROM chunk_terms ct"
                f" JOIN chunks c ON c.chunk_id = ct.chunk_id"
                f" WHERE c.live = 1 AND c.namespace IN ({ns_marks})"
                f" AND ct.term IN ({term_marks}) GROUP BY ct.term",
                (*namespaces, *terms),
            ).fetchall()
            df = {r["term"]: int(r["df"]) for r in df_rows}
            if not df:
                return []
            rows = conn.execute(
                f"SELECT ct.chunk_id, ct.term, ct.tf, c.length, c.node_id, c.kind,"
                f" c.section_path, c.content, c.bu_owner"
                f" FROM chunk_terms ct JOIN chunks c ON c.chunk_id = ct.chunk_id"
                f" WHERE c.live = 1 AND c.namespace IN ({ns_marks})"
                f" AND ct.term IN ({term_marks})",
                (*namespaces, *terms),
            ).fetchall()
        finally:
            conn.close()
        scores: dict[str, float] = {}
        meta: dict[str, dict] = {}
        for row in rows:
            idf = math.log(1 + (total - df[row["term"]] + 0.5) / (df[row["term"]] + 0.5))
            tf = float(row["tf"])
            norm = tf + k1 * (1 - b + b * float(row["length"]) / avg_len)
            scores[row["chunk_id"]] = scores.get(row["chunk_id"], 0.0) + idf * tf * (k1 + 1) / norm
            meta.setdefault(
                row["chunk_id"],
                {
                    "chunk_id": row["chunk_id"],
                    "node_id": row["node_id"],
                    "kind": row["kind"],
                    "section_path": row["section_path"],
                    "content": row["content"],
                    "bu_owner": row["bu_owner"],
                },
            )
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        return [dict(meta[cid], score=round(score, 4)) for cid, score in ranked]

    def chunks_for_nodes(self, node_ids: list[str], namespaces: list[str]) -> list[dict]:
        """Targeted fetch by node ID (FR-11.2 step 8), liveness filtered in-query."""
        if not node_ids or not namespaces:
            return []
        id_marks = ",".join("?" for _ in node_ids)
        ns_marks = ",".join("?" for _ in namespaces)
        conn = self.connect()
        try:
            rows = conn.execute(
                f"SELECT chunk_id, node_id, kind, section_path, content, bu_owner FROM chunks"
                f" WHERE live = 1 AND node_id IN ({id_marks}) AND namespace IN ({ns_marks})"
                f" ORDER BY node_id, section_path",
                (*node_ids, *namespaces),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r, score=0.0) for r in rows]

    # --- projector state -----------------------------------------------------

    def projector_state(self, projector: str) -> dict[str, Any]:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM projector_state WHERE projector=?", (projector,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"projector": projector, "high_water": 0, "halted": False,
                    "halt_seq": None, "halt_error": None}
        return {
            "projector": projector,
            "high_water": int(row["high_water"]),
            "halted": bool(row["halted"]),
            "halt_seq": row["halt_seq"],
            "halt_error": row["halt_error"],
        }

    def set_high_water(self, conn: sqlite3.Connection, projector: str, seq: int) -> None:
        conn.execute(
            "INSERT INTO projector_state (projector, high_water, halted, updated_at)"
            " VALUES (?,?,0,?) ON CONFLICT(projector) DO UPDATE SET"
            " high_water=excluded.high_water, updated_at=excluded.updated_at",
            (projector, seq, now()),
        )

    def halt_projector(self, projector: str, seq: int, error: str) -> None:
        """Halt and dead-letter. Never skip: a skipped event diverges silently
        (FR-7.8, AT-27)."""
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO projector_state (projector, high_water, halted, halt_seq, halt_error,"
                " updated_at) VALUES (?, COALESCE((SELECT high_water FROM projector_state"
                " WHERE projector=?), 0), 1, ?, ?, ?)"
                " ON CONFLICT(projector) DO UPDATE SET halted=1, halt_seq=excluded.halt_seq,"
                " halt_error=excluded.halt_error, updated_at=excluded.updated_at",
                (projector, projector, seq, error, now()),
            )
            conn.execute(
                "INSERT INTO dead_letters (projector, seq, error, created_at) VALUES (?,?,?,?)",
                (projector, seq, error, now()),
            )

    def resume_projector(self, projector: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE projector_state SET halted=0, halt_seq=NULL, halt_error=NULL,"
                " updated_at=? WHERE projector=?",
                (now(), projector),
            )

    def dead_letters(self) -> list[dict]:
        conn = self.connect()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM dead_letters ORDER BY id DESC LIMIT 50"
            ).fetchall()]
        finally:
            conn.close()

    # --- tasks (human queue) -------------------------------------------------

    def put_task(
        self,
        *,
        task_id: str,
        kind: str,
        state: str,
        scope: list[str],
        payload: dict,
        reason: str | None = None,
        context_id: str | None = None,
        run_id: str | None = None,
        assignee: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        with self.transaction(conn) as use:
            use.execute(
                "INSERT INTO tasks (task_id, kind, state, scope, payload, reason, context_id,"
                " run_id, assignee, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(task_id) DO UPDATE SET state=excluded.state,"
                " payload=excluded.payload, reason=excluded.reason, assignee=excluded.assignee,"
                " updated_at=excluded.updated_at",
                (task_id, kind, state, json.dumps(scope), json.dumps(payload), reason,
                 context_id, run_id, assignee, now(), now()),
            )

    def tasks(self, state: str | None = None, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM tasks"
        args: list[Any] = []
        clauses = []
        if state:
            clauses.append("state=?")
            args.append(state)
        if kind:
            clauses.append("kind=?")
            args.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at"
        conn = self.connect()
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            item = dict(row)
            item["scope"] = json.loads(item["scope"])
            item["payload"] = json.loads(item["payload"])
            out.append(item)
        return out

    def get_task(self, task_id: str) -> dict | None:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        item = dict(row)
        item["scope"] = json.loads(item["scope"])
        item["payload"] = json.loads(item["payload"])
        return item

    # --- audit, cache, ingest bookkeeping ------------------------------------

    def put_audit(self, record: dict) -> None:
        """No exclusion data: what a caller could not see is derived offline by
        replay at `seq` (FR-15.2, §20 DR7)."""
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO audit (request_id, user_id, application, scope, request,"
                " seq, cache_epoch, node_ids, chunk_ids, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record["request_id"],
                    record["user_id"],
                    record["application"],
                    json.dumps(record["scope"]),
                    json.dumps(record["request"]),
                    record["seq"],
                    record["cache_epoch"],
                    json.dumps(record["node_ids"]),
                    json.dumps(record["chunk_ids"]),
                    now(),
                ),
            )

    def audit_record(self, request_id: str) -> dict | None:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM audit WHERE request_id=?", (request_id,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def cache_get(self, cache_key: str, epoch: int) -> dict | None:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT response FROM serve_cache WHERE cache_key=? AND cache_epoch=?",
                (cache_key, epoch),
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row["response"]) if row else None

    def cache_put(self, cache_key: str, epoch: int, response: dict) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO serve_cache (cache_key, cache_epoch, response, created_at)"
                " VALUES (?,?,?,?)",
                (cache_key, epoch, json.dumps(response), now()),
            )

    def record_skip(
        self, source_system: str, container: str | None, source_id: str | None, reason: str
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO skips (source_system, container, source_id, reason, created_at)"
                " VALUES (?,?,?,?,?)",
                (source_system, container, source_id, reason, now()),
            )

    def skip_counts(self) -> dict[str, int]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT reason, COUNT(*) AS n FROM skips GROUP BY reason"
            ).fetchall()
            return {r["reason"]: int(r["n"]) for r in rows}
        finally:
            conn.close()

    def record_ingest(self, source_key: str, hash_value: str | None, llm_used: bool,
                      dropped: bool) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO ingest_stats (source_key, hash, llm_used, dropped, created_at)"
                " VALUES (?,?,?,?,?)",
                (source_key, hash_value, int(llm_used), int(dropped), now()),
            )

    def ingest_stats(self) -> dict[str, int]:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS total, SUM(llm_used) AS llm, SUM(dropped) AS dropped"
                " FROM ingest_stats"
            ).fetchone()
        finally:
            conn.close()
        return {
            "total": int(row["total"] or 0),
            "llm": int(row["llm"] or 0),
            "dropped": int(row["dropped"] or 0),
        }

    def source_hash(self, source_key: str) -> dict | None:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM source_hashes WHERE source_key=?", (source_key,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def put_source_hash(self, source_key: str, hash_value: str, extraction_version: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO source_hashes (source_key, hash, extraction_version, updated_at)"
                " VALUES (?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET hash=excluded.hash,"
                " extraction_version=excluded.extraction_version, updated_at=excluded.updated_at",
                (source_key, hash_value, extraction_version, now()),
            )


    # --- agent memory (FR-12.1, FR-12.6) ------------------------------------
    # Physically separate from the knowledge graph in deployment: on PostgreSQL
    # this table belongs in its own schema with its own grants, so agent
    # beliefs can never be read back as enterprise facts. Recall is filtered by
    # the recalling task's scope, so a BU-A task cannot see BU-B evidence.

    def put_memory(
        self, *, memory_id: str, kind: str, agent: str, task_id: str | None,
        bus: list[str], body: dict
    ) -> None:
        if not agent or agent in {"", "anonymous", "unknown"}:
            raise ValueError("episodic memory needs a resolved agent identity (FR-12.3)")
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_memory (memory_id, kind, agent, task_id, bus, body,"
                " created_at) VALUES (?,?,?,?,?,?,?)",
                (memory_id, kind, agent, task_id, json.dumps(sorted(bus)),
                 json.dumps(body), now()),
            )

    def recall_memory(self, *, agent: str, task_scope: list[str], kind: str | None = None,
                      limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM agent_memory WHERE agent=?"
        args: list[Any] = [agent]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit * 5)
        conn = self.connect()
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
        scope = set(task_scope)
        out = []
        for row in rows:
            bus = set(json.loads(row["bus"]))
            if bus and not bus <= scope:
                continue  # outside the task scope (FR-12.6, AT-57)
            out.append({"memory_id": row["memory_id"], "kind": row["kind"],
                        "agent": row["agent"], "bus": sorted(bus),
                        "body": json.loads(row["body"]), "created_at": row["created_at"]})
            if len(out) >= limit:
                break
        return out

    # --- health counts -------------------------------------------------------

    def counts(self) -> dict[str, int]:
        conn = self.connect()
        try:
            def one(sql: str) -> int:
                return int(conn.execute(sql).fetchone()[0])

            return {
                "events": one("SELECT COUNT(*) FROM change_log"),
                "claims_open": one("SELECT COUNT(*) FROM claims WHERE valid_to IS NULL"),
                "claims_total": one("SELECT COUNT(*) FROM claims"),
                "nodes_live": one("SELECT COUNT(*) FROM nodes WHERE live=1"),
                "edges_live": one("SELECT COUNT(*) FROM edges WHERE valid_to IS NULL"),
                "chunks_live": one("SELECT COUNT(*) FROM chunks WHERE live=1"),
                "tasks_open": one("SELECT COUNT(*) FROM tasks WHERE state!='closed'"),
                "aliases": one("SELECT COUNT(*) FROM aliases"),
            }
        finally:
            conn.close()

    def services_with_owner_and_deps(self) -> tuple[int, int]:
        conn = self.connect()
        try:
            total = int(
                conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Service' AND live=1")
                .fetchone()[0]
            )
            covered = int(
                conn.execute(
                    "SELECT COUNT(*) FROM nodes n WHERE n.type='Service' AND n.live=1"
                    " AND EXISTS (SELECT 1 FROM edges e WHERE e.from_id=n.id"
                    "   AND e.edge_type='OWNED_BY' AND e.valid_to IS NULL)"
                    " AND EXISTS (SELECT 1 FROM edges e WHERE e.from_id=n.id"
                    "   AND e.edge_type IN ('DEPENDS_ON','CALLS') AND e.valid_to IS NULL)"
                ).fetchone()[0]
            )
            return covered, total
        finally:
            conn.close()

    def graph_fingerprint(self) -> str:
        """Digest of the whole projection: rebuild must reproduce it exactly
        (NFR-5, AT-28)."""
        conn = self.connect()
        try:
            parts = []
            for row in conn.execute(
                "SELECT id, type, subtype, bu_owner, visibility_class, live, props"
                " FROM nodes ORDER BY id"
            ):
                parts.append("|".join(str(row[k]) for k in row.keys()))
            for row in conn.execute(
                "SELECT id, edge_type, from_id, to_id, bu_owner, visibility_class, confidence,"
                " valid_to, source_systems, corroboration FROM edges ORDER BY id"
            ):
                parts.append("|".join(str(row[k]) for k in row.keys()))
            for row in conn.execute(
                "SELECT alias_key, kind, node_id FROM aliases ORDER BY alias_key, kind, node_id"
            ):
                parts.append("|".join(str(row[k]) for k in row.keys()))
        finally:
            conn.close()
        return ids.content_hash("\n".join(parts))
