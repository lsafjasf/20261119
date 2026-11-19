"""Motion-compensated temporal denoising.

Per-pixel recursive temporal filter:
  * optical flow (Farneback) warps the running estimate onto the new frame;
  * forward/backward flow consistency rejects disocclusion / mismatch pixels;
  * history weight shrinks on large compensated residuals (motion failure);
  * strong-texture pixels are never fully averaged (anti plastic look) and
    receive detail injected back from the current noisy frame;
  * a global scene cut is detected and the history is reset, so content from
    the old scene is never dragged into the new one.
"""

import numpy as np
import cv2

SIGMA_KNOWN = 20 / 255
TAU = 3.0          # residual scale in noise std for the weight curve
W_MAX = 0.97       # strongest temporal weight (smooth static pixels)
W_TEX = 0.88       # cap for strong-texture pixels (preserve structure)
DETAIL = 0.35      # fraction of high-frequency detail restored in textures


def immerkaer_sigma(frame):
    """Noise std estimate from the Immerkaer (1996) convolution mask."""
    m = np.float32([[1, -2, 1], [-2, 4, -2], [1, -2, 1]])
    sigma = np.abs(cv2.filter2D(frame, cv2.CV_32F, m)).sum()
    h, w = frame.shape
    return sigma * np.sqrt(0.5 * np.pi) / (6 * (w - 2) * (h - 2))


def texture_mask(frame):
    """Strong-texture pixels only (top ~8% high-frequency energy).

    Kept narrow so ordinary textured background still receives strong temporal
    averaging; the mask only prevents genuine detail peaks from being plastic.
    """
    lp = cv2.GaussianBlur(frame, (0, 0), 2.0)
    energy = cv2.GaussianBlur((frame - lp) ** 2, (0, 0), 3.0)
    local = cv2.GaussianBlur(energy, (0, 0), 25.0) + 1e-6
    prominence = energy / local
    return (prominence > 1.4).astype(np.uint8)


def _flow(prev, nxt):
    # This OpenCV build returns zero flow for float32 frames; use uint8.
    p8 = (np.clip(prev, 0, 1) * 255).astype(np.uint8)
    n8 = (np.clip(nxt, 0, 1) * 255).astype(np.uint8)
    return cv2.calcOpticalFlowFarneback(
        p8, n8, None, 0.5, 4, 21, 5, 7, 1.4,
        cv2.OPTFLOW_FARNEBACK_GAUSSIAN)


def _warp(img, flow):
    """Warp prev image into current coordinates.

    Farneback flow(x) = x_cur - x_prev, so the source sample point in the
    old image is x_cur - flow(x).
    """
    h, w = img.shape
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
    mapx = gx - flow[..., 0]
    mapy = gy - flow[..., 1]
    return cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE), mapx, mapy


def denoise_sequence(noisy, reset_on_cut=True, use_mc=True):
    """Denoise a [0,1] float sequence. Returns outputs dict per frame."""
    T, H, W = noisy.shape
    out = np.zeros_like(noisy)
    weight_maps = np.zeros_like(noisy)
    n_eff = np.ones(T, np.float32)
    est_sigma = np.zeros(T, np.float32)
    cut_scores = np.zeros(T, np.float32)
    cut_flags = np.zeros(T, np.uint8)

    state = cv2.bilateralFilter(noisy[0], 5, 0.08, 5)
    ncount = np.ones((H, W), np.float32)
    out[0] = state
    est_sigma[0] = immerkaer_sigma(noisy[0])

    for t in range(1, T):
        cur, prev = noisy[t], noisy[t - 1]
        est_sigma[t] = immerkaer_sigma(cur)
        cut_scores[t] = np.mean(np.abs(cur - prev))

        # Global scene cut: abrupt global change vs. typical within-scene
        # level (which is driven by the moving objects, ~1.2 sigma here).
        typical = np.median(cut_scores[1:t]) if t > 1 else 0.0
        is_cut = bool(t > 1 and cut_scores[t] > 2.0 * typical)
        if is_cut:
            cut_flags[t] = 1
        if is_cut and reset_on_cut:
            state = cv2.bilateralFilter(cur, 5, 0.08, 5)
            ncount = np.ones((H, W), np.float32)
            out[t] = state
            n_eff[t] = 1.0
            continue

        if use_mc:
            flow_f = _flow(prev, cur)
            flow_b = _flow(cur, prev)
            warped_state, mapx, mapy = _warp(state, flow_f)
            warped_count, _, _ = _warp(ncount, flow_f)

            # Flow field and borders. Dense Farneback round-trip error is
            # unreliable on periodic texture (aperture effect), so confidence
            # uses border validity plus the compensated residual downstream.
            fbx, _, _ = _warp(flow_b[..., 0], flow_f)
            fby, _, _ = _warp(flow_b[..., 1], flow_f)
            fb_err = np.sqrt((flow_f[..., 0] - fbx) ** 2 +
                             (flow_f[..., 1] - fby) ** 2)
            border = (mapx >= 1) & (mapx <= W - 2) & \
                     (mapy >= 1) & (mapy <= H - 2)
            # Absolute (not normalized) FB check: only gross contradictions
            # beyond plausible flow magnitude are treated as mismatch.
            flow_mag = np.sqrt(flow_f[..., 0] ** 2 + flow_f[..., 1] ** 2)
            hard_fail = fb_err > np.maximum(2.5, 1.5 * flow_mag)
            reliable = np.where(border & ~hard_fail, 1.0, 0.12).astype(np.float32)
        else:
            warped_state, warped_count = state, ncount
            reliable = np.ones((H, W), np.float32)

        # Compensated residual is the primary signal: small after correct
        # warping, large on entering content / occlusion / flow failure.
        residual = np.abs(cur - warped_state)
        w = np.exp(-0.5 * (residual / (TAU * SIGMA_KNOWN)) ** 2)
        w = 0.12 + 0.88 * w * reliable

        tex = texture_mask(cur)
        w = np.where(tex > 0, np.minimum(w, W_TEX), np.minimum(w, W_MAX))

        cur_lp = cv2.GaussianBlur(cur, (0, 0), 1.2)
        detail = cur - cur_lp
        blended = (1 - w) * cur + w * warped_state
        out[t] = blended + DETAIL * tex * detail
        out[t] = np.clip(out[t], 0, 1)

        # Recursive effective sample count: the new frame contributes one
        # sample; history (already including previous samples) is taken with
        # weight w. N grows ~1/(1-w) in stable static regions, resets to 1
        # where w collapses (motion failure / entering content).
        ncount = (1 - w) + w * (warped_count + 1.0)
        n_eff[t] = float(np.mean(ncount))
        weight_maps[t] = w
        state = out[t]

    return {
        "out": out, "weights": weight_maps, "n_eff": n_eff,
        "est_sigma": est_sigma, "cut_scores": cut_scores,
        "cut_flags": cut_flags,
    }
