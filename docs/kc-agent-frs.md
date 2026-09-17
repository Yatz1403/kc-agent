# Enterprise Knowledge Context (KC) Agent — Functional Requirements

**Version:** 0.1 (MVP)
**Status:** Draft for implementation
**Audience:** Implementation agent (GitHub Copilot) and reviewing engineers

---

## 1. Purpose

The KC Agent is a shared, self-populating knowledge service for the enterprise. It ingests knowledge from engineering source systems, maintains it as a governed knowledge graph, and serves **structured context** to any business application that needs it.

**Core design constraint:** the KC Agent does **not** generate prose answers. It returns structured context (OKF). The calling application's own LLM performs generation. This is both a cost control and an architectural boundary.

---

## 2. Definitions

| Term | Meaning |
|---|---|
| **OKF** | Open Knowledge Format — the canonical, versioned JSON envelope in which all retrieved context is returned. House schema, defined in §9. |
| **A2A** | Agent2Agent protocol, v0.3.0. Used for all agent-to-agent and application-to-KC communication. |
| **Fast path** | Deterministic ingestion pipeline with no LLM planning. Handles the majority of events. |
| **Spoke agent** | One of six specialist agents invoked by the Curator over A2A. |
| **Change API** | The single write path into all persistence stores. |
| **BU** | Business Unit / business group. The unit of data segregation. |
| **Visibility class** | `public` \| `topology` \| `restricted`. Controls what a caller may see of a node. |
| **Provenance class** | `source-derived` \| `agent-derived` \| `human-confirmed`. |
| **Autonomy tier** | T0–T5. Determines whether a proposed change applies automatically or requires human approval. |

---

## 3. Actors

| Actor | Description |
|---|---|
| Source systems | Jira, Confluence, Azure DevOps, Git/OpenAPI, Zephyr |
| Business applications | API testing solution, design assistant, impact analysis tool (A2A clients) |
| Curator | Orchestrating agent; sole holder of write authority |
| Spoke agents | Resolver, Reconciler, Verifier, Prospector, Retrieval Critic, Schema Steward |
| Human reviewer | Approves T4/T5 changes; works the escalation queue |
| Platform operator | Configures thresholds, BU mappings, entitlements |

---

## 4. Knowledge Model

### FR-4.1 — Node types
The system MUST support these node types:
`Service`, `Component`, `API`, `Endpoint`, `Requirement`, `TestCase`, `TestRun`, `Defect`, `Document`, `Environment`, `Team`, `BusinessGroup`, `DataEntity`.

### FR-4.2 — Edge types
The system MUST support these edge types:
`DEPENDS_ON`, `CALLS`, `PUBLISHES_TO`, `CONSUMES_FROM`, `VALIDATES`, `COVERS`, `RAISED_AGAINST`, `IMPLEMENTS`, `OWNED_BY`, `DOCUMENTS`, `SUPERSEDES`, `DERIVED_FROM`.

### FR-4.3 — Mandatory properties
Every node and every edge MUST carry:

```
id                 string, globally unique
type               enum
bu_owner           string
visibility_class   public | topology | restricted
provenance_class   source-derived | agent-derived | human-confirmed
source_system      string
source_id          string
valid_from         ISO-8601
valid_to           ISO-8601 | null
confidence         float 0.0–1.0
last_verified_at   ISO-8601
extraction_version string
```

**Acceptance:** the Change API MUST reject any write missing any of these fields.

### FR-4.4 — Temporal integrity
Superseding a claim MUST set `valid_to` on the old node/edge and create a `SUPERSEDES` edge from new to old. The old record MUST NOT be deleted.

### FR-4.5 — Content separation
The graph MUST NOT store prose content. Prose lives in the lexical and vector indexes, keyed by `node_id`. Graph nodes store identity, relationships, and metadata only.

---

## 5. Segregation and Entitlements

### FR-5.1 — Single graph, tenant-labelled
The knowledge graph MUST be a single physical graph. Per-BU sharding is prohibited — cross-BU dependency traversal is a core capability.

### FR-5.2 — Visibility classes
- `public` — visible to the whole enterprise
- `topology` — name, owner, type and status visible cross-BU; content restricted to the owning BU
- `restricted` — invisible outside the owning BU

Defaults: `Service`/`API` → `topology`; `Requirement`/`Defect`/`TestCase` → `restricted`; standards and glossary → `public`.

