"""Source coverage and immutable target contracts, without model-quality claims."""

from copy import deepcopy
import unittest

from mra.domain import digest
from mra.paper_kernel import compile_candidate, languages
from mra.paper_protocol import (
    audit_result,
    block_context,
    chunks,
    formal_result,
    scan_input,
    scan_result,
)
from paper_fixtures import audit, candidate, content, reading, source


class PaperProtocolTests(unittest.TestCase):
    def test_partition_preserves_every_code_point_including_plain_prose(self):
        value = source("\ufeff普通段落😀\r\n" * 3000, "passage")
        units = [u for c in chunks(value) for u in c]
        self.assertEqual("".join(u["text"] for u in units), value["blocks"][0]["text"])
        self.assertEqual(
            sum(u["span"][1] - u["span"][0] for u in units), len(value["blocks"][0]["text"])
        )
        self.assertTrue(all(len(u["text"]) <= 7000 for u in units))
        self.assertEqual(chunks(source(""))[0][0]["span"], [0, 0])

    def packet(self):
        value = source("😀For every natural number n, n+0=n.")
        focus = chunks(value)[0]
        packet = {"source_fingerprint": digest(value), **scan_input(value, focus)}
        return value, focus, packet

    def test_exact_quote_validation_and_no_authority_fields(self):
        value, focus, packet = self.packet()
        raw = reading(packet)
        result = scan_result(raw, value, focus, digest(value))
        self.assertIn("完整结论", result["items"]["o1"]["statement"])
        for change in (
            lambda r: r["obligations"][0]["quotes"][0].update(quote="invented"),
            lambda r: r["obligations"][0]["quotes"][0].update(
                span=[1, len(value["blocks"][0]["text"]) + 1]
            ),
            lambda r: r.update(math_verdict="PROVED"),
        ):
            broken = deepcopy(raw)
            change(broken)
            with self.assertRaises(ValueError):
                scan_result(broken, value, focus, digest(value))

    def test_coverage_cannot_omit_duplicate_or_discard_claims(self):
        value, focus, packet = self.packet()
        raw = reading(packet)
        changes = [
            lambda r: r.update(coverage=[]),
            lambda r: r["coverage"].append(deepcopy(r["coverage"][0])),
            lambda r: r["coverage"][0].update(disposition="CONTEXT", obligations=[]),
            lambda r: r["coverage"][0].update(obligations=[]),
        ]
        for change in changes:
            broken = deepcopy(raw)
            change(broken)
            with self.assertRaises(ValueError):
                scan_result(broken, value, focus, digest(value))

    def test_context_is_complete_or_rejected_never_truncated(self):
        value = source()
        value["blocks"].append(
            {
                "id": "block-0002",
                "kind": "definition",
                "text": "x" * 80000,
                "source": "owned",
                "dependencies": [],
            }
        )
        self.assertEqual(len(block_context(value, ["block-0002"])[0]["text"]), 80000)
        with self.assertRaisesRegex(ValueError, "complete context"):
            scan_input(value, chunks(value)[0], ["block-0002"])
        with self.assertRaises(ValueError):
            block_context(value, ["missing"])

    def test_model_audit_must_bind_both_inputs_and_all_dimensions(self):
        value, _, packet = self.packet()
        packet["proposal_fingerprint"] = "proposal"
        raw = audit(packet)
        self.assertEqual(audit_result(raw, value, digest(value), "proposal")["decision"], "ACCEPT")
        raw["checks"]["assumptions"] = False
        with self.assertRaises(ValueError):
            audit_result(raw, value, digest(value), "proposal")
        with self.assertRaises(ValueError):
            audit_result(audit(packet), value, digest(value), "different")

    def test_compiler_owns_full_statement_and_disallows_injected_original(self):
        compiled = compile_candidate("typed", candidate(), content())
        self.assertEqual(compiled["request"]["original"].count("全部前提"), 1)
        self.assertIn("Nat", compiled["formal_statement"])
        with self.assertRaises(ValueError):
            compile_candidate("typed", {**candidate(), "original": "easier target"}, content())
        for name in ("typed", "calculus", "proof2"):
            self.assertIn("example", languages()[name])

    def test_formalization_cannot_change_obligation_or_provide_lean(self):
        value = source()
        item = {"content": content()}
        raw = {
            "source_fingerprint": digest(value),
            "obligation_fingerprint": digest(item),
            "status": "CANDIDATE",
            "profile": "typed",
            "request": candidate(),
            "questions": [],
            "required_context": [],
        }
        self.assertEqual(formal_result(raw, value, digest(value), item), raw)
        raw["request"]["original"] = "not the original"
        with self.assertRaises(ValueError):
            formal_result(raw, value, digest(value), item)
        with self.assertRaises(ValueError):
            compile_candidate("typed", {"schema": "lean", "code": "by sorry"}, content())
