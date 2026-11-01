from dataclasses import FrozenInstanceError, replace
import unittest

from mra.domain import Claim, Formalization, Verdict


class DomainTests(unittest.TestCase):
    def test_claim_changes_invalidate_fingerprint(self):
        claim = Claim("test", 1, "Every n has n + 0 = n", "∀ n : Nat, n + 0 = n")
        for changed in (
            replace(claim, version=2),
            replace(claim, assumptions=("n > 0",)),
            replace(claim, formal_statement="True"),
            replace(claim, original_statement="All numbers are zero"),
        ):
            self.assertNotEqual(claim.fingerprint, changed.fingerprint)

    def test_contract_rejects_invalid_versions(self):
        with self.assertRaises(ValueError):
            Claim("test", 0, "statement", "True")
        with self.assertRaises(ValueError):
            Claim("test", 1, "statement", "True", schema_version="99")
        with self.assertRaises(ValueError):
            Formalization("fingerprint", "True", direction="trust-model")

    def test_claim_is_immutable(self):
        claim = Claim("test", 1, "statement", "True")
        with self.assertRaises(FrozenInstanceError):
            claim.version = 2

    def test_math_verdicts_exclude_execution_errors(self):
        self.assertEqual(len(Verdict), 5)
        with self.assertRaises(ValueError):
            Verdict("TIMEOUT")


if __name__ == "__main__":
    unittest.main()