### FR-5.3 — BU assignment is deterministic
`bu_owner` MUST be derived from a configured source mapping (Jira project → BU, Confluence space → BU, git org/repo → BU). It MUST NOT be inferred by an LLM.

### FR-5.4 — Pre-filtering, not post-filtering
The entitlement filter MUST be applied **inside** the datastore query, before traversal. Post-filtering of returned results is prohibited.

**Acceptance:** a red-team test suite MUST verify no leakage via (a) direct query, (b) traversal, (c) metadata inference, (d) error messages, (e) latency/token-count side channels.

### FR-5.5 — Identity binding
Every request MUST carry a resolved caller identity. If identity is unresolved, absent, or resolves to an anonymous placeholder, the request MUST be rejected. Fail closed.

### FR-5.6 — Index namespacing
Vector and lexical indexes MUST be physically separated per BU, plus a shared `commons` namespace. Queries fan out only to namespaces the caller is entitled to, plus `commons`.

### FR-5.7 — Agent entitlement
`source.fetch` and `graph.query` performed by spoke agents MUST be entitlement-scoped identically to human callers. Agent identity does not grant elevated read access.

---

## 6. Ingestion — Fast Path

### FR-6.1 — Event intake
Each source system connector MUST accept webhooks and emit a normalised `SourceEvent` onto a queue. Processing MUST be asynchronous. No inline processing on the webhook thread.

### FR-6.2 — Change detection
On each event the system MUST fetch the full object, compute a content hash, and compare against the last stored snapshot. If unchanged, the event MUST be dropped with no further processing.

### FR-6.3 — Raw snapshot store
Every fetched object MUST be persisted to object storage keyed by `source_id + version_hash`, to permit reprocessing when `extraction_version` changes.

### FR-6.4 — Deterministic extraction (no LLM)
For structured sources — OpenAPI specs, IaC files, `package.json`/`pom.xml`, Jira structured fields, git metadata — entity and relationship extraction MUST use parsers, not an LLM.

### FR-6.5 — LLM extraction (free text only)
For free text — Confluence pages, ticket descriptions — extraction MAY use a small LLM with forced JSON output constrained to the schema in §4. Any output that does not conform MUST be discarded and logged for the Schema Steward. The system MUST NOT persist off-schema entities.

### FR-6.6 — Normalisation
Before extraction the system MUST: strip boilerplate, scrub PII, convert to canonical document format, and assign `bu_owner` per FR-5.3.

### FR-6.7 — Chunking
Chunking MUST be structure-aware (by heading/section). Each chunk MUST carry parent context in its header and the `node_id` of its owning entity.

### FR-6.8 — Selective embedding
Only content classes requiring paraphrase matching (Confluence prose, ADRs, incident notes) MUST be embedded. Structured artefacts (specs, test cases, defect records, configs) MUST be lexically indexed only.

### FR-6.9 — Entity resolution cascade
Resolution MUST proceed in this order, stopping at the first confident result:
1. Alias registry exact match (no cost)
2. String/structural similarity (no cost)
3. Escalation to the Resolver agent (cost)

Thresholds MUST be configuration, not code.

### FR-6.10 — Under-merge bias
Where confidence is below the merge threshold, the system MUST create a new entity rather than merge. A false merge is a segregation breach; a missed merge is a quality issue.

### FR-6.11 — Contradiction detection
Before writing, the system MUST check whether the claim contradicts an existing claim on the same node. If so, the event MUST be escalated to the Reconciler agent.

### FR-6.12 — Confidence scoring
Confidence MUST be computed as a function of source authority rank, recency, and extraction certainty. It MUST NOT be self-reported by an LLM.

### FR-6.13 — Source authority ranking
The system MUST hold a configured authority ranking. Default order, highest first:
`CMDB/running config` > `code and OpenAPI specs` > `ADO/Jira current state` > `Confluence` > `chat/email`.

### FR-6.14 — Throughput target
At least 85% of ingestion events MUST complete without any LLM invocation. This MUST be measured and reported (see FR-14.3).

---

## 7. Write Control

### FR-7.1 — Single write path
All mutations to the graph, indexes, alias registry and change log MUST go through the Change API. No component may write directly to a store.

### FR-7.2 — Identity restriction
The Change API MUST accept writes only from the Curator's service identity. Requests from spoke agents or business applications MUST be rejected.

### FR-7.3 — Autonomy tiers

