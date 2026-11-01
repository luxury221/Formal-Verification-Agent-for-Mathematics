"""Repository-owned real least-squares proofs; never accepts caller Lean text."""

from textwrap import indent

EXPANSION = r"""
  have expand (b : ℝ) : ‖u - b • v‖ ^ 2 =
      ‖u‖ ^ 2 - 2 * b * inner ℝ u v + b * b * ‖v‖ ^ 2 := by
    rw [norm_sub_sq_real, real_inner_smul_right, norm_smul, Real.norm_eq_abs, mul_pow, sq_abs]
    ring
"""

MINIMUM_STATEMENT = r"""
  ((∃ b : ℝ, ‖$U - b • $V‖ ^ 2 =
      ‖$U‖ ^ 2 - |inner ℝ $U $V| ^ 2 / ‖$V‖ ^ 2) ∧
   (∀ b : ℝ, ‖$U‖ ^ 2 - |inner ℝ $U $V| ^ 2 / ‖$V‖ ^ 2 ≤ ‖$U - b • $V‖ ^ 2)) ∧
  (‖$U‖ ^ 2 * ‖$V‖ ^ 2 - |inner ℝ $U $V| ^ 2 =
    ‖$V‖ ^ 2 * (‖$U‖ ^ 2 - |inner ℝ $U $V| ^ 2 / ‖$V‖ ^ 2))"""

MINIMUM = (
    "(show"
    + MINIMUM_STATEMENT
    + r""" from by
  let u := $U
  let v := $V
  let m := ‖u‖ ^ 2 - |inner ℝ u v| ^ 2 / ‖v‖ ^ 2
  change ((∃ b : ℝ, ‖u - b • v‖ ^ 2 = m) ∧
    (∀ b : ℝ, m ≤ ‖u - b • v‖ ^ 2)) ∧
    (‖u‖ ^ 2 * ‖v‖ ^ 2 - |inner ℝ u v| ^ 2 = ‖v‖ ^ 2 * m)
$EXPANSION
  by_cases hv : v = 0
  · constructor
    · constructor
      · exact ⟨0, by simp [m, hv]⟩
      · intro b
        simp [m, hv]
    · simp [m, hv]
  · have hn : ‖v‖ ^ 2 ≠ 0 := pow_ne_zero 2 (norm_ne_zero_iff.mpr hv)
    have complete (b : ℝ) : ‖u - b • v‖ ^ 2 =
        m + (‖v‖ * (b - inner ℝ u v / ‖v‖ ^ 2)) ^ 2 := by
      rw [expand]
      dsimp [m]
      rw [sq_abs]
      field_simp [hn] <;> ring
    constructor
    · constructor
      · refine ⟨inner ℝ u v / ‖v‖ ^ 2, ?_⟩
        rw [complete]
        simp
      · intro b
        rw [complete]
        nlinarith only [sq_nonneg (‖v‖ * (b - inner ℝ u v / ‖v‖ ^ 2))]
    · dsimp [m]
      rw [sq_abs]
      field_simp [hn] <;> ring)
"""
)

DERIVATIVE = r"""(show HasDerivAt (fun b : ℝ => ‖$U - b • $V‖ ^ 2)
  (2 * $B * ‖$V‖ ^ 2 - 2 * inner ℝ $U $V) $B from by
  let u := $U
  let v := $V
  let t := $B
$EXPANSION
  change HasDerivAt (fun b : ℝ => ‖u - b • v‖ ^ 2)
    (2 * t * ‖v‖ ^ 2 - 2 * inner ℝ u v) t
  apply hasDerivAt_iff_isLittleO_nhds_zero.mpr
  have error : (fun h : ℝ => ‖u - (t + h) • v‖ ^ 2 - ‖u - t • v‖ ^ 2 -
      h • (2 * t * ‖v‖ ^ 2 - 2 * inner ℝ u v)) =
      (fun h : ℝ => h * (h * ‖v‖ ^ 2)) := by
    funext h
    rw [expand, expand]
    simp only [smul_eq_mul]
    ring
  rw [error]
  apply Asymptotics.isLittleO_of_tendsto
  · intro h hz
    simp [hz]
  · have hc : Filter.Tendsto (fun h : ℝ => h * ‖v‖ ^ 2) (nhds (0 : ℝ)) (nhds 0) := by
      exact (continuous_id.mul continuous_const).tendsto' 0 0 (zero_mul _)
    convert hc using 1
    funext h
    by_cases hzero : h = 0
    · simp [hzero]
    · field_simp)
"""

STATIONARY = r"""(show
  deriv (fun b : ℝ => ‖$U - b • $V‖ ^ 2) $B = 0 →
    $B = inner ℝ $U $V / ‖$V‖ ^ 2 from by
  have hv : $V ≠ 0 := $HV
  have hn : ‖$V‖ ^ 2 ≠ 0 := pow_ne_zero 2 (norm_ne_zero_iff.mpr hv)
  intro stationary
  have hd := ($DERIVATIVE).deriv
  rw [hd] at stationary
  apply (eq_div_iff hn).2
  nlinarith only [stationary])
"""


def instantiate(template, terms, proofs=()):
    """Substitute only already compiled, parenthesized typed terms/proofs."""
    result = template.replace("$EXPANSION", EXPANSION)
    if "$DERIVATIVE" in result:
        result = result.replace("$DERIVATIVE", indent(instantiate(DERIVATIVE, terms).strip(), "  "))
    for key, value in zip(("$U", "$V", "$B"), terms, strict=False):
        result = result.replace(key, "(" + value + ")")
    if proofs:
        result = result.replace("$HV", "(" + proofs[0] + ")")
    if "$" in result:
        raise ValueError("Incomplete fixed optimization proof")
    return result
