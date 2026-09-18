"""HTTP endpoint on `http.server` (FR-10.2, FR-10.3).

A2A-shaped, not A2A-complete: the skeleton has no framework and no SDK, so this
speaks the two external skills as JSON over POST and returns OKF messages with
their A2A task state attached. Wiring a real A2A server later replaces this
module and nothing else, because every payload is already an OKF message.

Identity arrives in headers standing in for the on-behalf-of token
(FR-5.5): `X-KC-User` and `X-KC-App`. On the enterprise laptop these become
claims from a validated token — the TODO(office) below says where.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from kc.agents.curator import Curator
from kc.connectors import registry
from kc.core.errors import Ambiguous, IdentityUnresolved, KCError
from kc.serve import okf
from kc.serve.pipeline import ServePipeline
from kc.stores.sqlite_store import Store

SKILL_RETRIEVE = "/okf/kc.context.retrieve"
SKILL_FEEDBACK = "/okf/kc.context.feedback"
AGENT_CARD = "/.well-known/agent-card.json"


def agent_card(host: str, port: int) -> dict[str, Any]:
    """The external card only. `kc.orchestrate` and `kc.review` live on the
    internal card, which is not served here (FR-10.3, M5)."""
    return {
        "protocolVersion": "0.3.0",
        "name": "KC Agent (Curator)",
        "description": "Returns structured context (OKF). Never prose.",
        "url": f"http://{host}:{port}{SKILL_RETRIEVE}",
        "preferredTransport": "JSONRPC",
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "skills": [
            {
                "id": "kc.context.retrieve",
                "name": "Retrieve context",
                "description": "OKF context_request in, context_response out.",
                "tags": ["knowledge", "context"],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "kc.context.feedback",
                "name": "Report context quality",
                "description": "OKF feedback against a prior request_id.",
                "tags": ["feedback"],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
        ],
        "okf_version": okf.OKF_VERSION,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "kc-skeleton/0.1"
    db_path = "kc.db"

    # --- plumbing ------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default log
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _identity(self) -> tuple[str | None, str | None]:
        """TODO(office): replace with on-behalf-of token validation (RFC 8693).
        Read the bearer token, verify it, and take the end user and client from
        its claims. Until then these headers stand in, and a missing header is
        an unresolved identity rather than a default (FR-5.5, AT-5)."""
        return self.headers.get("X-KC-User"), self.headers.get("X-KC-App", "api-testing-solution")

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    # --- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        store = Store(self.db_path)
        if self.path == AGENT_CARD:
            host, port = self.server.server_address[0], self.server.server_address[1]
            self._send(200, agent_card(str(host), int(port)))
        elif self.path == "/health":
            from kc import health

            self._send(
                200,
                {
                    "metrics": health.metrics(store),
                    "guardrails": health.guardrails(store),
                    "connectors": registry.status(),
                },
            )
        elif self.path == "/tasks":
            self._send(200, {"tasks": store.tasks()})
        else:
            self._send(404, okf.error("NOT_FOUND"))

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        store = Store(self.db_path)
        user, app = self._identity()
        try:
            message = self._body()
        except json.JSONDecodeError:
            self._send(400, okf.error("INVALID_REQUEST"))
            return

        if self.path == SKILL_RETRIEVE:
            try:
                response = ServePipeline(store).retrieve(message, user=user, application=app)
            except Ambiguous as exc:
                self._send(
                    200,
                    okf.disambiguation(
                        request_id=str(message.get("request_id")),
                        ambiguous_anchors=[
                            {"anchor_index": exc.anchor_index, "name": exc.name,
                             "candidates": exc.candidates}
                        ],
                    ),
                )
            except IdentityUnresolved as exc:
                self._send(401, okf.error(exc.code, message.get("request_id")))
            except KCError as exc:
                self._send(200, okf.error(exc.code, message.get("request_id")))
            else:
                self._send(200, response)
            return

        if self.path == SKILL_FEEDBACK:
            record = store.audit_record(str(message.get("request_id", "")))
            if record is None or record["user_id"] != user:
                # Feedback is accepted only from the caller that made the
                # request (FR-9.11, AT-49).
                self._send(200, okf.error("NOT_FOUND", message.get("request_id")))
                return
            store.put_task(
                task_id=f"t_feedback_{message['request_id']}",
                kind="bad_answer",
                state="open",
                scope=json.loads(record["scope"]),
                payload={"feedback": message, "audit": dict(record)},
                reason="consumer reported context quality (FR-11.7)",
            )
            self._send(200, {"accepted": True, "routed_to": "retrieval-critic"})
            return

        if self.path == "/work":
            self._send(200, {"results": Curator(store).work()})
            return

        self._send(404, okf.error("NOT_FOUND"))


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8080) -> None:
    Handler.db_path = db_path
    server = ThreadingHTTPServer((host, port), Handler)
    print(
        json.dumps(
            {
                "listening": f"http://{host}:{port}",
                "skills": [SKILL_RETRIEVE, SKILL_FEEDBACK],
                "agent_card": AGENT_CARD,
                "identity_headers": ["X-KC-User", "X-KC-App"],
            }
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