| Tier | Action class | Gate |
|---|---|---|
| T0 | Read, investigate, file task | Free |
| T1 | Update confidence, `last_verified_at`, quality signals | Auto |
| T2 | Add node/edge with full provenance | Auto, flagged agent-authored |
| T3 | Merge entities, supersede claim, set `valid_to` | Auto if confidence > threshold AND single-BU; else human |
| T4 | Cross-BU edge, visibility class change, alias registry change | Human, always |
| T5 | Schema change, bulk reprocessing, delete | Human + change record |

Thresholds MUST be configuration. T4 and T5 MUST NOT be unlockable by threshold.

### FR-7.4 — Reversibility
Every write MUST append to an event-sourced change log. The system MUST support rollback of all changes from a single agent run by run ID.

### FR-7.5 — Rate limiting
Writes MUST be rate-limited per agent run and per day. Breaching the limit MUST halt the cognitive layer without halting the fast path.

### FR-7.6 — Kill switch
An operator MUST be able to disable the cognitive layer (Curator + spokes) while leaving the fast path and serve path operational.

### FR-7.7 — Dry-run mode
The system MUST support a global dry-run mode in which agent proposals are recorded and surfaced for human application but not applied. This MUST be the default for initial deployment.

---

## 8. Agents

### FR-8.1 — Generic agent contract
Every agent MUST implement:
- A **standing goal** (not a task type)
- A **tool set** from which it selects at runtime
- A **bounded loop** with limits on tool calls, tokens, and wall-clock time
- **Terminal states**: `proposal`, `no_action_needed`, `escalate`
- **Episodic recall** at loop start and **episodic write** at loop end

`escalate` and `no_action_needed` are valid successful outcomes, not failures.

### FR-8.2 — No direct write
No spoke agent may hold a write tool. All mutation is expressed as a proposal returned to the Curator.

### FR-8.3 — Shared base tools
Every agent MUST have: `graph.query`, `memory.episodic.recall`, `memory.episodic.write`, `task.escalate`, `budget.check`, `trace.log`.

### FR-8.4 — Budget enforcement
On budget exhaustion the agent MUST terminate with `escalate` and return partial evidence. It MUST NOT continue looping.

### FR-8.5 — Batching
The Curator MUST batch same-type tasks into a single agent invocation where possible (e.g. ten ambiguous merges as one Resolver task with ten candidates).

### FR-8.6 — Model tiering
Resolver and Verifier MUST use a small model. Reconciler and Schema Steward MAY use a larger model. Model selection MUST be configuration.

### FR-8.7 — Curator
**Goal:** fabric health.
**Tools:** `metrics.read`, `queue.inspect`, `queue.prioritise`, `a2a.dispatch`, `budget.allocate`, `gate.evaluate`, `change.apply`, `human.request_input`.
**Behaviour:** allocates the daily compute budget across spokes against the weakest health metric. Triage routing MUST be a deterministic lookup, not an LLM call.

### FR-8.8 — Resolver
**Goal:** correct entity identity.
**Tools:** `alias.lookup`, `alias.propose`, `graph.neighbourhood`, `source.fetch`, `source.search`, `embedding.similarity`, `fingerprint.compare`, `merge.propose`, `split.propose`.
**Behaviour:** MUST propose a new alias rule after observing the same manual correction three times.

### FR-8.9 — Reconciler
**Goal:** one coherent truth per claim.
**Tools:** `claim.fetch`, `source.current_state`, `authority.rank`, `graph.temporal_query`, `owner.lookup`, `human.ask_owner`, `supersede.propose`, `coexist.propose`.
**Behaviour:** MUST apply the configured authority ranking (FR-6.13), not an invented one. Where ambiguous, MUST propose coexistence with lowered confidence on both claims rather than guessing.

### FR-8.10 — Verifier
**Goal:** graph trustworthiness.
**Tools:** `staleness.list`, `retrieval.traffic`, `blast_radius`, `source.fetch`, `hash.compare`, `verification.record`.
**Behaviour:** given a fixed nightly verification budget, MUST select which nodes to verify using retrieval traffic and blast radius. MUST NOT simply drain the staleness queue in order.

### FR-8.11 — Prospector
**Goal:** close knowledge gaps.
**Tools:** `gap_query.run`, `source.discover`, `code.parse_imports`, `iac.parse`, `graph.diff`, `hypothesis.test`, `task.create`.
**Behaviour:** MUST verify a hypothesis against a second source before filing a task. Has no write scope.

