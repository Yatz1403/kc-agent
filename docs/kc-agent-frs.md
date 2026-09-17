# Enterprise Knowledge Context (KC) Agent — Functional Requirements

**Version:** 0.3 (MVP)
**Status:** Approved for Phase 0/1 implementation
**Supersedes:** 0.2, 0.1
**Audience:** Implementation agent (GitHub Copilot) and reviewing engineers

The document version and the wire-format version are independent: this is document 0.3, and the OKF contract it defines is still `okf_version` **0.2** (§9). Bumping the document does not bump the contract.

---

## How to read this version

- **Numbering is stable.** Every v0.1 ID (FR, NFR, AT, §) keeps its meaning. New requirements take the next free number in their section. No requirement is withdrawn in 0.2; a requirement withdrawn later stays in place, marked *Withdrawn in x.y — see FR-n*.
- **Tags.** A requirement changed in this version carries *Changed in 0.2* under its heading; a new one carries *New in 0.2*. Both name the source of the change. Untagged requirements are unchanged from 0.1.
- **The delta.** `git diff main -- docs/kc-agent-frs.md` shows every textual change against v0.1.
- **Numbers.** Thresholds follow the three-tier policy in §19: fixed guardrails, targets set from the first measurement, or tracked with no gate until a baseline exists. A number marked *(provisional)* is a placeholder awaiting its baseline; one marked *(guardrail)* is fixed now and needs none.

### Source codes

| Code | Meaning |
|---|---|
| B1–B4, M1–M15 | Blocking and major findings in the v0.1 review |
| N1–N21 | Minor items in the v0.1 review, numbered in the order listed there |
| D1–D8 | Decisions made in the review discussion, 17 Sep 2026 |
| DR1–DR11 | Added while drafting 0.2 and not yet reviewed (§20) |

Decisions: **D1** change log is authoritative, all other stores are projections, a reconciliation job repairs drift · **D2** per-BU envelope keys plus per-person keys · **D3** snapshot validation, retention and full replay from origin · **D4** graph holds the highest-authority open claim; claim store read only with an explicit time scope; indexed current-claim filter · **D5** object-level source permissions checked, container-level ignored unless unmapped · **D6** Git test code is the MVP TestCase source · **D7** add Topic; Standard and GlossaryTerm as Document subtypes; defer Person · **D8** cross-BU approver policy configurable.

---

## Changes from v0.2

Owner decisions, 17 Sep 2026. Each closes an item that v0.2 left open or provisional.

| # | Change | Requirements | Why | What breaks if reverted |
|---|---|---|---|---|
| 1 | Inline current-claim metadata held as one `props` document per node, not one column per field per property; only traversal- and filter-relevant properties indexed | FR-4.7 | DR2 accepted, with a storage constraint | Adding a property changes the physical schema, so the table migrates every time the model grows |
| 2 | Audit record fixed at caller, scope, cache epoch, sequence number and what was returned; exclusion data derived offline only | FR-15.2, §20 DR7 | DR7 accepted as a deliberate deviation from the v0.1 review | Someone "fixes" in-path counting back and reopens the AT-6 timing channel |
| 3 | Three-tier threshold policy: guardrails fixed now, targets set from the first measurement, the rest tracked without a gate | §19, FR-5.9, FR-13.4, FR-14.1 | A threshold cannot be set before the first measurement; saying so is policy, not a gap | Invented targets get enforced, or real numbers are quietly renegotiated after the results are in |
| 4 | Phase 3 gate replaced with blinded pairwise preference: 40 backlog tasks, two arms, two blinded reviewers, third adjudicates, win-or-tie ≥ 80% and strict win ≥ 40% | §19 | A percentage-improvement metric invents its effect size, and p < 0.05 needs more hand-labelled tasks than is realistic | The hard gate becomes unmeasurable at the sample size available and gets waived |
| 5 | Approval expiry set to 5 business days, auto-escalating to the owning team's manager; the clock freezes under the kill switch and restarts on release | FR-10.5 | Open items had no expiry owner and no clock semantics | Approvals sit indefinitely, or expire while the system that would apply them is switched off |
| 6 | TTLs: v0.1 defaults kept, every remaining node type provisionally 30d, re-baselined at Phase 2 from observed change rates | FR-14.1 | Guessing per-type TTLs twice is worse than measuring once | The staleness sweep runs on numbers nobody has checked against real change rates |
| 7 | MVP persistence decided: one PostgreSQL instance for log, claim store, graph projection, alias registry, lexical and vector indexes, with row-level security as the in-query entitlement filter | §21, FR-5.4, FR-5.6 | B3 gated the store choice and was blocking the build | The build stalls, or a graph database is bought before there is evidence it is needed |

---

## Changes from v0.1

| # | Change | Requirements | Source | Why | What breaks if reverted |
|---|---|---|---|---|---|
| 1 | Change log is the system of record; graph, claim store, indexes and alias registry are projections of it | FR-7.1, NFR-5, §1, §17 | D1 | v0.1 wrote to five stores with no transactional semantics | A graph write that succeeds while its index write fails leaves unreachable nodes or retrievable orphan chunks; rollback and rebuild have no single source to work from |
| 2 | Change API validates before append; projectors never reject; a failing projector halts and dead-letters | FR-7.8 | D1 | An authoritative log must never contain an invalid event | One bad event breaks every future rebuild, or projectors skip it and diverge silently |
| 3 | Sequence numbers and per-projector high-water marks make replay a no-op for applied events | FR-7.9, FR-4.10 | D1, M8 | Projections are replayed and rebuilt routinely | Retries and replays create duplicate nodes and claims |
| 4 | Reconciliation job compares projections with the log and repairs by replay | FR-7.10, FR-14.3 | D1 | Projections can still drift through bugs or partial restores | Drift stays invisible until a consumer receives a wrong answer |
| 5 | Snapshots validated against replay before use; retention; quarterly full replay from origin | FR-7.11, NFR-5 | D3 | A corrupt snapshot poisons every rebuild that starts from it | Rebuilds reproduce corruption silently; nobody knows whether origin replay still works or how long it takes |
| 6 | Claims defined; node IDs stable; supersession links claims in the claim store | FR-4.3, FR-4.4, FR-4.6, FR-4.8, FR-4.11 | B3 | v0.1 superseded whole nodes and edges | Every property change mints a new node ID and orphans edges, cache anchors and consumer references; edge supersession cannot be represented |
| 7 | One representation per property: the graph holds the highest-authority open claim's value with its claim metadata; the claim store holds history | FR-4.7 | B3, D4, DR2 | Avoid two read paths for the same property | Readers must check two locations for one value, or the serve path joins the claim store and loses one-hop traversal |
| 8 | Serve path reads the claim store only when the request sets `as_of` | FR-11.8, FR-11.2 | D4 | Preserve the one-hop guarantee | A default join doubles traversal cost and breaks NFR-1 |
| 9 | Indexed current-claim filter on every traversal hop | FR-11.9, FR-11.2 | B3, D4 | Closed edges stay in the graph | Closed edges and dead nodes are served as current, or traversal cost grows with history |
| 10 | Chunks carry node liveness; retrieval excludes chunks of nodes that are not live | FR-11.10 | D1 | Index and graph projections update independently | Chunks of rolled-back or deleted nodes stay retrievable |
| 11 | Write-identity table; tiers and dry-run apply only to agent proposals; three action pairs separated | §3, FR-7.2, FR-7.3, FR-7.7 | B1 | Curator-only writes contradicted the kill switch and blocked ingestion | Under the kill switch or dry-run the fast path cannot write; every Jira edit waits for a human; NFR-2 fails |
| 12 | Names resolved inside the caller's scope; alias entries carry BU and visibility; not-found and not-entitled responses byte-identical | FR-11.2, FR-11.3, FR-5.8 | B2 | v0.1 resolved names before applying the entitlement filter | A disambiguation reply reveals restricted entities in other BUs (fails AT-6) |
| 13 | OKF becomes a message family: request, response, disambiguation, error, feedback, spoke task, proposal, escalation, no-action, review decision | FR-9.1, FR-9.7–FR-9.14, FR-10.4 | B4 | v0.1 defined only the response envelope | Each module invents its own shapes for proposals, errors and approvals; the freeze covers one message of eleven |
| 14 | Response envelope: claim IDs on values and relations, `anchors[]`, `paths[]`, requirement/adr/incident kinds, gap codes, named tokenizer | FR-9.1, FR-9.5, FR-9.6, FR-11.5 | M1 | Provenance could not be joined and paths were lost | Consumers cannot trace values to sources; impact paths collapse; free-text gaps can name hidden nodes |
| 15 | OKF drafted in Phase 0, frozen at Phase 2 exit | FR-9.2, §17, §19 | M6 | Freezing before the serve path is measured freezes guesses | A contract frozen at Phase 0 needs a version bump as soon as recall@k is measured |
| 16 | Topic node type; PART_OF, EXECUTES, DEPLOYED_TO, MEMBER_OF, READS, WRITES edges; Document subtypes | FR-4.1, FR-4.2 | M2, D7, DR5 | Two edge types had no valid target; standards and glossary had no type | Asynchronous dependencies go untracked; FR-5.2 references types that do not exist |
| 17 | Edge domain/range, cardinality and property-cardinality tables | FR-4.9 | M2, DR4 | "Off-schema" and "contradiction" were undefined | AT-17 is untestable; contradiction detection has no rule to apply |
| 18 | Person deferred; when added, an opaque directory ID; structured person fields dropped at normalisation | FR-4.12, FR-6.6 | D7 | Nothing in the MVP needs Person, and PII scrubbing conflicts with it | Person nodes built now would store names that FR-6.6 requires scrubbed |
| 19 | Object-level source permissions checked; container-level ignored unless unmapped; skip rate is a health metric | FR-5.9, FR-6.2, FR-14.2, FR-14.3 | M3, D5, DR11 | BU-level entitlements widened access to restricted pages, issues and repos | Restricted content becomes readable BU-wide; or, if space permissions were checked, most of Confluence is skipped |
| 20 | Unmapped containers quarantined; complete visibility defaults; topology property whitelist; tightening ungated | FR-5.2, FR-5.3, FR-7.3 | M3, DR3 | Defaults covered 5 of 13 types; restricting content waited for a human | Events from unmapped projects get an arbitrary BU; most node types have undefined visibility; content restricted at source stays exposed until approved |
| 21 | An edge is visible only when both endpoints are visible | FR-5.12 | DR1 | FR-5.4(b) forbids traversal leakage but v0.1 had no edge rule | Edges reveal that restricted nodes exist |
| 22 | On-behalf-of tokens; IdP groups mapped to BUs; effective scope is user scope ∩ application scope | FR-5.5, FR-5.10, FR-10.7 | M11 | Consumer apps would call with their own identity | Every user of a consumer app receives the app's scope |
| 23 | Per-BU envelope keys plus per-person keys; erasure by key destruction; raw-store retention | FR-5.11, FR-6.3 | D2, M9 | An immutable, authoritative log needs an erasure mechanism | Erasure requests require rewriting the log; BU segregation at rest is unenforced |
| 24 | Agent task scope derived from subject BUs; memory labelled by BU; replay under the original scope; same-scope batching | FR-5.7, FR-8.5, FR-8.12, FR-10.7, FR-12.6 | M13 | Background agents have no human caller | Agents run with elevated scope or cannot see what they reconcile; memory recall leaks cross-BU evidence |
| 25 | Cross-BU approver policy configurable (default: each affected BU); reviewer evidence visibility fixed | FR-7.12, FR-7.13, FR-10.5 | M13, D8 | Approval policy is a business choice; evidence visibility is a security constraint | Either cross-BU edges stall permanently, or relaxing approval also exposes evidence |
| 26 | Raw store partitioned, encrypted and secret-scanned; traces record IDs, not payloads; audit records omit excluded nodes | FR-6.3, FR-15.1, FR-15.2, FR-15.3, NFR-6 | M9, DR7 | Side stores mirrored every BU's content | The trace backend and raw store become unsegregated copies of every BU; in-path counting of excluded nodes reopens a timing side channel |
| 27 | Gate and apply are deterministic; proposal confidence computed from evidence; LLM extraction grounded in quoted spans; procedural-memory edits reviewed | FR-6.5, FR-7.3, FR-7.14, FR-8.7, FR-12.2 | M14 | Anyone who edits a source page controls text the agents read | Injected text in a Confluence page can steer a proposal through the gate |
| 28 | Inbreeding guard enforced per claim at the gate; read tools label provenance; LLM extraction is source-derived with `extraction_method` | FR-4.3, FR-6.5, FR-13.1, FR-13.4 | M4 | The guard lived in an unnamed tool | Agents bypass the guard through `graph.query`; Confluence extraction either trips the 15% alarm or hides model inference |
| 29 | LLM extraction never creates nodes; unresolved mentions become Resolver candidates; extraction certainty deterministic | FR-6.10, FR-6.12, FR-6.15, FR-8.8 | M15 | Under-merge bias is right for structured identity and wrong for prose | One service splits into a node per spelling |
| 30 | Contradictions defined by cardinality; different ranks resolved deterministically; authority per type and property; confirmations vs later evidence; fast path never blocks | FR-6.11, FR-6.13, FR-6.16, FR-6.17, FR-8.9 | M7 | Every contradiction went to a stubbed Reconciler | The human queue floods with Confluence-versus-code disagreements; confirmed facts freeze; ingestion stalls when the cognitive layer is off |
| 31 | Claim-set diff closes removed claims; deletions close all claims; rollback by compensating events that stops on dependents; proposals carry base versions; deterministic IDs | FR-4.10, FR-6.18, FR-7.4, FR-7.7 | M8 | Deletions, dependent rollbacks and stale proposals were unspecified | Removed endpoints stay current forever; rollback corrupts later changes; stale proposals overwrite newer facts |
| 32 | Cache invalidated when projectors apply events, with dependency tracking and a global epoch for visibility and configuration changes | FR-11.6 | M12, D1 | Invalidation missed real changes and leaked through `cache_hit` | Stale results are cached as fresh; `cache_hit` flips when hidden nodes change |
| 33 | Batch tasks list source contextIds; separate internal Curator card; fast path uses the queue; approval tasks expire | FR-8.5, FR-10.2, FR-10.3, FR-10.5, FR-10.6 | M5, DR6 | The A2A mapping contradicted itself in four places | Batched work loses its audit trail; an internal skill is advertised externally; approvals wait forever |
| 34 | Git test code is the MVP TestCase source; COVERS from test code, VALIDATES from Jira links; parsed in Phase 1 | FR-6.4, FR-6.19, §17, §19 | M10, D6 | Test code is authoritative for what is executed | The Phase 3 gate is tested without coverage context, the weakest version of the value proposition |
| 35 | Synthetic BU-B fixture from Phase 1; every AT mapped to a phase | §17, §18, §19 | M10 | One BU cannot exercise the entitlement tests | The pre-filter is built against one BU and retrofitted later, the costliest retrofit in the design |
| 36 | Pass/fail numbers on exit criteria; pre-registered Phase 3 evaluation; statistical timing test; LLM-free denominator | FR-5.4, FR-6.14, §18, §19 | M6, DR8 | Gates had no thresholds | Hard gates pass or fail by opinion; duplicate webhooks inflate the LLM-free rate |
| 37 | Delivery sequence rescoped; MVP boundary at end of Phase 5; escalation queue in Phase 1; Curator build assigned a phase | §17, §19 | N20, review discussion | v0.1 understated Phases 0 and 1 and disagreed with itself on MVP scope | The plan commits to dates for work it does not list |
| 38 | Resolution candidates limited to the event's BU and commons | FR-6.9 | DR9 | Linking at ingest is automatic | The fast path links a reference to another BU's node, a false merge in effect |

