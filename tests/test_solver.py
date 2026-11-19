import inspect

import numpy as np

from blind_deconv.samples import build_sample_specs, make_observations
from blind_deconv.solver import BlindDeconvolutionSolver, SolverConfig, blindly_deconvolve
from blind_deconv.trajectory import centered_gaussian_on_canvas


def _small_problem():
    spec = build_sample_specs()[0]
    truth, clean, observed = make_observations(spec, 21)
    return spec, truth, clean, observed


def test_public_solver_has_no_ground_truth_parameters():
    signature = inspect.signature(BlindDeconvolutionSolver.solve)
    assert list(signature.parameters) == ["self", "blurred", "max_kernel_size"]


def test_initial_kernel_is_solver_generated_gaussian_not_truth():
    _, truth, _, observed = _small_problem()
    config = SolverConfig(max_iterations=3)
    result = blindly_deconvolve(observed, 21, config)
    expected = centered_gaussian_on_canvas(21, config.init_sigma)
    assert np.allclose(result.initial_kernel, expected)
    assert not np.allclose(result.initial_kernel, truth)
    assert result.metadata["truth_used_by_solver"] is False


def test_estimated_kernel_obeys_simplex_constraints():
    _, _, _, observed = _small_problem()
    result = blindly_deconvolve(observed, 21, SolverConfig(max_iterations=4))
    assert result.estimated_kernel.shape == (21, 21)
    assert np.all(result.estimated_kernel >= 0)
    assert np.isclose(result.estimated_kernel.sum(), 1.0)
    assert len(result.kernel_history) == result.config.max_iterations + 1
    assert len(result.objective) == result.config.max_iterations
    assert np.all(np.isfinite(result.objective))


def test_larger_size_cap_still_returns_requested_canvas():
    _, _, _, observed = _small_problem()
    result = blindly_deconvolve(observed, 25, SolverConfig(max_iterations=2))
    assert result.estimated_kernel.shape == (25, 25)
    assert result.metadata["max_kernel_size"] == 25
