from blind_deconv.samples import build_sample_specs, make_observations
from blind_deconv.solver import SolverConfig
from blind_deconv.uncertainty import estimate_kernel_uncertainty


def test_low_texture_input_is_reported_as_high_uncertainty():
    spec = build_sample_specs()[-1]
    _, _, observed = make_observations(spec, 21)
    config = SolverConfig()
    uncertainty = estimate_kernel_uncertainty(observed, 21, config, members=5)
    assert uncertainty.label == "high"
    assert uncertainty.observability < 0.25
    assert uncertainty.centroid_std_px > 0.5


def test_text_edges_have_lower_uncertainty_than_flat_input():
    text_spec = build_sample_specs()[0]
    flat_spec = build_sample_specs()[-1]
    _, _, text_image = make_observations(text_spec, 21)
    _, _, flat_image = make_observations(flat_spec, 21)
    config = SolverConfig()
    text = estimate_kernel_uncertainty(text_image, 21, config, members=5)
    flat = estimate_kernel_uncertainty(flat_image, 21, config, members=5)
    assert text.uncertainty_score < flat.uncertainty_score