### Minor changes

| # | Change | Requirements | Source | What breaks if reverted |
|---|---|---|---|---|
| N1 | Hash over normalised, extraction-relevant fields; an `extraction_version` change bypasses the hash drop | FR-6.2 | N1 | Volatile fields change constantly, so most duplicate webhooks re-run extraction; AT-1 passes only for byte-identical replays |
| N2 | Source keys include `source_instance` | FR-4.3, FR-6.3 | N2 | PROJ-123 in two Jira instances becomes one object |
| N3 | Change API validates value ranges, not only presence | FR-4.3, FR-7.8 | N3 | Out-of-range confidence and inverted validity intervals enter the log permanently |
| N4 | "Delete" means erasure by key destruction | FR-7.3, FR-5.11 | N4 | Implementers add hard deletes to an append-only log |
| N5 | Webhook signatures verified; events debounced per source object | FR-6.1 | N5 | Anyone who can reach the endpoint drives full fetches against source APIs |
| N6 | Unit of discard in LLM extraction is the item | FR-6.5 | N6 | One bad item discards a whole page's extraction, or bad items ride along with good ones |
| N7 | Embedding classes stated as MUST / MUST NOT; Requirement text lexical only | FR-6.8 | N7 | Whether Requirement text is embedded depends on who reads the sentence |
| N8 | Per-run limit ends the run; daily limit halts the cognitive layer | FR-7.5 | N8 | One runaway run stops all agent work for the day |
| N9 | Write tool defined; `verification.record` renamed `verification.propose` | FR-8.2, FR-8.10 | N9 | The Verifier holds a write tool that bypasses the gate |
| N10 | Health metrics normalised against targets before comparison | FR-8.7, FR-14.4 | N10 | The "weakest" metric is whichever has the smallest raw number |
| N11 | A second source must be independent | FR-8.11 | N11 | Two copies of one wrong page confirm each other |
| N12 | Metadata-visibility wording scoped to callers outside the owning BU | FR-9.4 | N12 | In-BU callers get metadata-only views of their own nodes, or out-of-BU callers get content |
| N13 | "LLM" defined; serve path deployed without LLM credentials or egress | §2, FR-11.1, AT-16 | N13 | An uninstrumented LLM call passes AT-16 |
| N14 | Trim by hop, then by rerank score; remove relations to trimmed entities | FR-11.2 | N14 | Responses contain relations that point at missing entities |
| N15 | `design` hop default; edge criticality weights in configuration | FR-11.4 | N15 | `design` requests have no hop limit; fan-out ranking is arbitrary |
| N16 | TTL defaults for every node type | FR-14.1 | N16 | The staleness sweep ignores most node types |
| N17 | Latency targets for `test_planning` and `design`; ingestion lag measured to projection on the fast path | NFR-1, NFR-2 | N17 | The MVP consumer's own intent has no latency budget |
| N18 | Capacity and operations assumptions set in Phase 0 | NFR-8 | N18 | The graph store is chosen without a load profile |
| N19 | A2A version pin confirmed at Phase 0 exit | FR-10.1 | N19 | Agent Cards freeze against a superseded protocol version |
| N20 | See change 37 | §17, §19 | N20 | See change 37 |
| N21 | New ATs for uncovered requirements; each AT names what it covers and its phase; full FR→AT matrix is a Phase 0 deliverable | §18, §19 | N21 | Untested requirements are implemented by guesswork and regress silently |

---

## 1. Purpose

The KC Agent is a shared, self-populating knowledge service for the enterprise. It ingests knowledge from engineering source systems, maintains it as a governed knowledge graph, and serves **structured context** to any business application that needs it.

**Core design constraint:** the KC Agent does **not** generate prose answers. It returns structured context (OKF). The calling application's own LLM performs generation. This is both a cost control and an architectural boundary.

**Write model** *(new in 0.2 — D1)*: every change is an event appended to a single change log through the Change API. The graph, claim store, lexical and vector indexes and alias registry are projections rebuilt from that log. There is no second write path and no cross-store transaction.

---

## 2. Definitions

*Changed in 0.2 — B1, B3, D1, M11, N13.*

| Term | Meaning |
|---|---|
| **OKF** | Open Knowledge Format — the versioned family of JSON messages defined in §9, including the envelope in which retrieved context is returned. House schema. |
| **A2A** | Agent2Agent protocol, v0.3.0. Used for all agent-to-agent and application-to-KC communication. |
| **Fast path** | Deterministic ingestion pipeline with no LLM planning. Handles the majority of events. May call a small LLM for free-text extraction only (FR-6.5). Writes as `kc-ingest` and never waits on the cognitive layer (FR-6.17). |
| **Cognitive layer** | The Curator and the six spoke agents. |
| **Serve path** | The retrieval pipeline of §11. |
| **Spoke agent** | One of six specialist agents invoked by the Curator over A2A. |
| **Change API** | The single write path. Validates each event and appends it to the change log (FR-7.1, FR-7.8). |
| **Change log** | Append-only, ordered event log. The system of record. |
| **Projection** | A store derived from the change log by a projector: graph, claim store, lexical index, vector index, alias registry. |
| **Claim** | One assertion — that an entity exists, that a property has a value, or that a relationship holds — carrying its own provenance, confidence and validity interval (FR-4.6). |
| **Open claim** | A claim whose `valid_to` is null. |
| **Claim store** | The projection holding every claim, open and closed, with supersession links (FR-4.8). |
| **Live node** | A node with at least one open identity claim. |
| **BU** | Business Unit / business group. The unit of data segregation. |
| **Effective scope** | The BUs a request may read: the end user's scope intersected with the calling application's scope, plus `commons` (FR-5.10). |
| **Task scope** | The BUs an agent task may read, derived from its subject nodes (FR-5.7). |
| **Visibility class** | `public` \| `topology` \| `restricted`. Controls what a caller may see of a node. |
| **Provenance class** | `source-derived` \| `agent-derived` \| `human-confirmed`. |
| **Extraction method** | `parser` \| `llm` \| `agent` \| `human`. How a claim was produced. |
| **Autonomy tier** | T0–T5. Determines whether a proposed change applies automatically or requires human approval. Applies to `kc-curator` proposals only (FR-7.3). |
| **LLM** | A generative language model. Embedding models, cross-encoders and classifiers are not LLMs for the purposes of this specification. |
| **Write identity** | The service identity a Change API caller authenticates as (FR-7.2). |

---

## 3. Actors

*Changed in 0.2 — B1, M11.*

| Actor | Description |
|---|---|
| Source systems | Jira, Confluence, Azure DevOps, Git/OpenAPI, Zephyr |
| Business applications | API testing solution, design assistant, impact analysis tool. A2A clients, calling on behalf of an end user (FR-5.5) |
| End user | The person on whose behalf a business application calls. Entitlements are evaluated against this identity intersected with the application's |
| Ingestion service | The fast path. Write identity `kc-ingest` |
| Maintenance jobs | Staleness sweep, reconciliation sweep, projection reconciliation. Write identity `kc-maintenance` |
| Curator | Orchestrating agent. Applies gated proposals as `kc-curator`; holds no other write authority |
| Spoke agents | Resolver, Reconciler, Verifier, Prospector, Retrieval Critic, Schema Steward. No write identity (FR-8.2) |
| Human reviewer | Approves T3 below threshold, T4 and T5 changes through the review UI; works the escalation queue. Decisions applied as `kc-review` |
| Platform operator | Configures thresholds, BU mappings, entitlements and policies; performs rollback and the kill switch as `kc-operator` |

---

## 4. Knowledge Model

### FR-4.1 — Node types
*Changed in 0.2 — M2, D7, DR5.*

The system MUST support these node types:
`Service`, `Component`, `API`, `Endpoint`, `Topic`, `Requirement`, `TestCase`, `TestRun`, `Defect`, `Document`, `Environment`, `Team`, `BusinessGroup`, `DataEntity`.

`Topic` is a message topic, queue or event channel: the target of `PUBLISHES_TO` and `CONSUMES_FROM`.

`Document` MUST carry a `subtype`: `page` \| `adr` \| `incident` \| `standard` \| `glossary_term`. Standards and the glossary are Document subtypes, not separate node types; FR-5.2's public defaults depend on this.

### FR-4.2 — Edge types
*Changed in 0.2 — M2.*

The system MUST support these edge types:
`DEPENDS_ON`, `CALLS`, `PUBLISHES_TO`, `CONSUMES_FROM`, `VALIDATES`, `COVERS`, `RAISED_AGAINST`, `IMPLEMENTS`, `OWNED_BY`, `DOCUMENTS`, `SUPERSEDES`, `DERIVED_FROM`, `PART_OF`, `EXECUTES`, `DEPLOYED_TO`, `MEMBER_OF`, `READS`, `WRITES`.

`SUPERSEDES` relates two entities, for example one service replacing another. It MUST NOT be used for claim versions, which are linked in the claim store (FR-4.4).

### FR-4.3 — Mandatory properties
*Changed in 0.2 — B3, M4, N2, N3.*

**Every node** MUST carry:

```
id                 string, see FR-4.10
type               enum (FR-4.1)
subtype            enum | null            (Document only)
bu_owner           string
visibility_class   public | topology | restricted
live               boolean               (derived, FR-4.6)
created_at         ISO-8601
```

**Every claim** — identity, property and relationship claims alike, an edge being a relationship claim — MUST carry:

```
claim_id           string, see FR-4.10
kind               identity | property | relationship
subject_id         node id (identity, property) | edge id (relationship)
property           string | null         (property claims only)
value              typed per FR-4.9 | null
from_id, to_id, edge_type                (relationship claims only)
bu_owner           string
provenance_class   source-derived | agent-derived | human-confirmed
extraction_method  parser | llm | agent | human
source_system      string               (jira, confluence, git, ...; kc-agent when agent-derived; kc-review when human-confirmed)
source_instance    string               (which installation of that system)
source_id          string               (source object key; agent run_id; review decision id)
valid_from         ISO-8601
valid_to           ISO-8601 | null
recorded_at        ISO-8601
confidence         float 0.0-1.0
last_verified_at   ISO-8601
extraction_version string
evidence           list of claim_id / chunk_id  (required when provenance_class = agent-derived, otherwise empty)
```

**Every edge** additionally carries the fields the graph projection derives for it: `id`, `type`, `from_id`, `to_id`, `bu_owner`, `visibility_class`, `valid_from`, `valid_to`, `live` (FR-4.7).

**Acceptance:** the Change API MUST reject any event that omits a mandatory field, carries a value outside its enum, a `confidence` outside 0.0-1.0, or a `valid_to` earlier than its `valid_from` (FR-7.8).

### FR-4.4 — Temporal integrity
*Changed in 0.2 — B3.*

1. Superseding a claim MUST set `valid_to` on the old claim and record a supersession link from the new claim to the old one in the claim store. The old claim MUST NOT be deleted.
2. Superseding a claim MUST NOT change any node ID.
3. `valid_from` and `valid_to` record when the claim was true at the source. `recorded_at` and the change-log sequence number record when KC learned it. Both are required; neither substitutes for the other.
4. Claim versions MUST be linked in the claim store, never by a `SUPERSEDES` edge (FR-4.2).

### FR-4.5 — Content separation
The graph MUST NOT store prose content. Prose lives in the lexical and vector indexes, keyed by `node_id`. Graph nodes store identity, relationships, and metadata only.

### FR-4.6 — Claims
*New in 0.2 — B3, DR10.*

A claim is one assertion made by one source at one time. There are three kinds:

- **identity** — a source object asserts that an entity exists
- **property** — a value for one property of one node
- **relationship** — an edge between two nodes

Claims are the unit of provenance (FR-9.5), confidence (FR-6.12), contradiction (FR-6.11), supersession (FR-4.4), evidence (FR-13.1) and rollback (FR-7.4). A node is **live** while it has at least one open identity claim. When its last identity claim closes, the node stops being live; it is never deleted (FR-6.18).

### FR-4.7 — Graph projection contents
*New in 0.2 — B3, D4, DR2, DR10.*

The graph is a projection of the change log, holding exactly one representation of each current value:

1. **Nodes** carry the identity fields of FR-4.3 and the current value of each property.
2. For a **single-valued** property (FR-4.9) the current value is that of the highest-authority open claim, ranked per FR-6.13. Ties break by later `valid_from`, then by lexically smaller `claim_id`. Where more than one open claim exists for that property, it MUST be marked `contested`.
3. For a **multi-valued** property the graph holds the values of all open claims.
4. Each value MUST carry its claim's `claim_id`, `provenance_class`, `source_system`, `source_instance`, `source_id`, `valid_from`, `confidence` and `extraction_method`, so that the serve path can assemble provenance without reading the claim store (FR-11.8).
   **Storage constraint.** This metadata MUST be held in a single `props` document per node, keyed by property name:
   `{"<property>": {"value": ..., "claim_id": ..., "source_system": ..., "provenance_class": ..., "confidence": ..., "valid_from": ...}}`.
   It MUST NOT be spread across one column per field per property, which would change the physical schema every time a property is added. The row is wide; the schema is fixed. Only the properties that traversal or filtering actually uses MUST be indexed (FR-11.9), and those indexes are configuration (NFR-4).
5. **Edges:** one edge per `(from_id, to_id, edge_type)`, carrying the metadata of its highest-authority open relationship claim under the same rule. Its `valid_from` is the earliest `valid_from` among its claims; `valid_to` is null while any relationship claim is open, otherwise the latest `valid_to`. Closed edges remain in the graph and are excluded by the current-claim filter (FR-11.9).
6. Nodes that are not live remain in the graph with `live = false`.

### FR-4.8 — Claim store
*New in 0.2 — B3.*

The claim store is a projection of the change log holding every claim, open and closed, with supersession and coexistence links. It is the only store of property history. It MUST be read only by the serve path under FR-11.8, by agent tools (`claim.fetch`, `graph.temporal_query`), by projectors, and by the reconciliation job (FR-7.10).

### FR-4.9 — Schema constraints
*New in 0.2 — M2, DR4.*

Every edge type MUST declare its permitted source and target node types and its cardinality, and every property MUST declare whether it is single- or multi-valued. Without these, "off-schema" (FR-6.5, AT-17) and "contradiction" (FR-6.11) have no definition. Cardinality **one** means at most one live edge of that type per source node; a second open relationship claim with a different target is a contradiction.

