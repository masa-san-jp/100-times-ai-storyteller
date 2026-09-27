from __future__ import annotations

import hashlib
import random

import pytest

from storyteller.seed import (
    MAX_SEED,
    derive_run_seed,
    derive_task_seed,
    generated_seed,
    task_random,
)


def expected_seed(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def test_run_seed_matches_the_documented_derivation() -> None:
    assert derive_run_seed(123456, 7) == expected_seed("123456:7")


def test_task_seed_is_stable_and_attempt_changes_it() -> None:
    first = derive_task_seed(123456, "D2.echo-d1", 0)
    assert first == derive_task_seed(123456, "D2.echo-d1", 0)
    assert first == expected_seed("123456:D2.echo-d1:0")
    assert first != derive_task_seed(123456, "D2.echo-d1", 1)


def test_task_seed_does_not_depend_on_process_random_state() -> None:
    random.seed(1)
    first = task_random(99, "D1.items", 0).getrandbits(32)
    random.seed(999)
    second = task_random(99, "D1.items", 0).getrandbits(32)
    assert first == second


def test_generated_seed_is_a_32_bit_integer() -> None:
    seed = generated_seed()
    assert isinstance(seed, int)
    assert 0 <= seed <= MAX_SEED


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (derive_run_seed, ("1", 0)),
        (derive_run_seed, (1, -1)),
        (derive_task_seed, ("1", "D1.items", 0)),
        (derive_task_seed, (1, "D1.items", -1)),
    ],
)
def test_seed_inputs_are_checked(function, args) -> None:
    with pytest.raises((TypeError, ValueError)):
        function(*args)
