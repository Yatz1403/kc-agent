# KC Agent — structural skeleton

Implementation skeleton for the Enterprise Knowledge Context Agent, built
against `docs/kc-agent-frs.md`. Capability is in place; ingestion is deferred
to the enterprise laptop, where the credentials and the network are.

**Standard library only.** No graph database driver, no search engine, no web
framework, no model client. SQLite for all five stores, `http.server` for the
API, hand-rolled BM25. It runs the moment you copy it across.

---

## Copy to the laptop

```bash
# 1. copy the folder, then, inside it:
python --version                  # 3.12 or newer
python -m kc.cli init             # create kc.db
python -m kc.cli fixture          # load the synthetic BU-A / BU-B graph
python -m kc.cli query --identity alice@bu-a --intent impact_analysis \
    --anchor ledger-api --summary
python -m kc.cli query --identity bob@bu-b --intent impact_analysis \
    --anchor ledger-api --summary
python -m kc.cli health
python -m pytest -q               # 104 tests, no network
```

No install step and no virtual environment are required: nothing is imported
that is not in the standard library. `pip install -e ".[dev]"` only adds pytest.

The same two queries over HTTP:

```bash
python -m kc.cli serve &
curl -s localhost:8080/.well-known/agent-card.json
curl -s -X POST localhost:8080/okf/kc.context.retrieve \
  -H 'X-KC-User: alice@bu-a' -H 'X-KC-App: api-testing-solution' \
  -d '{"okf_version":"0.2","message_type":"context_request","request_id":"r1",
       "intent":"impact_analysis","anchors":[{"name":"ledger-api"}]}'
```

### CLI

| Command | What it does |
|---|---|
| `init` | Create the schema. Dry-run defaults to on (FR-7.7) |
| `fixture` | Load BU-A, BU-B and commons, including a corroborated cross-BU edge |
| `query` | One OKF `context_request`; `--summary` for a digest, otherwise the full envelope |
| `health` | Metrics, guardrails, connector wiring, agent declarations, budget allocation |
| `tasks` | The human queue: escalations, approvals, mention candidates |
| `work` | Curator dispatches open tasks to their spoke |
| `project` | Apply pending change-log events to the projections |
| `rebuild` | Rebuild every projection from the log and compare fingerprints (AT-28) |
| `flags` | Dry-run and the kill switch |
| `serve` | HTTP endpoint for the two external skills |

---

## Where to plug in your connector

Every connector is a **documented seam**, not a stub that returns nothing. Three
methods raise `NotImplementedError` with a `TODO(office)` naming the exact API
call; everything downstream of them is implemented and tested.

| File | Wire these | Already working |
|---|---|---|
| `kc/connectors/jira.py` | `discover` (JQL search), `fetch` (issue + changelog), `object_permissions_narrower_than_bu` (issue security level) | `hash_projection` with the real volatile-field list, `extract` over structured fields, issue-link `VALIDATES` |
| `kc/connectors/confluence.py` | `discover` (CQL), `fetch` (body.storage + labels + restrictions), permission check | chunking by heading, Document claims, mention extraction, the grounded-LLM path |
| `kc/connectors/git_openapi.py` | `discover` (tree listing), `fetch` (contents API) | **all parsers**: OpenAPI, `package.json`, `requirements.txt`, pytest → TestCase/COVERS/VALIDATES |
| `kc/connectors/ado.py` | all three, plus an `ado` entry in `entitlements.json` | work-item type map, hash projection |

```python
# kc/connectors/jira.py
def fetch(self, source_id: str) -> SourceObject:
    issue = self._client.get(f"/rest/api/3/issue/{source_id}?expand=changelog,names")
    return SourceObject(
        source_system="jira",
        source_instance=self.instance,
        source_id=source_id,
        container=source_id.split("-")[0],   # project key -> BU via entitlements.json
        content=issue,
        raw=json.dumps(issue),
    )
```

Then drive it:

```python
from kc.connectors.base import SourceObject
from kc.ingest import FastPath
from kc.stores.sqlite_store import Store

fast = FastPath(Store("kc.db"))
fast.ingest_object(connector.fetch("LED-101"))    # hash diff, extract, resolve, append
```

Two rules the seams enforce for you:

* **An unmapped container is quarantined**, never given a default BU. Add the
  project, space or repo to `kc/config/entitlements.json` first (FR-5.3).
* **Confluence may not create anchor nodes.** `creates_nodes = False` and
  `creatable_types = ("Document",)`; an unresolved mention becomes a Resolver
  candidate in the task queue rather than a new Service (FR-6.15).