| Edge | From | To | Cardinality |
|---|---|---|---|
| DEPENDS_ON | Service, Component | Service, Component, API, DataEntity | many |
| CALLS | Service, Component | API, Endpoint | many |
| PUBLISHES_TO | Service, Component | Topic | many |
| CONSUMES_FROM | Service, Component | Topic | many |
| VALIDATES | TestCase | Requirement | many |
| COVERS | TestCase | API, Endpoint | many |
| RAISED_AGAINST | Defect | Service, Component, API, Endpoint, Requirement | many |
| IMPLEMENTS | Service, Component | API, Requirement | many |
| OWNED_BY | Service, Component, API, Topic, DataEntity, Environment, Document | Team | **one** |
| DOCUMENTS | Document | any type except Document | many |
| SUPERSEDES | Service, Component, API, Endpoint, Requirement, Document | same type as source | many |
| DERIVED_FROM | Requirement, TestCase, DataEntity | Requirement, Document, DataEntity | many |
| PART_OF | Endpoint; Component | API (from Endpoint); Service (from Component) | **one** |
| EXECUTES | TestRun | TestCase | **one** |
| DEPLOYED_TO | Service, Component | Environment | many |
| MEMBER_OF | Team | BusinessGroup | **one** |
| READS | Service, Component, Endpoint | DataEntity | many |
| WRITES | Service, Component, Endpoint | DataEntity | many |

MVP property set. Additions are T5 schema changes (FR-7.3).

| Node type | Property | Values | Cardinality |
|---|---|---|---|
| all | `name` | string, 200 chars or fewer | single |
| all | `status` | enum per type, configured | single |
| API | `version`, `base_path` | string | single |
| Endpoint | `method`, `path` | HTTP method; string | single |
| Requirement, Defect, TestCase | `key` | source key, e.g. PROJ-123 | multi, one per source |
| Requirement | `priority` | enum, configured | single |
| Defect | `severity` | enum, configured | single |
| TestCase | `framework` | string | single |
| TestCase | `file_path` | string | multi |
| TestRun | `result` | pass \| fail \| error \| skipped | single |
| TestRun | `executed_at` | ISO-8601 | single |
| Topic | `broker` | string | single |
| Environment | `tier` | dev \| test \| staging \| prod | single |
| Document | `url` | string | single |
| DataEntity | `classification` | enum, configured | single |

Property values MUST be identifiers, enums, numbers, dates or short labels. Prose MUST NOT be stored as a property value (FR-4.5).

### FR-4.10 — Deterministic identifiers
*New in 0.2 — M8.*

IDs for source-keyed entities and claims MUST be deterministic. The same `(source_system, source_instance, source_id, entity key)` MUST always produce the same node ID, and the same `(subject, property, source object, valid_from)` the same `claim_id`. Edge IDs derive from `(from_id, to_id, edge_type)`. Replaying the log, or re-extracting a source object, MUST NOT create duplicates (FR-7.9).

### FR-4.11 — Identifier stability
*New in 0.2 — B3, M8.*

A node ID, once issued, MUST always resolve: to its node, or, after a merge, to the surviving node through a redirect recorded in the alias registry. IDs MUST NOT be reused. A merge MUST carry the redirect for the absorbed ID in the same change-log event as the merge itself, so that the two cannot diverge (FR-7.3).

### FR-4.12 — Person deferred
*New in 0.2 — D7.*

`Person` is not an MVP node type. Owner references resolve to `Team` (FR-8.9), and structured person fields are dropped at normalisation (FR-6.6). When `Person` is introduced it MUST be an opaque directory identifier; names, email addresses and other identifying attributes MUST NOT be stored in the graph, claim store or indexes.

---

## 5. Segregation and Entitlements

### FR-5.1 — Single graph, tenant-labelled
The knowledge graph MUST be a single physical graph. Per-BU sharding is prohibited — cross-BU dependency traversal is a core capability.

### FR-5.2 — Visibility classes
*Changed in 0.2 — M3, DR3.*

- `public` — visible to the whole enterprise
- `topology` — whitelisted metadata visible cross-BU; content restricted to the owning BU
- `restricted` — invisible outside the owning BU

**Topology whitelist.** A node returned under `topology` visibility to a caller outside its BU MUST expose only: `id`, `type`, `subtype`, `name`, `status`, `bu_owner`, `live`, and its `OWNED_BY` edge with the Team's `id` and `name`. No other property, no artifact and no claim metadata beyond `provenance_class` may be returned (FR-9.4).

**Defaults** (configuration, NFR-4):

| Node type | Default visibility |
|---|---|
| Service, API, Endpoint, Topic, Team | `topology` |
| BusinessGroup; Document subtypes `standard`, `glossary_term` | `public` |
| Component, Environment, DataEntity, Requirement, TestCase, TestRun, Defect; Document subtypes `page`, `adr`, `incident` | `restricted` |

### FR-5.3 — BU assignment is deterministic
*Changed in 0.2 — M3.*

`bu_owner` MUST be derived from a configured source mapping (Jira project → BU, Confluence space → BU, git org/repo → BU). It MUST NOT be inferred by an LLM.

An event from a container with no BU mapping MUST be quarantined: not extracted, not projected, logged with reason code `UNMAPPED_CONTAINER` and its source key, and counted in health metric 10 (FR-14.3). It MUST NOT be assigned a default BU.

### FR-5.4 — Pre-filtering, not post-filtering
*Changed in 0.2 — M6, D1.*

The entitlement filter MUST be applied **inside** the datastore query, before traversal. Post-filtering of returned results is prohibited. On PostgreSQL (§21) this MUST be implemented with row-level security policies, so the planner applies the filter as part of the query rather than the application applying it by convention. The liveness check of FR-11.10 operates on results that namespace selection has already entitlement-scoped and is not post-filtering under this requirement.

**Acceptance:** a red-team test suite MUST verify no leakage via (a) direct query, (b) traversal, (c) metadata inference, (d) error messages, (e) latency and token-count side channels. The timing test MUST be statistical: response-time distributions for requests naming a restricted node in another BU and requests naming a nonexistent node MUST be indistinguishable by a two-sample Kolmogorov–Smirnov test at α = 0.01 over at least 1,000 requests per arm (proposed). The suite MUST run against the synthetic BU-B fixture from Phase 1 (§17).

### FR-5.5 — Identity binding
*Changed in 0.2 — M11.*

Every request MUST carry an on-behalf-of access token, obtained by OAuth 2.0 Token Exchange (RFC 8693), that identifies both the end user and the calling application. If either identity is unresolved, absent, or resolves to an anonymous placeholder, the request MUST be rejected. Fail closed.

An application-only token MUST be rejected on `kc.context.retrieve` and `kc.context.feedback` unless configuration grants that application an explicit service scope; every such call MUST be audited with the service scope used.

### FR-5.6 — Index namespacing
Vector and lexical indexes MUST be physically separated per BU, plus a shared `commons` namespace. Queries fan out only to namespaces the caller is entitled to, plus `commons`.

Namespace separation and row-level security (FR-5.4) are deliberately redundant and both MUST be kept: row-level security protects against a query bug, and separate namespaces protect against a row-level-security misconfiguration.

### FR-5.7 — Agent entitlement
*Changed in 0.2 — M13.*

Agent reads MUST be entitlement-scoped. Agent identity does not grant elevated read access.

1. Every agent task carries a **task scope**: the set of BUs of its subject nodes, plus `commons`. All reads in that task — `graph.query`, `source.fetch`, `claim.fetch`, memory recall — MUST be limited to it.
2. A task whose subjects span BUs receives the union of their BUs, and any proposal it produces is T4 (FR-7.3).
3. A task that replays a consumer request MUST run under that request's effective scope, not the agent's (FR-8.12).
4. Only tasks with the same task scope may share a batch (FR-8.5).

### FR-5.8 — Uniform absence
*New in 0.2 — B2.*

A node, name or artifact that does not exist and one that exists outside the caller's effective scope MUST produce byte-identical responses: the same error code, body, headers and A2A task state (FR-9.10). Nothing in a response, error or timing may distinguish the two cases.

### FR-5.9 — Source-system permissions
*New in 0.2 — M3, D5, DR11.*

BU-level entitlement is coarser than the permissions some source objects carry. Connectors MUST therefore compare **object-level** permissions with the object's mapped BU:

- **Object-level, checked:** Confluence page restrictions; Jira issue security levels; Git repositories whose read access is limited to named users or teams rather than to the mapped BU.
- **Container-level, ignored:** Confluence space permissions; Jira project permission schemes; Git organisation membership. Narrower-than-BU container permissions are the normal case, not the exception, and ignoring them is deliberate. An unmapped container is handled by FR-5.3 instead.

Rules:

1. An object whose object-level permissions are narrower than its BU MUST NOT be fetched into the raw store, extracted, embedded or indexed. The skip MUST be logged with reason code `OBJECT_RESTRICTED`, its source key and its container.
2. When an already-ingested object becomes restricted, `kc-ingest` MUST close its claims and retract its chunks immediately, with no tier gate (FR-7.3).
3. The skip rate (FR-14.3, metric 10) above a configured threshold (5%) MUST raise an alarm: it indicates a BU mapping error, not restricted content. This is a fixed guardrail, not a provisional target (§19, threshold policy).

### FR-5.10 — Identity to scope
*New in 0.2 — M11.*

Configuration MUST map IdP groups to BUs. The end user's scope is the union of the BUs of their groups; an application's scope is configured per client. **Effective scope** is the intersection of the two, plus `commons`. A user in several BUs receives the union of those BUs before intersection. Effective scope MUST be recomputed per request, never cached across requests, and MUST be part of the cache key (FR-11.6).

### FR-5.11 — Encryption and erasure
*New in 0.2 — D2, M9.*

1. All content at rest — change log, raw store, claim store, graph, indexes, snapshots, agent memory, decision log, audit records — MUST be encrypted with per-BU envelope keys. `commons` has its own key.
2. Every field that can identify a person — caller identities in audit records, reviewer identities in review decisions, structured person fields retained in raw snapshots — MUST additionally be encrypted with a per-person key.
3. Erasure of a person is performed by destroying that person's key. Replay of the change log MUST then yield `[erased]` for those fields, and every projection MUST rebuild without error.
4. Destroying a BU key renders that BU's content unreadable and is a T5 action (FR-7.3).
5. Free text in raw snapshots cannot be reliably attributed to a person. Raw snapshots MUST therefore carry a bounded retention period (configuration) and MUST NOT be required for rebuild (NFR-5).
6. Key granularity is fixed at Phase 0 exit. Changing it later requires re-encrypting the whole log.

### FR-5.12 — Edge visibility
*New in 0.2 — DR1.*

An edge MUST be visible to a caller only if both endpoint nodes are visible to that caller, at `full` or `metadata` level. Traversal MUST NOT pass through a node the caller cannot see. An edge's `visibility_class` is derived as the more restrictive of its endpoints. This rule is evaluated in-query with FR-5.4.

---

## 6. Ingestion — Fast Path

### FR-6.1 — Event intake
*Changed in 0.2 — N5.*

Each source system connector MUST accept webhooks and emit a normalised `SourceEvent` onto a queue. Processing MUST be asynchronous. No inline processing on the webhook thread. Webhook authenticity MUST be verified (signature or equivalent) before any fetch, and events for the same source object MUST be debounced over a configured window (default 30s, proposed).

### FR-6.2 — Change detection
*Changed in 0.2 — N1, M3, M8.*

On each event the system MUST, in order:

1. Fetch the full object.
2. Apply the source-permission check (FR-5.9); a restricted object stops here.
3. Compute a hash over a normalised projection of the fields that extraction uses, configured per source. Volatile fields — update timestamps, rank, watcher and view counts — MUST be excluded.
4. Drop the event with no further processing if the hash matches the last stored hash for that source key **and** `extraction_version` is unchanged. A changed `extraction_version` MUST bypass the drop.

A fetch reporting the object deleted or not found MUST be handled under FR-6.18.

### FR-6.3 — Raw snapshot store
*Changed in 0.2 — M9, N2.*

Every fetched object that passes FR-5.9 and FR-6.2 MUST be persisted to object storage keyed by `source_system + source_instance + source_id + version_hash`, to permit re-extraction when `extraction_version` changes. Before persisting, the system MUST run secret detection and redact what it finds, logging each detection without the secret value (NFR-6). The raw store MUST be partitioned by BU, encrypted per FR-5.11, and subject to a configured retention period. Read access is limited to the connector, the extractor, and operators with a recorded reason.

### FR-6.4 — Deterministic extraction (no LLM)
*Changed in 0.2 — M10, D6.*

For structured sources — OpenAPI specs, IaC files, `package.json`/`pom.xml`, Jira structured fields and issue links, git metadata, and test code and API collection files (FR-6.19) — entity and relationship extraction MUST use parsers, not an LLM. Identity claims for source objects that describe an entity (a Confluence page, a Jira issue, a spec file) are created by the connector, never by LLM extraction (FR-6.15).

### FR-6.5 — LLM extraction (free text only)
*Changed in 0.2 — M4, M14, N6.*

For free text — Confluence pages, ticket descriptions — extraction MAY use a small LLM with forced JSON output constrained to the schema in §4, including the domain, range and cardinality rules of FR-4.9.

1. Source text MUST be passed to the model as delimited data, never as instructions. Text from a source system is untrusted input (FR-7.14).
2. Every extracted property value and relationship MUST quote a span of the normalised source text. An item whose quoted span is not found in the source MUST be discarded.
3. The unit of discard is the item: an off-schema or ungrounded item is discarded and logged for the Schema Steward while the other items of the same output are kept. Output that is not parseable JSON is discarded whole.
4. The system MUST NOT persist off-schema or ungrounded items.
5. Claims produced this way are `source-derived` with `extraction_method = llm`. They are not `agent-derived`: they carry a source span (FR-13.4).

### FR-6.6 — Normalisation
*Changed in 0.2 — M9, D7.*

Before extraction the system MUST: strip boilerplate; detect and redact secrets; scrub PII from free text; drop structured person fields (assignee, reporter, author, watchers) — ownership comes from Team mappings until `Person` exists (FR-4.12); convert to canonical document format; and assign `bu_owner` per FR-5.3.

### FR-6.7 — Chunking
Chunking MUST be structure-aware (by heading/section). Each chunk MUST carry parent context in its header and the `node_id` of its owning entity.

### FR-6.8 — Selective embedding
*Changed in 0.2 — N7.*

The system MUST embed Document content of subtypes `page`, `adr` and `incident`. The system MUST NOT embed OpenAPI specs, test cases, defect records, Requirement text or configuration; these are lexically indexed only.

### FR-6.9 — Entity resolution cascade
*Changed in 0.2 — B1, M7, DR9.*

Resolving an incoming reference MUST proceed in this order, stopping at the first confident result:

1. Source-key mapping or alias registry exact match (no cost)
2. String/structural similarity (no cost)
3. Escalation to the Resolver agent (cost)

