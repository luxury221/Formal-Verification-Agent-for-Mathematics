"""Bounded reading → audit → formalization → audit → real kernel/replay queue."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import os
import threading
import time

from .chat_model import ModelConfig
from .domain import canonical, digest
from .formalizer import strict_json
from .model_service import ModelBudget
from .paper_automation import ACTIVE
from .paper_models import PaperModelRequest, PaperModelService
from .paper_protocol import (
    MAX_INPUT,
    audit_result,
    block_context,
    chunks,
    formal_result,
    scan_input,
    scan_result,
)
from . import paper_kernel
from .tasks import WorkerBusy, _now


class BudgetExhausted(RuntimeError):
    pass


class KernelInterrupted(OSError):
    pass


class PaperPump:
    def __init__(
        self, store, *, service_factory=PaperModelService, kernel_executor=paper_kernel.execute
    ):
        self.store, self.service_factory, self.kernel_executor = (
            store,
            service_factory,
            kernel_executor,
        )
        self.stopped, self.wake = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.run, name="whole-paper-automation", daemon=True)
        self.state, self.error, self.current_id = "IDLE", None, None
        self.deadline = None

    def snapshot(self):
        return {"state": self.state, "error": self.error, "job_id": self.current_id}

    @contextmanager
    def owner(self):
        if self.store.base.resolve() != self.store.base:
            raise ValueError("Paper queue cannot be redirected")
        fd = os.open(self.store.base / ".owner", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WorkerBusy("Another automatic paper worker owns the queue") from error
            yield fd
        finally:
            os.close(fd)

    def recover(self):
        # Caller owns queue lease. Child processes inherit that lease, so recovery
        # cannot overlap an orphan model or checker supervisor.
        for path in self.store.paths():
            with self.store.edit(path.name) as row:
                if row["state"] != "RUNNING":
                    continue
                if row.get("active_started_at"):
                    elapsed = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(row["active_started_at"])
                    ).total_seconds()
                    row["elapsed_seconds"] += max(0, elapsed)
                active = row["active"]
                if active:
                    if active["kind"] == "MODEL":
                        row["calls"][active["step"]]["status"] = "INTERRUPTED_COST_UNKNOWN"
                    target = (
                        row["chunks"][active["chunk"]]
                        if "chunk" in active
                        else row["items"][active["item"]]
                    )
                    target.update(
                        status="PENDING" if "chunk" in active else "READY",
                        feedback="上次调用中断；已保留预算占用，需显式恢复后才会尝试下一次。",
                    )
                row.update(
                    state="INTERRUPTED",
                    active=None,
                    active_started_at=None,
                    error="Supervisor interrupted; no provider request was automatically retried",
                )

    def stop_requested(self, key):
        return (
            self.stopped.is_set()
            or self.store.read(key)["cancel_requested"]
            or (self.deadline is not None and time.monotonic() >= self.deadline)
        )

    def guard(self, key):
        row = self.store.read(key)
        if self.stopped.is_set() or row["cancel_requested"]:
            raise InterruptedError("Automatic paper job stopped")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise BudgetExhausted("Whole-paper active wall-time budget exhausted")
        self.store.current(row)
        config = ModelConfig.load(self.store.root)
        if config is None:
            raise OSError("MODEL_NOT_CONFIGURED")
        if row["model"] and row["model"]["config_fingerprint"] != config.fingerprint:
            raise ValueError("Model configuration changed; create a new paper job")
        return config

    def call(self, key, step, task, packet, owner_fd, target):
        config = self.guard(key)
        budget = ModelBudget(
            timeout_seconds=30,
            max_input_chars=MAX_INPUT,
            max_output_tokens=2048 if task == "paper_audit" else 8192,
            max_output_bytes=262144,
        )
        request = PaperModelRequest(key + ":" + step, task, canonical(packet).decode(), budget)
        row = self.store.read(key)
        previous = row["calls"].get(step)
        if previous:
            if (
                previous["request_fingerprint"] != request.fingerprint
                or previous["status"] != "RESPONDED"
            ):
                raise OSError(
                    "Prior call has uncertain outcome; explicit recovery must start another bounded attempt"
                )
            return strict_json(self.store.artifact(row, previous["response"])["output_json"])
        request_name = self.store.put(key, "calls/" + step + "-request.json", asdict(request))
        with self.store.edit(key) as row:
            used, limit = row["used"], row["budget"]
            if (
                used["model_calls"] >= limit["model_calls"]
                or used["output_tokens_reserved"] + budget.max_output_tokens
                > limit["output_tokens"]
            ):
                raise BudgetExhausted("Shared model-call or output-token allowance exhausted")
            used["model_calls"] += 1
            used["output_tokens_reserved"] += budget.max_output_tokens
            row["calls"][step] = {
                "status": "STARTED",
                "request": request_name,
                "request_fingerprint": request.fingerprint,
                "response": None,
            }
            row["active"] = {"kind": "MODEL", "step": step, **target}
        service = self.service_factory(
            self.store.root,
            config,
            cancel_requested=lambda: self.stop_requested(key),
            lifecycle_lock_fd=owner_fd,
        )
        try:
            response = service.generate(request)
        except ValueError as error:
            raise OSError(
                "Model transport returned an invalid provider envelope; no implicit retry"
            ) from error
        if (
            response.request_fingerprint != request.fingerprint
            or response.usage.output_tokens > budget.max_output_tokens
            or len(response.output_json.encode()) > budget.max_output_bytes
        ):
            raise ValueError("Model response does not match the reserved request")
        response_name = self.store.put(key, "calls/" + step + "-response.json", asdict(response))
        with self.store.edit(key) as row:
            row["calls"][step].update(status="RESPONDED", response=response_name)
            row["used"]["input_tokens_reported"] += response.usage.input_tokens
            row["used"]["output_tokens_reported"] += response.usage.output_tokens
            row["active"] = None
        self.guard(key)
        return strict_json(response.output_json)

    def retry(self, key, target, error, context=()):
        message = str(error)[:6000]
        with self.store.edit(key) as row:
            item = (
                row["chunks"][target["chunk"]]
                if "chunk" in target
                else row["items"][target["item"]]
            )
            extra = list(dict.fromkeys([*item["extra_context"], *context]))
            if len(extra) > 24:
                item.update(status="GAP", feedback="完整上下文超过 24 个来源片段；需要进一步拆解。")
            else:
                item.update(
                    status="PENDING" if "chunk" in target else "READY",
                    feedback=message,
                    extra_context=extra,
                )

    def reading(self, key, index, owner_fd):
        row = self.store.read(key)
        source, fp = self.store.source(row), row["source_fingerprint"]
        focus, target = chunks(source)[index], {"chunk": index}
        while True:
            self.guard(key)
            chunk = self.store.read(key)["chunks"][index]
            if chunk["status"] == "GAP":
                return
            attempt = chunk["attempts"] + (chunk["status"] != "WORKING")
            if attempt > row["budget"]["repair_attempts"] + 1:
                with self.store.edit(key) as current:
                    current["chunks"][index]["status"] = "GAP"
                return
            with self.store.edit(key) as current:
                current["chunks"][index].update(status="WORKING", attempts=attempt)
            prefix = f"read-{index}-{attempt}"
            try:
                packet = {
                    "source_fingerprint": fp,
                    **scan_input(source, focus, chunk["extra_context"]),
                    "feedback": chunk["feedback"],
                }
                raw = self.call(key, prefix, "paper_scan", packet, owner_fd, target)
                reading = scan_result(raw, source, focus, fp)
                if reading["required_context"]:
                    self.retry(
                        key,
                        target,
                        "需要补充完整原文上下文：" + ", ".join(reading["required_context"]),
                        reading["required_context"],
                    )
                    continue
                name = self.store.put(key, "readings/" + prefix + ".json", reading)
                audit_packet = {
                    **packet,
                    "proposal_fingerprint": digest(reading),
                    "reading": reading,
                }
                review = audit_result(
                    self.call(
                        key, prefix + "-audit", "paper_audit", audit_packet, owner_fd, target
                    ),
                    source,
                    fp,
                    digest(reading),
                )
                review_name = self.store.put(key, "readings/" + prefix + "-audit.json", review)
                if review["decision"] != "ACCEPT":
                    self.retry(
                        key,
                        target,
                        "自动语义核对未通过：" + "; ".join(review["issues"]),
                        review["required_context"],
                    )
                    continue
                self.guard(key)
                created = []
                existing = self.store.read(key)
                if len(existing["items"]) + len(reading["items"]) > row["budget"]["obligations"]:
                    raise BudgetExhausted("Whole-paper obligation allowance exhausted")
                for local_id, obligation in reading["items"].items():
                    item_id = f"c{index}-{local_id}"
                    artifact = self.store.put(key, "obligations/" + item_id + ".json", obligation)
                    unresolved = obligation["content"]["questions"]
                    created.append(
                        {
                            "id": item_id,
                            "title": obligation["content"]["title"],
                            "obligation": artifact,
                            "status": "NEEDS_CLARIFICATION" if unresolved else "READY",
                            "chunk": index,
                            "attempts": 0,
                            "extra_context": [],
                            "feedback": None,
                            "dependencies": [],
                            "candidate": None,
                            "receipt": None,
                            "kernel_directory": None,
                        }
                    )
                with self.store.edit(key) as current:
                    current["chunks"][index].update(
                        status="ACCEPTED",
                        reading=name,
                        audit=review_name,
                        feedback="; ".join(reading["issues"]) or None,
                    )
                    for item in created:
                        current["items"][item["id"]] = item
                return
            except (ValueError, KeyError, TypeError, RecursionError) as error:
                self.retry(key, target, "阅读响应未满足协议：" + str(error))

    def dependencies(self, key):
        row = self.store.read(key)
        source = self.store.source(row)
        proposals = {
            i: self.store.artifact(row, item["obligation"]) for i, item in row["items"].items()
        }
        by_block = {}
        for i, proposal in proposals.items():
            for quote in proposal["quotes"]:
                if quote["role"] == "statement":
                    by_block.setdefault(quote["block_id"], set()).add(i)
        unresolved_blocks = set()
        for chunk in row["chunks"]:
            if chunk["status"] != "ACCEPTED":
                unresolved_blocks.update(u["block_id"] for u in chunks(source)[chunk["index"]])
        with self.store.edit(key) as current:
            for i, proposal in proposals.items():
                current["items"][i]["dependencies"] = sorted(
                    {dep for b in proposal["dependency_blocks"] for dep in by_block.get(b, ())}
                    - {i}
                )
                if (
                    set(proposal["dependency_blocks"]) & unresolved_blocks
                    and current["items"][i]["status"] == "READY"
                ):
                    current["items"][i].update(
                        status="BLOCKED_DEPENDENCY",
                        feedback="所引用片段尚未完整读取，不能作为已证明前提。",
                    )
            current["stage"] = "FORMALIZING"

    def formalize(self, key, item_id, owner_fd):
        row = self.store.read(key)
        source, fp = self.store.source(row), row["source_fingerprint"]
        obligation = self.store.artifact(row, row["items"][item_id]["obligation"])
        target = {"item": item_id}
        while True:
            self.guard(key)
            item = self.store.read(key)["items"][item_id]
            if item["status"] == "GAP":
                return
            attempt = item["attempts"] + (item["status"] != "WORKING")
            if attempt > row["budget"]["repair_attempts"] + 1:
                with self.store.edit(key) as current:
                    current["items"][item_id]["status"] = "GAP"
                return
            with self.store.edit(key) as current:
                current["items"][item_id].update(status="WORKING", attempts=attempt)
            prefix = f"formal-{item_id}-{attempt}"
            try:
                ids = list(
                    dict.fromkeys(
                        [
                            *[q["block_id"] for q in obligation["quotes"]],
                            *obligation["dependency_blocks"],
                            *item["extra_context"],
                        ]
                    )
                )
                packet = {
                    "source_fingerprint": fp,
                    "obligation_fingerprint": digest(obligation),
                    "obligation": obligation,
                    "context": block_context(source, ids),
                    "languages": paper_kernel.languages(),
                    "feedback": item["feedback"],
                }
                proposal = formal_result(
                    self.call(key, prefix, "paper_formalize", packet, owner_fd, target),
                    source,
                    fp,
                    obligation,
                )
                self.store.put(key, "candidates/" + prefix + "-proposal.json", proposal)
                if proposal["status"] == "NEEDS_CONTEXT":
                    self.retry(
                        key, target, "; ".join(proposal["questions"]), proposal["required_context"]
                    )
                    continue
                if proposal["status"] == "UNSUPPORTED":
                    with self.store.edit(key) as current:
                        current["items"][item_id].update(
                            status="UNSUPPORTED", feedback="; ".join(proposal["questions"])
                        )
                    return
                remaining = (
                    self.store.read(key)["budget"]["kernel_seconds"]
                    - self.store.read(key)["used"]["kernel_seconds_reserved"]
                )
                if remaining < 1:
                    raise BudgetExhausted("Shared kernel-worker time allowance exhausted")
                candidate = paper_kernel.compile_candidate(
                    proposal["profile"],
                    proposal["request"],
                    obligation["content"],
                    min(30, remaining),
                )
                candidate_name = self.store.put(key, "candidates/" + prefix + ".json", candidate)
                review = audit_result(
                    self.call(
                        key,
                        prefix + "-audit",
                        "paper_audit",
                        {
                            **packet,
                            "candidate": candidate,
                            "proposal_fingerprint": digest(candidate),
                        },
                        owner_fd,
                        target,
                    ),
                    source,
                    fp,
                    digest(candidate),
                )
                review_name = self.store.put(key, "candidates/" + prefix + "-audit.json", review)
                if review["decision"] != "ACCEPT":
                    self.retry(
                        key,
                        target,
                        "形式化语义核对未通过：" + "; ".join(review["issues"]),
                        review["required_context"],
                    )
                    continue
                self.guard(key)
                kernel_name = "kernel/" + prefix
                with self.store.edit(key) as current:
                    current["used"]["kernel_seconds_reserved"] += candidate["request"][
                        "timeout_seconds"
                    ]
                    current["active"] = {"kind": "KERNEL", "item": item_id}
                    current["items"][item_id].update(
                        candidate=candidate_name,
                        receipt=None,
                        semantic_audit=review_name,
                        kernel_directory=kernel_name,
                    )
                try:
                    receipt = self.kernel_executor(
                        self.store.runtime_root,
                        self.store.directory(key) / kernel_name,
                        candidate,
                        cancel_requested=lambda: self.stop_requested(key),
                        lifecycle_lock_fd=owner_fd,
                        wall_seconds=min(600, max(1, self.deadline - time.monotonic())),
                    )
                except InterruptedError:
                    raise
                except OSError as error:
                    raise KernelInterrupted(
                        "Kernel process interrupted; reserved worker budget retained"
                    ) from error
                except ValueError as error:
                    raise RuntimeError(
                        "Kernel artifact or receipt integrity check failed"
                    ) from error
                receipt_name = self.store.put(
                    key, "candidates/" + prefix + "-receipt.json", receipt
                )
                with self.store.edit(key) as current:
                    current["used"]["kernel_seconds_reported"] += receipt["worker_seconds"]
                    current["items"][item_id]["receipt"] = receipt_name
                    current["active"] = None
                self.guard(key)
                if receipt["kernel_status"] == "UNAVAILABLE":
                    self.retry(key, target, "验证环境不可用：" + str(receipt.get("diagnostic")))
                    raise KernelInterrupted(
                        "Kernel setup or integrity validation unavailable; no automatic model repair"
                    )
                if receipt["kernel_status"] == "KERNEL_CHECKED":
                    with self.store.edit(key) as current:
                        current["items"][item_id].update(status="KERNEL_CHECKED", feedback=None)
                    return
                self.retry(
                    key, target, "真实检查未通过（不代表反驳）：" + str(receipt.get("diagnostic"))
                )
            except (ValueError, KeyError, TypeError, RecursionError) as error:
                self.retry(key, target, "形式化响应未满足协议：" + str(error))

    def execute(self, key, owner_fd):
        row = self.store.read(key)
        if row["state"] not in ("QUEUED", "WAITING_FOR_SOURCE") or row["cancel_requested"]:
            return
        doc = self.store.current(row)
        if not row["source_fingerprint"]:
            if doc["state"] in ("QUEUED", "RUNNING"):
                return
            if doc["state"] != "READY":
                with self.store.edit(key) as current:
                    current.update(
                        state="SOURCE_UNAVAILABLE", error="Document extraction did not finish"
                    )
                return
            source = self.store.documents.draft(row["document_id"])
            self.store.put(key, "source.json", source)
            with self.store.edit(key) as current:
                current.update(
                    source_fingerprint=digest(source),
                    chunks=self.store.chunk_records(source),
                    state="QUEUED",
                )
        config = ModelConfig.load(self.store.root)
        if config is None:
            with self.store.edit(key) as current:
                current.update(
                    state="WAITING_FOR_MODEL",
                    error="MODEL_NOT_CONFIGURED: configure the local service, then explicitly resume",
                )
            return
        started = time.monotonic()
        with self.store.edit(key) as current:
            if current["model"] and current["model"]["config_fingerprint"] != config.fingerprint:
                raise ValueError("Model configuration changed; create a new paper job")
            current.update(
                state="RUNNING", model=config.public(), active_started_at=_now(), error=None
            )
            self.deadline = started + max(
                0, current["budget"]["wall_seconds"] - current["elapsed_seconds"]
            )
        state, error = None, None
        try:
            self.guard(key)
            row = self.store.read(key)
            if row["stage"] == "READING":
                for chunk in row["chunks"]:
                    if chunk["status"] not in ("ACCEPTED", "GAP"):
                        self.reading(key, chunk["index"], owner_fd)
                self.dependencies(key)
            while True:
                self.guard(key)
                row = self.store.read(key)
                pending = [
                    i for i, item in row["items"].items() if item["status"] in ("READY", "WORKING")
                ]
                available = next(
                    (
                        i
                        for i in pending
                        if all(
                            row["items"][d]["status"] == "KERNEL_CHECKED"
                            for d in row["items"][i]["dependencies"]
                        )
                    ),
                    None,
                )
                if available is None:
                    with self.store.edit(key) as current:
                        for i in pending:
                            current["items"][i].update(
                                status="BLOCKED_DEPENDENCY",
                                feedback="前置义务未通过或存在依赖环；未将其当作已证明假设。",
                            )
                    break
                self.formalize(key, available, owner_fd)
            row = self.store.read(key)
            gaps = any(c["status"] != "ACCEPTED" or c["feedback"] for c in row["chunks"]) or any(
                i["status"] != "KERNEL_CHECKED" for i in row["items"].values()
            )
            state = "COMPLETED_WITH_GAPS" if gaps else "COMPLETED"
        except InterruptedError as caught:
            state = (
                "INTERRUPTED"
                if self.stopped.is_set()
                else (
                    "CANCELLED" if self.store.read(key)["cancel_requested"] else "BUDGET_EXHAUSTED"
                )
            )
            error = str(caught)
        except BudgetExhausted as caught:
            state, error = "BUDGET_EXHAUSTED", str(caught)
        except KernelInterrupted as caught:
            state, error = "EXECUTION_ERROR", str(caught)
        except (OSError, TimeoutError) as caught:
            state, error = "MODEL_ERROR", type(caught).__name__ + ": " + str(caught)[:1000]
        except (ValueError, KeyError, TypeError, RuntimeError) as caught:
            state, error = "POLICY_BLOCKED", type(caught).__name__ + ": " + str(caught)[:1000]
        finally:
            with self.store.edit(key) as current:
                if current["cancel_requested"]:
                    state = "CANCELLED"
                current.update(
                    state=state or "INTERRUPTED",
                    error=error,
                    active_started_at=None,
                    elapsed_seconds=current["elapsed_seconds"] + time.monotonic() - started,
                )
                # Keep active operation provenance after failure. Explicit resume
                # advances its attempt; no duplicate unknown-charge request.
                if current["active"]:
                    active = current["active"]
                    if active["kind"] == "MODEL":
                        current["calls"][active["step"]]["status"] = "INTERRUPTED_COST_UNKNOWN"
                    target = (
                        current["chunks"][active["chunk"]]
                        if "chunk" in active
                        else current["items"][active["item"]]
                    )
                    target["status"] = "PENDING" if "chunk" in active else "READY"
                    current["active"] = None
            self.deadline = None

    def run(self):
        while not self.stopped.is_set():
            try:
                self._run_once()
                return
            except WorkerBusy as error:
                # A prior bounded child may still retain the inherited lease.
                # Retry ownership only; recover() never retries provider calls.
                self.state, self.error = "WAITING_FOR_OWNER", str(error)
                self.stopped.wait(0.25)
        self.state = "STOPPED"

    def _run_once(self):
        try:
            with self.owner() as fd:
                self.state, self.error = "IDLE", None
                self.recover()
                while not self.stopped.is_set():
                    for path in self.store.paths():
                        row = self.store.read(path.name)
                        if row["state"] not in ACTIVE or row["state"] == "RUNNING":
                            continue
                        self.current_id, self.state = path.name, "RUNNING"
                        try:
                            self.execute(path.name, fd)
                        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                            with self.store.edit(path.name) as current:
                                current.update(
                                    state="POLICY_BLOCKED",
                                    error=type(error).__name__ + ": " + str(error)[:1000],
                                )
                        finally:
                            self.current_id, self.state = None, "IDLE"
                        if self.stopped.is_set():
                            break
                    self.wake.wait(0.4)
                    self.wake.clear()
        except WorkerBusy:
            raise
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            self.state, self.error = "UNAVAILABLE", type(error).__name__ + ": " + str(error)[:1000]
        finally:
            if self.state != "UNAVAILABLE":
                self.state = "STOPPED"

    def close(self):
        self.stopped.set()
        self.wake.set()
        if self.thread.is_alive():
            self.thread.join(timeout=10)
