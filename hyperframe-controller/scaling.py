"""
Part V, Step 23 - queue depth -> desired worker count.

The doc's literal table (0->0, 1-3->1, 4-8->2, 9-15->3, 15+->4) is used as the
starting point, unchanged - Part O explicitly says these thresholds get tuned
from real load-test results, which haven't happened yet, so shipping anything
other than the doc's own numbers today would be guessing, not improving.

MAX_WORKERS is a hard ceiling NOT specified in the doc (flagged earlier as an
open decision) - defaulting to 4 (matching the top of the doc's own table) as
a safe ceiling until the team sets a real cost-driven cap.
"""

import os

MAX_WORKERS = int(os.getenv("HYPERFRAME_MAX_WORKERS", "4"))

# (upper_bound_inclusive, worker_count) - first row whose bound covers the
# queue depth wins. float("inf") covers "15+".
_THRESHOLDS = [
    (0, 0),
    (3, 1),
    (8, 2),
    (15, 3),
    (float("inf"), 4),
]


def desired_worker_count(depth: int) -> int:
    for upper_bound, workers in _THRESHOLDS:
        if depth <= upper_bound:
            return min(workers, MAX_WORKERS)
    return MAX_WORKERS