Candidates at every step MUST be limited to nodes whose `bu_owner` is the event's BU or `commons`. Thresholds MUST be configuration, not code. Linking an incoming reference to an existing node is a `kc-ingest` write and is not tier-gated; merging two existing nodes is T3 (FR-7.3). Step 3 MUST NOT block ingestion: the fast path applies FR-6.10 and enqueues the Resolver candidate (FR-6.17).

### FR-6.10 — Under-merge bias
*Changed in 0.2 — M15.*

For deterministic extraction (FR-6.4), where confidence is below the merge threshold the system MUST create a new entity rather than link or merge. A false merge is a segregation breach; a missed merge is a quality issue. This bias applies to structured identity only: LLM extraction never creates entities (FR-6.15).

### FR-6.11 — Contradiction detection
*Changed in 0.2 — M7, M2.*

Before appending, the system MUST check the new claim against the open claims on the same subject. A contradiction exists when two open claims from **different sources** assert different values for a single-valued property, or different targets for a relationship whose cardinality is one (FR-4.9).

A newer claim from the **same source object** for the same property is an update, not a contradiction: it supersedes the older claim automatically (FR-4.4) as a `kc-ingest` write. Contradictions are resolved under FR-6.16.

### FR-6.12 — Confidence scoring
*Changed in 0.2 — M15.*

Confidence MUST be computed by a configured function of three inputs: authority weight (FR-6.13, for the claim's node type and property), recency (age of the source object's last modification at extraction time), and extraction certainty.

Extraction certainty MUST be deterministic: a configured constant per extractor and extractor version, multiplied — for LLM extraction — by a grounding factor of 1.0 when the quoted span matches the source verbatim and a configured factor (default 0.8, proposed) when it matches only after case and whitespace normalisation. Confidence MUST NOT be self-reported by an LLM, and an agent's reported confidence MUST be ignored (FR-7.14).

### FR-6.13 — Source authority ranking
*Changed in 0.2 — M7.*

The system MUST hold an authority ranking configured per `(node type, property)` and per edge type, with a default order used where no specific entry exists.

Default order, highest first: `CMDB/running config` > `code, OpenAPI specs and test code` > `ADO/Jira current state` > `Confluence` > `chat/email`.

Per-property overrides are required where the global order is wrong. For example: Jira is the highest authority for `Requirement.status` and `Defect.severity`; test code is the highest authority for `TestCase` properties (FR-6.19).

A `human-confirmed` claim outranks any claim whose `valid_from` precedes the confirmation. It MUST NOT outrank a claim with a later `valid_from`: a confirmation does not freeze a fact against later evidence.

### FR-6.14 — Throughput target
*Changed in 0.2 — M6.*

At least 85% of ingestion events that pass change detection (FR-6.2) and the source-permission check (FR-5.9) MUST complete without any LLM invocation. Events dropped at the hash comparison, quarantined (FR-5.3) or skipped (FR-5.9) are excluded from the denominator. This MUST be measured and reported (FR-14.3, metric 8).

### FR-6.15 — Prose extraction may link, not create
*New in 0.2 — M15.*

LLM extraction MUST NOT create nodes. It may add property and relationship claims whose subject and target are existing live nodes or the source Document node. A mention that does not resolve under FR-6.9 steps 1–2 MUST be recorded as a mention candidate — source key, quoted span, proposed type — and queued for the Resolver. It MUST NOT become a node.

### FR-6.16 — Contradiction resolution
*New in 0.2 — M7.*

1. **Different authority ranks** (FR-6.13): resolve deterministically, with no escalation. The higher-ranked claim supplies the graph value (FR-4.7). The lower-ranked claim stays open in the claim store, linked as coexisting, with its confidence multiplied by a configured factor (default 0.5, proposed).
2. **Equal authority ranks:** both claims stay open, the graph value follows the FR-4.7 tie-break, the property is marked `contested`, and the contradiction is escalated to the Reconciler — in MVP, to the human queue (§17).

### FR-6.17 — The fast path never blocks
*New in 0.2 — M7.*

The fast path MUST NOT wait on the cognitive layer. When the Curator or a spoke is unavailable — kill switch (FR-7.6), rate-limit halt (FR-7.5), or a stubbed spoke — the fast path MUST append its claims under FR-6.10 and FR-6.16 and enqueue the escalation for later handling.

### FR-6.18 — Deletions and removals
*New in 0.2 — M8.*

1. After each extraction the system MUST compare the new claim set for a source object with the open claims previously derived from that object, and close every open claim absent from the new set, setting `valid_to` to the source modification time. An endpoint removed from an OpenAPI spec is closed this way.
2. When a source reports an object deleted, or a fetch returns not-found, the system MUST close all open claims derived from that object.
3. A node with no remaining open identity claim stops being live (FR-4.6). Nothing is deleted; erasure is T5 and works by key destruction (FR-5.11).

### FR-6.19 — Test cases from code
*New in 0.2 — M10, D6.*

Test code in Git is the MVP source of `TestCase` nodes and is the authority for what is actually executed; Zephyr records intent and is out of scope (§17).

1. The connector MUST parse test files for configured frameworks, and API collection files, creating a `TestCase` per test.
2. A `COVERS` edge MUST be created where the test's target is deterministic: a literal HTTP method and path, or a collection request, that matches an `Endpoint` extracted from an OpenAPI spec. Tests whose target cannot be determined get no `COVERS` edge.
3. `VALIDATES` edges MUST come from Jira issue links between test issues and requirement issues. Jira test issues also produce `TestCase` nodes.
4. Where test code carries a Jira test issue key (configured annotation or naming pattern), the Git test and the Jira test issue MUST resolve to one `TestCase` through a source-key mapping.
5. Test-file parsing is Phase 1 scope (§19).

---

## 7. Write Control

### FR-7.1 — Single write path
*Changed in 0.2 — D1.*

The Change API is the only way to append to the change log, and the change log is the system of record. The graph, claim store, lexical indexes, vector indexes and alias registry are projections of it; only their projectors may write to them. The change log MUST be appended before any projection is updated, so that no write can succeed in one store and fail in another.

Not projections, and therefore outside this rule: the raw store (written by connectors, FR-6.3), agent memory (§12), the task queue, traces, and the serve cache.

### FR-7.2 — Write identities
*Changed in 0.2 — B1.*

The Change API MUST authenticate the calling service identity and accept only the operations mapped to it. v0.1 restricted writes to the Curator, which contradicted FR-7.6 and AT-18 and left the fast path, the sweeps, reviewers and rollback with no legal write path.

| Identity | May append | Tier and dry-run |
|---|---|---|
| `kc-ingest` (fast path) | Identity, property and relationship claims for source objects it ingested; same-source updates; closing those claims (FR-6.18); source-key mappings; chunk add and retract; permission-driven retraction (FR-5.9); visibility tightening | Not gated |
| `kc-curator` | Changes from proposals that passed `gate.evaluate` (FR-7.14) | Tier gate (FR-7.3) and dry-run (FR-7.7) |
| `kc-maintenance` | T1 signals only: staleness flags, `last_verified_at`, quality signals | Not gated |
| `kc-review` | Changes from proposals a human approved or modified, recorded as `human-confirmed` with the reviewer identity | The approval is the gate |
| `kc-operator` | Rollback (FR-7.4), configuration change records, T5 actions | Change record required |
| Spoke agents, business applications, any other identity | Nothing | Rejected (AT-4) |

### FR-7.3 — Autonomy tiers
*Changed in 0.2 — B1, M3, M14, N4.*

Tiers apply to `kc-curator` proposals only. Deterministic ingestion, maintenance signals and approved review decisions are governed by FR-7.2, not by tiers. The gate uses the confidence it computes itself (FR-7.14), never a value an agent reported.

| Tier | Action class | Gate |
|---|---|---|
| T0 | Read, investigate, file task | Free |
| T1 | Update confidence, `last_verified_at`, quality signals | Auto |
| T2 | Add a claim with full provenance | Auto, flagged `agent-derived` |
| T3 | Merge existing entities, including the absorbed ID's redirect (FR-4.11); supersede a claim from a different source; close a claim | Auto if computed confidence > threshold AND single-BU; else human |
| T4 | Cross-BU edge; **loosening** a visibility class; name-alias rule change | Human, always; approver policy per FR-7.12 |
| T5 | Schema change; bulk reprocessing; erasure by key destruction (FR-5.11) | Human + change record |

Three pairs of actions that v0.1 conflated are distinguished throughout:

- **Same-source update** (`kc-ingest`, ungated) vs **cross-source supersession** (T3).
- **Source-key mapping** (`kc-ingest`, ungated) vs **name-alias rule** (T4).
- **Linking an incoming reference** to an existing node (`kc-ingest`, ungated) vs **merging two existing nodes** (T3).

**Tightening** a visibility class is never gated: `kc-ingest` applies it immediately when a source restricts access (FR-5.9). Only loosening is T4. "Delete" means erasure only; claims and nodes are otherwise never deleted (FR-4.4, FR-6.18).

Thresholds MUST be configuration. T4 and T5 MUST NOT be unlockable by threshold.

### FR-7.4 — Reversibility
*Changed in 0.2 — M8, D1.*

Every change MUST be an event in the change log. The system MUST support rollback of all changes from a single agent run by run ID.

1. Rollback MUST append compensating events. It MUST NOT remove or rewrite log entries.
2. Before appending, rollback MUST find later events that depend on the run's changes: claims added to entities the run created, edges attached to nodes it merged, human confirmations of its claims.
3. If any dependents exist, rollback MUST stop, append nothing, and report them for a human decision. Rollback MUST NOT cascade.

### FR-7.5 — Rate limiting
*Changed in 0.2 — N8.*

Writes MUST be rate-limited per agent run and per day. A run that breaches its per-run limit MUST terminate with `escalate`; other runs continue. Breaching the daily limit MUST halt the cognitive layer. Neither halts the fast path.

### FR-7.6 — Kill switch
*Changed in 0.2 — M5.*

An operator MUST be able to disable the cognitive layer (Curator + spokes) while leaving the fast path and serve path operational. While the kill switch is engaged, approval tasks stay in `input-required`, review decisions are recorded but not applied, and on release every recorded decision MUST be re-validated under FR-7.7 before it is applied.

### FR-7.7 — Dry-run mode
*Changed in 0.2 — B1, M8.*

The system MUST support a global dry-run mode in which `kc-curator` proposals are recorded and surfaced for human application but not applied. It MUST be the default for initial deployment, and it is the MVP state for all agent proposals (§17). Dry-run MUST NOT apply to `kc-ingest` or `kc-maintenance`.

Every proposal MUST record the version — the last change-log sequence number — of each subject node and claim it was based on. Applying a proposal, whether by `kc-curator` or `kc-review`, MUST first check those versions. If any subject has changed, the proposal MUST be marked stale, MUST NOT be applied, and MUST be returned to the Curator as an escalation (`STALE_PROPOSAL`).

### FR-7.8 — Validation before append
*New in 0.2 — D1.*

The Change API MUST validate every event before appending it: caller identity and permitted operations (FR-7.2), mandatory fields and value ranges (FR-4.3), schema constraints (FR-4.9), the tier gate (FR-7.3), and base versions (FR-7.7). Only valid events are appended, because an invalid event in an authoritative log breaks every future rebuild.

Projectors MUST NOT reject an appended event. If a projector cannot apply one, it MUST halt, move the event to a dead-letter queue, and raise an alarm; it MUST NOT skip the event and continue. Other projectors continue. The serve path continues on the halted projection's last applied state, and the lag is reported in health metrics (FR-14.3, metric 11).

### FR-7.9 — Idempotent projections
*New in 0.2 — D1, M8.*

Every change-log event MUST carry a monotonically increasing sequence number within its log partition. Each projector MUST record the highest sequence number it has applied, and applying an event at or below that number MUST be a no-op. With deterministic IDs (FR-4.10), replaying any range of the log MUST NOT create duplicates.

### FR-7.10 — Projection reconciliation
*New in 0.2 — D1.*

A reconciliation job MUST periodically compare each projection with the change log — by per-range checksums or sampled replay — and repair divergence by replaying the affected range. Divergence MUST be counted in health metrics (FR-14.3, metric 12) and alarmed above a configured threshold.

### FR-7.11 — Snapshots and replay
*New in 0.2 — D3.*

Projectors MAY write snapshots. Each snapshot MUST record the sequence number it covers and a checksum of the projection state.

1. **Validation.** Before a snapshot is used for a rebuild it MUST be validated: restore the previous validated snapshot (or origin), replay the events up to the new snapshot's sequence number, and compare checksums. A snapshot that fails validation MUST be discarded, and the rebuild MUST fall back to the previous validated snapshot.
2. **Retention.** At least the three most recent validated snapshots per projection MUST be kept (proposed).
3. **Replay depth.** The change log MUST never be truncated, and every projection MUST be rebuildable by replay from sequence 0. A full replay from origin MUST be exercised at least quarterly (proposed), and its duration recorded against the rebuild target (NFR-5, NFR-8).

### FR-7.12 — Cross-BU approver policy
*New in 0.2 — M13, D8.*

The approver policy for T4 cross-BU changes MUST be configuration with a documented default. Default: one approver from each affected BU. Alternative policies MAY be configured — for example, approval by the source node's BU with the other BU notified and given a veto window — because requiring both BUs will stall cross-BU edges in a large enterprise. Every policy change MUST be recorded as a change record by `kc-operator`.

### FR-7.13 — Reviewer evidence visibility
*New in 0.2 — M13.*

A reviewer MUST see only evidence within the reviewer's own entitlement scope. Evidence outside it MUST NOT be shown, in whole, in part, or as counts; the review UI states only that evidence outside the reviewer's scope was withheld. This is not configurable and does not vary with the approver policy (FR-7.12).

### FR-7.14 — Deterministic gate
*New in 0.2 — M14.*

`gate.evaluate` and `change.apply` MUST be deterministic code with no model invocation. Any employee who can edit a Confluence page or Jira description controls text that extraction and the spoke agents read, so no model output may decide whether a change is applied.

1. The confidence used by the tier gate MUST be computed by `gate.evaluate` from the proposal's cited evidence, using a configured function of evidence features — fingerprint similarity scores, number of independent sources (FR-8.11), authority rank of the evidence (FR-6.13).
2. A confidence value reported by an agent MUST be ignored.
3. A proposal with no cited evidence MUST be rejected (FR-9.13).
4. Evidence eligibility MUST be checked here (FR-13.1).

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
*Changed in 0.2 — N9.*

No spoke agent may hold a write tool. A **write tool** is any tool that appends to the change log or writes to a projection. All mutation is expressed as a proposal returned to the Curator (FR-9.13). Tools that write only to the task queue, agent memory or traces — `task.create`, `task.escalate`, `memory.episodic.write`, `trace.log` — are not write tools.

### FR-8.3 — Shared base tools
Every agent MUST have: `graph.query`, `memory.episodic.recall`, `memory.episodic.write`, `task.escalate`, `budget.check`, `trace.log`.

