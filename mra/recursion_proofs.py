"""Versioned bounded proofs: fixed Nat computation and previously checked local lemmas."""

from .formalizer import exact_object
from .structured_terms import compile_request

ARITHMETIC = ("Nat.add_zero", "Nat.zero_add", "Nat.add_succ", "Nat.succ_add")


def compile_proof(raw, compiled, lemmas=()):
    hypotheses = {a["name"]: f"mra_h{i}" for i, a in enumerate(compiled["assumptions"])}
    variables = compiled["variable_mapping"]
    references = {"@lemma." + item["name"]: f"mra_use{i}" for i, item in enumerate(lemmas)}

    def branch(value, induction_hypothesis=False):
        if not isinstance(value, dict):
            raise ValueError("Use a structured branch")
        method = value.get("method")
        if method in ("rfl", "grind"):
            exact_object(value, {"method"}, "Fixed proof")
            return ["all_goals " + method]
        if method != "rewrite":
            raise ValueError("A branch uses rfl, grind or bounded named rewrites")
        exact_object(value, {"method", "steps", "finish"}, "Named rewrite branch")
        if not isinstance(value["steps"], list) or not 1 <= len(value["steps"]) <= 24:
            raise ValueError("A rewrite branch needs 1–24 steps")
        if value["finish"] not in ("rfl", "grind"):
            raise ValueError("Use rfl or grind to finish")
        lines = []
        for step in value["steps"]:
            exact_object(step, {"rule", "reverse"}, "Named rewrite step")
            rule = step["rule"]
            if not isinstance(rule, str) or type(step["reverse"]) is not bool:
                raise ValueError("Use a named rule and explicit boolean direction")
            if rule == "@ih" and induction_hypothesis:
                lean = "mra_ih"
            elif rule in hypotheses:
                lean = hypotheses[rule]
            elif rule in references:
                lean = references[rule]
            elif rule in ARITHMETIC:
                lean = rule
            else:
                raise ValueError(
                    "Only original laws, fixed Nat rules, current IH or earlier local lemmas are allowed"
                )
            direction = "← " if step["reverse"] else ""
            lines.append(f"all_goals (rw [{direction}{lean}])")
        return [*lines, "all_goals " + value["finish"]]

    if not isinstance(raw, dict):
        raise ValueError("Use an explicit proof program")
    outer = [*compiled["carriers"].values(), *[o["lean"] for o in compiled["operations"]]]
    lines = ["by"]
    if outer:
        lines.append("  intro " + " ".join(outer))
    lines.append("  dsimp (config := { failIfUnchanged := false }) only")
    inner = [*[v["lean"] for v in variables.values()], *hypotheses.values()]
    if inner:
        lines.append("  intro " + " ".join(inner))
    for i, item in enumerate(lemmas):
        helper = item["compiled"]
        unconditional = compile_request({**helper["request"], "assumptions": []})
        type_lines = unconditional["formal_statement"].splitlines()
        if outer:
            type_lines = type_lines[1:]
        lines += [f"  have mra_use{i} : (", *["    " + s for s in type_lines], "  ) := by"]
        lines.append("    dsimp (config := { failIfUnchanged := false }) only")
        arguments = [v["lean"] for v in helper["variable_mapping"].values()]
        if arguments:
            lines.append("    intro " + " ".join(arguments))
        args = [*outer, *arguments, *hypotheses.values()]
        lines.append("    exact (" + item["lean"] + " " + " ".join(args) + ")")
    recursion = []
    if compiled["fold_count"]:
        lines += [
            "  have mra_rec_zero : ∀ (a : Type) (z : a) (s : Nat → a → a), Nat.rec (motive := fun _ => a) z s 0 = z := by",
            "    intro a z s; rfl",
            "  have mra_rec_succ : ∀ (a : Type) (z : a) (s : Nat → a → a) (k : Nat), Nat.rec (motive := fun _ => a) z s (Nat.succ k) = s k (Nat.rec (motive := fun _ => a) z s k) := by",
            "    intro a z s k; rfl",
        ]
        recursion = ["mra_rec_zero", "mra_rec_succ"]
    rules = ", ".join([*ARITHMETIC, *recursion])
    normalize = [
        f"simp (config := {{ failIfUnchanged := false }}) only [{rules}] at *",
        "all_goals (dsimp (config := { failIfUnchanged := false }) only at *)",
    ]
    if raw.get("method") != "nat_induction":
        return "\n".join([*lines, *["  " + s for s in normalize], *["  " + s for s in branch(raw)]])
    exact_object(raw, {"method", "variable", "generalize", "base", "step"}, "Natural induction")
    name, generalize = raw["variable"], raw["generalize"]
    if not isinstance(name, str) or name not in variables or variables[name]["type"] != "Nat":
        raise ValueError("Induction uses an original Nat variable")
    if (
        not isinstance(generalize, list)
        or len(generalize) > 7
        or any(not isinstance(n, str) or n not in variables or n == name for n in generalize)
        or len(set(generalize)) != len(generalize)
    ):
        raise ValueError("Generalize distinct other original parameters")
    suffix = (
        " generalizing " + " ".join(variables[n]["lean"] for n in generalize) if generalize else ""
    )
    lines.append("  induction " + variables[name]["lean"] + suffix + " with")
    for label, proof, ih in (
        ("zero", raw["base"], False),
        ("succ mra_index mra_ih", raw["step"], True),
    ):
        lines += [
            "  | " + label + " =>",
            *["    " + s for s in normalize],
            *["    " + s for s in branch(proof, ih)],
        ]
    return "\n".join(lines)