### FR-8.12 — Retrieval Critic
**Goal:** reduce bad answers.
**Tools:** `retrieval.replay`, `retrieval.replay_variant`, `answer.feedback_fetch`, `trace.fetch`, `node.attribute_blame`, `task.create`.
**Behaviour:** MUST isolate the cause by varying one retrieval parameter at a time (hop budget, anchor, track selection) before filing a repair task.

### FR-8.13 — Schema Steward
**Goal:** schema fits reality.
**Tools:** `extraction.failure_sample`, `cluster`, `schema.read`, `schema.simulate`, `coverage.estimate`, `proposal.submit`.
**Behaviour:** advisory only. MUST simulate a proposed change against a held-out failure sample and report estimated coverage improvement. MUST NOT apply changes.

---

## 9. OKF Contract

### FR-9.1 — Canonical envelope
All retrieved context MUST be returned in this envelope, regardless of consumer or intent:

```json
{
  "okf_version": "0.1",
  "request_id": "string",
  "anchor": { "id": "string", "type": "string", "bu": "string" },
  "intent": "test_planning | design | impact_analysis | lookup",
  "entities": [
    { "id": "...", "type": "...", "props": {}, "visibility": "full|metadata", "hop": 0 }
  ],
  "relations": [
    { "from": "...", "to": "...", "type": "DEPENDS_ON", "props": {}, "hop": 1 }
  ],
  "artifacts": [
    { "node_id": "...", "kind": "openapi|doc|test|defect", "content": "...", "ref": "..." }
  ],
  "gaps": ["string"],
  "provenance": [
    { "claim_id": "...", "source_id": "...", "valid_from": "...", "class": "source-derived" }
  ],
  "budget": { "hops": 2, "tokens_returned": 4100, "cache_hit": true }
}
```

### FR-9.2 — Version freeze
`okf_version` MUST be present. The schema MUST be frozen before the first consumer integration. Breaking changes require a version bump; consumers pin a version.

### FR-9.3 — Artifact suppression
Consumers MUST be able to request `include_artifacts: false` and receive topology only.

### FR-9.4 — Visibility marking
Entities returned under `topology` visibility MUST be marked `"visibility": "metadata"` and MUST NOT include content in `artifacts`.

### FR-9.5 — Provenance marking
Every claim MUST be traceable to a `source_id` and `valid_from`. Agent-derived content MUST be labelled as such in the payload.

### FR-9.6 — No prose
The KC Agent MUST NOT return generated natural-language answers. Responses are data only.

---

## 10. A2A Layer

### FR-10.1 — Protocol version
The system MUST implement A2A v0.3.0.

### FR-10.2 — Topology
Hub and spoke. The Curator is the only A2A client. The six spokes are A2A servers. Spoke-to-spoke communication is prohibited.

### FR-10.3 — Agent Cards
Each agent MUST publish an Agent Card. Only the Curator's card is exposed outside the KC boundary.

| Agent | Skill ID |
|---|---|
| Curator (external) | `kc.context.retrieve` |
| Curator (internal) | `kc.orchestrate` |
| Resolver | `kc.entity.resolve` |
| Reconciler | `kc.claim.reconcile` |
| Verifier | `kc.node.verify` |
| Prospector | `kc.gap.discover` |
| Retrieval Critic | `kc.answer.diagnose` |
| Schema Steward | `kc.schema.propose` |

### FR-10.4 — Payload type
All A2A payloads MUST be `DataPart` carrying OKF. `TextPart` MUST NOT be used for inter-agent communication.

### FR-10.5 — Human-in-the-loop
Human approval MUST be implemented using the A2A `input-required` task state. Task state MUST persist until a human response arrives, then resume.

### FR-10.6 — Context grouping
All tasks arising from a single source event MUST share a `contextId` so the audit trail can be reconstructed.

### FR-10.7 — Identity propagation
Caller identity MUST propagate through the A2A authentication context and MUST be the identity against which entitlements are evaluated.

---

## 11. Serve Path

### FR-11.1 — No LLM
The serve path MUST contain no LLM invocation. Reranking MUST use a cross-encoder, not a generative model.