### FR-8.4 — Budget enforcement
On budget exhaustion the agent MUST terminate with `escalate` and return partial evidence. It MUST NOT continue looping.

### FR-8.5 — Batching
*Changed in 0.2 — M5, M13.*

The Curator MUST batch same-type tasks into a single agent invocation where possible (e.g. ten ambiguous merges as one Resolver task with ten candidates). Only tasks with the same task scope may share a batch (FR-5.7). A batch task carries its own `contextId` and MUST list the `contextId` of every source event it covers (FR-9.12, FR-10.6).

### FR-8.6 — Model tiering
Resolver and Verifier MUST use a small model. Reconciler and Schema Steward MAY use a larger model. Model selection MUST be configuration.

### FR-8.7 — Curator
*Changed in 0.2 — M5, M14, N10.*

**Goal:** fabric health.
**Tools:** `metrics.read`, `queue.inspect`, `queue.prioritise`, `a2a.dispatch`, `budget.allocate`, `gate.evaluate`, `change.apply`, `human.request_input`.
**Behaviour:** allocates the daily compute budget across spokes against the weakest health metric, each metric first normalised against its configured target (FR-14.4). Triage routing MUST be a deterministic lookup, not an LLM call. `gate.evaluate` and `change.apply` MUST be deterministic code (FR-7.14); any model use is confined to prioritisation. The Curator receives fast-path escalations from the task queue, not over A2A (FR-10.2).

### FR-8.8 — Resolver
*Changed in 0.2 — M15.*

**Goal:** correct entity identity.
**Tools:** `alias.lookup`, `alias.propose`, `graph.neighbourhood`, `source.fetch`, `source.search`, `embedding.similarity`, `fingerprint.compare`, `merge.propose`, `split.propose`.
**Behaviour:** MUST propose a new alias rule after observing the same manual correction three times. Consumes mention candidates produced by prose extraction (FR-6.15) as well as resolution escalations (FR-6.9).

### FR-8.9 — Reconciler
*Changed in 0.2 — M7, D7.*

**Goal:** one coherent truth per claim.
**Tools:** `claim.fetch`, `source.current_state`, `authority.rank`, `graph.temporal_query`, `owner.lookup`, `human.ask_owner`, `supersede.propose`, `coexist.propose`.
**Behaviour:** MUST apply the configured authority ranking for the claim's node type and property (FR-6.13), not an invented one. Only equal-rank conflicts reach it; different-rank conflicts are resolved deterministically by the fast path (FR-6.16). Where ambiguous, MUST propose coexistence with lowered confidence on both claims rather than guessing. `owner.lookup` and `human.ask_owner` resolve to the `OWNED_BY` Team until `Person` exists (FR-4.12). In MVP the Reconciler is stubbed and escalates to the human queue (§17).

### FR-8.10 — Verifier
*Changed in 0.2 — N9.*

**Goal:** graph trustworthiness.
**Tools:** `staleness.list`, `retrieval.traffic`, `blast_radius`, `source.fetch`, `hash.compare`, `verification.propose`.
**Behaviour:** given a fixed nightly verification budget, MUST select which nodes to verify using retrieval traffic and blast radius. MUST NOT simply drain the staleness queue in order. `verification.propose` returns a T1 proposal; it does not write (FR-8.2).

### FR-8.11 — Prospector
*Changed in 0.2 — N11.*

**Goal:** close knowledge gaps.
**Tools:** `gap_query.run`, `source.discover`, `code.parse_imports`, `iac.parse`, `graph.diff`, `hypothesis.test`, `task.create`.
**Behaviour:** MUST verify a hypothesis against a second, **independent** source — a different source system, or a different author within the same system — before filing a task. Two copies of one document are not corroboration. Has no write scope.

### FR-8.12 — Retrieval Critic
*Changed in 0.2 — M13.*

**Goal:** reduce bad answers.
**Tools:** `retrieval.replay`, `retrieval.replay_variant`, `answer.feedback_fetch`, `trace.fetch`, `node.attribute_blame`, `task.create`.
**Behaviour:** MUST isolate the cause by varying one retrieval parameter at a time (hop budget, anchor, track selection) before filing a repair task. Replays MUST run under the original request's effective scope, never the agent's own (FR-5.7).

### FR-8.13 — Schema Steward
**Goal:** schema fits reality.
**Tools:** `extraction.failure_sample`, `cluster`, `schema.read`, `schema.simulate`, `coverage.estimate`, `proposal.submit`.
**Behaviour:** advisory only. MUST simulate a proposed change against a held-out failure sample and report estimated coverage improvement. MUST NOT apply changes.

---

## 9. OKF Contract

OKF v0.2 is a **family** of messages (FR-9.7), not a single envelope. Every message is JSON, carries `okf_version` and `message_type`, and travels in an A2A `DataPart` (FR-10.4).

### FR-9.1 — Canonical envelope (`context_response`)
*Changed in 0.2 — B4, M1.*

All retrieved context MUST be returned in this envelope, regardless of consumer or intent:

```json
{
  "okf_version": "0.2",
  "message_type": "context_response",
  "request_id": "string",
  "intent": "test_planning | design | impact_analysis | lookup",
  "as_of": "ISO-8601 | null",
  "anchors": [
    { "id": "string", "type": "string", "bu": "string" }
  ],
  "entities": [
    {
      "id": "string",
      "type": "string",
      "subtype": "string | null",
      "visibility": "full | metadata",
      "hop": 0,
      "identity_claim_ids": ["string"],
      "props": {
        "status": {
          "value": "active",
          "claim_id": "string",
          "provenance_class": "source-derived",
          "confidence": 0.92,
          "contested": false
        }
      }
    }
  ],
  "relations": [
    {
      "id": "string",
      "from": "string",
      "to": "string",
      "type": "DEPENDS_ON",
      "hop": 1,
      "claim_id": "string",
      "provenance_class": "source-derived",
      "confidence": 0.88,
      "props": {}
    }
  ],
  "paths": [
    { "id": "string", "anchor_id": "string", "relation_ids": ["string"] }
  ],
  "artifacts": [
    {
      "node_id": "string",
      "chunk_id": "string",
      "kind": "openapi | requirement | test | defect | doc | adr | incident",
      "section_path": ["string"],
      "content": "string",
      "ref": "string",
      "score": 0.0
    }
  ],
  "gaps": [
    { "code": "NO_TEST_COVERAGE", "node_id": "string | null" }
  ],
  "provenance": [
    {
      "id": "claim_id or chunk_id",
      "source_system": "string",
      "source_instance": "string",
      "source_id": "string",
      "valid_from": "ISO-8601",
      "valid_to": "ISO-8601 | null",
      "provenance_class": "source-derived | agent-derived | human-confirmed",
      "extraction_method": "parser | llm | agent | human"
    }
  ],
  "budget": {
    "hops": 2,
    "tokens_returned": 4100,
    "tokenizer": "string",
    "cache_hit": true
  }
}
```

Rules:

1. Every `claim_id` and `chunk_id` referenced anywhere in the response MUST have exactly one matching entry in `provenance`. v0.1 keyed provenance by `claim_id` while carrying it nowhere else, so nothing could be traced.
2. `anchors` is a list: `impact_analysis` over a change set needs several seeds, and FR-11.2 anchors on seed nodes in the plural.
3. `paths` expresses traversal routes explicitly; `hop` alone cannot express which route reached a node when two do (FR-11.5).
4. A multi-valued property (FR-4.9) appears as an array of the same value objects.
5. An entity with `"visibility": "metadata"` MUST carry only the whitelisted properties of FR-5.2 and no artifacts (FR-9.4).
6. `gaps` codes are a closed enum in 0.2: `NO_TEST_COVERAGE`, `NO_REQUIREMENT_LINK`, `NO_OWNER`, `CONTESTED_PROPERTY`, `HOP_BUDGET_REACHED`, `TOKEN_CEILING_TRIMMED`. A code carrying a `node_id` MUST be emitted only for entities returned with `"visibility": "full"`; budget codes carry `node_id: null`. Free-text gaps could name nodes the caller cannot see.
7. `tokenizer` names the configured tokenizer that `tokens_returned` was counted with.

### FR-9.2 — Version freeze
*Changed in 0.2 — M6, B4.*

`okf_version` MUST be present in every message. The whole family (FR-9.7) is drafted at Phase 0 and frozen at the **Phase 2 exit**, once recall@k has been measured against the draft (§19) — freezing at Phase 0, before the serve path exists, would freeze guesses. Breaking changes after the freeze require a version bump. Consumers pin a version in `context_request.okf_version`, and KC MUST reject an unsupported version with error code `UNSUPPORTED_VERSION`.

### FR-9.3 — Artifact suppression
*Changed in 0.2 — B4.*

Consumers MUST be able to request `include_artifacts: false` (FR-9.8) and receive topology only.

### FR-9.4 — Visibility marking
*Changed in 0.2 — N12.*

An entity whose visibility class is `topology` and whose owning BU is **not** in the caller's effective scope MUST be returned with `"visibility": "metadata"`, MUST carry only the properties whitelisted in FR-5.2, and MUST NOT have content in `artifacts`. Callers inside the owning BU receive `"visibility": "full"`.

### FR-9.5 — Provenance marking
*Changed in 0.2 — M1.*

Every property value, relation and artifact MUST reference its provenance entry by `claim_id` or `chunk_id`, and that entry MUST give `source_id` and `valid_from`. Agent-derived content MUST be labelled `provenance_class: agent-derived` where it appears, not only inside `provenance`.

### FR-9.6 — No prose
*Changed in 0.2 — M1.*

The KC Agent MUST NOT return generated natural-language answers. Responses are data only: gaps and errors are codes, never sentences. The only free text a response may carry is verbatim source content in `artifacts.content`.

---

### FR-9.7 — Message family
*New in 0.2 — B4.*

v0.1 defined the response only, while requiring every A2A payload to carry OKF — which a merge proposal, an escalation or an approval cannot do. The family is:

| `message_type` | Direction | Defined in |
|---|---|---|
| `context_request` | Consumer → Curator | FR-9.8 |
| `context_response` | Curator → consumer | FR-9.1 |
| `disambiguation` | Curator → consumer | FR-9.9 |
| `disambiguation_choice` | Consumer → Curator | FR-9.9 |
| `error` | KC → any caller | FR-9.10 |
| `feedback` | Consumer → Curator | FR-9.11 |
| `spoke_task` | Curator → spoke | FR-9.12 |
| `proposal` | Spoke → Curator; Curator → review UI | FR-9.13 |
| `escalation` | Spoke → Curator; Curator → human queue | FR-9.13 |
| `no_action` | Spoke → Curator | FR-9.13 |
| `review_decision` | Review UI → Curator | FR-9.14 |

Every message MUST carry `okf_version` and `message_type`. The family is versioned and frozen together (FR-9.2).

### FR-9.8 — `context_request`
*New in 0.2 — B4.*

```json
{
  "okf_version": "0.2",
  "message_type": "context_request",
  "request_id": "string",
  "intent": "test_planning | design | impact_analysis | lookup",
  "anchors": [
    { "id": "string" },
    { "name": "string", "type": "string | null" }
  ],
  "query_text": "string | null",
  "as_of": "ISO-8601 | null",
  "include_artifacts": true,
  "max_tokens": 6000,
  "max_hops": 2
}
```

1. `request_id` is client-generated and MUST be unique per caller; reuse MUST be rejected with `INVALID_REQUEST`.
2. Each anchor is given by `id` or by `name` (optionally typed). The number of anchors MUST be capped by configuration (default 10, proposed).
3. `intent` is supplied by the caller, not classified by KC: the serve path has no LLM (FR-11.1).
4. `query_text` feeds the lexical, vector and cross-encoder tracks (FR-11.2, steps 8 and 11). When null, KC derives it from anchor names and a per-intent template in configuration.
5. `as_of` sets the time scope. Only a request carrying it reads the claim store (FR-11.8).
6. `max_tokens` and `max_hops` are optional and may only **lower** the per-intent ceilings in configuration (FR-11.4). Null uses the configured default.
7. "Scope" in the FR-11.6 cache key means `as_of`, `max_hops`, `max_tokens` and `query_text`.

### FR-9.9 — `disambiguation`
*New in 0.2 — B4, B2.*

Where an anchor name matches more than one node **within the caller's effective scope**, the Curator MUST set the task to A2A state `input-required` and return:

```json
{
  "okf_version": "0.2",
  "message_type": "disambiguation",
  "request_id": "string",
  "ambiguous_anchors": [
    {
      "anchor_index": 0,
      "name": "ledger",
      "candidates": [
        { "id": "string", "type": "API", "name": "ledger-api", "bu": "payments" }
      ]
    }
  ]
}
```

The consumer replies in the same task:

```json
{
  "okf_version": "0.2",
  "message_type": "disambiguation_choice",
  "request_id": "string",
  "choices": [ { "anchor_index": 0, "id": "string" } ]
}
```

Candidates MUST be limited to the caller's effective scope. Nodes outside it MUST NOT appear, MUST NOT be counted, and MUST NOT cause a disambiguation to be raised at all (FR-11.3).

### FR-9.10 — `error`
*New in 0.2 — B4, B2.*

```json
{
  "okf_version": "0.2",
  "message_type": "error",
  "request_id": "string | null",
  "code": "UNAUTHENTICATED | NOT_FOUND | INVALID_REQUEST | UNSUPPORTED_VERSION | BUDGET_EXCEEDED | UNAVAILABLE",
  "retryable": false
}
```

1. An error MUST NOT carry free text, a hint, or a partial result.
2. `NOT_FOUND` MUST be returned both when a name or ID does not exist and when it exists outside the caller's effective scope, byte-identically (FR-5.8).
3. Errors are returned as a task in A2A state `failed`. Transport-level authentication failures use the transport's standard response and carry no OKF body.

### FR-9.11 — `feedback`
*New in 0.2 — B4.*

```json
{
  "okf_version": "0.2",
  "message_type": "feedback",
  "request_id": "string",
  "rating": "poor | acceptable | good",
  "reasons": ["MISSING_ENTITY | WRONG_ENTITY | STALE | IRRELEVANT_ARTIFACT | TRUNCATED | AMBIGUOUS_ANCHOR"],
  "node_ids": ["string"]
}
```

`request_id` MUST reference a prior `context_response`. Feedback MUST be accepted only from the same end user and application that made that request, and `node_ids` MUST be limited to IDs present in that response. Feedback carries no free text. It is submitted through skill `kc.context.feedback` (FR-10.3) and routed per FR-11.7.

### FR-9.12 — `spoke_task`
*New in 0.2 — B4.*

```json
{
  "okf_version": "0.2",
  "message_type": "spoke_task",
  "task_type": "resolve | reconcile | verify | discover | diagnose | schema",
  "run_id": "string",
  "task_scope": ["bu"],
  "source_context_ids": ["string"],
  "subjects": [ { "id": "string", "base_version": 0 } ],
  "candidates": [ {} ],
  "budget": { "tool_calls": 40, "tokens": 60000, "wall_clock_s": 120 }
}
```

