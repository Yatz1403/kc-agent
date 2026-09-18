"""Git connector: the one that is fully implemented (FR-6.4, FR-6.19, D6).

Its parsers are pure functions over file content, so they are testable without
credentials and they are the MVP's authoritative source:

* OpenAPI JSON  -> API, Endpoint, PART_OF, IMPLEMENTS
* package.json  -> Component, DEPENDS_ON
* requirements.txt -> Component, DEPENDS_ON
* pytest files  -> TestCase, COVERS (literal method + path matched to an
  Endpoint), VALIDATES (Jira key in a marker or docstring)

Test code is the authority for what is executed; Zephyr records intent and is
out of MVP scope (§17). A test whose target cannot be determined gets no COVERS
edge rather than a guessed one (FR-6.19 item 2).
"""

from __future__ import annotations

import json
import re
from typing import Any

from kc.connectors.base import Connector, Extraction, SourceObject
from kc.core import ids
from kc.core.model import now

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")

_CLIENT_CALL = re.compile(
    r"""(?:client|session|requests|app|api)\s*\.\s*(get|put|post|delete|patch)\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
_TEST_DEF = re.compile(r"^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)
_PATH_PARAM = re.compile(r"\{[^}]+\}")


class GitConnector(Connector):
    """Wire `discover` and `fetch` to whichever host you have.

    TODO(office): discover -> GET /repos/{org}/{repo}/commits?since=... then
    GET /repos/{org}/{repo}/git/trees/{sha}?recursive=1, filtered to the paths
    in the repo's ingest globs (openapi*.json, package.json, requirements.txt,
    tests/**). fetch -> GET /repos/{org}/{repo}/contents/{path}?ref={sha} and
    base64-decode `content` into SourceObject.raw.
    object_permissions_narrower_than_bu -> GET /repos/{org}/{repo} and treat
    `private` with a team list narrower than the mapped BU as restricted
    (FR-5.9, DR11).
    """

    source_system = "git"
    api_base_env = "KC_GIT_BASE"  # e.g. https://github.company.internal/api/v3
    creates_nodes = True
    creatable_types = ("API", "Endpoint", "Component", "TestCase", "Service")
    # Commit metadata moves on every push and says nothing about the artefact.
    # `committed_at` is deliberately NOT volatile: it lands in every claim's
    # valid_from, and a field that reaches a claim cannot be excluded from the
    # hash without making content changes invisible (FR-6.2). The invariant test
    # in tests/test_guards.py enforces that rule for every connector.
    volatile_fields = ("sha", "commit", "last_modified", "etag", "size", "url")
    extraction_version = "1"

    def extract(self, obj: SourceObject, bu: str) -> Extraction:
        path = obj.content.get("path", obj.source_id)
        text = obj.raw or ""
        valid_from = obj.content.get("committed_at") or now()

        if path.endswith(".json") and _looks_like_openapi(text):
            return self._openapi(obj, bu, text, valid_from)
        if path.endswith("package.json"):
            return self._package_json(obj, bu, text, valid_from)
        if path.endswith("requirements.txt"):
            return self._requirements(obj, bu, text, valid_from)
        if _is_test_file(path):
            return self._pytest(obj, bu, text, valid_from)
        return Extraction()

    # --- OpenAPI -------------------------------------------------------------

    def _openapi(self, obj: SourceObject, bu: str, text: str, valid_from: str) -> Extraction:
        spec = json.loads(text)
        info = spec.get("info", {})
        title = info.get("title") or obj.container
        api_id = ids.node_id("API", obj.source_key)
        out = Extraction()
        out.claims.append(
            self.identity_claim(node_id=api_id, node_type="API", bu=bu, obj=obj,
                                valid_from=valid_from, confidence=0.95)
        )
        out.claims.append(
            self.property_claim(node_id=api_id, node_type="API", prop="name", value=title,
                                bu=bu, obj=obj, valid_from=valid_from, confidence=0.95)
        )
        if info.get("version"):
            out.claims.append(
                self.property_claim(node_id=api_id, node_type="API", prop="version",
                                    value=str(info["version"]), bu=bu, obj=obj,
                                    valid_from=valid_from, confidence=0.95)
            )
        servers = spec.get("servers") or []
        if servers and isinstance(servers, list) and servers[0].get("url"):
            out.claims.append(
                self.property_claim(node_id=api_id, node_type="API", prop="base_path",
                                    value=servers[0]["url"], bu=bu, obj=obj,
                                    valid_from=valid_from, confidence=0.9)
            )
        out.claims.append(
            self.property_claim(node_id=api_id, node_type="API", prop="status", value="active",
                                bu=bu, obj=obj, valid_from=valid_from, confidence=0.7)
        )

        for path, operations in (spec.get("paths") or {}).items():
            if not isinstance(operations, dict):
                continue
            for method, operation in operations.items():
                if method.lower() not in HTTP_METHODS:
                    continue
                endpoint_id = ids.node_id("Endpoint", obj.source_key, f"{method.upper()} {path}")
                out.claims.append(
                    self.identity_claim(node_id=endpoint_id, node_type="Endpoint", bu=bu,
                                        obj=obj, valid_from=valid_from, confidence=0.95)
                )
                name = f"{method.upper()} {path}"
                for prop, value in (
                    ("name", name),
                    ("method", method.upper()),
                    ("path", path),
                    ("status", "active"),
                ):
                    out.claims.append(
                        self.property_claim(node_id=endpoint_id, node_type="Endpoint", prop=prop,
                                            value=value, bu=bu, obj=obj, valid_from=valid_from,
                                            confidence=0.95)
                    )
                out.claims.append(
                    self.edge_claim(from_id=endpoint_id, to_id=api_id, edge_type="PART_OF",
                                    bu=bu, obj=obj, valid_from=valid_from, confidence=0.95)
                )
                summary = (operation or {}).get("summary") or ""
                description = (operation or {}).get("description") or ""
                if summary or description:
                    out.chunks.append(
                        {
                            "node_id": endpoint_id,
                            "kind": "openapi",
                            "section_path": f"{method.upper()} {path}",
                            "content": f"{name}\n{summary}\n{description}".strip(),
                        }
                    )
        return out

    # --- dependency manifests ------------------------------------------------

    def _package_json(self, obj: SourceObject, bu: str, text: str, valid_from: str) -> Extraction:
        manifest = json.loads(text)
        service_id = ids.node_id("Service", obj.source_key)
        out = Extraction()
        out.claims.append(
            self.identity_claim(node_id=service_id, node_type="Service", bu=bu, obj=obj,
                                valid_from=valid_from, confidence=0.9)
        )
        out.claims.append(
            self.property_claim(node_id=service_id, node_type="Service", prop="name",
                                value=manifest.get("name") or obj.container, bu=bu, obj=obj,
                                valid_from=valid_from, confidence=0.9)
        )
        deps = {**(manifest.get("dependencies") or {})}
        for package, spec in sorted(deps.items()):
            out.claims.extend(
                self._component(obj, bu, service_id, package, str(spec), "npm", valid_from)
            )
        return out

    def _requirements(self, obj: SourceObject, bu: str, text: str, valid_from: str) -> Extraction:
        service_id = ids.node_id("Service", obj.source_key)
        out = Extraction()
        out.claims.append(
            self.identity_claim(node_id=service_id, node_type="Service", bu=bu, obj=obj,
                                valid_from=valid_from, confidence=0.85)
        )
        out.claims.append(
            self.property_claim(node_id=service_id, node_type="Service", prop="name",
                                value=obj.container, bu=bu, obj=obj, valid_from=valid_from,
                                confidence=0.85)
        )
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s*([<>=!~]=?.*)?$", line)
            if not match:
                continue
            out.claims.extend(
                self._component(obj, bu, service_id, match.group(1),
                                (match.group(2) or "").strip(), "pypi", valid_from)
            )
        return out

    def _component(
        self, obj: SourceObject, bu: str, service_id: str, package: str, spec: str,
        ecosystem: str, valid_from: str,
    ) -> list[Any]:
        component_id = ids.node_id("Component", f"{ecosystem}:{package}")
        claims = [
            self.identity_claim(node_id=component_id, node_type="Component", bu=bu, obj=obj,
                                valid_from=valid_from, confidence=0.9),
            self.property_claim(node_id=component_id, node_type="Component", prop="name",
                                value=package, bu=bu, obj=obj, valid_from=valid_from,
                                confidence=0.9),
            self.property_claim(node_id=component_id, node_type="Component", prop="ecosystem",
                                value=ecosystem, bu=bu, obj=obj, valid_from=valid_from,
                                confidence=0.9),
            self.edge_claim(from_id=service_id, to_id=component_id, edge_type="DEPENDS_ON",
                            bu=bu, obj=obj, valid_from=valid_from, confidence=0.9),
        ]
        if spec:
            claims.append(
                self.property_claim(node_id=component_id, node_type="Component",
                                    prop="version_spec", value=spec, bu=bu, obj=obj,
                                    valid_from=valid_from, confidence=0.85)
            )
        return claims

    # --- pytest --------------------------------------------------------------

    def _pytest(self, obj: SourceObject, bu: str, text: str, valid_from: str) -> Extraction:
        """TestCase per test function, with COVERS only when the target is
        literal and matches a known Endpoint (FR-6.19)."""
        out = Extraction()
        path = obj.content.get("path", obj.source_id)
        endpoint_index: dict[str, str] = obj.content.get("endpoint_index", {}) or {}
        blocks = _split_tests(text)
        for test_name, body in blocks:
            test_id = ids.node_id("TestCase", obj.source_key, test_name)
            out.claims.append(
                self.identity_claim(node_id=test_id, node_type="TestCase", bu=bu, obj=obj,
                                    valid_from=valid_from, confidence=0.95)
            )
            for prop, value in (
                ("name", test_name),
                ("framework", "pytest"),
                ("file_path", path),
                ("status", "active"),
            ):
                out.claims.append(
                    self.property_claim(node_id=test_id, node_type="TestCase", prop=prop,
                                        value=value, bu=bu, obj=obj, valid_from=valid_from,
                                        confidence=0.95)
                )
            for method, url in _CLIENT_CALL.findall(body):
                target = endpoint_index.get(f"{method.upper()} {_template(url)}")
                if target:
                    out.claims.append(
                        self.edge_claim(from_id=test_id, to_id=target, edge_type="COVERS",
                                        bu=bu, obj=obj, valid_from=valid_from, confidence=0.95)
                    )
            for key in _JIRA_KEY.findall(body):
                requirement_id = obj.content.get("requirement_index", {}).get(key)
                if requirement_id:
                    out.claims.append(
                        self.edge_claim(from_id=test_id, to_id=requirement_id,
                                        edge_type="VALIDATES", bu=bu, obj=obj,
                                        valid_from=valid_from, confidence=0.9)
                    )
                else:
                    # A Jira key with no node yet is a mention, not an edge: the
                    # Resolver picks it up once Jira is wired (FR-6.15).
                    out.mentions.append(
                        {
                            "name": key,
                            "proposed_type": "Requirement",
                            "bu": bu,
                            "source_key": obj.source_key,
                            "span": key,
                        }
                    )
            out.chunks.append(
                {
                    "node_id": test_id,
                    "kind": "test",
                    "section_path": f"{path}::{test_name}",
                    "content": body.strip(),
                }
            )
        return out


    def self_check_samples(self) -> list[SourceObject]:
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "sample-api", "version": "1.0.0"},
            "servers": [{"url": "/v1"}],
            "paths": {"/things/{id}": {"get": {"summary": "Read a thing"}}},
        }
        manifest = {"name": "sample-service", "dependencies": {"express": "^4.19.2"}}
        return [
            SourceObject(
                source_system=self.source_system, source_instance="self-check",
                source_id="acme/ledger-api:openapi.json", container="acme/ledger-api",
                content={"path": "openapi.json", "committed_at": "2026-01-01T00:00:00+00:00",
                         "sha": "0" * 40},
                raw=json.dumps(spec),
            ),
            SourceObject(
                source_system=self.source_system, source_instance="self-check",
                source_id="acme/ledger-api:package.json", container="acme/ledger-api",
                content={"path": "package.json", "sha": "1" * 40},
                raw=json.dumps(manifest),
            ),
            SourceObject(
                source_system=self.source_system, source_instance="self-check",
                source_id="acme/ledger-api:tests/test_things.py", container="acme/ledger-api",
                content={"path": "tests/test_things.py", "endpoint_index": {}},
                raw="\n".join(
                    [
                        "def test_reads_thing(client):",
                        '    client.get("/things/7")',
                        "",
                    ]
                ),
            ),
        ]


# --- pure helpers, exercised directly by tests -------------------------------


def _looks_like_openapi(text: str) -> bool:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(data, dict) and ("openapi" in data or "swagger" in data)


def _is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py")


def _split_tests(text: str) -> list[tuple[str, str]]:
    matches = list(_TEST_DEF.finditer(text))
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((match.group(1), text[start:end]))
    return blocks


def _template(url: str) -> str:
    """Turn a called URL into its OpenAPI path template.

    `/accounts/12345/entries` becomes `/accounts/{id}/entries`, so a literal
    call matches the endpoint it exercises. Numeric and UUID-ish segments are
    the only ones templated: guessing more would invent coverage.
    """
    path = url.split("?", 1)[0]
    if path.startswith("http://") or path.startswith("https://"):
        path = "/" + path.split("/", 3)[-1] if path.count("/") > 2 else "/"
    segments = []
    for segment in path.split("/"):
        if segment.isdigit() or re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}", segment):
            segments.append("{id}")
        else:
            segments.append(segment)
    return "/".join(segments) or "/"


def endpoint_index(claims: list[Any]) -> dict[str, str]:
    """Build the "METHOD path-template" -> endpoint node index that pytest
    extraction needs. Built from OpenAPI claims, so coverage is only ever
    asserted against endpoints that actually exist. Keys are strings because
    this index travels inside SourceObject.content, which is hashed as JSON."""
    method: dict[str, str] = {}
    path: dict[str, str] = {}
    for claim in claims:
        if claim.kind != "property" or claim.node_type != "Endpoint":
            continue
        if claim.prop == "method":
            method[claim.subject_id] = str(claim.value)
        elif claim.prop == "path":
            path[claim.subject_id] = str(claim.value)
    index: dict[str, str] = {}
    for node_id, verb in method.items():
        if node_id in path:
            index[f"{verb} {_PATH_PARAM.sub('{id}', path[node_id])}"] = node_id
    return index
