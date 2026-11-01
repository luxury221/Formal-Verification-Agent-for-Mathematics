"""Reviewed tactic prefixes in fresh Lean processes; open goals are never proofs."""

from copy import deepcopy
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
import uuid

from .domain import digest
from .formalizer import exact_object, strict_json
from .integrity import file_hash
from .knowledge_instances import storage_busy
from .lean_goal_trace import checkpoint
from .lean_strategies import STRATEGIES
from .mathlib_lab import MathlibLabStore, now, source_manifest
from .number_theory_search import TARGET_FIELDS
from .number_theory_terms import compile_request
from .research_flow import FlowCheckMixin, ResearchFlowStore, atomic, identifier, lease
from .search_evaluation import _envelope
from .tasks import TaskStore, WorkerBusy, _identifier

MODE = "mathlib-nat-proof-steps-v1"
SCHEMA = "project-proof-steps-v1"
TACTICS = MappingProxyType(
    {
        "intros": ("引入变量与全部前提", "intros"),
        "dvd-gcd": ("拆开 gcd 整除目标", "apply Nat.dvd_gcd"),
        "dvd-add": ("拆开和的整除目标", "apply Nat.dvd_add"),
        "assumption": ("用已有假设关闭当前目标", "assumption"),
        "all-assumptions": ("用已有假设关闭全部目标", "all_goals assumption"),
        "omega": ("尝试自然数线性算术", "omega"),
        "simp-gcd": (
            "尝试受控 gcd 化简",
            STRATEGIES["simp-gcd"]["proof_source"].split("  intros\n", 1)[-1].strip(),
        ),
    }
)
LIMITS = {"workspaces": 50, "attempts": 16, "depth": 12, "maximum_worker_seconds": 480}


def manifest(root):
    return {"executor": source_manifest(root), "extension": file_hash(root / "mra/proof_steps.py")}


def prepare(root, raw):
    exact_object(raw, TARGET_FIELDS | {"steps"}, "Complete controlled proof prefix")
    if (
        raw["schema"] != MODE
        or not isinstance(raw["steps"], list)
        or len(raw["steps"]) > LIMITS["depth"]
    ):
        raise ValueError("Use the versioned proof-step schema and at most twelve steps")
    for step in raw["steps"]:
        if not isinstance(step, str) or step not in TACTICS:
            raise ValueError("Only named fixed tactics are accepted")
    target = {k: v for k, v in raw.items() if k != "steps"}
    target["schema"] = "mathlib-nat-gcd-v1"
    compiled = compile_request(
        {**target, "proof": {"ref": "Nat.dvd_refl", "terms": [{"nat": 0}], "proofs": []}}
    )
    lines = ["by", checkpoint("0")]
    for index, step in enumerate(raw["steps"], 1):
        lines += ["  " + TACTICS[step][1], checkpoint(str(index))]
    plan = {
        "request": deepcopy(raw),
        "formal_statement": compiled["formal_statement"],
        "variable_mapping": compiled["variable_mapping"],
        "assumptions": compiled["assumptions"],
        "proof_source": "\n".join(lines),
        "source_manifest": manifest(Path(root).resolve()),
        "budget": {
            "shared_worker_seconds": raw["timeout_seconds"],
            "candidate_processes": 1,
            "replay_processes": 1,
            "max_heartbeats": 200000,
            "automatic_retries": 0,
            "integrity_time_separate": True,
        },
        "math_verdict": "UNKNOWN",
        "evidence_level": "E0",
        "model_calls": 0,
        "limitations": [
            "Every click rebuilds the complete prefix in a fresh Lean process; no persistent Lean session is restored.",
            "Open-goal observations intentionally fail theorem elaboration and remain UNKNOWN/E0; no sorry proof is accepted.",
            "Only completed exact theorem audits and independent proof-data replay can be PROVED/E4.",
            "No arbitrary tactic text, automatic retries, injected premises or model planning; premise consistency is not certified.",
        ],
    }
    plan["plan_fingerprint"] = digest(plan)
    return plan