`task_scope` bounds every read in the task (FR-5.7). `source_context_ids` lists the source events a batch covers (FR-8.5, FR-10.6). `candidates` holds task-type-specific items — merge candidates, conflicting claim IDs, mention candidates — each defined in that skill's schema in its Agent Card. `budget` MUST be enforced by the agent loop (FR-8.4, NFR-7).

### FR-9.13 — `proposal`, `escalation`, `no_action`
*New in 0.2 — B4.*

```json
{
  "okf_version": "0.2",
  "message_type": "proposal",
  "proposal_id": "string",
  "run_id": "string",
  "agent": "resolver",
  "action": "merge | split | add_claim | close_claim | supersede | coexist | alias_rule | verify | visibility_change | cross_bu_edge",
  "subjects": [ { "id": "string", "base_version": 0 } ],
  "changes": [ {} ],
  "evidence": [ { "claim_id": "string" }, { "chunk_id": "string" } ]
}
```

```json
{
  "okf_version": "0.2",
  "message_type": "escalation",
  "run_id": "string",
  "agent": "resolver",
  "reason": "BUDGET_EXHAUSTED | AMBIGUOUS | NO_AUTHORITY | STUBBED | APPROVAL_EXPIRED | STALE_PROPOSAL",
  "subjects": [ { "id": "string", "base_version": 0 } ],
  "partial_evidence": [ { "claim_id": "string" } ]
}
```

```json
{
  "okf_version": "0.2",
  "message_type": "no_action",
  "run_id": "string",
  "agent": "resolver",
  "reason": "ALREADY_CONSISTENT | DUPLICATE_TASK | SUBJECT_NOT_LIVE",
  "subjects": [ { "id": "string", "base_version": 0 } ]
}
```

1. A proposal MUST NOT carry a tier or a confidence value: the gate computes both (FR-7.14).
2. `changes` is a list of claim operations in change-log event format.
3. `evidence` MUST be non-empty, and MUST cite claims and chunks by ID so the gate can check eligibility (FR-13.1).
4. `base_version` is the change-log sequence number the agent read (FR-7.7).
5. The same `proposal` message, with evidence filtered to the reviewer (FR-7.13), is what a review task carries (FR-10.5).

### FR-9.14 — `review_decision`
*New in 0.2 — B4.*

```json
{
  "okf_version": "0.2",
  "message_type": "review_decision",
  "proposal_id": "string",
  "decision": "approve | reject | modify",
  "reason_code": "string",
  "rationale": "string",
  "modified_changes": [ {} ]
}
```

The reviewer identity comes from the authenticated A2A context, never from the message body. `rationale` is human-written free text; it is stored in episodic memory (FR-12.5) and MUST be treated as untrusted data by agents. Approved and modified changes are applied by `kc-review` as `human-confirmed` (FR-7.2), after the base-version check of FR-7.7.

---

## 10. A2A Layer

### FR-10.1 — Protocol version
*Changed in 0.2 — N19.*

The system MUST implement A2A v0.3.0. The pin MUST be re-confirmed at Phase 0 exit, before the Agent Cards are drafted for freeze: the protocol is still moving, and a card frozen against a superseded version is expensive to unwind.

### FR-10.2 — Topology
*Changed in 0.2 — M5.*

Hub and spoke. Inside the KC boundary the Curator is the only A2A client and the six spokes are A2A servers. Spoke-to-spoke communication is prohibited.

- The fast path reaches the Curator through the task queue, not over A2A (FR-6.17).
- Callers outside the boundary — consumer applications, the review UI, the scheduler and the operator console — are A2A clients of the Curator only, and never of a spoke.

### FR-10.3 — Agent Cards
*Changed in 0.2 — M5, B4, DR6.*

Each agent MUST publish an Agent Card. The Curator publishes **two**: an external card, the only card exposed outside the KC boundary, and an internal card. v0.1 placed `kc.orchestrate` on the externally exposed card, which advertised an internal skill and left it with no legal caller.

| Card | Skill IDs | Callers |
|---|---|---|
| Curator (external) | `kc.context.retrieve`, `kc.context.feedback` | Consumer applications |
| Curator (internal) | `kc.orchestrate`, `kc.review` | Scheduler and operator console; review UI |
| Resolver | `kc.entity.resolve` | Curator |
| Reconciler | `kc.claim.reconcile` | Curator |
| Verifier | `kc.node.verify` | Curator |
| Prospector | `kc.gap.discover` | Curator |
| Retrieval Critic | `kc.answer.diagnose` | Curator |
| Schema Steward | `kc.schema.propose` | Curator |

The internal card MUST NOT be reachable from outside the boundary. Where A2A's authenticated extended card is used instead of a separate endpoint, the internal skills MUST appear only in the authenticated form.

### FR-10.4 — Payload type
*Changed in 0.2 — B4.*

All A2A payloads MUST be `DataPart`s carrying an OKF family message (FR-9.7). `TextPart` MUST NOT be used for inter-agent communication.

### FR-10.5 — Human-in-the-loop
*Changed in 0.2 — M5, M13.*

Human approval MUST use the A2A `input-required` task state:

1. When the gate requires a human (FR-7.3), the Curator creates a task under `kc.review` carrying the `proposal` (FR-9.13) with evidence filtered to what the reviewer may see (FR-7.13), and sets the task to `input-required`.
2. The review UI, acting as A2A client, responds in that task with a `review_decision` (FR-9.14). The approver policy (FR-7.12) decides when enough decisions have been received.
3. Task state MUST persist until the policy is satisfied or the task expires. Expiry is **5 business days**, configuration (NFR-4). On expiry the task MUST auto-escalate to the manager of the owning team (`OWNED_BY` → Team → manager, resolved through the directory) with an `escalation` carrying reason `APPROVAL_EXPIRED`. Nothing is applied by the expiry itself.
4. While the kill switch is engaged, pending approval tasks freeze: decisions are recorded but not applied (FR-7.6), and the expiry clock stops. On release the clock **restarts** from the full 5 business days, and every recorded decision is re-validated against base versions (FR-7.7) before it is applied.

### FR-10.6 — Context grouping
*Changed in 0.2 — M5.*

All tasks arising from a single source event MUST share that event's `contextId`. An A2A task has exactly one `contextId`, so a batch task (FR-8.5) takes its own and MUST list every source event's `contextId` in `source_context_ids` (FR-9.12). The audit trail MUST be reconstructable in both directions: from a source event to every task it caused, and from a task to every source event it covers.

### FR-10.7 — Identity propagation
*Changed in 0.2 — M11, M13.*

The on-behalf-of identity of a consumer request (FR-5.5) MUST propagate through the A2A authentication context and MUST be the identity against which the serve path evaluates entitlements.

Curator-to-spoke tasks carry a `task_scope` instead (FR-5.7, FR-9.12), because a background task has no human caller. For a task that replays a consumer request, `task_scope` MUST be that request's effective scope.

---

## 11. Serve Path

### FR-11.1 — No LLM
*Changed in 0.2 — N13.*

The serve path MUST contain no LLM invocation, where LLM means a generative language model (§2). Reranking MUST use a cross-encoder; embedding models and classifiers are permitted. The serve path MUST be deployed without LLM credentials and without network egress to LLM endpoints, so that the property is structural rather than a matter of trace inspection (AT-16).

### FR-11.2 — Pipeline order
*Changed in 0.2 — B2, B3, D1, D4, N14.*

The serve path MUST execute in this order. v0.1 resolved names at step 3 and applied the entitlement filter at step 4, which leaked the existence of restricted nodes through disambiguation.

1. Authenticate: resolve the end user and the calling application (FR-5.5); reject if either is unresolved.
2. Derive the effective scope (FR-5.10).
3. Validate the request (FR-9.8); apply the per-intent hop and token ceilings (FR-11.4).
4. Look up the cache (FR-11.6). On a hit, go to step 15.
5. Resolve anchor names to node IDs through the alias registry, **within the effective scope** (FR-11.3).
6. Anchor on seed nodes.
7. Traverse within budget, applying in-query: the entitlement filter (FR-5.4), the edge-visibility rule (FR-5.12), and the current-claim or `as_of` filter on every hop (FR-11.9).
8. Multi-track fetch over entitled namespaces only (FR-5.6): lexical BM25, vector (embedded classes only), and targeted fetch by node ID.
9. Apply the chunk liveness filter (FR-11.10).
10. RRF fusion.
11. Cross-encoder rerank.
12. If `as_of` is set, read historical property values from the claim store (FR-11.8). Otherwise the claim store is not read.
13. Assemble the `context_response`, preserving traversal paths (FR-11.5).
14. Trim to the token ceiling: outermost hop first, then lowest rerank score within a hop. Relations and paths referencing a trimmed entity MUST be removed so the response stays referentially intact, and `TOKEN_CEILING_TRIMMED` MUST be emitted in `gaps`.
15. Write the cache entry (on a miss) and emit the trace and audit records (FR-15.1, FR-15.2).

### FR-11.3 — Ambiguity handling
*Changed in 0.2 — B2.*

If an anchor name resolves to more than one node **within the caller's effective scope**, the system MUST return a `disambiguation` (FR-9.9) rather than guessing.

Nodes outside the effective scope MUST NOT be considered at any point:

- a name matching one in-scope node and any number of out-of-scope nodes resolves to the in-scope node, with no disambiguation;
- a name matching only out-of-scope nodes returns `NOT_FOUND`, byte-identical to an unknown name (FR-5.8).

Alias registry entries MUST carry `bu_owner` and `visibility_class` so that lookup can be scoped in-query.

### FR-11.4 — Traversal budget
*Changed in 0.2 — N15.*

Hop limits MUST be configured per intent. Defaults: `lookup` = 1, `test_planning` = 2, `design` = 2 (proposed), `impact_analysis` = 3. Fan-out per hop MUST be capped and ranked by edge criticality, a per-edge-type weight in configuration. Relevance MUST decay per hop. Reaching a hop or fan-out limit MUST emit `HOP_BUDGET_REACHED` in `gaps`.

### FR-11.5 — Dependency path preservation
*Changed in 0.2 — M1.*

The assembled OKF MUST express traversal explicitly: `relations` carry `hop` depth, and `paths` lists each route from an anchor as an ordered list of relation IDs. Results MUST NOT be flattened into an unordered set.

### FR-11.6 — Caching
*Changed in 0.2 — M12, D1.*

Responses MUST be cached on `hash(okf_version + intent + anchors as requested + as_of + max_hops + max_tokens + query_text + include_artifacts + effective_scope + cache_epoch)`.

Invalidation MUST be triggered when a projector **applies** an event, not when the event is appended to the log; otherwise a request arriving in between recomputes stale data and caches it as fresh.

An entry MUST be invalidated when an applied event touches:

- any node considered during its traversal, including nodes excluded by fan-out caps or token trimming;
- any edge incident to those nodes;
- any alias key used to resolve its anchors;
- any chunk returned or ranked for it.

Visibility-class changes and configuration changes affecting the serve path MUST instead increment a **global cache epoch**. Invalidation therefore never depends on nodes outside the caller's scope: if it did, `budget.cache_hit` would flip when a restricted node in another BU changed, which is a side channel under FR-5.4(e).

Cache hit status MUST be reported in `budget.cache_hit`.

### FR-11.7 — Feedback intake
*Changed in 0.2 — B4.*

Consumers MUST be able to signal poor context quality against a `request_id` with a `feedback` message (FR-9.11) through skill `kc.context.feedback`. Signals MUST be routed to the Retrieval Critic — in MVP, to the human queue (§17).

### FR-11.8 — Claim store read trigger
*New in 0.2 — D4.*

The serve path MUST read the claim store **only** when the request carries `as_of`. Without `as_of`, current values and their provenance come from the graph projection (FR-4.7), and the serve path MUST NOT read the claim store. A default join would cost a second hop per entity and break NFR-1.

### FR-11.9 — Current-claim filter
*New in 0.2 — B3, D4.*

Closed claims and closed edges remain in the graph (FR-4.4, FR-4.7), so currency MUST be filtered explicitly:

1. Traversal MUST exclude, in-query and on every hop, nodes that are not live and edges whose `valid_to` is not null.
2. For an `as_of` request, traversal MUST filter edges on `valid_from <= as_of` and (`valid_to` is null or `valid_to > as_of`), then confirm node liveness and edge validity at `as_of` against the claim store (FR-11.8).
3. These filters MUST be served by indexes, so that traversal cost is bounded by live edges rather than by total history (AT-41).

### FR-11.10 — Chunk liveness
*New in 0.2 — D1.*

The index and graph projections advance independently, so an index can still hold chunks of a node that has been closed or rolled back.

1. Index entries MUST carry their node's liveness, maintained by the index projector.
2. Lexical and vector queries MUST filter on liveness inside the index query where the index supports it, and otherwise before fusion.
3. For an `as_of` request, liveness at `as_of` applies.
4. This is a liveness check on results that namespace selection has already entitlement-scoped (FR-5.6); it is not entitlement post-filtering under FR-5.4.

---

## 12. Agent Memory

### FR-12.1 — Physical separation
Agent memory MUST be stored separately from the knowledge graph. Agent beliefs MUST NOT be retrievable as enterprise facts.

### FR-12.2 — Memory types
*Changed in 0.2 — M14.*

- **Working** — per-run, ephemeral
- **Episodic** — prior decisions with rationale and evidence, durable
- **Procedural** — learned heuristics, human-readable and human-editable

Procedural memory is an instruction channel into the agents. Edits to it MUST be versioned and reviewed before agents can recall them, and every edit MUST be attributed to a reviewer identity.

### FR-12.3 — Actor isolation
Episodic memory MUST be keyed to a resolved agent and task identity. If the identity resolves to an anonymous placeholder, memory writes MUST be rejected.

### FR-12.4 — Promotion
A pattern observed three or more times MAY be promoted from episodic to procedural. Promotion MUST be logged and MUST be human-reviewable.

### FR-12.5 — Rejection capture
Human rejection of an agent proposal MUST be written to episodic memory with the rejection rationale (FR-9.14).

### FR-12.6 — BU labelling
*New in 0.2 — M13.*

Every episodic and procedural memory entry MUST be labelled with the BUs of the subjects it concerns; entries with no BU-specific content are labelled `commons`. Recall MUST return only entries whose labels fall within the recalling task's scope (FR-5.7). Without this, a recall during a BU-A task can return evidence gathered in a BU-B task.

---

## 13. Inbreeding Guard

### FR-13.1 — Evidence eligibility
*Changed in 0.2 — M4, B3.*

An `agent-derived` claim MUST NOT be usable as evidence supporting another `agent-derived` claim.

