"""Measured engine work, with repeated outputs checked against the first result."""

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np

from sensorfusion.contracts import RunConfig
from sensorfusion.engine import estimate
from sensorfusion.fixtures import make_session
from sensorfusion.storage import timestamp, write_json

parser = argparse.ArgumentParser()
parser.add_argument("--config", type=Path)
parser.add_argument("--output", type=Path, default=Path("evidence/benchmark.json"))
args = parser.parse_args()
config = (
    RunConfig.model_validate(json.loads(args.config.read_text())) if args.config else RunConfig()
)
if config.dataset_id == "fixture":
    session = make_session(config)
else:
    from sensorfusion.data import load_session
    from sensorfusion.study import protected_session

    if protected_session(config):
        raise SystemExit("Performance checks use development recordings, not protected final data")
    session = load_session(
        Path("workspace"), config.dataset_id, config.session_id, config.calibration_id
    )
times = []
baseline = None
for _ in range(5):
    start = time.perf_counter()
    result = estimate(session.observations, config)
    times.append(time.perf_counter() - start)
    if baseline is not None:
        np.testing.assert_array_equal(result.states, baseline.states)
        np.testing.assert_array_equal(result.covariances, baseline.covariances)
    else:
        baseline = result
write_json(
    args.output,
    {
        "created_at": timestamp(),
        "platform": platform.platform(),
        "config": config.model_dump(),
        "synthetic": config.dataset_id == "fixture",
        "seconds": times,
        "median_s": float(np.median(times)),
        "outputs_equal": True,
        "notice": "Measured workload only; not an accuracy or hardware-general speed claim.",
    },
)
print(args.output.read_text(), flush=True)
