"""Deterministic Markov calculation over client-supplied, sourced upgrade rules."""
from datetime import date
import math
from urllib.parse import urlparse
import numpy as np


def calculate_expectation(states: list[dict], start_state: str, target_state: str, source_urls: list[str], rules_as_of: str, assumptions: list[str]) -> dict:
    date.fromisoformat(rules_as_of)
    if not source_urls or any(urlparse(url).scheme != "https" or not urlparse(url).hostname for url in source_urls):
        raise ValueError("Supply HTTPS sources for the rules and costs.")
    if not assumptions:
        raise ValueError("Describe event discounts and the destruction/recovery policy in assumptions.")
    if not 1 <= len(states) <= 100:
        raise ValueError("Supply between 1 and 100 non-target states.")
    identifiers = [state["id"] for state in states]
    if len(set(identifiers)) != len(identifiers) or target_state in identifiers or start_state not in identifiers:
        raise ValueError("State ids must be unique, and the target is an implicit absorbing state.")
    index = {identifier: i for i, identifier in enumerate(identifiers)}
    matrix = np.eye(len(states), dtype=float)
    rewards = np.zeros((len(states), 4), dtype=float)
    reachable = {target_state}
    edges = {}
    for row, state in enumerate(states):
        cost = float(state["attempt_cost"])
        transitions = state["transitions"]
        if not math.isfinite(cost) or cost < 0 or not 1 <= len(transitions) <= 20:
            raise ValueError("Attempt costs must be finite and nonnegative; supply 1-20 transitions.")
        rewards[row, 0], rewards[row, 2] = cost, 1.0
        total = 0.0
        edges[state["id"]] = set()
        for transition in transitions:
            destination = transition["next_state"]
            probability = float(transition["probability"])
            extra = float(transition.get("extra_cost", 0))
            if not math.isfinite(probability) or not 0 <= probability <= 1 or not math.isfinite(extra) or extra < 0:
                raise ValueError("Probabilities must be between 0 and 1, and extra costs finite and nonnegative.")
            if destination != target_state and destination not in index:
                raise ValueError("A transition points to an unknown state.")
            total += probability
            if probability > 0:
                edges[state["id"]].add(destination)
            if destination in index:
                matrix[row, index[destination]] -= probability
            rewards[row, 1] += probability * extra
            if transition.get("destruction", False):
                rewards[row, 3] += probability
        if not math.isclose(total, 1.0, abs_tol=1e-8):
            raise ValueError("Transition probabilities must add to 1 in each state.")
    while True:
        newly_reachable = {identifier for identifier, destinations in edges.items() if destinations & reachable}
        if newly_reachable <= reachable:
            break
        reachable |= newly_reachable
    if any(identifier not in reachable for identifier in identifiers):
        raise ValueError("Every state must have a positive-probability path to the target.")
    try:
        expected = np.linalg.solve(matrix, rewards)[index[start_state]]
    except np.linalg.LinAlgError:
        raise ValueError("The supplied rules do not define a finite expectation.") from None
    if not np.isfinite(expected).all() or (expected < -1e-6).any():
        raise ValueError("The supplied rules do not define a finite expectation.")
    return {"expected_meso": float(expected[0] + expected[1]), "expected_attempt_meso": float(expected[0]), "expected_recovery_meso": float(expected[1]), "expected_attempts": float(expected[2]), "expected_destructions": float(expected[3]), "start_state": start_state, "target_state": target_state, "rules_as_of": rules_as_of, "source_urls": source_urls, "assumptions": assumptions, "source": "client_supplied_rules", "rules_verified_by_server": False, "budget_success_probability": None}
