"""Synthetic ground-truth video sequence with known noise.

The sequence contains:
  * a static, strongly textured background (static region),
  * textured patches that translate across the frame (motion region),
  * an object entering from outside the left border (occlusion entry),
  * a hard scene cut halfway through (old scene must not be dragged in).
"""

import numpy as np
import cv2

H, W = 256, 320
T = 48
CUT = 24          # frame index of the hard scene cut
SIGMA = 20 / 255  # Gaussian noise standard deviation on a [0, 1] scale


def _grid():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    return yy, xx


def background_a():
    yy, xx = _grid()
    img = np.full((H, W), 0.38, np.float32)
    img += 0.10 * np.sin(xx / 6.0)
    img += 0.08 * np.sin(yy / 9.0)
    img += 0.07 * np.sin((xx + yy) / 11.0)
    chk = (((xx // 16).astype(np.int32) + (yy // 16).astype(np.int32)) % 2) == 0
    img += np.where(chk, 0.06, -0.06).astype(np.float32)
    for cx, cy, r, amp in [(70, 185, 34, 0.18), (252, 58, 26, -0.16), (165, 120, 18, 0.12)]:
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        img += amp * np.clip(1 - dist / r, 0, 1)
    return np.clip(img, 0, 1)


def background_b():
    yy, xx = _grid()
    rng = np.random.default_rng(7)
    tex = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 3.0)
    img = np.full((H, W), 0.5, np.float32)
    img += 0.16 * np.sin((xx - yy) / 7.5)
    img += 0.09 * np.sin((xx * 1.7 + yy * 0.6) / 13.0)
    img += 0.10 * tex / (tex.std() + 1e-8)
    chk = (((xx // 11).astype(np.int32) - (yy // 11).astype(np.int32)) % 2) == 0
    img += np.where(chk, -0.05, 0.05).astype(np.float32)
    for cx, cy, r, amp in [(50, 60, 30, -0.18), (235, 195, 38, 0.16), (150, 40, 16, 0.14)]:
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        img += amp * np.clip(1 - dist / r, 0, 1)
    return np.clip(img, 0, 1)


def make_patch(size, seed):
    rng = np.random.default_rng(seed)
    p = 0.5 + 0.22 * np.sin(np.arange(size) / 3.2)[None, :]
    p = np.repeat(p, size, axis=0)
    yy, xx = np.mgrid[0:size, 0:size]
    p += 0.18 * np.sin((xx + yy) / 4.5)
    chk = (((xx // 8).astype(np.int32) + (yy // 8).astype(np.int32)) % 2) == 0
    p += np.where(chk, 0.12, -0.12)
    p += 0.10 * cv2.GaussianBlur(
        rng.normal(0, 1, (size, size)).astype(np.float32), (0, 0), 1.2)
    return np.clip(p, 0, 1).astype(np.float32)


def _place(bg, patch, x, y):
    """Overlay patch centered at (x, y); returns (frame, object mask)."""
    frame = bg.copy()
    mask = np.zeros(bg.shape, np.uint8)
    h, w = patch.shape
    x0, y0 = x - w / 2, y - h / 2
    M = np.float32([[1, 0, x0], [0, 1, y0]])
    canvas = cv2.warpAffine(patch, M, (W, H), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT)
    mcanvas = cv2.warpAffine(np.ones((h, w), np.uint8), M, (W, H),
                             flags=cv2.INTER_NEAREST,
                             borderMode=cv2.BORDER_CONSTANT)
    frame[mcanvas > 0] = canvas[mcanvas > 0]
    mask[mcanvas > 0] = 1
    return frame, mask


def _object_masks(scene_bg, objects, t):
    frame = scene_bg
    masks = []
    for patch, xfun, yfun in objects:
        frame, m = _place(frame, patch, xfun(t), yfun(t))
        masks.append(m)
    return frame, masks


def build():
    """Return dict with GT frames, noisy frames and region masks."""
    bg_a, bg_b = background_a(), background_b()
    pa = make_patch(56, 101)
    pb = make_patch(48, 202)
    pc = make_patch(52, 303)

    # Scene A: object 1 enters from the left (occlusion entry), object 2
    # translates in the opposite direction and is visible from the start.
    obj_a1 = (pa, lambda t: -52 + 3.6 * (t - 4), lambda t: 78 + 6 * np.sin(t / 5.0))
    obj_a2 = (pb, lambda t: 268 - 2.4 * t, lambda t: 182 + 4 * np.cos(t / 4.0))
    # Scene B: fresh objects, different motion direction and speed.
    obj_b1 = (pc, lambda t: -46 + 3.1 * (t - CUT - 2), lambda t: 92 + 5 * np.sin(t / 6.0))
    obj_b2 = (pa, lambda t: 300 - 2.8 * (t - CUT), lambda t: 190)

    gt = np.zeros((T, H, W), np.float32)
    motion = np.zeros((T, H, W), np.uint8)
    occ = np.zeros((T, H, W), np.uint8)

    prev_masks = None
    for t in range(T):
        if t < CUT:
            frame, masks = _object_masks(bg_a, [obj_a1, obj_a2], t)
        else:
            frame, masks = _object_masks(bg_b, [obj_b1, obj_b2], t)
        gt[t] = frame
        m_union = np.clip(sum(masks), 0, 1)
        motion[t] = m_union
        if prev_masks is not None and t != CUT:
            prev_union = np.clip(sum(prev_masks), 0, 1)
            occ[t] = (m_union & (1 - prev_union)).astype(np.uint8)
        prev_masks = masks

    static = np.zeros_like(motion)
    kernel = np.ones((5, 5), np.uint8)
    for t in range(T):
        static[t] = 1 - cv2.dilate(motion[t], kernel, iterations=1)
        motion[t] = cv2.erode(motion[t], np.ones((3, 3), np.uint8), iterations=1)

    rng = np.random.default_rng(42)
    noise = rng.normal(0, SIGMA, size=gt.shape).astype(np.float32)
    noisy = np.clip(gt + noise, 0, 1)
    return {
        "gt": gt, "noisy": noisy, "motion": motion, "static": static,
        "occ": occ, "cut": CUT, "sigma": SIGMA,
    }


if __name__ == "__main__":
    d = build()
    print("gt", d["gt"].shape, "noisy std err:",
          float((d["noisy"] - d["gt"]).std()), "target:", SIGMA)
    print("motion px/frame:", d["motion"].reshape(T, -1).sum(1).min(),
          d["motion"].reshape(T, -1).sum(1).max())
    print("occ px/frame:", d["occ"].reshape(T, -1).sum(1).max())