### FR-11.2 — Pipeline order
The serve path MUST execute in this order:
1. Resolve and validate caller identity (reject if unresolved)
2. Classify intent; extract entities and time scope
3. Resolve entity names to node IDs via the alias registry
4. Apply entitlement filter (in-query)
5. Anchor on seed nodes
6. Traverse within budget
7. Multi-track fetch: lexical BM25, vector (embedded classes only), targeted by node ID
8. RRF fusion
9. Cross-encoder rerank
10. Assemble OKF, preserving dependency paths
11. Trim to token ceiling, outermost hop first
12. Emit trace and audit record

### FR-11.3 — Ambiguity handling
If an entity name resolves to multiple candidate nodes, the system MUST return a disambiguation request rather than guessing.

### FR-11.4 — Traversal budget
Hop limits MUST be configured per intent. Defaults: `lookup` = 1, `test_planning` = 2, `impact_analysis` = 3. Fan-out per hop MUST be capped and ranked by edge criticality. Relevance MUST decay per hop.

### FR-11.5 — Dependency path preservation
The assembled OKF MUST express traversal paths explicitly in `relations` with `hop` depth. Results MUST NOT be flattened into an unordered set.

### FR-11.6 — Caching
Responses MUST be cached on `hash(intent + anchor + scope + include_artifacts + entitlement_scope)`. Cache MUST be invalidated when any node in the cached neighbourhood changes. Cache hit status MUST be reported in `budget.cache_hit`.

### FR-11.7 — Feedback intake
Consumers MUST be able to signal poor context quality against a `request_id`. Such signals MUST be routed to the Retrieval Critic.

---

## 12. Agent Memory

### FR-12.1 — Physical separation
Agent memory MUST be stored separately from the knowledge graph. Agent beliefs MUST NOT be retrievable as enterprise facts.

### FR-12.2 — Memory types
- **Working** — per-run, ephemeral
- **Episodic** — prior decisions with rationale and evidence, durable
- **Procedural** — learned heuristics, human-readable and human-editable

### FR-12.3 — Actor isolation
Episodic memory MUST be keyed to a resolved agent and task identity. If the identity resolves to an anonymous placeholder, memory writes MUST be rejected.

### FR-12.4 — Promotion
A pattern observed three or more times MAY be promoted from episodic to procedural. Promotion MUST be logged and MUST be human-reviewable.

### FR-12.5 — Rejection capture
Human rejection of an agent proposal MUST be written to episodic memory with the rejection rationale.

---

## 13. Inbreeding Guard

### FR-13.1 — Evidence eligibility
An `agent-derived` node MUST NOT be usable as evidence supporting another `agent-derived` claim. This MUST be enforced in the evidence-fetch tool, not by prompt instruction.

### FR-13.2 — Differential decay
`agent-derived` nodes MUST carry a shorter TTL and faster confidence decay than `source-derived` nodes.

### FR-13.3 — No auto-promotion
Promotion from `agent-derived` to `human-confirmed` MUST require explicit human review. No automatic promotion path may exist.

### FR-13.4 — Ratio alarm
The system MUST track the ratio of `agent-derived` to `source-derived` nodes and raise an alarm above a configured threshold (default 15%).

---

## 14. Maintenance and Health

### FR-14.1 — Staleness sweep
A nightly job MUST flag nodes exceeding their per-type TTL. Defaults: service topology 7d, requirements 30d, documents 90d.

### FR-14.2 — Reconciliation sweep
A weekly full source scan MUST diff source state against the graph and repair drift from missed webhooks.

### FR-14.3 — Health metrics
The system MUST compute and expose:
1. Coverage — % of services with owner, dependencies and test linkage
2. Freshness — median node age relative to TTL
3. Confidence — % of graph above the retrieval threshold
4. Open contradiction count
5. Gap closure rate — Prospector tasks filed vs closed
6. Retrieval quality — recall@k and nDCG on a fixed question set
7. Human intervention rate
8. LLM-free ingestion rate (target ≥85%, per FR-6.14)
9. Agent-derived ratio (per FR-13.4)

### FR-14.4 — Metrics drive allocation
The Curator's budget allocator MUST read the health metrics and weight dispatch toward the weakest.

### FR-14.5 — Confidence decay
Unverified nodes MUST lose confidence over time. Nodes below the retrieval threshold MUST be excluded from serve results and surfaced for review.

---

## 15. Observability

### FR-15.1 — Tracing
The system MUST emit OpenInference-conformant spans over OTLP, one span per pipeline stage and one per agent tool call.

