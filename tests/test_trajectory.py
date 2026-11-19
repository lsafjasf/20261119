import numpy as np
from scipy import ndimage

from blind_deconv.trajectory import (
    blur_image,
    embed_kernel,
    gaussian_kernel,
    kernel_moments,
    trajectory_kernel,
)


def test_trajectory_kernel_is_nonnegative_and_normalized():
    kernel = trajectory_kernel([(-4, -1), (0, 2), (4, -1)], 15)
    assert kernel.shape == (15, 15)
    assert np.all(kernel >= 0)
    assert np.isclose(kernel.sum(), 1.0)


def test_straight_kernel_has_horizontal_major_axis():
    kernel = trajectory_kernel([(-5, 0), (5, 0)], 13)
    y, x = np.mgrid[:13, :13]
    cy, cx = kernel_moments(kernel)
    var_x = np.sum((x - cx) ** 2 * kernel)
    var_y = np.sum((y - cy) ** 2 * kernel)
    assert var_x > 8 * var_y


def test_embedding_preserves_mass_and_centroid_location():
    compact = trajectory_kernel([(-3, -1), (3, 1)], 9)
    embedded = embed_kernel(compact, 21)
    assert np.isclose(embedded.sum(), compact.sum())
    assert np.allclose(kernel_moments(compact), (4.0, 4.0), atol=0.5)
    assert np.allclose(kernel_moments(embedded), (10.0, 10.0))


def test_blur_keeps_constant_image_constant():
    image = np.full((64, 64), 0.37)
    kernel = gaussian_kernel(9, 1.2)
    blurred = blur_image(image, kernel)
    assert np.allclose(blurred, 0.37, atol=1.0e-8)


def test_blur_matches_direct_convolution():
    rng = np.random.default_rng(3)
    image = rng.random((40, 40))
    kernel = trajectory_kernel([(-2, 0), (2, 0)], 9)
    expected = ndimage.convolve(image, kernel, mode="wrap")
    assert np.allclose(blur_image(image, kernel), expected)
