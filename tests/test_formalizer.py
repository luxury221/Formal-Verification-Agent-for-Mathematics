"""Protocol tests with explicitly mocked model responses; no LLM or Lean claims."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import time
import unittest

from mra.domain import EvidenceLevel, RunStatus, Verdict, canonical
from mra.formalizer import (
    CandidateReview,
    DraftStatus,
    SourceClaim,
    StructuredFormalizer,
    review_candidate,
    strict_json,
)
from mra.intake import FixtureModelService, IntakeStore
from mra.model_service import ModelBudget, ModelIdentity, ModelResponse, TokenUsage

ROOT = Path(__file__).resolve().parents[1]


class MockModelService:
    """A protocol stub, explicitly not a live model or a mathematical checker."""

    def __init__(self, transform=None, failure=None, delay=0):
        self.transform, self.failure, self.delay = transform, failure, delay
        self.requests = []
        self.identity = ModelIdentity("test-only-mock", "protocol-stub", "test-revision-1", "model")

    def generate(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        if self.delay:
            time.sleep(self.delay)
        source = strict_json(request.input_json)
        output = {
            "schema_version": "1",
            "source_fingerprint": source["source_fingerprint"],
            "status": "CANDIDATES_READY",
            "candidates": [
                {
                    "label": "Universal natural claim",
                    "lean_statement": "∀ n : Nat, n + 0 = n",
                    "variables": [{"name": "n", "domain": "Nat", "quantifier": "forall"}],
                    "assumptions": [],
                    "interpretation": "For every natural n, n + 0 equals n",
                }
            ],
            "questions": [],
        }
        response = ModelResponse(
            request.fingerprint, self.identity, json.dumps(output), TokenUsage(20, 30)
        )
        return self.transform(response, output) if self.transform else response


class FormalizerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.source = SourceClaim("natural-claim", 1, "Every natural n satisfies n + 0 = n")
        self.service = MockModelService()
        self.formalizer = StructuredFormalizer(self.service)

    def review(self, draft, decision="accept"):
        return CandidateReview(
            draft.fingerprint,
            draft.candidates[0].fingerprint,
            "named reviewer",
            decision,
            "Reviewed domain, quantifier, assumptions, and conclusion",
        )

    def mutate_output(self, change):
        def transform(response, output):
            change(output)
            return replace(response, output_json=json.dumps(output))

        return StructuredFormalizer(MockModelService(transform)).formalize_claim(self.source)

    def test_structured_candidate_records_scope_model_and_request(self):
        draft = self.formalizer.formalize_claim(self.source)
        candidate = draft.candidates[0]
        self.assertEqual(draft.status, DraftStatus.CANDIDATES_READY)
        self.assertEqual(candidate.variables[0].domain, "Nat")
        self.assertEqual(candidate.variables[0].quantifier, "forall")
        self.assertEqual(candidate.producer, self.service.identity)
        self.assertEqual(draft.request.budget, ModelBudget())
        self.assertEqual(draft.request_fingerprint, self.service.requests[0].fingerprint)
        self.assertEqual(draft.to_dict()["evidence_level"], EvidenceLevel.E0)
        self.assertEqual(draft.math_verdict, Verdict.UNKNOWN)
        self.assertFalse(draft.to_dict()["can_execute_lean"])

    def test_claim_and_step_tasks_preserve_original_context(self):
        source = replace(
            self.source,
            context=(
                "n is a natural number",
                "Prior statement is a hypothesis, not a verified fact",
            ),
        )
        draft = self.formalizer.formalize_step(source)
        self.assertEqual(draft.request.task, "formalize_step")
        self.assertEqual(
            strict_json(draft.request.input_json)["source"]["context"], list(source.context)
        )

    def test_producer_cannot_be_supplied_inside_generated_text(self):
        draft = self.mutate_output(lambda data: data.update(producer={"model": "trusted"}))
        self.assertEqual(draft.status, DraftStatus.INVALID_RESPONSE)

    def test_model_self_report_and_proof_fields_cannot_grant_evidence(self):
        for field, value in (
            ("math_verdict", "PROVED"),
            ("evidence_level", "E4_ALIGNED_AND_VERIFIED"),
            ("proof", "by sorry"),
            ("alignment", "confirmed"),
        ):
            with self.subTest(field=field):
                draft = self.mutate_output(lambda data: data.update({field: value}))
                self.assertEqual(draft.status, DraftStatus.INVALID_RESPONSE)
                self.assertEqual(draft.math_verdict, Verdict.UNKNOWN)

    def test_wrong_source_and_schema_are_rejected(self):
        for field, value in (("source_fingerprint", "another source"), ("schema_version", "99")):
            with self.subTest(field=field):
                draft = self.mutate_output(lambda data: data.update({field: value}))
                self.assertEqual(draft.run_status, RunStatus.POLICY_BLOCKED)
                self.assertFalse(draft.candidates)

    def test_response_for_another_request_is_rejected(self):
        service = MockModelService(
            lambda response, _: replace(response, request_fingerprint="old request")
        )
        draft = StructuredFormalizer(service).formalize_claim(self.source)
        self.assertEqual(draft.status, DraftStatus.INVALID_RESPONSE)

    def test_unknown_quantifier_missing_domain_and_duplicate_variable_are_rejected(self):
        def duplicate(data):
            data["candidates"][0]["variables"] *= 2

        for change in (
            lambda data: data["candidates"][0]["variables"][0].update(quantifier="probably"),
            lambda data: data["candidates"][0]["variables"][0].pop("domain"),
            lambda data: data["candidates"][0]["variables"][0].update(domain=" "),
            duplicate,
        ):
            with self.subTest(change=change):
                self.assertEqual(self.mutate_output(change).status, DraftStatus.INVALID_RESPONSE)

    def test_ambiguity_with_alternative_candidates_still_blocks_review(self):
        def ambiguous(data):
            data.update(status="NEEDS_CLARIFICATION", questions=["Which domain is intended?"])

        draft = self.mutate_output(ambiguous)
        self.assertEqual(draft.math_verdict, Verdict.NEEDS_CLARIFICATION)
        self.assertEqual(
            self.formalizer.suggest_clarifications(draft), ("Which domain is intended?",)
        )
        with self.assertRaises(ValueError):
            review_candidate(draft, self.review(draft))

    def test_inconsistent_status_and_too_many_alternatives_are_rejected(self):
        for change in (
            lambda data: data.update(questions=["Unresolved domain"]),
            lambda data: data.update(status="NEEDS_CLARIFICATION"),
            lambda data: data.update(status="UNSUPPORTED"),
            lambda data: data.update(status="PROVED"),
            lambda data: data.update(status="CANDIDATES_READY", candidates=[]),
            lambda data: data.update(candidates=data["candidates"] * 4),
            lambda data: data.update(status="NEEDS_CLARIFICATION", questions=["Question"] * 4),
        ):
            with self.subTest(change=change):
                self.assertEqual(self.mutate_output(change).status, DraftStatus.INVALID_RESPONSE)

    def test_duplicate_keys_nonstandard_constants_and_non_json_are_rejected(self):
        for raw in (
            '{"status":"UNSUPPORTED","status":"CANDIDATES_READY"}',
            '{"x":NaN}',
            "[]",
            "```json\n{}\n```",
            "{broken",
        ):
            with self.subTest(raw=raw):
                service = MockModelService(lambda response, _: replace(response, output_json=raw))
                draft = StructuredFormalizer(service).formalize_claim(self.source)
                self.assertEqual(draft.status, DraftStatus.INVALID_RESPONSE)

    def test_input_budget_blocks_transport_call(self):
        draft = self.formalizer.formalize_claim(self.source, ModelBudget(max_input_chars=1))
        self.assertEqual(draft.run_status, RunStatus.POLICY_BLOCKED)
        self.assertFalse(self.service.requests)

    def test_output_token_and_byte_budgets_are_enforced(self):
        for budget in (ModelBudget(max_output_tokens=1), ModelBudget(max_output_bytes=1)):
            with self.subTest(budget=budget):
                draft = self.formalizer.formalize_claim(self.source, budget)
                self.assertEqual(draft.status, DraftStatus.INVALID_RESPONSE)

    def test_invalid_budgets_and_usage_are_rejected(self):
        for kwargs in (
            {"timeout_seconds": float("nan")},
            {"timeout_seconds": True},
            {"timeout_seconds": 0},
            {"max_output_tokens": True},
            {"max_input_chars": -1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ModelBudget(**kwargs)
        with self.assertRaises(ValueError):
            TokenUsage(1, -1)
        with self.assertRaises(ValueError):
            ModelIdentity("test", "model", "latest", "model")

    def test_invalid_provider_response_is_recorded_without_private_error_text(self):
        draft = StructuredFormalizer(
            MockModelService(failure=ValueError("private server detail"))
        ).formalize_claim(self.source)
        self.assertEqual(draft.status, DraftStatus.INVALID_RESPONSE)
        self.assertNotIn("private", draft.reason)

    def test_timeout_and_transport_failure_never_refute_a_claim(self):
        for failure, status in (
            (TimeoutError("private transport detail"), RunStatus.TIMEOUT),
            (OSError("secret token must not appear"), RunStatus.EXECUTION_ERROR),
        ):
            draft = StructuredFormalizer(MockModelService(failure=failure)).formalize_claim(
                self.source
            )
            self.assertEqual(draft.run_status, status)
            self.assertEqual(draft.math_verdict, Verdict.UNKNOWN)
            self.assertNotIn("secret", draft.reason)

    def test_response_arriving_after_budget_is_not_used(self):
        draft = StructuredFormalizer(MockModelService(delay=0.02)).formalize_claim(
            self.source, ModelBudget(timeout_seconds=0.001)
        )
        self.assertEqual(draft.status, DraftStatus.TIMEOUT)
        self.assertFalse(draft.candidates)

    def test_review_accept_and_reject_remain_unverified(self):
        draft = self.formalizer.formalize_claim(self.source)
        for decision, status in (("accept", "ACCEPTED"), ("reject", "REJECTED")):
            record = review_candidate(draft, self.review(draft, decision))
            self.assertEqual(record["review_status"], status)
            self.assertEqual(record["math_verdict"], Verdict.UNKNOWN)
            self.assertEqual(record["evidence_level"], EvidenceLevel.E0)
            self.assertFalse(record["can_execute_lean"])

    def test_source_assumptions_candidate_and_model_changes_invalidate_review(self):
        draft = self.formalizer.formalize_claim(self.source)
        review = self.review(draft)
        candidate = draft.candidates[0]
        for changed in (
            replace(draft, source=self.source.revise(self.source.statement, ("n > 0",))),
            replace(draft, candidates=(replace(candidate, assumptions=("n > 0",)),)),
            replace(draft, candidates=(replace(candidate, lean_statement="True"),)),
            replace(
                draft,
                candidates=(
                    replace(
                        candidate, producer=replace(candidate.producer, revision="new-revision")
                    ),
                ),
            ),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                review_candidate(changed, review)

    def test_revision_is_immutable_and_increments_source_version(self):
        revised = self.source.revise("A revised statement", ())
        self.assertEqual(revised.id, self.source.id)
        self.assertEqual(revised.version, 2)
        self.assertNotEqual(revised.fingerprint, self.source.fingerprint)
        with self.assertRaises(FrozenInstanceError):
            revised.version = 3
        with self.assertRaises(ValueError):
            self.source.revise(self.source.statement, self.source.context)
        with self.assertRaises(ValueError):
            SourceClaim("claim", True, "statement")
        with self.assertRaises(ValueError):
            replace(self.source, context=["mutable"])

    def test_review_requires_named_actor_note_and_exact_candidate(self):
        draft = self.formalizer.formalize_claim(self.source)
        for changes in ({"reviewer": " "}, {"note": ""}, {"decision": "PROVED"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.review(draft), **changes)
        with self.assertRaises(ValueError):
            review_candidate(
                draft, replace(self.review(draft), candidate_fingerprint="old candidate")
            )


class LocalFormalizationArtifactTests(unittest.TestCase):
    """Real local fixture implementation and filesystem, no mock model service."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = IntakeStore(Path(self.temp.name))
        self.service = FixtureModelService(ROOT / "evals/formalization/fixtures.json")
        self.formalizer = StructuredFormalizer(self.service)
        self.source = SourceClaim.from_dict(
            strict_json((ROOT / "evals/formalization/clear-nat.json").read_text(encoding="utf-8"))
        )

    def test_fixture_identity_is_explicit_and_unknown_text_is_unsupported(self):
        draft = self.formalizer.formalize_claim(self.source)
        self.assertEqual(draft.producer.kind, "fixture")
        self.assertEqual(draft.usage, TokenUsage(0, 0))
        unknown = self.formalizer.formalize_claim(
            replace(self.source, statement="Unknown research problem")
        )
        self.assertEqual(unknown.status, DraftStatus.UNSUPPORTED)
        self.assertEqual(unknown.math_verdict, Verdict.UNKNOWN)
        self.assertFalse(unknown.candidates)
        contextual = self.formalizer.formalize_claim(replace(self.source, context=("n > 0",)))
        self.assertEqual(contextual.status, DraftStatus.UNSUPPORTED)

    def test_step_fixture_does_not_invent_author_proof_validation(self):
        self.assertEqual(
            self.formalizer.formalize_step(self.source).status, DraftStatus.UNSUPPORTED
        )

    def test_false_statement_is_preserved_without_repair_or_verdict(self):
        source = replace(self.source, statement="所有自然数都等于零。")
        draft = self.formalizer.formalize_claim(source)
        self.assertEqual(draft.candidates[0].lean_statement, "∀ n : Nat, n = 0")
        self.assertEqual(draft.math_verdict, Verdict.UNKNOWN)

    def test_ambiguous_fixture_requests_domain_clarification(self):
        source = SourceClaim.from_dict(
            strict_json((ROOT / "evals/formalization/ambiguous.json").read_text(encoding="utf-8"))
        )
        draft = self.formalizer.formalize_claim(source)
        self.assertEqual(draft.math_verdict, Verdict.NEEDS_CLARIFICATION)
        self.assertEqual(len(draft.questions), 1)

    def test_draft_round_trip_preserves_request_and_candidate_fingerprints(self):
        draft = self.formalizer.formalize_claim(self.source)
        directory = self.store.save(draft)
        self.assertEqual(self.store.load(directory), draft)
        self.assertEqual(self.store.load(directory).request.budget, ModelBudget())
        review = CandidateReview(
            draft.fingerprint,
            draft.candidates[0].fingerprint,
            "fixture author",
            "accept",
            "Reviewed exact source mapping",
        )
        original = (directory / "draft.json").read_bytes()
        first = self.store.review(directory, review)
        second = self.store.review(directory, replace(review, decision="reject"))
        self.assertNotEqual(first, second)
        self.assertEqual((directory / "draft.json").read_bytes(), original)
        self.assertEqual(strict_json(first.read_text())["review_status"], "ACCEPTED")

    def test_modified_and_missing_artifacts_are_rejected(self):
        draft = self.formalizer.formalize_claim(self.source)
        for removed in (False, True):
            directory = self.store.save(draft)
            if removed:
                (directory / "source.json").unlink()
            else:
                (directory / "draft.json").write_bytes(canonical({"math_verdict": "PROVED"}))
            with self.subTest(removed=removed), self.assertRaises((ValueError, OSError)):
                self.store.load(directory)

    def test_path_escape_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "inside this project"):
            self.store.load(Path(self.temp.name))


if __name__ == "__main__":
    unittest.main()
