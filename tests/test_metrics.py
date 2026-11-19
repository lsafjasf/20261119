import numpy as np

from blind_deconv.metrics import compute_kernel_metrics, compute_reconstruction_metrics
from blind_deconv.trajectory import trajectory_kernel


def test_identical_kernel_has_zero_position_error():
    kernel = trajectory_kernel([(-3, 0), (3, 1)], 11)
    metrics = compute_kernel_metrics(kernel, kernel)
    assert metrics.centroid_error_px == 0.0
    assert metrics.peak_error_px == 0.0
    assert np.isclose(metrics.ncc, 1.0)


def test_position_error_uses_centroid_distance():
    kernel = trajectory_kernel([(-2, 0), (2, 1)], 11)
    shifted = np.roll(kernel, shift=(0, 2), axis=1)
    metrics = compute_kernel_metrics(kernel, shifted)
    assert np.isclose(metrics.centroid_error_px, 2.0, atol=0.02)


def test_perfect_reconstruction_has_zero_error_and_ringing():
    rng = np.random.default_rng(4)
    image = rng.random((96, 96))
    metrics = compute_reconstruction_metrics(image, image, margin=12)
    assert metrics.rmse == 0.0
    assert metrics.relative_error_pct == 0.0
    assert metrics.ringing_index_pct == 0.0
    assert metrics.psnr_db > 200.0