1. Enforcement is in `gate.evaluate` (FR-7.14), not in a single tool and not by prompt instruction. v0.1 placed it in "the evidence-fetch tool", which does not exist; `graph.query` is a base tool every agent holds (FR-8.3) and returns agent-derived content.
2. Every proposal MUST cite its evidence by ID (FR-9.13), and the gate MUST reject an agent-derived proposal that cites agent-derived evidence.
3. Every agent read tool MUST label each returned claim with its `provenance_class`.
4. Eligibility is evaluated **per claim**, not per node: a node carrying both agent-derived and source-derived claims may be supported by its source-derived claims. This is how an agent hypothesis later corroborated by a source becomes usable.

### FR-13.2 — Differential decay
*Changed in 0.2 — B3.*

`agent-derived` claims MUST carry a shorter TTL and faster confidence decay than `source-derived` claims. (Decay itself is out of MVP scope, §17.)

### FR-13.3 — No auto-promotion
Promotion from `agent-derived` to `human-confirmed` MUST require explicit human review. No automatic promotion path may exist.

### FR-13.4 — Ratio alarm
*Changed in 0.2 — M4, B3.*

The system MUST track the ratio of open `agent-derived` claims to open `source-derived` claims and raise an alarm above a configured threshold (15%). This is a fixed guardrail, not a provisional target: it needs no baseline (§19, threshold policy). LLM extraction output is `source-derived` (FR-6.5) and counts in the denominator: it quotes a source span, so classing it as agent-derived would trip this alarm on Confluence ingestion alone.

---

## 14. Maintenance and Health

### FR-14.1 — Staleness sweep
*Changed in 0.2 — N16, B1.*

A nightly job, writing as `kc-maintenance` (FR-7.2), MUST flag nodes exceeding their per-type TTL. Defaults, all configuration:

| Node types | TTL | Status |
|---|---|---|
| Service, API, Endpoint, Topic (service topology) | 7d | v0.1 default, kept |
| Requirement | 30d | v0.1 default, kept |
| Document (all subtypes) | 90d | v0.1 default, kept |
| Component, Environment, DataEntity, TestCase, TestRun, Defect, Team, BusinessGroup | 30d | provisional |

The provisional 30d MUST be re-baselined at Phase 2 from the observed change rate per node type, rather than guessed again (§19, threshold policy).

### FR-14.2 — Reconciliation sweep
*Changed in 0.2 — B1, M8, M3.*

A weekly full source scan, writing as `kc-ingest`, MUST diff source state against the graph and repair drift from missed webhooks. The scan MUST compare the **set of source object IDs**, to catch deletions whose webhooks were missed (FR-6.18), and **object-level permissions** (FR-5.9). This is distinct from projection reconciliation (FR-7.10), which compares projections with the change log.

### FR-14.3 — Health metrics
*Changed in 0.2 — M3, M6, D1, D5.*

The system MUST compute and expose:

1. Coverage — % of live services with owner, dependencies and test linkage
2. Freshness — median node age relative to TTL
3. Confidence — % of open claims above the retrieval threshold
4. Open contradiction count, split by equal-rank (escalated) and different-rank (auto-resolved)
5. Gap closure rate — Prospector tasks filed vs closed
6. Retrieval quality — recall@k and nDCG on the fixed question set
7. Human intervention rate
8. LLM-free ingestion rate (target ≥ 85%, denominator per FR-6.14)
9. Agent-derived claim ratio (per FR-13.4)
10. Source skip rate — objects skipped `OBJECT_RESTRICTED` or quarantined `UNMAPPED_CONTAINER`, by source and container, with an alarm threshold (FR-5.9, FR-5.3)
11. Projection lag — per projector, events behind the log and seconds behind, plus any halted projector (FR-7.8)
12. Projection divergence — detections by the reconciliation job (FR-7.10)

### FR-14.4 — Metrics drive allocation
*Changed in 0.2 — N10.*

The Curator's budget allocator MUST read the health metrics, normalise each against its configured target, and weight dispatch toward the weakest. Raw values are not comparable across metrics.

### FR-14.5 — Confidence decay
Unverified nodes MUST lose confidence over time. Nodes below the retrieval threshold MUST be excluded from serve results and surfaced for review.

---

## 15. Observability

### FR-15.1 — Tracing
*Changed in 0.2 — M9.*

The system MUST emit OpenInference-conformant spans over OTLP, one span per pipeline stage and one per agent tool call.

Spans MUST record identifiers, counts, scores and timings only. They MUST NOT record chunk or document content, property values, query text, prompts or completions; instrumentation MUST be configured to suppress input and output payloads, which it captures by default. Otherwise the trace backend becomes an unsegregated mirror of every BU's content, readable by anyone with observability access. Trace store access is limited to operators.

Agent prompts and completions MAY be retained only in the BU-partitioned decision log (FR-15.3), never in the trace store.

### FR-15.2 — Audit
*Changed in 0.2 — M9, DR7.*

Every serve request MUST record exactly: end user and application identity, effective scope, the request, the cache epoch, the change-log sequence number the response was built from, the node IDs returned, and the artifact chunk IDs returned. The sequence number is what makes offline replay possible, and the chunk IDs are the record of which content was disclosed; neither may be dropped.

The record MUST NOT contain IDs or counts of nodes excluded by entitlement. Pre-filtering (FR-5.4) never produces that list, and computing it in-path would both place other BUs' identifiers in this caller's record and reintroduce a timing side channel. Where an investigation needs it, a privileged audit job MUST recompute it offline by replaying the request against the projections at the recorded sequence number; that job and its output are access-controlled and themselves audited.

This is a **deliberate deviation** from the v0.1 review, which asked for per-BU counts of excluded nodes in the request record (§20, DR7). Counting in-path requires touching restricted nodes in order to count them, which is the timing side channel AT-6 exists to prevent. Do not restore in-path counting.

### FR-15.3 — Decision log
*Changed in 0.2 — M13.*

Every agent decision MUST record: goal, tools invoked, evidence gathered (by claim and chunk ID), verdict, confidence as computed by the gate, and terminal state. The decision log MUST be partitioned by BU like agent memory (FR-12.6) and encrypted per FR-5.11.

---

## 16. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 | Serve path P95 latency, measured on cache miss: < 2s for `lookup`, < 3s for `test_planning` (proposed), < 3s for `design` (proposed), < 5s for `impact_analysis` *(changed in 0.2 — N17)* |
| NFR-2 | Ingestion lag: median < 5 minutes from source change to the change being applied by the graph and index projectors, measured on the fast path only *(changed in 0.2 — N17, D1)* |
| NFR-3 | Fail closed on identity, entitlement, or store errors |
| NFR-4 | All thresholds, TTLs, budgets, BU mappings, IdP group mappings, visibility defaults, authority rankings, approver policies, tokenizer choice and key policy in configuration, not code |
| NFR-5 | The graph, claim store, indexes and alias registry MUST be fully rebuildable from the change log alone (FR-7.11). The raw store is required only to re-extract under a new `extraction_version`. Rebuild duration MUST be measured against a target set in Phase 0 *(changed in 0.2 — M8, D1, D3)* |
| NFR-6 | No secrets in the change log, raw store, projections, traces, agent memory or audit records. Secret detection MUST run before raw-store persistence (FR-6.3) and before change-log append *(changed in 0.2 — M9)* |
| NFR-7 | Agent loops MUST have hard caps on tool calls, tokens and wall-clock time |
| NFR-8 | Capacity and operations assumptions MUST be fixed at Phase 0 exit. Store choice and NFR-1 depend on them *(new in 0.2 — N18)* |

**NFR-8 — assumptions to fix in Phase 0**

| Assumption | Value |
|---|---|
| Live nodes and open claims at 12 months | TBD Phase 0 |
| Source events per day, and peak hour | TBD Phase 0 |
| Serve requests per second, peak | TBD Phase 0 |
| Serve availability SLO | TBD Phase 0 |
| Retention: raw store, traces, audit, agent memory | TBD Phase 0 |
| Full rebuild target duration (NFR-5) | TBD Phase 0 |
| LLM providers and data-residency constraints | TBD Phase 0 |
| Backup and restore: RPO and RTO | TBD Phase 0 |

---

## 17. MVP Scope

*Changed in 0.2 — B1, D1, D6, M10, N20.*

**In scope:**
1. Sources: Git (OpenAPI specs, dependency manifests, test code, API collection files), Jira, Confluence
2. One production BU plus `commons`, **and a synthetic BU-B fixture** used by every entitlement test from Phase 1 onward (FR-5.4)
3. Change-log-first write model: graph, claim store, indexes and alias registry as projections (FR-7.1)
4. Lexical-first retrieval; embedding of Confluence prose only
5. Curator plus Resolver as full agents; remaining four spokes stubbed to escalate to the human queue
6. Event-driven ingestion; the fast path writes directly as `kc-ingest` (FR-7.2)
7. Dry-run for all agent proposals: the agent proposes, a human applies (FR-7.7)
8. OKF v0.2 message family and Curator Agent Cards drafted in Phase 0, frozen at the Phase 2 exit (FR-9.2)
9. One consumer integration: the API testing solution, calling with on-behalf-of identity (FR-5.5)

**Out of scope for MVP:**
1. Zephyr, ADO and CMDB connectors
2. A second **production** BU. Cross-BU entitlement enforcement is built and tested against the synthetic BU-B fixture, because retrofitting it later is the most expensive change in this design
3. Prospector, Verifier, Retrieval Critic and Schema Steward as live agents
4. Confidence decay, and agent-driven cross-source supersession. Same-source updates and different-rank contradiction resolution by the fast path are in scope (FR-6.11, FR-6.16)
5. A2A streaming; synchronous request/response and `input-required` tasks only
6. Automated application of any agent proposal, at T1 and above. All proposals are dry-run until Phase 6
7. The `Person` node type (FR-4.12)

**MVP boundary:** end of Phase 5 (§19).

---

## 18. Acceptance Tests

*Changed in 0.2 — N21, M6, and the requirements each row cites.*

Every MUST requirement is expected to map to at least one row here, or to be marked review-verified in the FR→AT matrix produced in Phase 0 (§19). The "0.2" column records whether a row is new or changed since v0.1.

| ID | Scenario | Expected | Covers | Phase | 0.2 |
|---|---|---|---|---|---|
| AT-1 | Unchanged webhook replayed | Dropped at hash diff; no LLM call; nothing appended to the log | FR-6.2 | 1 | changed |
| AT-2 | OpenAPI spec change ingested | Claims extracted with zero LLM calls | FR-6.4 | 1 | changed |
| AT-3 | Event submitted without `bu_owner` | Change API rejects; nothing appended | FR-4.3, FR-7.8 | 1 | changed |
| AT-4 | Spoke agent or consumer identity calls the Change API | Rejected on identity | FR-7.2 | 1 | changed |
| AT-5 | Request with unresolved user or application identity, or an app-only token without service scope | `UNAUTHENTICATED`; no data returned | FR-5.5 | 2 | changed |
| AT-6 | BU-A caller queries a BU-B `restricted` node (synthetic fixture) | Node absent; response byte-identical to an unknown name; timing indistinguishable from unknown-name requests by two-sample KS test, ≥ 1,000 requests per arm, α = 0.01 (proposed) | FR-5.4, FR-5.8 | 2 | changed |
| AT-7 | BU-A caller queries a BU-B `topology` node | Whitelisted metadata only; no artifacts | FR-5.2, FR-9.4 | 2 | — |
| AT-8 | Anchor name matching two nodes in the caller's scope | `disambiguation` in `input-required`, not a guess | FR-11.3, FR-9.9 | 2 | changed |
| AT-9 | Resolver exhausts its budget | Terminates with `escalation` (`BUDGET_EXHAUSTED`) and partial evidence; no loop continuation | FR-8.4 | 5 | — |
| AT-10 | Agent-derived proposal citing agent-derived evidence | `gate.evaluate` rejects it | FR-13.1, FR-7.14 | 5 | changed |
| AT-11 | T4 cross-BU edge proposal (synthetic fixture) | Approval task in `input-required`; nothing appended until the approver policy is satisfied | FR-7.3, FR-7.12, FR-10.5 | 5 | changed |
| AT-12 | Human rejects a proposal | Rejection and rationale written to episodic memory | FR-12.5 | 5 | — |
| AT-13 | Rollback of an agent run, with and without later dependent changes | Without dependents: compensating events reverse every change. With dependents: nothing appended, dependents reported | FR-7.4 | 5 | changed |
| AT-14 | Identical serve request repeated | Second response reports `cache_hit: true` | FR-11.6 | 2 | — |
| AT-15 | Node in a cached result changes | Entry invalidated when the projector applies the change; next request recomputes | FR-11.6 | 2 | changed |
| AT-16 | Serve path executed | Deployment has no LLM credentials and no egress to LLM endpoints; trace shows zero generative-model spans | FR-11.1 | 2 | changed |
| AT-17 | Off-schema or ungrounded item in LLM extraction output | That item discarded and logged for the Schema Steward; sibling items kept; nothing off-schema persisted | FR-6.5 | 4 | changed |
| AT-18 | Kill switch engaged | Cognitive layer halts; fast path and serve path continue; review decisions recorded but not applied | FR-7.6 | 5 | changed |
| AT-19 | Name matches one in-scope node and one restricted BU-B node | Resolves to the in-scope node; no disambiguation | FR-11.3 | 2 | new |
| AT-20 | Unknown name vs name of a restricted BU-B node | Byte-identical `NOT_FOUND` responses | FR-5.8 | 2 | new |
| AT-21 | Jira field changes on an ingested issue | New claim supersedes the old; node ID unchanged; old claim retained with `valid_to` | FR-4.4, FR-6.11 | 1 | new |
| AT-22 | Endpoint removed from an OpenAPI spec | Its claims closed; node not live; absent from serve results | FR-6.18, FR-11.9 | 1 | new |
| AT-23 | Source object deleted, or fetch returns not-found | All claims derived from that object closed | FR-6.18 | 1 | new |
| AT-24 | Event from an unmapped container | Quarantined as `UNMAPPED_CONTAINER`; nothing appended; counted in metric 10 | FR-5.3 | 1 | new |
| AT-25 | Page with a page-level restriction; page in a space restricted below BU level | The first skipped as `OBJECT_RESTRICTED`; the second ingested | FR-5.9 | 4 | new |
| AT-26 | Previously ingested page gains a page-level restriction | Claims closed and chunks retracted immediately, with no tier gate | FR-5.9, FR-7.3 | 4 | new |
| AT-27 | Graph projector fails on an event | That projector halts, event dead-lettered, alarm raised; log unchanged; other projectors and the serve path continue | FR-7.8 | 1 | new |
| AT-28 | Full replay from sequence 0 into empty stores | Projection checksums match the live projections | NFR-5, FR-7.11 | 1 | new |
| AT-29 | Corrupted snapshot offered for rebuild | Validation fails; rebuild falls back to the previous validated snapshot | FR-7.11 | 1 | new |
| AT-30 | Replay of an already-applied event range | No duplicates; projection unchanged | FR-7.9, FR-4.10 | 1 | new |
| AT-31 | Two sources of different authority rank disagree on a single-valued property | Graph holds the higher-ranked value; lower claim stays open at reduced confidence; no escalation | FR-6.16 | 1 | new |
| AT-32 | Two sources of equal authority rank disagree | Both claims open; property marked `contested`; escalation queued | FR-6.16, FR-6.11 | 1 | new |
| AT-33 | Resolution needs escalation while the cognitive layer is unavailable | Fast path appends provisional claims and queues the escalation without waiting | FR-6.17, FR-6.10 | 1 | new |
| AT-34 | Git test file calls a literal method and path defined in an OpenAPI spec | `TestCase` created with a `COVERS` edge to that Endpoint | FR-6.19 | 1 | new |
| AT-35 | Git test annotated with a Jira test issue key | One `TestCase` for both sources; `VALIDATES` present from the Jira link | FR-6.19 | 1 | new |
| AT-36 | Similarity below the merge threshold during deterministic extraction | New entity created; no link and no merge | FR-6.10 | 1 | new |
| AT-37 | Webhook with an invalid signature | Rejected; no fetch performed | FR-6.1 | 1 | new |
| AT-38 | Person key destroyed | Replay yields `[erased]` for that person's fields; projections rebuild without error | FR-5.11 | 1 | new |
| AT-39 | Chunk of a node that has been closed or rolled back | Not returned by the lexical or vector tracks | FR-11.10 | 2 | new |
| AT-40 | Request without `as_of` | Trace shows zero claim-store reads | FR-11.8 | 2 | new |
| AT-41 | `lookup` P95 with closed edges at 5× open edges | Within 10% of the baseline P95 (proposed) | FR-11.9 | 2 | new |
| AT-42 | Consumer app calls with an on-behalf-of token | Effective scope equals user scope ∩ application scope | FR-5.10 | 2 | new |
| AT-43 | Serve request traced | Spans carry no chunk content, property values or query text | FR-15.1 | 2 | new |
| AT-44 | Serve request audited | Record holds identities, scope, sequence number and returned IDs; no excluded-node IDs or counts | FR-15.2 | 2 | new |
| AT-45 | `include_artifacts: false` | Topology only; no artifacts | FR-9.3 | 2 | new |
| AT-46 | `impact_analysis` request | Relations carry `hop`; `paths` lists routes; no route exceeds 3 hops | FR-11.4, FR-11.5 | 2 | new |
| AT-47 | BU-A caller's lexical query | Only BU-A and `commons` namespaces queried | FR-5.6 | 2 | new |
| AT-48 | Unsupported `okf_version` in a request | `UNSUPPORTED_VERSION` | FR-9.2 | 2 | new |
| AT-49 | Feedback submitted by an identity other than the original caller | Rejected | FR-11.7, FR-9.11 | 2 | new |
| AT-50 | Visibility class changed anywhere | Global cache epoch increments; cached entries not served; no per-caller difference in `cache_hit` behaviour | FR-11.6 | 2 | new |
| AT-51 | LLM extraction mentions an unknown service name | No node created; mention candidate queued for the Resolver | FR-6.15 | 4 | new |
| AT-52 | Source skip rate above threshold | Health alarm raised, identifying the mapping as suspect | FR-14.3, FR-5.9 | 4 | new |
| AT-53 | Dry-run on; agent proposes a T2 change | Proposal recorded and surfaced; nothing appended | FR-7.7 | 5 | new |
| AT-54 | Approved proposal whose subject changed after it was proposed | Marked stale; not applied; returned as `STALE_PROPOSAL` | FR-7.7 | 5 | new |
| AT-55 | Per-run write limit breached, then the daily limit | Run ends with `escalate` while others continue; daily breach halts the cognitive layer with the fast path unaffected | FR-7.5 | 5 | new |
| AT-56 | Batched Resolver task covering several source events | Task carries its own `contextId` and lists every source `contextId` | FR-10.6, FR-8.5 | 5 | new |
| AT-57 | Memory recall in a task scoped to BU-A | No entries labelled only BU-B | FR-12.6 | 5 | new |
| AT-58 | Memory write under an anonymous agent identity | Rejected | FR-12.3 | 5 | new |
| AT-59 | Approval task passes its expiry | `escalation` with `APPROVAL_EXPIRED`; nothing appended | FR-10.5 | 5 | new |
| AT-60 | Cross-BU proposal shown to a BU-A reviewer | Only BU-A-entitled evidence shown; withheld evidence not counted | FR-7.13 | 5 | new |
| AT-61 | Agent-derived claim ratio above threshold | Alarm raised | FR-13.4 | 5 | new |
| AT-62 | Proposal carrying an agent-reported confidence value | Gate ignores it and computes confidence from cited evidence | FR-7.14, FR-6.12 | 5 | new |

