"""Whole-paper v2 contracts. Scripted outputs test orchestration, never LLM quality."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from mra import (
    paper_alignment as alignment,
    paper_budget as accounting,
    paper_structure as structure,
)
from mra.domain import canonical, digest
from mra.paper_automation import LEGACY_SCHEMA
from mra.paper_kernel import compile_candidate
from mra.paper_protocol import scan_result
import test_paper_automation as helpers
from paper_fixtures import (
    FixtureService,
    audit,
    candidate,
    content,
    reading,
    source,
    unavailable_kernel,
)


class StructureTests(unittest.TestCase):
    def test_repeated_tex_heading_titles_keep_distinct_source_scopes(self):
        value = source("x", "definition")
        value["blocks"][0].update(source="owned.tex", section="Notation", span=[20, 21])
        second = {**deepcopy(value["blocks"][0]), "id": "block-0002", "span": [80, 81]}
        value["blocks"].append(second)
        value["sections"] = [
            {"title": "Notation", "source": "owned.tex", "span": [0, 10]},
            {"title": "Notation", "source": "owned.tex", "span": [60, 70]},
        ]
        index = structure.build_index(value)
        self.assertEqual(len(index["sections"]), 2)
        self.assertNotEqual(index["symbols"][0]["scope_id"], index["symbols"][1]["scope_id"])

    def test_sections_unicode_crlf_formulas_and_lossless_recursive_partition(self):
        value = source(("😀定义 $x+1$。\r\n\r\n" * 700), "definition")
        second = deepcopy(value["blocks"][0])
        second.update(id="block-0002", section="new-section", kind="proof")
        value["blocks"].append(second)
        index = structure.build_index(value)
        self.assertEqual(len(index["sections"]), 2)
        self.assertNotEqual(index["symbols"][0]["scope_id"], index["symbols"][-1]["scope_id"])
        self.assertFalse(index["semantic_scope_resolved"])
        partitions = structure.partition(value)
        refs = [u for part in partitions for u in part]
        for block in value["blocks"]:
            pieces = structure.materialize(value, [r for r in refs if r["block_id"] == block["id"]])
            self.assertEqual("".join(p["text"] for p in pieces), block["text"])
        left, right = structure.split_focus(value, partitions[0])
        before = "".join(u["text"] for u in structure.materialize(value, partitions[0]))
        self.assertEqual(
            "".join(u["text"] for u in structure.materialize(value, left + right)), before
        )
        self.assertTrue(index["blocks"][0]["formulas"])
        with self.assertRaises(ValueError):
            structure.materialize(value, [{**refs[0], "span": [0, 1]}])

    def test_formula_escaping_unpaired_delimiters_and_explicit_index_limit(self):
        text = r"cost \$5 then $x$ and \(y\) and $$z$$; \[unpaired"
        spans, issues = structure.math_spans(text)
        self.assertEqual([text[slice(*s)] for s in spans], ["$x$", r"\(y\)", "$$z$$"])
        self.assertEqual(len(issues), 1)
        value = source("$x$ " * 40000, "passage")
        index = structure.build_index(value)
        self.assertEqual(index["blocks"][0]["formula_count"], 40000)
        self.assertEqual(len(index["blocks"][0]["formulas"]), structure.MAX_FORMULAS)
        self.assertIn("FORMULA_INDEX_LIMIT", [i["reason"] for i in index["issues"]])
        self.assertLess(len(canonical(index)), 1800000)
        self.assertEqual(value["blocks"][0]["text"], "$x$ " * 40000)

    def test_complete_context_for_partial_unit_and_initial_plan_limit(self):
        value = source("😀" * 12000, "passage")
        focus = structure.materialize(value, structure.partition(value)[0])
        packet = structure.scan_packet(value, structure.build_index(value), focus)
        self.assertEqual(packet["context"][0]["text"], value["blocks"][0]["text"])
        blocks = []
        for i in range(513):
            b = deepcopy(value["blocks"][0])
            b.update(id="block-" + str(i), text="x", section=str(i))
            blocks.append(b)
        with self.assertRaisesRegex(ValueError, "not truncated"):
            structure.partition({"blocks": blocks})


class AlignmentTests(unittest.TestCase):
    def packet(self, c=None, raw=None):
        value = source("For every natural n, if n=n and n+0=n, then n+0=n and n=n.")
        focus = structure.materialize(value, structure.partition(value)[0])
        proposed = reading({"focus": focus, "source_fingerprint": digest(value)})
        if c:
            proposed["obligations"][0].update(c)
        item = scan_result(proposed, value, focus, digest(value))["items"]["o1"]
        compiled = compile_candidate("typed", raw or candidate(), item["content"])
        requirements = alignment.formal_requirements(value, item, compiled)
        packet = {
            "source_fingerprint": digest(value),
            "proposal_fingerprint": digest(compiled),
            "alignment_requirements": requirements,
        }
        return value, compiled, requirements, audit(packet)

    def validate(self, value, compiled, requirements, raw):
        return alignment.validate_audit(
            raw, value, digest(value), digest(compiled), requirements, compiled
        )

    def test_component_mapping_rejects_missing_foreign_or_unanchored_rows(self):
        value, compiled, requirements, raw = self.packet()
        self.validate(value, compiled, requirements, raw)
        for change in (
            lambda r: r["alignments"].pop(),
            lambda r: r["alignments"][0].update(id="foreign"),
            lambda r: r["alignments"][0]["source_refs"][0].update(slice_sha256="0" * 64),
            lambda r: r["alignments"][0].update(formal_paths=["/request/proof"]),
            lambda r: r["alignments"][0].update(relation="UNRESOLVED"),
        ):
            broken = deepcopy(raw)
            change(broken)
            with self.assertRaises(ValueError):
                self.validate(value, compiled, requirements, broken)

    def test_separate_original_premises_cannot_be_dropped_added_or_merged(self):
        eq = {"eq": [{"var": "n"}, {"var": "n"}]}
        raw = {
            **candidate(),
            "assumptions": [{"name": "h1", "formula": eq}, {"name": "h2", "formula": eq}],
        }
        value, compiled, requirements, accepted = self.packet(
            {"assumptions": ["n=n", "n+0=n"]}, raw
        )
        self.validate(value, compiled, requirements, accepted)
        for premises in (
            raw["assumptions"][:1],
            raw["assumptions"] + [{"name": "h3", "formula": eq}],
        ):
            v, c, req, proposal = self.packet(
                {"assumptions": ["n=n", "n+0=n"]}, {**raw, "assumptions": premises}
            )
            with self.assertRaises(ValueError):
                self.validate(v, c, req, proposal)

    def test_separate_subconclusions_need_exhaustive_disjoint_ast_paths(self):
        eq = candidate()["goal"]
        value, compiled, requirements, raw = self.packet(
            {"conclusion": "n+0=n\nn=n"}, {**candidate(), "goal": {"and": [eq, eq]}}
        )
        self.validate(value, compiled, requirements, raw)
        broken = deepcopy(raw)
        conclusions = [r for r in broken["alignments"] if r["id"].startswith("conclusion:")]
        conclusions[1]["formal_paths"] = conclusions[0]["formal_paths"]
        with self.assertRaisesRegex(ValueError, "exhaustive"):
            self.validate(value, compiled, requirements, broken)
        v, c, req, proposal = self.packet({"conclusion": "n+0=n\nn=n"})
        with self.assertRaises(ValueError):
            self.validate(v, c, req, proposal)

    def test_repair_contract_locks_types_premises_quantifiers_and_goal(self):
        original = compile_candidate("typed", candidate(), content())
        strategy = compile_candidate("typed", {**candidate(), "strategy": "omega"}, content(), 12)
        self.assertEqual(
            alignment.target_fingerprint(original), alignment.target_fingerprint(strategy)
        )
        for request in (
            {**candidate(), "goal": {"eq": [{"var": "n"}, {"var": "n"}]}},
            {
                **candidate(),
                "variables": [{"name": "n", "type": "Int"}],
                "goal": {"eq": [{"add": [{"var": "n"}, {"int": 0}]}, {"var": "n"}]},
            },
            {**candidate(), "assumptions": [{"name": "h", "formula": candidate()["goal"]}]},
        ):
            changed = compile_candidate("typed", request, content())
            self.assertNotEqual(
                alignment.target_fingerprint(original), alignment.target_fingerprint(changed)
            )
        quantified = {
            **candidate(),
            "variables": [],
            "goal": {
                "forall": {"binders": [{"name": "n", "type": "Nat"}], "body": candidate()["goal"]}
            },
        }
        universal = compile_candidate("typed", quantified, content())
        existential = compile_candidate(
            "typed", {**quantified, "goal": {"exists": quantified["goal"]["forall"]}}, content()
        )
        self.assertNotEqual(
            alignment.target_fingerprint(universal), alignment.target_fingerprint(existential)
        )

    def test_explicit_quantifier_cannot_be_left_out_of_the_correspondence(self):
        request = {
            **candidate(),
            "variables": [],
            "goal": {
                "forall": {"binders": [{"name": "n", "type": "Nat"}], "body": candidate()["goal"]}
            },
        }
        value, compiled, requirements, raw = self.packet(raw=request)
        self.validate(value, compiled, requirements, raw)
        quantifier = next(r for r in raw["alignments"] if r["id"].startswith("quantifiers:"))
        quantifier["formal_paths"] = ["/request/goal/forall/binders/0"]
        with self.assertRaises(ValueError):
            self.validate(value, compiled, requirements, raw)


class BudgetTests(unittest.TestCase):
    def row(self):
        return {
            "budget": {"output_tokens": 100, "kernel_seconds": 30},
            "reservations": {},
            "used": {"output_tokens_reserved": 0, "kernel_seconds_reserved": 0},
            "artifacts": {"receipt": "fixed-hash"},
        }

    def test_confirmed_use_is_settled_once_and_unknown_allowance_is_not_refunded(self):
        row = self.row()
        accounting.reserve(row, "a", "output_tokens", 80)
        accounting.settle(row, "a", 5, "receipt")
        accounting.settle(row, "a", 5, "receipt")
        self.assertEqual(accounting.available(row, "output_tokens"), 95)
        accounting.reserve(row, "b", "output_tokens", 80)
        accounting.uncertain(row, "b")
        self.assertEqual(
            accounting.totals(row)["output_tokens"],
            {"settled": 5, "held": 80, "uncertain": 80, "occupied": 85},
        )
        with self.assertRaises(ValueError):
            accounting.settle(row, "b", 0, "receipt")
        with self.assertRaises(ValueError):
            accounting.settle(row, "a", 4, "receipt")

    def test_overrun_and_missing_or_invalid_receipts_are_never_hidden(self):
        row = self.row()
        accounting.reserve(row, "kernel", "kernel_seconds", 30)
        for value in (float("nan"), -1, True):
            with self.assertRaises(ValueError):
                accounting.settle(row, "kernel", value, "receipt")
        with self.assertRaises(ValueError):
            accounting.settle(row, "kernel", 0, "unregistered")
        accounting.settle(row, "kernel", 35, "receipt")
        self.assertEqual(accounting.totals(row)["kernel_seconds"]["settled"], 35)
        self.assertEqual(accounting.available(row, "kernel_seconds"), 0)
        self.assertTrue(row["reservations"]["kernel"]["overrun"])


class PipelineV2Tests(unittest.TestCase):
    setUp = helpers.AutomaticPaperTests.setUp
    configure = helpers.AutomaticPaperTests.configure
    document = helpers.AutomaticPaperTests.document
    job = helpers.AutomaticPaperTests.job
    execute = helpers.AutomaticPaperTests.execute

    def test_explicit_adaptive_split_preserves_leaf_coverage_and_structure(self):
        class Dense(FixtureService):
            def generate(inner, request):
                response = super(Dense, inner).generate(request)
                if request.task == "paper_scan" and "read-0-1" in request.id:
                    return replace(
                        response,
                        output_json=json.dumps(
                            {
                                "action": "SPLIT",
                                "source_fingerprint": json.loads(request.input_json)[
                                    "source_fingerprint"
                                ],
                                "reason": "Separate claim and proof",
                            }
                        ),
                    )
                return response

        self.configure()
        self.pump.service_factory = Dense
        result = self.execute(self.job(repair_attempts=1)["job_id"])
        self.assertEqual(result["summary"]["split_chunks"], 1)
        self.assertEqual(result["summary"]["reading_leaves"], 2)
        self.assertTrue(all(u["reading_accepted"] for u in result["coverage"]))
        self.assertEqual(
            sum(u["span"][1] - u["span"][0] for u in result["coverage"]),
            result["summary"]["source_characters"],
        )
        self.assertEqual(len(result["reading_audits"]), 2)
        self.assertFalse(result["structure_index"]["semantic_scope_resolved"])

    def test_dense_response_is_split_without_truncating_obligation_list(self):
        class Dense(FixtureService):
            def generate(inner, request):
                response = super(Dense, inner).generate(request)
                if request.task == "paper_scan" and "read-0-1" in request.id:
                    output = json.loads(response.output_json)
                    output["obligations"] *= 5
                    return replace(response, output_json=json.dumps(output))
                return response

        self.configure()
        self.pump.service_factory = Dense
        result = self.execute(self.job(repair_attempts=1)["job_id"])
        self.assertEqual(result["chunks"][0]["status"], "SPLIT")
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(all(u["reading_accepted"] for u in result["coverage"]))

    def test_short_confirmed_checks_release_shared_budget_for_more_work(self):
        self.configure()
        count = []

        def fast(*args, **kwargs):
            count.append(str(args[1]))
            return {**unavailable_kernel(*args, **kwargs), "worker_seconds": 0.01}

        self.pump.kernel_executor = fast
        result = self.execute(
            self.job(repair_attempts=1, output_tokens=17000, kernel_seconds=2)["job_id"]
        )
        self.assertEqual(result["state"], "COMPLETED_WITH_GAPS", result["error"])
        self.assertEqual(len(count), 4)
        self.assertGreater(
            result["used"]["kernel_seconds_reserved"], result["budget"]["kernel_seconds"]
        )
        self.assertAlmostEqual(result["accounting"]["kernel_seconds"]["settled"], 0.04)
        self.assertEqual(
            result["accounting"]["output_tokens"]["settled"], 10 * result["used"]["model_calls"]
        )
        self.assertEqual(result["accounting"]["output_tokens"]["held"], 0)

    def test_target_drift_is_rejected_before_another_audit_or_kernel(self):
        class Drift(FixtureService):
            def generate(inner, request):
                response = super(Drift, inner).generate(request)
                if request.task == "paper_formalize" and request.id.endswith("-2"):
                    output = json.loads(response.output_json)
                    output["request"]["goal"] = {"eq": [{"var": "n"}, {"var": "n"}]}
                    return replace(response, output_json=json.dumps(output))
                return response

        self.configure()
        self.pump.service_factory = Drift
        count = []

        def checked(*args, **kwargs):
            count.append(args[2]["formal_statement"])
            return unavailable_kernel(*args, **kwargs)

        self.pump.kernel_executor = checked
        result = self.execute(self.job(repair_attempts=1)["job_id"])
        self.assertEqual(len(count), 2)
        self.assertTrue(all("Locked semantic target" in i["feedback"] for i in result["items"]))
        self.assertTrue(
            all(
                i["locked_target"]["target_fingerprint"]
                == alignment.target_fingerprint(i["candidate"])
                for i in result["items"]
            )
        )
        self.assertTrue(
            any(p.get("locked_target") for t, p in Drift.seen if t == "paper_formalize")
        )

    def test_unavailable_environment_keeps_entire_unknown_reservation(self):
        self.configure()

        def missing(*args, **kwargs):
            return {**unavailable_kernel(*args, **kwargs), "kernel_status": "UNAVAILABLE"}

        self.pump.kernel_executor = missing
        result = self.execute(self.job()["job_id"])
        self.assertEqual(result["state"], "EXECUTION_ERROR")
        self.assertEqual(result["accounting"]["kernel_seconds"]["uncertain"], 30)
        self.assertEqual(result["accounting"]["kernel_seconds"]["settled"], 0)

    def test_invalid_kernel_receipt_stops_without_repair_or_refund(self):
        self.configure()

        def invalid(*args, **kwargs):
            return {**unavailable_kernel(*args, **kwargs), "candidate_fingerprint": "wrong-target"}

        self.pump.kernel_executor = invalid
        result = self.execute(self.job()["job_id"])
        self.assertEqual(result["state"], "POLICY_BLOCKED")
        self.assertEqual(result["accounting"]["kernel_seconds"]["uncertain"], 30)
        self.assertEqual(sum(t == "paper_formalize" for t, _ in FixtureService.seen), 1)

    def test_actual_kernel_overrun_is_charged_and_blocks_new_work(self):
        self.configure()

        def slow(*args, **kwargs):
            return {**unavailable_kernel(*args, **kwargs), "worker_seconds": 35}

        self.pump.kernel_executor = slow
        result = self.execute(self.job(kernel_seconds=30)["job_id"])
        self.assertEqual(result["state"], "BUDGET_EXHAUSTED")
        self.assertEqual(result["accounting"]["kernel_seconds"]["settled"], 35)
        self.assertEqual(result["accounting"]["kernel_seconds"]["held"], 0)
        self.assertEqual(sum(t == "paper_formalize" for t, _ in FixtureService.seen), 1)

    def test_explicit_resume_advances_unknown_call_without_refunding_it(self):
        requests = []

        class BrokenOnce(FixtureService):
            def generate(inner, request):
                requests.append(request.id)
                if len(requests) == 1:
                    raise OSError("Uncertain provider outcome fixture")
                return super(BrokenOnce, inner).generate(request)

        self.configure()
        self.pump.service_factory = BrokenOnce
        row = self.job(repair_attempts=1)
        first = self.execute(row["job_id"])
        self.assertEqual(first["accounting"]["output_tokens"]["uncertain"], 8192)
        self.assertEqual(len(requests), 1)
        self.store.action(
            row["job_id"],
            "resume",
            {"request_id": "after-uncertain", "expected_revision": first["revision"]},
        )
        final = self.execute(row["job_id"])
        self.assertEqual(final["state"], "COMPLETED_WITH_GAPS", final["error"])
        self.assertEqual(final["accounting"]["output_tokens"]["uncertain"], 8192)
        self.assertEqual(len(requests), len(set(requests)))
        self.assertEqual(final["calls"]["read-0-1"]["status"], "INTERRUPTED_COST_UNKNOWN")
        self.assertEqual(final["calls"]["read-0-2"]["status"], "RESPONDED")

    def test_committed_kernel_receipt_is_reused_after_resume_without_new_charge(self):
        self.configure()
        row = self.job(repair_attempts=1)
        finish = self.pump.finish_kernel
        broken = []

        def crash_once(*args):
            if not broken:
                broken.append(True)
                raise OSError("Crash fixture after committed receipt, before status")
            return finish(*args)

        self.pump.finish_kernel = crash_once
        first = self.execute(row["job_id"])
        self.assertEqual(first["state"], "MODEL_ERROR")
        self.assertEqual(first["accounting"]["kernel_seconds"]["held"], 0)
        self.store.action(
            row["job_id"],
            "resume",
            {"request_id": "after-receipt", "expected_revision": first["revision"]},
        )
        result = self.execute(row["job_id"])
        self.assertEqual(result["state"], "COMPLETED_WITH_GAPS", result["error"])
        self.assertEqual(sum(t == "paper_formalize" for t, _ in FixtureService.seen), 4)
        self.assertEqual(len([k for k in result["reservations"] if k.startswith("kernel:")]), 4)

    def test_damaged_committed_kernel_files_block_resume_before_new_work(self):
        from mra.integrity import file_hash

        self.configure()
        row = self.job(repair_attempts=1)
        finish, files = self.pump.finish_kernel, []

        def recorded(*args, **kwargs):
            result = unavailable_kernel(*args, **kwargs)
            path = args[1] / "owned-evidence.txt"
            path.write_text("Owned evidence fixture; no mathematical proof")
            files.append(path)
            return {**result, "files": {path.name: file_hash(path)}}

        def interrupted(*args):
            if len(files) == 1:
                raise OSError("Crash fixture after committed kernel checkpoint")
            return finish(*args)

        self.pump.kernel_executor = recorded
        self.pump.finish_kernel = interrupted
        first = self.execute(row["job_id"])
        self.assertEqual(first["state"], "MODEL_ERROR")
        files[0].write_text("damaged after checkpoint")
        self.pump.finish_kernel = finish
        self.store.action(
            row["job_id"],
            "resume",
            {"request_id": "damaged-checkpoint", "expected_revision": first["revision"]},
        )
        result = self.execute(row["job_id"])
        self.assertEqual(result["state"], "POLICY_BLOCKED")
        self.assertEqual(len(files), 1, "No checker may launch after damaged checkpoint reuse")
        self.assertEqual(
            result["used"], first["used"], "No model or kernel consumption is hidden or added"
        )

    def test_split_limit_keeps_explicit_gap_without_losing_source_ranges(self):
        row = self.job()
        key, index = row["job_id"], 0
        for _ in range(structure.MAX_DEPTH + 3):
            self.store.split(key, index, "Dense source fixture")
            current = self.store.read(key)["chunks"][index]
            if current["status"] == "GAP":
                break
            index = current["children"][0]
        result = self.store.report(key)
        self.assertEqual(result["chunks"][index]["status"], "GAP")
        self.assertEqual(
            sum(u["span"][1] - u["span"][0] for u in result["coverage"]),
            result["summary"]["source_characters"],
        )

    def test_v1_job_stays_readable_and_is_never_recovered_or_resumed_as_v2(self):
        row = self.job()
        key = row["job_id"]
        with self.store.edit(key) as current:
            current.update(schema=LEGACY_SCHEMA, state="RUNNING")
        before = self.store.read(key)
        with self.pump.owner():
            self.pump.recover()
        self.assertEqual(self.store.read(key), before)
        self.assertTrue(self.store.report(key)["legacy_job"])
        with self.store.edit(key) as current:
            current["state"] = "INTERRUPTED"
        current = self.store.read(key)
        with self.assertRaisesRegex(ValueError, "Historical v1"):
            self.store.action(
                key, "resume", {"request_id": "v1-resume", "expected_revision": current["revision"]}
            )
