"""The two commands that exist for the enterprise laptop: `ingest` and `doctor`.

`ingest --dry-run` must append nothing, and `doctor` must fail loudly on the
things that go wrong on a locked-down machine rather than later, in the middle
of ingestion.
"""

from __future__ import annotations

import json

import pytest

from kc import cli, doctor
from kc.connectors.base import SourceObject
from kc.core.errors import Quarantined
from kc.ingest import FastPath
from kc.stores.sqlite_store import Store

TICKET = {
    "source_id": "LED-777",
    "container": "LED",
    "content": {
        "fields": {
            "issuetype": {"name": "Story"},
            "summary": "Refund reversals appear same day",
            "status": {"name": "In Progress"},
            "priority": {"name": "High"},
            "created": "2026-09-01T10:00:00+00:00",
            "description": "Acceptance: a reversal before 22:00 posts the same day.",
        },
        "updated": "2026-09-17T14:03:00+00:00",
        "watches": {"watchCount": 6},
    },
}


def _ticket_file(tmp_path, payload=None, name="LED-777.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload or TICKET), encoding="utf-8")
    return str(path)


def _jira_object(source_id: str = "LED-777", container: str = "LED") -> SourceObject:
    return SourceObject(
        source_system="jira", source_instance="manual", source_id=source_id,
        container=container, content=TICKET["content"],
        raw=json.dumps(TICKET["content"]),
    )


class TestPlanObject:
    def test_plan_reports_what_would_be_written(self, store: Store):
        plan = FastPath(store).plan_object(_jira_object())
        assert plan["decision"] == "would_ingest"
        assert plan["bu_owner"] == "bu-a"
        assert plan["would_write"]["nodes"] == {"Requirement": 1}
        assert plan["would_write"]["chunks"] == 1
        assert plan["sample_values"]["Requirement.status"] == "In Progress"

    def test_plan_appends_nothing(self, store: Store):
        before = store.last_seq()
        FastPath(store).plan_object(_jira_object())
        assert store.last_seq() == before
        assert store.counts()["claims_total"] == 0

    def test_plan_reports_the_permission_check_as_unwired(self, store: Store):
        plan = FastPath(store).plan_object(_jira_object())
        assert "not wired" in plan["permission_check"]

    def test_plan_reports_quarantine_without_raising(self, store: Store):
        plan = FastPath(store).plan_object(_jira_object(container="NOPE"))
        assert plan["decision"] == "quarantine"
        assert plan["reason"] == "UNMAPPED_CONTAINER"
        # The real path raises; the plan only reports (nothing is recorded).
        with pytest.raises(Quarantined):
            FastPath(store).ingest_object(_jira_object(container="NOPE"))

    def test_plan_reports_an_unchanged_object(self, store: Store):
        fast = FastPath(store)
        fast.ingest_object(_jira_object())
        assert fast.plan_object(_jira_object())["decision"] == "drop_unchanged"

    def test_plan_lists_what_would_close(self, store: Store):
        fast = FastPath(store)
        fast.ingest_object(_jira_object())
        changed = _jira_object()
        changed.content = json.loads(json.dumps(TICKET["content"]))
        changed.content["fields"].pop("priority")
        changed.raw = json.dumps(changed.content)
        plan = fast.plan_object(changed)
        assert plan["decision"] == "would_ingest"
        closing = {item["property"] for item in plan["would_close"]}
        assert "priority" in closing


class TestIngestCommand:
    def test_dry_run_from_file_appends_nothing(self, tmp_path, capsys):
        db = str(tmp_path / "kc.db")
        Store(db).init_schema()
        code = cli.main(["--db", db, "ingest", "--connector", "jira",
                         "--from-file", _ticket_file(tmp_path), "--dry-run"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["mode"].startswith("dry-run")
        assert payload["results"][0]["decision"] == "would_ingest"
        assert Store(db).last_seq() == 0

    def test_ingest_from_file_writes_and_projects(self, tmp_path, capsys):
        db = str(tmp_path / "kc.db")
        Store(db).init_schema()
        code = cli.main(["--db", db, "ingest", "--connector", "jira",
                         "--from-file", _ticket_file(tmp_path)])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["projected"]["applied"] > 0
        store = Store(db)
        assert [n.value("name") for n in store.nodes_by_type("Requirement")] == [
            "Refund reversals appear same day"
        ]

    def test_unwired_discover_reports_where_to_implement(self, tmp_path, capsys):
        db = str(tmp_path / "kc.db")
        Store(db).init_schema()
        code = cli.main(["--db", db, "ingest", "--connector", "jira",
                         "--since", "2026-09-01", "--dry-run"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 2
        assert payload["wired"] is False
        assert payload["stage"] == "discover"
        assert "kc/connectors/jira.py" in payload["next"]

    def test_unknown_connector_is_rejected(self, tmp_path, capsys):
        db = str(tmp_path / "kc.db")
        Store(db).init_schema()
        code = cli.main(["--db", db, "ingest", "--connector", "zephyr", "--dry-run"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 2 and "known" in payload

    def test_quarantine_is_reported_per_object_not_fatal(self, tmp_path, capsys):
        db = str(tmp_path / "kc.db")
        Store(db).init_schema()
        unmapped = dict(TICKET, container="NOPE", source_id="NOPE-1")
        code = cli.main(["--db", db, "ingest", "--connector", "jira",
                         "--from-file", _ticket_file(tmp_path, unmapped, "NOPE-1.json")])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["results"][0]["rejected"] == "Quarantined"
        assert Store(db).last_seq() == 0


class TestDoctor:
    def test_healthy_store_passes_without_network(self, loaded):
        store, _ = loaded
        report = doctor.run(store.path, probe_network=False)
        assert report["verdict"] in ("pass", "warn")
        assert report["counts"]["fail"] == 0
        names = {c["check"] for c in report["checks"]}
        assert {"python version", "dependencies", "write-ahead log", "config files parse",
                "clock plausible", "projector", "offline extraction"} <= names

    def test_stdlib_only_is_asserted(self, loaded):
        store, _ = loaded
        report = doctor.run(store.path, probe_network=False)
        check = next(c for c in report["checks"] if c["check"] == "dependencies")
        assert check["status"] == "pass", check["detail"]

    def test_offline_extraction_proves_parsers_work(self, loaded):
        store, _ = loaded
        check = next(
            c for c in doctor.run(store.path, probe_network=False)["checks"]
            if c["check"] == "offline extraction"
        )
        assert check["status"] == "pass"
        detail = json.loads(check["detail"])
        assert set(detail) == {"git", "jira", "confluence", "ado"}

    def test_halted_projector_fails(self, loaded):
        store, _ = loaded
        store.halt_projector("graph", 3, "deliberate")
        report = doctor.run(store.path, probe_network=False)
        assert report["verdict"] == "fail"
        check = next(c for c in report["checks"] if c["check"] == "projector")
        assert check["status"] == "fail" and "halted" in check["detail"]

    def test_pending_events_warn(self, loaded):
        store, _ = loaded
        from kc.core.changeapi import ChangeAPI
        from kc.core.model import Claim, now

        ChangeAPI(store).submit(
            "kc-ingest", "add_claim",
            claims=[Claim(kind="identity", subject_id="n_pending", node_type="Service",
                          bu_owner="bu-a", source_system="git", source_instance="t",
                          source_id="acme/pending", valid_from=now())],
        )
        check = next(
            c for c in doctor.run(store.path, probe_network=False)["checks"]
            if c["check"] == "projector"
        )
        assert check["status"] == "warn" and "kc.cli project" in check["remedy"]

    def test_missing_database_warns_with_the_fix(self, tmp_path):
        report = doctor.run(str(tmp_path / "absent.db"), probe_network=False)
        check = next(c for c in report["checks"] if c["check"] == "database file")
        assert check["status"] == "warn" and "kc.cli init" in check["remedy"]

    def test_unresolvable_connector_base_warns_not_fails(self, loaded, monkeypatch):
        store, _ = loaded
        monkeypatch.setenv("KC_JIRA_BASE", "https://nothing.invalid.example")
        report = doctor.run(store.path, probe_network=True, timeout=0.5)
        check = next(c for c in report["checks"] if c["check"] == "connector jira")
        assert check["status"] == "warn"
        assert "proxy" in check["remedy"] or "unreachable" in check["detail"]
        assert report["counts"]["fail"] == 0

    def test_unset_base_is_a_warning_with_the_variable_name(self, loaded, monkeypatch):
        store, _ = loaded
        monkeypatch.delenv("KC_JIRA_BASE", raising=False)
        report = doctor.run(store.path, probe_network=False)
        check = next(c for c in report["checks"] if c["check"] == "connector jira")
        assert check["status"] == "warn" and "KC_JIRA_BASE" in check["detail"]

    def test_cli_exit_codes(self, loaded, capsys):
        store, _ = loaded
        assert cli.main(["--db", store.path, "doctor", "--no-network"]) == 0
        capsys.readouterr()
        store.halt_projector("graph", 1, "deliberate")
        assert cli.main(["--db", store.path, "doctor", "--no-network"]) == 1
        capsys.readouterr()


class TestTaskDistribution:
    def test_distribution_ranks_kinds_and_explains_them(self, loaded, capsys):
        store, _ = loaded
        for index in range(3):
            store.put_task(task_id=f"t_m{index}", kind="mention_candidate", state="open",
                           scope=["bu-a"], payload={}, reason="unresolved mention")
        store.put_task(task_id="t_s1", kind="staleness", state="open", scope=["bu-a"],
                       payload={}, reason="ttl exceeded")

        assert cli.main(["--db", store.path, "tasks", "--distribution"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dominant"] == "mention_candidate"
        assert payload["by_kind"]["mention_candidate"]["total"] == 3
        assert "thresholds" in payload["reading"]["mention_candidate"]