### Plugging in a model

`kc/agents/llm.py` is the only seam, and it defaults to `None`.

```python
from kc.agents import llm
llm.set_default_client(MyGatewayClient(...))   # needs one method: complete_json
```

`None` means an agent **escalates instead of guessing** — its terminal state
becomes `escalate` with reason `NO_LLM_CLIENT`, and the case lands in the human
queue. Nothing in `kc/serve/` imports this module, and a test asserts that
(FR-11.1, AT-16).

---

## What is real and what is declared

**Real, with tests:**

- **Change API** — identity check, tier gate, schema validation, log append and
  claim write in one transaction. ID collisions raise instead of merging.
- **Projector framework** — sequence numbers, per-projector high-water marks,
  dead-letter and halt on failure (never skip), `rebuild()` from the log alone,
  cache epoch bumped when an event is *applied*.
- **Graph projection** — property folding to the highest-authority open claim
  with a `contested` flag; one edge per `(from, to, type)` carrying
  `source_systems` and a corroboration count, with confidence rising as
  independent sources agree.
- **Entitlements** — scope derived before anything else, fail closed, all
  filtering as in-query SQL fragments. There is deliberately **no** post-filter
  helper. Edge rule: near endpoint visible AND far endpoint at least topology.
- **Serve path** — scoped alias resolution, budgeted traversal with
  `valid_to IS NULL` in-query, BM25 plus targeted fetch, RRF fusion, liveness
  drop, trim by hop then score, structured gap codes, audit record with no
  exclusion data.
- **OKF** — all eleven messages in one module, versioned together as `0.2`.
- **Resolver** — a real bounded loop: alias → similarity → fingerprint → live
  source → escalate. Confidence comes from evidence features only.
- **Gate** — deterministic code; the inbreeding guard is enforced here, not in
  a read tool.
- **Curator** — deterministic triage routing, budget allocation weighted by the
  weakest normalised health metric.
- **Fast path** — hash diff over a normalised projection, claim-set diff that
  closes what a source dropped, quarantine and skip paths.

**Declared, and wired into the Curator and the human queue from day one:**
Reconciler, Verifier, Prospector, Retrieval Critic and Schema Steward each carry
their real goal, tool surface, write scope and model tier, and a loop that
terminates as `escalate`. `python -m kc.cli work` routes to them and the
escalation lands in `tasks`. That is the point: the queue tells you the real
distribution of hard cases before anyone builds an agent for the wrong ones.

**Absent by design:** any LLM in the serve path, including as a reranker; a
vector index (the MVP embeds Confluence prose only, which arrives with that
connector); A2A streaming.

---

## Layout

```
kc/config/       schema.json, identities.json, retrieval.json, entitlements.json
                 loader.py — the only reader of those files
kc/core/         ids, model (claims), errors, changeapi, projector
kc/stores/       sqlite_store.py — the only SQL in the codebase
kc/entitlements/ filter.py — scope and in-query fragments
kc/resolve/      cascade.py, fingerprint.py
kc/serve/        okf.py, pipeline.py
kc/agents/       base, tools, gate, curator, resolver, stubs, llm
kc/connectors/   base, registry, jira, confluence, ado, git_openapi
kc/api/          server.py
kc/ingest.py     the fast path
kc/health.py     metrics and guardrails
kc/fixture.py    synthetic BU-A / BU-B
kc/cli.py
tests/           104 tests: the named acceptance tests plus the structural ones
```

## Moving to PostgreSQL

`kc/stores/sqlite_store.py` is the swap seam (§21 of the FRS). The change log is
identical either way; the graph, indexes and alias registry are projections and
are rebuilt by replay, so the swap costs no re-ingestion. Two things change with
it:

1. The entitlement SQL fragments in `kc/entitlements/filter.py` become row-level
   security policies, so the planner applies them rather than the application.
2. `Store.neighbours` becomes a recursive CTE in PostgreSQL, and later a graph
   database if hop-3 `impact_analysis` needs it — nothing above that method
   knows how traversal is implemented.

Keep per-BU index namespaces **and** row-level security: namespaces protect
against a policy misconfiguration, policies protect against a query bug.

## Requirement references

Docstrings cite the FRS (`FR-5.4`, `AT-6`, `§21`) rather than restating it.
`grep -rn "FR-11" kc/` finds every place a requirement is implemented, and the
tests in `tests/test_invariants.py` are named for the acceptance tests they
cover.