class PrefixProvider(MathlibLabStore):
    def preview(self, raw):
        return prepare(self.root, raw)

    def gate(self, row, check_files=True):
        if row["plan"]["source_manifest"] != manifest(self.root):
            return "Proof-step program or trusted executor changed"
        view = {
            **row,
            "plan": {**row["plan"], "source_manifest": row["plan"]["source_manifest"]["executor"]},
        }
        return super().gate(view, check_files)


class PrefixChecks(FlowCheckMixin, PrefixProvider):
    pass


def preview_check(root, request):
    checker = object.__new__(PrefixChecks)
    checker.root = root
    return checker.preview(request)


def observations(checker, child):
    """Read actual diagnostics, distinguishing expected open goals from tactic errors."""
    records = []
    if not child["files"] or child["invalidated"] or child["result"] is None:
        return {"outcome": "UNAVAILABLE", "checkpoints": []}
    output = (checker.directory(child["run_id"]) / "candidate/stdout.log").read_text(
        encoding="utf-8"
    )
    for line in output.splitlines():
        if not line.startswith("MRA_GOALS:"):
            continue
        value = strict_json(line.removeprefix("MRA_GOALS:"))
        exact_object(value, {"label", "goals"}, "Actual prefix checkpoint")
        if not isinstance(value["goals"], list) or len(value["goals"]) > 24:
            raise ValueError("Actual prefix goal limit exceeded")
        for goal in value["goals"]:
            exact_object(goal, {"mvar", "pretty"}, "Actual prefix goal")
            if any(not isinstance(v, str) or not 0 < len(v) <= 16000 for v in goal.values()):
                raise ValueError("Invalid actual goal diagnostic")
        records.append(value)
    expected = [str(i) for i in range(len(child["plan"]["request"]["steps"]) + 1)]
    complete = [v["label"] for v in records] == expected
    outcome = "TACTIC_FAILED"
    if complete and records[-1]["goals"]:
        errors = re.findall(
            r"^Candidate\.lean:\d+:\d+: error(?:\([^)]*\))?: (.*)", output, re.MULTILINE
        )
        candidate = child["result"]["candidate"]
        if (
            child["state"] == "REJECTED"
            and candidate
            and candidate["returncode"] == 1
            and errors
            and all(s == "unsolved goals" for s in errors)
            and not (checker.directory(child["run_id"]) / "candidate/stderr.log").read_text()
        ):
            outcome = "OPEN"
    elif (
        complete
        and not records[-1]["goals"]
        and (child["state"], child["math_verdict"], child["evidence_level"])
        == ("COMPLETED", "PROVED", "E4")
    ):
        outcome = "CLOSED"
    return {"outcome": outcome, "checkpoints": records, "source": "candidate/stdout.log"}


class ProofStepStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = self.root / "artifacts/proof-steps"
        if self.base.resolve() != self.base:
            raise ValueError("Proof-step storage cannot be redirected")
        self.base.mkdir(parents=True, exist_ok=True)
        self.flows = ResearchFlowStore(self.root)
        self.tasks = TaskStore(self.root)

    def directory(self, key):
        path = self.base / identifier(key, "scope")
        if path.resolve() != path:
            raise ValueError("Proof-step workspace cannot be redirected")
        return path

    def paths(self):
        if self.base.resolve() != self.base:
            raise ValueError("Proof-step storage changed")
        rows = sorted(p for p in self.base.iterdir() if re.fullmatch(r"scope-[a-f0-9]{32}", p.name))
        if len(rows) > LIMITS["workspaces"]:
            raise ValueError("Proof-step workspace limit exceeded")
        return rows

    def read(self, key):
        try:
            row = _envelope(self.directory(key) / "record.json")
            if row["scope_id"] != key or row["schema"] != SCHEMA:
                raise ValueError("Proof-step record identity changed")
            return row
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            atomic(self.directory(key) / "invalidated.json", {"reason": str(error)})
            raise

    def records(self):
        rows = []
        # Creation publishes plan then record while holding this same lease.
        # Snapshot under it, releasing before yielding to any slow audit/worker.
        with lease(self.base):
            for path in self.paths():
                try:
                    rows.append((path, self.read(path.name)))
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    continue  # A quarantined record must not stop other workspaces.
        yield from rows

    def save(self, row):
        atomic(self.directory(row["scope_id"]) / "record.json", row)

    def context(self, flow_id, node_id):
        flow = self.flows.report(flow_id)
        if flow["invalidated"] or node_id not in flow["node_states"]:
            raise ValueError("A current exact research node is required")
        node = next(n for n in flow["plan"]["nodes"] if n["id"] == node_id)
        state = flow["node_states"][node_id]
        if state["blocked_by"]:
            raise ValueError("Complete the research prerequisites before stepping this node")
        return {
            "flow_id": flow_id,
            "node_id": node_id,
            "target": node["target"],
            "flow_plan_fingerprint": flow["plan_fingerprint"],
            "prerequisites": {
                d: flow["node_states"][d]["binding_fingerprint"] for d in node["depends_on"]
            },
        }

    def preview(self, raw):
        exact_object(raw, {"flow_id", "node_id"}, "Proof-step workspace")
        value = {
            "schema": SCHEMA,
            "context": self.context(**raw),
            "source_manifest": manifest(self.root),
            "limits": LIMITS,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
            "model_calls": 0,
        }
        value["plan_fingerprint"] = digest(value)
        return value

    def create(self, raw):
        exact_object(
            raw,
            {"flow_id", "node_id", "request_id", "plan_fingerprint"},
            "Save proof-step workspace",
        )
        _identifier(raw["request_id"])
        plan = self.preview({k: raw[k] for k in ("flow_id", "node_id")})
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Preview the exact research target again")
        with lease(self.base):
            paths = self.paths()
            for path in paths:
                try:
                    old = self.read(path.name)
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    continue
                if old["request_id"] == raw["request_id"]:
                    if old["submission_fingerprint"] != digest(raw):
                        raise ValueError("Request ID belongs to different workspace input")
                    return self.report(path.name)
            if len(paths) >= LIMITS["workspaces"]:
                raise ValueError("Export before creating more workspaces")
            key = "scope-" + uuid.uuid4().hex
            self.directory(key).mkdir()
            atomic(self.directory(key) / "plan.json", plan)
            self.save(
                {
                    "schema": SCHEMA,
                    "scope_id": key,
                    "request_id": raw["request_id"],
                    "submission_fingerprint": digest(raw),
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "created_at": now(),
                    "invalidated": False,
                    "reason": None,
                    "attempts": [],
                    "events": [],
                }
            )
        return self.report(key)

    def audit(self, row):
        path = self.directory(row["scope_id"])
        plan = _envelope(path / "plan.json")
        if (
            plan["plan_fingerprint"] != row["plan_fingerprint"]
            or digest({k: v for k, v in plan.items() if k != "plan_fingerprint"})
            != row["plan_fingerprint"]
        ):
            raise ValueError("Proof-step plan fingerprint changed")
        try:
            reason = (
                "Workspace was previously invalidated"
                if (path / "invalidated.json").exists()
                else "Proof-step source changed"
                if plan["source_manifest"] != manifest(self.root)
                else "Research target or prerequisites changed"
                if plan["context"]
                != self.context(plan["context"]["flow_id"], plan["context"]["node_id"])
                else None
            )
        except WorkerBusy:
            raise
        except sqlite3.Error as error:
            if storage_busy(error):
                raise
            reason = "Research context unavailable: " + str(error)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            reason = "Research context unavailable: " + str(error)
        if reason:
            row.update(invalidated=True, reason=reason)
        accepted, children = {}, {}
        for a in row["attempts"]:
            if row["invalidated"] or (a["parent_id"] and a["parent_id"] not in accepted):
                a.update(invalidated=True, reason="Workspace or prefix ancestor is unavailable")
            if not a["run_id"] or a["state"] in ("QUEUED", "RUNNING"):
                continue
            try:
                checker = PrefixChecks(self.root, path / a["attempt_id"])
                child = checker.report(a["run_id"])
                children[a["attempt_id"]] = child
                if a["state"] != "COMPLETED" or a["invalidated"]:
                    continue
                actual = observations(checker, child)
                source = digest({k: child[k] for k in ("plan", "alignment", "files", "result")})
                if (
                    actual["outcome"] not in ("OPEN", "CLOSED")
                    or actual != a["observation"]
                    or source != a["source_fingerprint"]
                    or child["invalidated"]
                    or child["plan"] != a["plan"]["child_plan"]
                ):
                    raise ValueError("Actual prefix evidence changed")
                if a["parent_id"]:
                    parent = accepted[a["parent_id"]]
                    if a["plan"]["parent_binding"] != parent["source_fingerprint"]:
                        raise ValueError("Prefix ancestor binding changed")
                accepted[a["attempt_id"]] = a
            except sqlite3.Error as error:
                if storage_busy(error):
                    raise
                a.update(invalidated=True, reason=str(error))
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                a.update(invalidated=True, reason=str(error))
        proved = any(a["observation"]["outcome"] == "CLOSED" for a in accepted.values())
        return {
            **row,
            "plan": plan,
            "accepted": list(accepted),
            "checks": children,
            "math_verdict": "PROVED" if proved else "UNKNOWN",
            "evidence_level": "E4" if proved else "E0",
            "model_calls": 0,
            "fresh_attestation": False,
        }

    def report(self, key):
        with lease(self.directory(key)):
            row = self.read(key)
            before = deepcopy(row)
            try:
                result = self.audit(row)
            except (sqlite3.Error, WorkerBusy):
                raise
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                atomic(self.directory(key) / "invalidated.json", {"reason": str(error)})
                raise
            if row != before:
                self.save(row)
            return result

    def catalog(self, flow_id, node_id):
        context = self.context(flow_id, node_id)
        rows = []
        for path, row in self.records():
            try:
                plan = _envelope(path / "plan.json")
                binding = (plan["context"]["flow_id"], plan["context"]["node_id"])
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                atomic(path / "invalidated.json", {"reason": str(error)})
                continue
            if binding == (flow_id, node_id):
                rows.append(
                    {
                        "scope_id": row["scope_id"],
                        "created_at": row["created_at"],
                        "invalidated": row["invalidated"],
                    }
                )
        return {
            "context": context,
            "workspaces": rows,
            "tactics": {k: {"label": v[0], "source": v[1]} for k, v in TACTICS.items()},
            "limits": LIMITS,
        }

    def preview_step(self, key, raw):
        exact_object(raw, {"parent_id", "tactic"}, "Controlled next step")
        row = self.report(key)
        if row["invalidated"]:
            raise ValueError("Workspace is invalidated")
        parent, steps = None, []
        if raw["parent_id"] is not None:
            parent = next((a for a in row["attempts"] if a["attempt_id"] == raw["parent_id"]), None)
            if (
                not parent
                or parent["attempt_id"] not in row["accepted"]
                or parent["observation"]["outcome"] != "OPEN"
            ):
                raise ValueError("Choose a checked open prefix as the branch parent")
            if not isinstance(raw["tactic"], str) or raw["tactic"] not in TACTICS:
                raise ValueError("Choose one fixed next tactic")
            steps = parent["plan"]["child_plan"]["request"]["steps"] + [raw["tactic"]]
        elif raw["tactic"] is not None:
            raise ValueError("First inspect the initial target without adding a tactic")
        target = row["plan"]["context"]["target"]
        child = preview_check(self.root, {**target, "schema": MODE, "steps": steps})
        value = {
            "scope_id": key,
            **raw,
            "parent_binding": parent["source_fingerprint"] if parent else None,
            "workspace_plan_fingerprint": row["plan_fingerprint"],
            "child_plan": child,
            "math_verdict": "UNKNOWN",
            "evidence_level": "E0",
        }
        value["plan_fingerprint"] = digest(value)
        return value

    def submit(self, key, raw):
        exact_object(
            raw,
            {"request_id", "parent_id", "tactic", "plan_fingerprint", "reviewer", "confirmed"},
            "Reviewed next step",
        )
        _identifier(raw["request_id"])
        if (
            raw["confirmed"] is not True
            or not isinstance(raw["reviewer"], str)
            or not 0 < len(raw["reviewer"].strip()) <= 200
        ):
            raise ValueError("Named review of the complete target, prefix and budget is required")
        plan = self.preview_step(key, {k: raw[k] for k in ("parent_id", "tactic")})
        if raw["plan_fingerprint"] != plan["plan_fingerprint"]:
            raise ValueError("Exact prefix plan changed; preview again")
        with lease(self.directory(key)):
            row = self.read(key)
            self.audit(row)
            if row["invalidated"]:
                self.save(row)
                raise ValueError("Workspace changed")
            if plan["parent_id"]:
                parent = next(a for a in row["attempts"] if a["attempt_id"] == plan["parent_id"])
                if parent["invalidated"] or parent["source_fingerprint"] != plan["parent_binding"]:
                    self.save(row)
                    raise ValueError("Branch parent changed")
            for a in row["attempts"]:
                if a["request_id"] == raw["request_id"]:
                    if a["submission_fingerprint"] != digest(raw):
                        raise ValueError("Request ID belongs to different confirmation")
                    return deepcopy(a)
            if len(row["attempts"]) >= LIMITS["attempts"] or any(
                a["state"] in ("QUEUED", "RUNNING") for a in row["attempts"]
            ):
                raise ValueError(
                    "Finish the active check or export before the check budget is exceeded"
                )
            a = {
                "attempt_id": "attempt-" + uuid.uuid4().hex,
                "request_id": raw["request_id"],
                "submission_fingerprint": digest(raw),
                "plan": plan,
                "parent_id": raw["parent_id"],
                "reviewer": raw["reviewer"].strip(),
                "state": "QUEUED",
                "run_id": None,
                "invalidated": False,
                "reason": None,
                "cancel_requested": False,
                "observation": None,
                "source_fingerprint": None,
                "error": None,
            }
            row["attempts"].append(a)
            row["events"].append(
                {"kind": "NAMED_PREFIX_QUEUED", "at": now(), "attempt_id": a["attempt_id"]}
            )
            self.save(row)
            return deepcopy(a)

    def cancel(self, key, attempt_id):
        with lease(self.directory(key)):
            row = self.read(key)
            a = next((a for a in row["attempts"] if a["attempt_id"] == attempt_id), None)
            if a is None:
                raise ValueError("Unknown prefix attempt")
            if a["state"] in ("QUEUED", "RUNNING"):
                a["cancel_requested"] = True
                if a["state"] == "QUEUED":
                    a["state"] = "CANCELLED"
                if a["run_id"]:
                    PrefixChecks(self.root, self.directory(key) / attempt_id).cancel(a["run_id"])
                self.save(row)
        return self.report(key)

    def recover_locked(self):
        recovered = []
        for path, initial in self.records():
            if not any(a["state"] == "RUNNING" for a in initial["attempts"]):
                continue
            with lease(path):
                row = self.read(path.name)
                for a in row["attempts"]:
                    if a["state"] != "RUNNING":
                        continue
                    directory = path / a["attempt_id"]
                    try:
                        if (directory / "checks/lab.sqlite3").is_file():
                            checker = PrefixChecks(self.root, directory)
                            with checker.transaction() as db:
                                for value in db.execute("SELECT body,sha256 FROM runs").fetchall():
                                    child = checker.decode(value)
                                    if child["state"] == "QUEUED":
                                        child.update(
                                            state="CANCELLED",
                                            stage="CANCELLED",
                                            cancel_requested=True,
                                        )
                                        checker.save(db, child)
                            checker.recover_locked()
                    except sqlite3.Error as error:
                        if storage_busy(error):
                            raise
                        a.update(
                            invalidated=True, reason="Child recovery unavailable: " + str(error)
                        )
                    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                        a.update(
                            invalidated=True, reason="Child recovery unavailable: " + str(error)
                        )
                    a.update(
                        state="INTERRUPTED",
                        error="Supervisor stopped; no prefix or proof promotion and no automatic retry",
                    )
                    recovered.append(a["attempt_id"])
                self.save(row)
        return {"interrupted": recovered, "automatic_retry": False}

    def recover(self):
        with self.tasks.worker_lock():
            return self.recover_locked()

    def execute(self, key, attempt_id, lock_fd):
        row = self.read(key)
        a = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
        directory = self.directory(key) / attempt_id
        checked, observation, error, checker = None, None, None, None
        try:
            directory.mkdir()
            checker = PrefixChecks(self.root, directory, create=True)
            child = checker.submit(
                {
                    "request_id": attempt_id,
                    "request": a["plan"]["child_plan"]["request"],
                    "plan_fingerprint": a["plan"]["child_plan"]["plan_fingerprint"],
                    "reviewer": a["reviewer"],
                    "confirmed": True,
                }
            )
            with lease(self.directory(key)):
                row = self.read(key)
                a = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
                a["run_id"] = child["run_id"]
                self.save(row)
                cancel = a["cancel_requested"] or a["invalidated"] or row["invalidated"]
            if cancel:
                checker.cancel(child["run_id"])
                checked = checker.report(child["run_id"])
            else:
                with checker.transaction() as db:
                    child = checker.read(child["run_id"], db)
                    child.update(state="RUNNING", stage="CHECKING")
                    checker.save(db, child)
                checked = checker.execute(child["run_id"], lock_fd)
                observation = observations(checker, checked)
                if a["parent_id"] and observation["outcome"] in ("OPEN", "CLOSED"):
                    parent = next(p for p in row["attempts"] if p["attempt_id"] == a["parent_id"])
                    before = parent["observation"]["checkpoints"]
                    for old, new in zip(before, observation["checkpoints"]):
                        if [g["pretty"] for g in old["goals"]] != [
                            g["pretty"] for g in new["goals"]
                        ]:
                            raise ValueError(
                                "Fresh Lean prefix differs from the chosen branch state"
                            )
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as caught:
            error = str(caught)
            if checker:
                checker.recover_locked()
        with lease(self.directory(key)):
            row = self.read(key)
            a = next(a for a in row["attempts"] if a["attempt_id"] == attempt_id)
            good = not error and observation and observation["outcome"] in ("OPEN", "CLOSED")
            state = (
                "EXECUTION_ERROR"
                if error
                else "COMPLETED"
                if good
                else checked["state"]
                if checked
                else "EXECUTION_ERROR"
            )
            if a["cancel_requested"]:
                state, good = "CANCELLED", False
            a.update(
                state=state,
                observation=observation,
                error=error
                or (
                    checked["result"]["error"]
                    if checked and checked["result"] and not good
                    else None
                ),
            )
            if good:
                a["source_fingerprint"] = digest(
                    {k: checked[k] for k in ("plan", "alignment", "files", "result")}
                )
            self.save(row)
        return self.report(key)

    def work_once(self):
        with self.tasks.worker_lock() as fd:
            self.recover_locked()
            for path, initial in self.records():
                if not any(a["state"] == "QUEUED" for a in initial["attempts"]):
                    continue
                try:
                    self.report(path.name)
                except (sqlite3.Error, WorkerBusy):
                    raise
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    continue  # A quarantined plan must not stop unrelated queued checks.
                with lease(path):
                    row = self.read(path.name)
                    self.audit(row)
                    a = next((a for a in row["attempts"] if a["state"] == "QUEUED"), None)
                    if not a:
                        continue
                    if row["invalidated"] or a["invalidated"]:
                        a.update(
                            state="POLICY_BLOCKED",
                            error="Research context or branch prefix changed",
                        )
                        self.save(row)
                        continue
                    a["state"] = "RUNNING"
                    self.save(row)
                self.execute(path.name, a["attempt_id"], fd)
                return {
                    "worker_state": "EXECUTED",
                    "scope_id": path.name,
                    "attempt_id": a["attempt_id"],
                }
        return {"worker_state": "IDLE"}
