"""Versioned reservation/settlement journal. Unknown costs remain fully occupied."""

import math

SCHEMA = "paper-budget-settlement-v1"
RESOURCES = {"output_tokens": "output_tokens_reserved", "kernel_seconds": "kernel_seconds_reserved"}


def number(value, resource):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid observed paper resource consumption")
    if resource == "output_tokens" and type(value) is not int:
        raise ValueError("Token accounting needs integer provider usage")


def totals(row):
    values = {
        resource: {"settled": 0, "held": 0, "uncertain": 0, "occupied": 0} for resource in RESOURCES
    }
    for entry in row["reservations"].values():
        resource, state = entry["resource"], entry["state"]
        if resource not in values or state not in ("HELD", "UNCERTAIN", "SETTLED"):
            raise ValueError("Unknown paper budget journal entry")
        number(entry["limit"], resource)
        amount = entry["charged"] if state == "SETTLED" else entry["limit"]
        number(amount, resource)
        values[resource]["settled" if state == "SETTLED" else "held"] += amount
        if state == "UNCERTAIN":
            values[resource]["uncertain"] += amount
        values[resource]["occupied"] += amount
    return values


def available(row, resource):
    return max(0, row["budget"][resource] - totals(row)[resource]["occupied"])


def reserve(row, key, resource, amount):
    if resource not in RESOURCES:
        raise ValueError("Unknown reservation resource")
    number(amount, resource)
    if amount <= 0 or key in row["reservations"]:
        raise ValueError("A paper operation needs a new positive reservation")
    if amount > available(row, resource):
        raise ValueError("Insufficient unoccupied paper budget")
    row["reservations"][key] = {
        "resource": resource,
        "limit": amount,
        "state": "HELD",
        "charged": None,
        "evidence": None,
    }
    row["used"][RESOURCES[resource]] += amount


def settle(row, key, amount, evidence):
    entry = row["reservations"][key]
    number(amount, entry["resource"])
    if not isinstance(evidence, str) or evidence not in row["artifacts"]:
        raise ValueError("Settlement needs a registered immutable response/receipt")
    if entry["state"] == "SETTLED":
        if entry["charged"] != amount or entry["evidence"] != evidence:
            raise ValueError("Conflicting repeated paper budget settlement")
        return
    if entry["state"] != "HELD":
        raise ValueError("An uncertain operation cannot be silently refunded")
    entry.update(
        state="SETTLED", charged=amount, evidence=evidence, overrun=amount > entry["limit"]
    )


def uncertain(row, key):
    if key and key in row["reservations"] and row["reservations"][key]["state"] == "HELD":
        row["reservations"][key]["state"] = "UNCERTAIN"