### FR-15.2 — Audit
Every serve request MUST record: caller identity, nodes traversed, nodes filtered out by entitlement, artifacts returned.

### FR-15.3 — Decision log
Every agent decision MUST record: goal, tools invoked, evidence gathered, verdict, confidence, terminal state.

---

## 16. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 | Serve path P95 latency < 2s for `lookup`, < 5s for `impact_analysis` |
| NFR-2 | Ingestion lag: median < 5 minutes from source change to graph reflection |
| NFR-3 | Fail closed on identity, entitlement, or store errors |
| NFR-4 | All thresholds, TTLs, budgets, BU mappings and authority rankings in configuration, not code |
| NFR-5 | Indexes MUST be fully rebuildable from the raw artefact store |
| NFR-6 | No secrets in graph, index, or log content |
| NFR-7 | Agent loops MUST have hard caps on tool calls, tokens and wall-clock time |

---

## 17. MVP Scope

**In scope:**
1. Sources: Git/OpenAPI, Jira, Confluence
2. One BU plus `commons`
3. Lexical-first retrieval; embedding of Confluence prose only
4. Curator plus Resolver as full agents; remaining four spokes stubbed to escalate to the human queue
5. Event-driven ingestion
6. Dry-run write mode (agent proposes, human applies)
7. OKF v0.1 and Curator Agent Card frozen before consumer integration
8. One consumer integration: the API testing solution

**Out of scope for MVP:**
1. Zephyr, ADO, CMDB connectors
2. Second BU and full cross-BU entitlement enforcement
3. Prospector, Verifier, Retrieval Critic, Schema Steward as live agents
4. Confidence decay and automated supersession
5. A2A streaming (synchronous only)
6. Automated autonomy above T2

---

## 18. Acceptance Tests

| ID | Scenario | Expected |
|---|---|---|
| AT-1 | Unchanged webhook replayed | Dropped at hash diff; no LLM call; no write |
| AT-2 | OpenAPI spec change ingested | Entities and edges extracted with zero LLM calls |
| AT-3 | Node written without `bu_owner` | Change API rejects |
| AT-4 | Spoke agent calls Change API directly | Rejected on identity |
| AT-5 | Request with unresolved identity | Rejected; no data returned |
| AT-6 | Caller from BU-A queries a BU-B `restricted` node | Node absent from results; absence not inferable from timing or error text |
| AT-7 | Caller from BU-A queries a BU-B `topology` node | Metadata returned; no artifacts |
| AT-8 | Ambiguous entity name in query | Disambiguation request returned, not a guess |
| AT-9 | Resolver exhausts its budget | Terminates with `escalate` and partial evidence; no loop continuation |
| AT-10 | Agent-derived node offered as evidence for another agent-derived claim | Evidence-fetch tool rejects |
| AT-11 | T4 cross-BU edge proposal | Task enters `input-required`; no write until human approves |
| AT-12 | Human rejects a proposal | Rejection written to episodic memory |
| AT-13 | Rollback of an agent run | All writes from that run reversed |
| AT-14 | Identical serve request repeated | Second request reports `cache_hit: true` |
| AT-15 | Node in a cached neighbourhood changes | Cache invalidated; next request recomputes |
| AT-16 | Serve path executed | Trace confirms zero LLM invocations |
| AT-17 | Off-schema LLM extraction output | Discarded; logged for Schema Steward; not persisted |
| AT-18 | Kill switch engaged | Cognitive layer halts; fast path and serve path continue |

---

## 19. Delivery Sequence

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 0 | Schema, BU mappings, alias registry, OKF v0.1, Agent Cards | Frozen and reviewed |
| 1 | Deterministic ingestion (Git + Jira), Change API, graph | Golden set of 200 entities resolved correctly |
| 2 | Serve path, OKF assembly, A2A Curator card | Recall@k measured on fixed question set |
| 3 | One consumer wired (API testing solution) | Consumer output measurably better with context than without |
| 4 | Confluence ingestion, selective embedding, escalation queue | Human queue populated with real hard cases |
| 5 | Resolver as a full agent, dry-run mode | Agreement rate with human decisions measured |
| 6 | Autonomy T1/T2 enabled; remaining spokes | Human intervention rate trending down |

**Phase 3 is a hard gate.** If fabric context does not measurably improve the consumer's output, the knowledge model is wrong and no further agent work should proceed until it is corrected.
