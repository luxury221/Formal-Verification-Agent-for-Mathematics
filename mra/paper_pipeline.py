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
from .paper_automation import ACTIVE, SCHEMA
from .paper_models import PaperModelRequest, PaperModelService
from .paper_protocol import (
    MAX_INPUT,
    block_context,
    formal_result,
    scan_result,
)
from . import paper_alignment, paper_budget, paper_kernel, paper_structure
from .formalizer import exact_object
from .model_service import text_field
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
                if row["schema"] != SCHEMA or row["state"] != "RUNNING":
                    continue
                if row.get("active_started_at"):
                    elapsed = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(row["active_started_at"])
                    ).total_seconds()
                    row["elapsed_seconds"] += max(0, elapsed)
                active = row["active"]
                if active:
                    paper_budget.uncertain(row, active.get("reservation"))
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
        if any(
            paper_budget.totals(row)[r]["occupied"] > row["budget"][r]
            for r in paper_budget.RESOURCES
        ):
            raise BudgetExhausted("Confirmed resource consumption exceeded the shared allowance")
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
            timeout_seconds=90 if task == "paper_formalize" else 60,
            max_input_chars=MAX_INPUT,
            max_output_tokens=16384 if task == "paper_audit" else 8192,
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
                or paper_budget.available(row, "output_tokens") < budget.max_output_tokens
            ):
                raise BudgetExhausted("Shared model-call or output-token allowance exhausted")
            used["model_calls"] += 1
            paper_budget.reserve(row, "model:" + step, "output_tokens", budget.max_output_tokens)
            row["calls"][step] = {
                "status": "STARTED",
                "request": request_name,
                "request_fingerprint": request.fingerprint,
                "response": None,
            }
            row["active"] = {
                "kind": "MODEL",
                "step": step,
                "reservation": "model:" + step,
                **target,
            }
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
            raise OSError("Model response does not match the reserved request; consumption unknown")
        response_name = self.store.put(key, "calls/" + step + "-response.json", asdict(response))
        with self.store.edit(key) as row:
            paper_budget.settle(row, "model:" + step, response.usage.output_tokens, response_name)
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
        focus, target = self.store.focus(row, row["chunks"][index], source), {"chunk": index}
        structure = self.store.artifact(row, row["structure"])
        while True:
            self.guard(key)
            chunk = self.store.read(key)["chunks"][index]
            if chunk["status"] in ("GAP", "SPLIT", "ACCEPTED"):
                return
            attempt = chunk["attempts"] + (chunk["status"] != "WORKING")
            if attempt > row["budget"]["repair_attempts"] + 1:
                if len(focus) > 1 or sum(len(u["text"]) for u in focus) > 4000:
                    self.store.split(key, index, "阅读或核对尝试已用尽：" + str(chunk["feedback"]))
                else:
                    with self.store.edit(key) as current:
                        current["chunks"][index]["status"] = "GAP"
                return
            with self.store.edit(key) as current:
                current["chunks"][index].update(status="WORKING", attempts=attempt)
            prefix = f"read-{index}-{attempt}"
            try:
                packet = {
                    "source_fingerprint": fp,
                    **paper_structure.scan_packet(source, structure, focus, chunk["extra_context"]),
                    "feedback": chunk["feedback"],
                }
                raw = self.call(key, prefix, "paper_scan", packet, owner_fd, target)
                if isinstance(raw, dict) and raw.get("action") == "SPLIT":
                    exact_object(
                        raw, {"action", "source_fingerprint", "reason"}, "Adaptive reading split"
                    )
                    if raw["source_fingerprint"] != fp:
                        raise ValueError("Split request does not match fixed source")
                    text_field(raw["reason"], "Specific split reason", 2000)
                    self.store.split(key, index, raw["reason"])
                    return
                if (
                    isinstance(raw, dict)
                    and isinstance(raw.get("obligations"), list)
                    and len(raw["obligations"]) > 8
                ):
                    self.store.split(key, index, "单区数学义务超过 8 项，需要减小阅读粒度")
                    return
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
                requirements = paper_alignment.reading_requirements(source, focus, reading)
                if len(requirements) > 128:
                    self.store.split(key, index, "逐项语义对应超过单次核对上限")
                    return
                audit_packet = {
                    **packet,
                    "proposal_fingerprint": digest(reading),
                    "reading": reading,
                    "alignment_requirements": requirements,
                }
                review = paper_alignment.validate_audit(
                    self.call(
                        key, prefix + "-audit", "paper_audit", audit_packet, owner_fd, target
                    ),
                    source,
                    fp,
                    digest(reading),
                    requirements,
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
                            "target_contract": None,
                            "attempt_contract": None,
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
            if not chunk.get("children") and chunk["status"] != "ACCEPTED":
                unresolved_blocks.update(
                    u["block_id"] for u in self.store.focus(row, chunk, source)
                )
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
                if item["status"] != "WORKING":
                    current["items"][item_id]["attempt_contract"] = item.get("target_contract")
                current["items"][item_id].update(status="WORKING", attempts=attempt)
            item = self.store.read(key)["items"][item_id]
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
                    "locked_target": self.store.artifact(
                        self.store.read(key), item["attempt_contract"]
                    )
                    if item.get("attempt_contract")
                    else None,
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
                current = self.store.read(key)
                candidate_name = "candidates/" + prefix + ".json"
                if candidate_name in current["artifacts"]:
                    candidate = self.store.artifact(current, candidate_name)
                else:
                    remaining = paper_budget.available(current, "kernel_seconds")
                    if remaining < 1:
                        raise BudgetExhausted("Shared kernel-worker time allowance exhausted")
                    candidate = paper_kernel.compile_candidate(
                        proposal["profile"],
                        proposal["request"],
                        obligation["content"],
                        min(30, int(remaining)),
                    )
                    self.store.put(key, candidate_name, candidate)
                if item.get("target_contract"):
                    locked = self.store.artifact(self.store.read(key), item["target_contract"])
                    if locked["target_fingerprint"] != paper_alignment.target_fingerprint(
                        candidate
                    ):
                        raise ValueError(
                            "Locked semantic target changed; repair may only change proof or strategy"
                        )
                requirements = paper_alignment.formal_requirements(source, obligation, candidate)
                if len(requirements) > 128:
                    with self.store.edit(key) as current:
                        current["items"][item_id].update(
                            status="GAP", feedback="形式化逐项对应超过核对上限，需要拆解原始义务"
                        )
                    return
                review = paper_alignment.validate_audit(
                    self.call(
                        key,
                        prefix + "-audit",
                        "paper_audit",
                        {
                            **packet,
                            "candidate": candidate,
                            "proposal_fingerprint": digest(candidate),
                            "alignment_requirements": requirements,
                        },
                        owner_fd,
                        target,
                    ),
                    source,
                    fp,
                    digest(candidate),
                    requirements,
                    candidate,
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
                contract_name = item.get("target_contract") or self.store.put(
                    key,
                    "candidates/" + prefix + "-target.json",
                    {
                        "target_fingerprint": paper_alignment.target_fingerprint(candidate),
                        "semantic_target": paper_alignment.semantic_target(candidate),
                        "source_fingerprint": fp,
                        "obligation_fingerprint": digest(obligation),
                        "audit": review_name,
                    },
                )
                with self.store.edit(key) as current:
                    current["items"][item_id]["target_contract"] = contract_name
                receipt_name = "candidates/" + prefix + "-receipt.json"
                current = self.store.read(key)
                if (
                    receipt_name in current["artifacts"]
                    and current["items"][item_id].get("receipt") == receipt_name
                ):
                    receipt = self.store.artifact(current, receipt_name)
                    try:
                        paper_kernel.validate_receipt(
                            self.store.directory(key) / kernel_name, candidate, receipt
                        )
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        raise RuntimeError(
                            "Committed kernel checkpoint evidence changed"
                        ) from error
                    self.finish_kernel(key, item_id, target, receipt)
                    if receipt["kernel_status"] == "KERNEL_CHECKED":
                        return
                    continue
                with self.store.edit(key) as current:
                    allowance = candidate["request"]["timeout_seconds"]
                    if allowance > paper_budget.available(current, "kernel_seconds"):
                        raise BudgetExhausted("Shared kernel-worker time allowance exhausted")
                    paper_budget.reserve(current, "kernel:" + prefix, "kernel_seconds", allowance)
                    current["active"] = {
                        "kind": "KERNEL",
                        "item": item_id,
                        "reservation": "kernel:" + prefix,
                    }
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
                if receipt.get("kernel_status") not in ("KERNEL_CHECKED", "UNKNOWN", "UNAVAILABLE"):
                    raise RuntimeError("Invalid kernel receipt status; reservation retained")
                try:
                    paper_kernel.validate_receipt(
                        self.store.directory(key) / kernel_name, candidate, receipt
                    )
                except (OSError, ValueError, KeyError, TypeError) as error:
                    raise RuntimeError(
                        "Kernel receipt could not confirm resource consumption"
                    ) from error
                receipt_name = self.store.put(
                    key, "candidates/" + prefix + "-receipt.json", receipt
                )
                with self.store.edit(key) as current:
                    if receipt["kernel_status"] == "UNAVAILABLE":
                        paper_budget.uncertain(current, "kernel:" + prefix)
                    else:
                        paper_budget.settle(
                            current, "kernel:" + prefix, receipt["worker_seconds"], receipt_name
                        )
                    current["used"]["kernel_seconds_reported"] += receipt["worker_seconds"]
                    current["items"][item_id]["receipt"] = receipt_name
                    current["active"] = None
                self.guard(key)
                self.finish_kernel(key, item_id, target, receipt)
                if receipt["kernel_status"] == "KERNEL_CHECKED":
                    return
            except (ValueError, KeyError, TypeError, RecursionError) as error:
                self.retry(key, target, "形式化响应未满足协议：" + str(error))

    def finish_kernel(self, key, item_id, target, receipt):
        if receipt["kernel_status"] == "UNAVAILABLE":
            self.retry(key, target, "验证环境不可用：" + str(receipt.get("diagnostic")))
            raise KernelInterrupted(
                "Kernel setup or integrity validation unavailable; no automatic model repair"
            )
        if receipt["kernel_status"] == "KERNEL_CHECKED":
            with self.store.edit(key) as current:
                current["items"][item_id].update(status="KERNEL_CHECKED", feedback=None)
        else:
            self.retry(
                key, target, "真实检查未通过（不代表反驳）：" + str(receipt.get("diagnostic"))
            )

    def execute(self, key, owner_fd):
        row = self.store.read(key)
        if (
            row["schema"] != SCHEMA
            or row["state"] not in ("QUEUED", "WAITING_FOR_SOURCE")
            or row["cancel_requested"]
        ):
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
            self.store.put(key, "structure.json", paper_structure.build_index(source))
            with self.store.edit(key) as current:
                current.update(
                    source_fingerprint=digest(source),
                    chunks=self.store.chunk_records(source),
                    state="QUEUED",
                    structure="structure.json",
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
                while True:
                    row = self.store.read(key)
                    pending_chunk = next(
                        (
                            c
                            for c in row["chunks"]
                            if c["status"] not in ("ACCEPTED", "GAP", "SPLIT")
                        ),
                        None,
                    )
                    if pending_chunk is None:
                        break
                    self.reading(key, pending_chunk["index"], owner_fd)
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
            gaps = any(
                (c["status"] != "ACCEPTED" or c["feedback"])
                for c in row["chunks"]
                if not c.get("children")
            ) or any(i["status"] != "KERNEL_CHECKED" for i in row["items"].values())
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
                    paper_budget.uncertain(current, active.get("reservation"))
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
                        if (
                            row["schema"] != SCHEMA
                            or row["state"] not in ACTIVE
                            or row["state"] == "RUNNING"
                        ):
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
