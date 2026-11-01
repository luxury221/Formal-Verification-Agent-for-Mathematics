"""Bounded named rewrites and actual Nat induction; no raw Lean or fallback tactics."""

from .formalizer import exact_object

METHODS = ("rfl", "grind", "rewrite", "nat_induction")


def compile_proof(raw, compiled):
    hypotheses = {a["name"]: f"mra_h{i}" for i, a in enumerate(compiled["assumptions"])}
    variables = compiled["variable_mapping"]

    def branch(value, induction_hypothesis=False):
        if not isinstance(value, dict):
            raise ValueError("Use a structured proof method")
        method = value.get("method")
        if method in ("rfl", "grind"):
            exact_object(value, {"method"}, "Fixed proof")
            return ["all_goals " + method]
        if method != "rewrite":
            raise ValueError("A branch uses rfl, grind or named rewrites")
        exact_object(value, {"method", "steps", "finish"}, "Bounded rewrite proof")
        steps = value["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 24:
            raise ValueError("A rewrite branch has 1–24 explicit steps")
        finish = value["finish"]
        if finish not in ("rfl", "grind"):
            raise ValueError("Rewrite finish must be rfl or grind")
        lines = []
        for step in steps:
            exact_object(step, {"rule", "reverse"}, "Named rewrite step")
            rule = step["rule"]
            if type(step["reverse"]) is not bool or not isinstance(rule, str):
                raise ValueError("Rewrite direction must be an explicit boolean")
            if rule == "@ih" and induction_hypothesis:
                lean = "mra_ih"
            elif rule in hypotheses:
                lean = hypotheses[rule]
            else:
                raise ValueError(
                    "Rewrite requires an original law or the step induction hypothesis"
                )
            direction = "← " if step["reverse"] else ""
            lines.append(f"all_goals (rw [{direction}{lean}])")
        return [*lines, "all_goals " + finish]

    if not isinstance(raw, dict):
        raise ValueError("Use an explicit proof method")
    outer = [*compiled["carriers"].values(), *[o["lean"] for o in compiled["operations"]]]
    inner = [*[v["lean"] for v in variables.values()], *hypotheses.values()]
    lines = ["by"]
    if outer:
        lines.append("  intro " + " ".join(outer))
    lines.append("  dsimp (config := { failIfUnchanged := false }) only")
    if inner:
        lines.append("  intro " + " ".join(inner))
    if raw.get("method") != "nat_induction":
        return "\n".join([*lines, *["  " + line for line in branch(raw)]])
    exact_object(raw, {"method", "variable", "generalize", "base", "step"}, "Natural induction")
    name = raw["variable"]
    if not isinstance(name, str) or name not in variables or variables[name]["type"] != "Nat":
        raise ValueError("Induction requires an original explicitly typed Nat variable")
    generalize = raw["generalize"]
    if (
        not isinstance(generalize, list)
        or len(generalize) > 7
        or any(not isinstance(n, str) or n not in variables or n == name for n in generalize)
        or len(set(generalize)) != len(generalize)
    ):
        raise ValueError("Generalize only distinct other original variables")
    target = variables[name]["lean"]
    suffix = (
        " generalizing " + " ".join(variables[n]["lean"] for n in generalize) if generalize else ""
    )
    recursion_rules = []
    if compiled["fold_count"]:
        # Prove the two computation equations by kernel reflexivity. These are
        # local theorems about the real Nat recursor, not assumed rewrite axioms.
        lines.extend(
            [
                "  have mra_rec_zero : ∀ (a : Type) (z : a) (s : Nat → a → a), Nat.rec (motive := fun _ => a) z s 0 = z := by",
                "    intro a z s; rfl",
                "  have mra_rec_succ : ∀ (a : Type) (z : a) (s : Nat → a → a) (k : Nat), Nat.rec (motive := fun _ => a) z s (Nat.succ k) = s k (Nat.rec (motive := fun _ => a) z s k) := by",
                "    intro a z s k; rfl",
            ]
        )
        recursion_rules = ["mra_rec_zero", "mra_rec_succ"]
    rules = ", ".join(["Nat.add_zero", "Nat.add_succ", *recursion_rules])
    lines.append(f"  induction {target}{suffix} with")
    for label, value, ih in (
        ("zero", raw["base"], False),
        ("succ mra_index mra_ih", raw["step"], True),
    ):
        lines.extend(
            [
                "  | " + label + " =>",
                f"    simp only [{rules}] at *",
                "    all_goals (dsimp (config := { failIfUnchanged := false }) only at *)",
            ]
        )
        lines.extend("    " + line for line in branch(value, ih))
    return "\n".join(lines)
