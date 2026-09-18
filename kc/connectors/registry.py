"""Connector registry: one place that knows which source systems exist.

Registration is explicit. `get` returns None for an unknown system rather than
raising, because callers must handle "no connector on this machine" as a normal
state — that is the deferred-ingestion design, not an error path.
"""

from __future__ import annotations

from kc.connectors.ado import AdoConnector
from kc.connectors.base import Connector
from kc.connectors.confluence import ConfluenceConnector
from kc.connectors.git_openapi import GitConnector
from kc.connectors.jira import JiraConnector

_REGISTRY: dict[str, Connector] = {}


def register(connector: Connector) -> None:
    _REGISTRY[connector.source_system] = connector


def get(source_system: str) -> Connector | None:
    return _REGISTRY.get(source_system)


def all_connectors() -> dict[str, Connector]:
    return dict(_REGISTRY)


def status() -> list[dict[str, object]]:
    """What is wired and what is a seam. `kc.cli health` prints this, so nobody
    has to read code to find out whether Jira is connected."""
    out = []
    for name, connector in sorted(_REGISTRY.items()):
        wired = {}
        for method in ("discover", "fetch", "object_permissions_narrower_than_bu", "extract"):
            try:
                getattr(type(connector), method)(connector, *_probe_args(method, connector))
                wired[method] = True
            except NotImplementedError:
                wired[method] = False
            except Exception:  # noqa: BLE001 - a real implementation failing on probe data
                wired[method] = True
        out.append(
            {
                "source_system": name,
                "creates_nodes": connector.creates_nodes,
                "creatable_types": list(connector.creatable_types),
                "volatile_fields": len(connector.volatile_fields),
                "wired": wired,
            }
        )
    return out


def _probe_args(method: str, connector: Connector) -> tuple:
    from kc.connectors.base import SourceObject

    probe = SourceObject(
        source_system=connector.source_system,
        source_instance="probe",
        source_id="probe",
        container="probe",
        content={},
        raw="",
    )
    if method == "discover":
        return (None,)
    if method == "fetch":
        return ("probe",)
    if method == "object_permissions_narrower_than_bu":
        return (probe,)
    return (probe, "commons")


register(GitConnector())
register(JiraConnector())
register(ConfluenceConnector())
register(AdoConnector())
