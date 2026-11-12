import numpy as np

from scripts.gate_geometry_v2 import reference_residuals
from sensorfusion.contracts import Observation, Session


def test_reference_geometry_keeps_offsets_masks_gaps_and_does_not_change_innovations():
    observation = Observation(
        0.05,
        0.05,
        "uwb",
        "anchor",
        np.array([2.0]),
        np.eye(1),
        anchor=np.zeros(3),
        offset=np.array([1.0, 0, 0]),
        source_row=2,
    )
    session = Session(
        "fixture",
        "test",
        [observation],
        np.array([0.0, 0.1]),
        np.array([[1.0, 0, 0], [1.0, 0, 0]]),
        {},
    )
    innovation = {
        "modality": "uwb",
        "sensor_id": "anchor",
        "source_row": 2,
        "time": 0.05,
        "reason": "gate",
        "value": [2.0],
        "residual": [5.0],
    }
    before = innovation.copy()
    result = reference_residuals(session, [innovation])
    assert result[0]["reference_geometry_residual_m"] == 0
    assert result[0]["filter_innovation_m"] == 5
    assert innovation == before
    session.reference_times = np.array([0.0, 1.0])
    assert reference_residuals(session, [innovation]) == []
