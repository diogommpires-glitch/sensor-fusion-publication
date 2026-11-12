import numpy as np

from sensorfusion.radar import associate_centroid, cluster_frame, spherical_to_cartesian


def test_spherical_coordinates_use_radians_and_full_slant_range():
    converted = spherical_to_cartesian(
        np.array([2.0]), np.array([np.pi / 2]), np.array([np.pi / 6])
    )
    np.testing.assert_allclose(converted, [[np.sqrt(3), 0, 1]], atol=1e-14)


def test_clutter_ambiguity_missing_target_and_duplicate_returns():
    target = np.array([[1.00, 2, 3], [1.02, 2.01, 3], [0.99, 2, 2.99]])
    clutter = target + [2, 0, 0]
    points = np.vstack([target, clutter, [[10, 10, 10]]])
    covariance = np.diag([0.04, 0.09, 0.16])
    clusters = cluster_frame(points, radius=0.1, min_samples=3)
    result = associate_centroid(clusters, np.array([1, 2, 3]), np.eye(3) * 0.01, covariance)
    assert result.accepted
    assert result.centroid is not None
    np.testing.assert_allclose(result.centroid, target.mean(axis=0))
    np.testing.assert_array_equal(result.covariance, covariance)
    repeated = cluster_frame(np.repeat(points, 8, axis=0), radius=0.1, min_samples=3)
    repeated_result = associate_centroid(
        repeated, np.array([1, 2, 3]), np.eye(3) * 0.01, covariance
    )
    assert repeated_result.centroid is not None
    np.testing.assert_allclose(repeated_result.centroid, result.centroid)
    np.testing.assert_array_equal(repeated_result.covariance, covariance)
    ambiguous = associate_centroid(clusters, np.array([2, 2, 3]), np.eye(3), covariance)
    assert ambiguous.reason == "ambiguous"
    missing = associate_centroid(clusters, np.zeros(3), np.eye(3) * 0.001, covariance)
    assert missing.reason == "outside_gate"
    assert associate_centroid([], np.zeros(3), np.eye(3), covariance).reason == "no_clusters"
