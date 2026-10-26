"""Unit tests for the small pieces of training logic that are easy to get wrong."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "training"))
import choose_threshold  # noqa: E402
import common  # noqa: E402


def row(**flags):
    base = {t: 0 for t in common.FAILURE_TYPES + ["RNF"]}
    base.update(flags)
    return base


def test_single_failure_type_uses_priority_order():
    assert common.single_failure_type(row(TWF=1, OSF=1)) == "TWF"
    assert common.single_failure_type(row(HDF=1, PWF=1)) == "HDF"
    assert common.single_failure_type(row(OSF=1)) == "OSF"


def test_rnf_only_and_no_failure_have_no_type():
    assert common.single_failure_type(row(RNF=1)) == ""
    assert common.single_failure_type(row()) == ""


def test_cost_counts_missed_failures_and_false_alarms():
    y = np.array([1, 1, 0, 0, 0])
    p = np.array([0.9, 0.1, 0.8, 0.2, 0.1])
    # at t=0.5: one missed failure (p=0.1), one false alarm (p=0.8)
    cost, fn, fp = choose_threshold.cost_at(y, p, 0.5)
    assert (fn, fp) == (1, 1)
    assert cost == choose_threshold.COST_MISSED_FAILURE + choose_threshold.COST_FALSE_ALARM
