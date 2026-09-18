"""Jira connector: seam, with the hash projection implemented (FR-6.2, N1).

Nothing here silently returns nothing. `discover`, `fetch` and the permission
check raise NotImplementedError naming the exact call to wire; `hash_projection`
and `extract` work now, because both are pure over content.

Volatile fields matter more here than anywhere: Jira rewrites `updated`, rank,
watcher and view counts on almost every change, so hashing the raw issue means
every replay looks new and AT-1 only ever passes for byte-identical payloads.
"""

from __future__ import annotations

import json
from typing import Any

from kc.config import loader as cfg
from kc.connectors.base import Connector, Extraction, SourceObject
from kc.core import ids
from kc.core.model import now

# Jira issue types -> node types. Anything unmapped is skipped rather than
# guessed, and the count shows up as an extraction gap.
ISSUE_TYPE_MAP = {
    "story": "Requirement",
    "requirement": "Requirement",
    "epic": "Requirement",
    "task": "Requirement",
    "bug": "Defect",
    "defect": "Defect",
    "test": "TestCase",
}


class JiraConnector(Connector):
    """TODO(office): wire these three.

    discover  -> POST /rest/api/3/search with JQL
                 `project in (LED, REC, STD) AND updated >= "{since}"`,
                 fields=`key`, paginated on `startAt`/`maxResults`.
    fetch     -> GET /rest/api/3/issue/{key}?expand=changelog,names
                 plus GET /rest/api/3/issue/{key}/remotelink for links.
    object_permissions_narrower_than_bu
              -> the issue's `security` field (issue security level) is
                 non-null, or GET /rest/api/3/issue/{key}/permissions shows a
                 grant narrower than the BU's group. Project permission schemes
                 are container-level and deliberately ignored (FR-5.9, D5).
    """

    source_system = "jira"
    api_base_env = "KC_JIRA_BASE"  # e.g. https://company.atlassian.net
    creates_nodes = True
    creatable_types = ("Requirement", "Defect", "TestCase")
    volatile_fields = (
        "updated", "updatedAt", "lastViewed", "watches", "watchCount", "votes", "voteCount",
        "rank", "customfield_10019", "aggregateprogress", "progress", "worklog",
        "timespent", "timeestimate", "expand", "self", "iconUrl", "avatarUrls",
        "thumbnail", "created_by_session", "statuscategorychangedate",
    )
    extraction_version = "1"

    def extract(self, obj: SourceObject, bu: str) -> Extraction:
        """Structured fields only: no LLM on this path (FR-6.4).

        Descriptions become chunks for lexical retrieval; turning prose into
        claims is Confluence's problem and is deferred (FR-6.5).
        """
        fields = obj.content.get("fields", obj.content)
        issue_type = str(
            (fields.get("issuetype") or {}).get("name", fields.get("issuetype", ""))
        ).lower()
        node_type = ISSUE_TYPE_MAP.get(issue_type)
        out = Extraction()
        if node_type is None:
            return out

        valid_from = fields.get("created") or now()
        node_id = ids.node_id(node_type, obj.source_key)
        out.claims.append(
            self.identity_claim(node_id=node_id, node_type=node_type, bu=bu, obj=obj,
                                valid_from=valid_from, confidence=0.9)
        )
        summary = fields.get("summary") or obj.source_id
        status = str((fields.get("status") or {}).get("name", fields.get("status", "unknown")))
        for prop, value in (("name", summary), ("key", obj.source_id), ("status", status)):
            out.claims.append(
                self.property_claim(node_id=node_id, node_type=node_type, prop=prop, value=value,
                                    bu=bu, obj=obj, valid_from=valid_from,
                                    confidence=self._confidence(node_type, prop))
            )
        if node_type == "Defect" and fields.get("priority"):
            out.claims.append(
                self.property_claim(node_id=node_id, node_type=node_type, prop="severity",
                                    value=str((fields["priority"] or {}).get("name", "")),
                                    bu=bu, obj=obj, valid_from=valid_from, confidence=0.9)
            )
        if node_type == "Requirement" and fields.get("priority"):
            out.claims.append(
                self.property_claim(node_id=node_id, node_type=node_type, prop="priority",
                                    value=str((fields["priority"] or {}).get("name", "")),
                                    bu=bu, obj=obj, valid_from=valid_from, confidence=0.9)
            )

        description = fields.get("description")
        if isinstance(description, str) and description.strip():
            out.chunks.append(
                {
                    "node_id": node_id,
                    "kind": "requirement" if node_type == "Requirement" else "defect",
                    "section_path": "description",
                    "content": f"{summary}\n{description}".strip(),
                }
            )

        # Issue links carry VALIDATES (test -> requirement) deterministically.
        for link in obj.content.get("issuelinks", []) or []:
            target_key = (
                (link.get("outwardIssue") or link.get("inwardIssue") or {}).get("key")
            )
            if not target_key:
                continue
            target_container = target_key.split("-")[0]
            target_bu = cfg.bu_for_source("jira", target_container)
            if target_bu is None:
                continue  # unmapped container: quarantine, never guess (FR-5.3)
            if node_type == "TestCase":
                target_id = ids.node_id(
                    "Requirement", ids.source_key("jira", obj.source_instance, target_key)
                )
                out.claims.append(
                    self.edge_claim(from_id=node_id, to_id=target_id, edge_type="VALIDATES",
                                    bu=bu, obj=obj, valid_from=valid_from, confidence=0.9)
                )
        return out

    def self_check_samples(self) -> list[SourceObject]:
        issue = {
            "fields": {
                "issuetype": {"name": "Story"},
                "summary": "Partial refunds post within one hour",
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "created": "2026-01-02T00:00:00+00:00",
                "description": "Acceptance: the entry appears within sixty minutes.",
            },
            "updated": "2026-03-09T11:00:00+00:00",
            "watches": {"watchCount": 4},
            "rank": "0|i0007",
        }
        defect = {
            "fields": {
                "issuetype": {"name": "Bug"},
                "summary": "Duplicate entries on retry",
                "status": {"name": "Open"},
                "priority": {"name": "Critical"},
                "created": "2026-02-02T00:00:00+00:00",
            },
            "updated": "2026-03-09T12:00:00+00:00",
        }
        return [
            SourceObject(source_system=self.source_system, source_instance="self-check",
                         source_id="LED-101", container="LED", content=issue,
                         raw=json.dumps(issue)),
            SourceObject(source_system=self.source_system, source_instance="self-check",
                         source_id="LED-204", container="LED", content=defect,
                         raw=json.dumps(defect)),
        ]


    @staticmethod
    def _confidence(node_type: str, prop: str) -> float:
        """Jira is the authority for requirement status; the authority table
        decides rank, this only reflects extraction certainty (FR-6.12)."""
        return 0.95 if (node_type, prop) in {("Requirement", "status"), ("Defect", "status")} else 0.9

    def webhook_to_source_id(self, payload: dict[str, Any]) -> str | None:
        """TODO(office): map the webhook body to an issue key.

        Verify the signature before this is called (FR-6.1, N5); Jira sends
        `X-Hub-Signature` when configured with a secret.
        """
        issue = payload.get("issue") or {}
        return issue.get("key")