---

## 19. Delivery Sequence

*Changed in 0.2 — M6, M10, D1, D6, N20.*

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 0 | Node, edge and property schema with the FR-4.9 tables, expressed as **executable configuration the validator reads**, not prose; claim model; persistence decided (§21); change-log design including encryption key granularity (FR-5.11); Change API identity table; BU and IdP group mappings; visibility defaults; authority matrix; alias registry schema with BU labels; OKF v0.2 family draft; Agent Card drafts; pre-registered Phase 3 evaluation protocol; capacity assumptions (NFR-8); FR→AT traceability matrix; decisions on every §20 open item | All of it reviewed and signed off. Every MUST maps to at least one AT or is marked review-verified. A2A version pin confirmed (FR-10.1) |
| 1 | Change API with validation; change log; projectors for graph, claim store, lexical index and alias registry, with idempotency, dead-lettering, reconciliation and validated snapshots; deterministic ingestion from Git (OpenAPI, manifests, test code, collections) and Jira; raw store partitioned and encrypted; trace redaction; synthetic BU-B fixture; basic escalation queue | Golden set of 200 entities across Git and Jira, labelled independently by two engineers from the owning BU with disagreements adjudicated by the spec owner. Gate: **merge precision ≥ 0.99** (guardrail — a false merge is a segregation breach, not a quality issue). Resolution **recall is measured, not gated, at this exit**; the standing target is then set 5 points above the measured value and enforced from Phase 2 onward. Full replay from origin reproduces live projection checksums. Phase 1 ATs pass |
| 2 | Serve path (FR-11.2) with scoped resolution, current-claim and liveness filters, and caching; on-behalf-of identity; OKF assembly; Curator external Agent Card; fixed question set of ≥ 100 questions with labelled relevant nodes, written before the serve path is tuned (proposed) | recall@10 and nDCG@10 measured on the fixed set and recorded as the baseline — **tracked, not gated**, because no threshold can be set before the first measurement. Resolution recall meets the target set at the Phase 1 exit. NFR-1 met; Phase 2 ATs pass. **OKF v0.2 family and Curator cards frozen at this exit** (FR-9.2). TTL defaults re-baselined from observed change rates (FR-14.1) |
| 3 | One consumer wired: the API testing solution | **Blinded pairwise preference**, pre-registered before any task is run: 40 tasks drawn from the API testing solution's real backlog; two arms, with and without fabric context, same consumer model and prompt; each pair reviewed independently by two engineers blind to the arm, disagreements adjudicated by a third. Gate: the with-context arm **wins or ties on ≥ 80% of tasks and strictly wins on ≥ 40%**. A percentage-improvement metric with a significance test was rejected: the effect size would be invented, and p < 0.05 needs a larger hand-labelled set than is realistic |
| 4 | Confluence ingestion with the source-permission rule, grounded LLM extraction and mention candidates; selective embedding | LLM-free ingestion rate ≥ 85% on the FR-6.14 denominator; source skip rate below threshold; human queue populated with real hard cases; Phase 4 ATs pass |
| 5 | Curator orchestration and deterministic gate; Resolver as a full agent; approval tasks; dry-run; kill switch; rollback | Resolver agreement with human decisions measured over ≥ 200 reviewed proposals and recorded as the baseline (tracked, not gated). Gate: **zero approved false merges** (guardrail). Phase 5 ATs pass |
| — | **MVP boundary** | Phases 0–5 constitute the MVP (§17) |
| 6 | Automated T1/T2 application of agent proposals; remaining spokes as live agents | Human intervention rate falling over 8 weeks against the Phase 5 baseline, with no rise in the rollback rate (guardrail). The size of the fall is set from the Phase 5 baseline, not fixed here |

### Threshold policy

*New in 0.3 — owner decision.* A threshold cannot be set before the thing has been measured once, so every number in this specification sits in one of three tiers. This is policy, not an omission.

| Tier | Numbers | When set |
|---|---|---|
| **Guardrail — fixed now** | Merge precision ≥ 0.99; agent-derived claim ratio alarm at 15% (FR-13.4); source-permission skip-rate alarm at 5% (FR-5.9); zero approved false merges; no rise in rollback rate | Fixed in this version. These are safety limits, not performance targets, so they need no baseline |
| **Set from the first measurement** | Entity resolution recall (measured at the Phase 1 exit, target then set 5 points above the measured value); TTL defaults (re-baselined at Phase 2 from observed change rates) | At the exit that first measures them |
| **Tracked, no gate yet** | recall@10 and nDCG@10 (Phase 2 baseline); Resolver agreement rate (Phase 5 baseline); human intervention rate (Phase 6) | Gated only once a baseline exists |

A number marked *(provisional)* elsewhere in this document belongs to the second or third tier: it is a placeholder for planning and MUST NOT be treated as an agreed target. A number marked *(guardrail)* is fixed.

**Phase 3 is a hard gate.** If fabric context does not measurably improve the consumer's output, the knowledge model is wrong and no further agent work should proceed until it is corrected. "Measurably" is defined by the protocol pre-registered in Phase 0, not decided after the results are seen.

**Scope change from v0.1.** Phases 0 and 1 carry substantially more work than in v0.1. Phase 0 gains the claim model and store decision, key granularity, the identity table, the schema tables, the full OKF family, the pre-registered evaluation protocol and the traceability matrix. Phase 1 gains the change-log-first write model with its projectors, reconciliation and snapshots, test-code parsing, the synthetic BU-B fixture, raw-store and trace controls, and the escalation queue. Each was pulled forward deliberately, because each is an expensive retrofit: entitlement scoping, the claim model, the contract family and token exchange cannot be added cheaply once code depends on their absence. Any dates set against the v0.1 sequence do not carry over.

---

## 20. Open Items for Phase 0 Review

*New in 0.2.*

These were added while drafting v0.2 and have **not** been reviewed. Each needs a decision before Phase 0 exit. They are written into the requirements above so the document is implementable, and are listed here so that nothing enters the spec unexamined.

| ID | Item | Where | Why it was added |
|---|---|---|---|
| DR1 | An edge is visible only when both endpoints are visible to the caller | FR-5.12 | FR-5.4(b) forbids leakage through traversal, but v0.1 had no rule for edges |
| DR2 | The graph stores current-claim metadata (`claim_id`, provenance, source, `valid_from`, confidence) inline with each value | FR-4.7 item 4 | **Reviewed and accepted, with a storage constraint:** one `props` document per node, not one column per field per property, so adding a property does not change the physical schema. Required because D4 forbids reading the claim store without `as_of` |
| DR3 | Default visibility for the types v0.1 did not cover: Topic, Endpoint and Team `topology`; BusinessGroup `public`; Component, Environment, DataEntity, TestRun and Document `page`/`adr`/`incident` `restricted` | FR-5.2 | v0.1 gave defaults for 5 of 13 node types |
| DR4 | The contents of the edge domain/range, cardinality and property tables | FR-4.9 | Needed to define off-schema and contradiction; the entries are a first draft and the domain expert should check them |
| DR5 | Document subtypes `page`, `adr` and `incident` alongside `standard` and `glossary_term` | FR-4.1 | FR-6.8 and the OKF artifact kinds already name ADRs and incident notes |
| DR6 | A `kc.review` skill on the internal Curator card | FR-10.3, FR-10.5 | FR-10.5 approval tasks need a skill to live on and a client to answer them |
| DR7 | Excluded-node audit data computed offline by replay rather than recorded per request | FR-15.2 | **Reviewed and accepted as a deliberate deviation from the v0.1 review**, which asked for per-BU counts in the request record. Counting in-path means touching restricted nodes to count them, which reopens the timing side channel AT-6 forbids. The request record holds caller, scope, cache epoch, sequence number and what was returned; everything about exclusions is derived later in a privileged job. Do not restore in-path counting |
| DR8 | Numeric values | throughout | **Superseded by the threshold policy in §19.** Guardrails are fixed now; targets are set from the first measurement; the rest are tracked without a gate. Values still marked *(provisional)* are planning placeholders, not agreed targets |
| DR9 | Resolution candidates limited to the event's BU and `commons` | FR-6.9 | Linking at ingest is automatic and ungated, so cross-BU linking would be a false merge in effect |
| DR10 | Claim kinds (identity, property, relationship), node liveness from open identity claims, and one graph edge per `(from, to, type)` aggregating its relationship claims | FR-4.6, FR-4.7 | Needed to define deletion, liveness, and what happens when two sources assert the same edge |
| DR11 | Git repositories whose read access is narrower than the mapped BU treated as object-level, not container-level | FR-5.9 | D5 listed private repos as object-level while FR-5.3 maps repos as containers; this resolves the overlap in favour of D5 |

---

## 21. MVP Persistence Decision

*New in 0.3 — owner decision.* FR-4.7 and FR-4.8 gated this choice; it is now made, and Phase 0 records the rationale rather than re-opening it.

**One PostgreSQL instance holds everything for the MVP.**

| Component | Implementation |
|---|---|
| Change log (FR-7.1) | Append-only table, authoritative, never truncated |
| Claim store (FR-4.8) | Tables in the same database, so the log append and the claim projection commit in one transaction |
| Graph projection (FR-4.7) | Node and edge tables; traversal by recursive CTE |
| Alias registry (FR-11.3) | Table, with `bu_owner` and `visibility_class` per row |
| Lexical index (FR-6.8) | Native `tsvector` — no second service |
| Vector index (FR-6.8) | `pgvector`, same database |
| Entitlement pre-filter (FR-5.4) | Row-level security policies, applied by the planner in-query |
| Per-BU index namespacing (FR-5.6) | Separate namespaces retained alongside row-level security |

**Why this is a decision rather than a compromise.** The graph and both indexes are projections of the change log (FR-7.1). If recursive-CTE traversal becomes the bottleneck at hop 3 (`impact_analysis`, FR-11.4), the graph projection is rebuilt into a dedicated graph database from the log and nothing else changes: no re-ingestion, no contract change, no consumer change. The log-first model bought that option, and the MVP takes advantage of it instead of paying for a graph database before there is evidence it is needed.

**What must hold for the swap-out to stay cheap:**

1. No component may read the graph tables directly except the serve path and the projectors (FR-7.1).
2. Traversal MUST be expressed behind one interface, so the recursive CTE is replaceable without touching FR-11.2 step 7.
3. The current-claim and liveness filters MUST stay indexed in whatever store holds the projection (FR-11.9, AT-41).
4. Only a single PostgreSQL instance is assumed for the MVP; NFR-8 capacity assumptions decide whether that holds beyond it.
