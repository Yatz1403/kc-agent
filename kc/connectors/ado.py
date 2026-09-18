"""Azure DevOps connector: seam only, out of MVP scope (§17).

Present so that wiring it later is filling in three methods rather than
inventing a shape, and so the registry has an entry to reject cleanly instead
of failing with a KeyError. `hash_projection` and the work-item type map are
implemented because they are content-only.
"""

from __future__ import annotations

from typing import Any

from kc.connectors.base import Connector, Extraction, SourceObject
from kc.core import ids
from kc.core.model import now

WORK_ITEM_MAP = {
    "user story": "Requirement",
    "requirement": "Requirement",
    "feature": "Requirement",
    "bug": "Defect",
    "test case": "TestCase",
}


class AdoConnector(Connector):
    """TODO(office): wire these three when ADO comes into scope.

    discover  -> POST /{org}/{project}/_apis/wit/wiql?api-version=7.1 with
                 `SELECT [System.Id] FROM WorkItems WHERE [System.ChangedDate]
                  >= '{since}'`, then batch GET /_apis/wit/workitemsbatch.
    fetch     -> GET /{org}/{project}/_apis/wit/workitems/{id}?$expand=relations
    object_permissions_narrower_than_bu
              -> area-path ACLs via
                 GET /_apis/accesscontrollists/{securityNamespaceId}; treat a
                 grant narrower than the mapped BU as restricted (FR-5.9).

    Also unresolved for ADO: which field carries the BU container. Until
    entitlements.json has an `ado` mapping, every ADO event quarantines as
    UNMAPPED_CONTAINER, which is the correct fail-closed behaviour (FR-5.3).
    """

    source_system = "ado"
    creates_nodes = True
    creatable_types = ("Requirement", "Defect", "TestCase")
    volatile_fields = (
        "System.ChangedDate", "System.Rev", "System.Watermark", "rev", "url", "_links",
        "System.CommentCount", "Microsoft.VSTS.Common.StateChangeDate",
    )
    extraction_version = "1"

    def extract(self, obj: SourceObject, bu: str) -> Extraction:
        fields = obj.content.get("fields", obj.content)
        work_item_type = str(fields.get("System.WorkItemType", "")).lower()
        node_type = WORK_ITEM_MAP.get(work_item_type)
        out = Extraction()
        if node_type is None:
            return out
        valid_from = fields.get("System.CreatedDate") or now()
        node_id = ids.node_id(node_type, obj.source_key)
        out.claims.append(
            self.identity_claim(node_id=node_id, node_type=node_type, bu=bu, obj=obj,
                                valid_from=valid_from, confidence=0.85)
        )
        for prop, value in (
            ("name", fields.get("System.Title", obj.source_id)),
            ("key", obj.source_id),
            ("status", fields.get("System.State", "unknown")),
        ):
            out.claims.append(
                self.property_claim(node_id=node_id, node_type=node_type, prop=prop,
                                    value=str(value), bu=bu, obj=obj, valid_from=valid_from,
                                    confidence=0.85)
            )
        return out

    def webhook_to_source_id(self, payload: dict[str, Any]) -> str | None:
        return str((payload.get("resource") or {}).get("id") or "") or None
