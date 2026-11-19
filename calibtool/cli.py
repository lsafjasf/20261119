"""Command-line interface for the calibration toolkit."""

from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np

from . import calibrate as calib
from . import detect, synth
from .straightness import grid_straightness


def _parse_pattern(text):
    cols, rows = text.lower().split("x")
    return (int(cols), int(rows))


def _load_truth(img_dir):
    path = os.path.join(img_dir, "ground_truth.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def cmd_generate(args):
    pattern = _parse_pattern(args.pattern)
    truth = synth.generate_dataset(args.out_dir, count=args.count,
                                   seed=args.seed,
                                   degenerate=args.degenerate,
                                   pattern=pattern, square=args.square)
    cam = truth["camera"]
    print(f"generated {args.count} images in {args.out_dir}")
    print(f"ground truth: fx=fy={cam['fx']}, cx={cam['cx']}, cy={cam['cy']}, "
          f"k1={cam['k1']}, k2={cam['k2']}, k3={cam['k3']}")
    if args.degenerate:
        print("NOTE: poses are near-identical on purpose; calibration on "
              "this set must be flagged as unreliable.")
    return 0


def _print_params(result):
    k = result["kmat"]
    d = result["dist"]
    print(f"  fx={k[0,0]:.3f} fy={k[1,1]:.3f} "
          f"cx={k[0,2]:.3f} cy={k[1,2]:.3f}")
    names = ["k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6"]
    coeffs = " ".join(f"{n}={v:.6f}" for n, v in zip(names, d))
    print(f"  {coeffs}")


def _compare_with_truth(result, truth):
    cam = truth["camera"]
    k = result["kmat"]
    d = result["dist"]
    print("ground-truth comparison:")
    print(f"  fx: est {k[0,0]:.3f} vs true {cam['fx']} "
          f"(err {abs(k[0,0]-cam['fx']):.3f})")
    print(f"  fy: est {k[1,1]:.3f} vs true {cam['fy']} "
          f"(err {abs(k[1,1]-cam['fy']):.3f})")
    print(f"  cx: est {k[0,2]:.3f} vs true {cam['cx']} "
          f"(err {abs(k[0,2]-cam['cx']):.3f})")
    print(f"  cy: est {k[1,2]:.3f} vs true {cam['cy']} "
          f"(err {abs(k[1,2]-cam['cy']):.3f})")
    for i, name in enumerate(["k1", "k2", "p1", "p2", "k3"]):
        true_v = cam[name]
        print(f"  {name}: est {d[i]:.6f} vs true {true_v} "
              f"(err {abs(d[i]-true_v):.6f})")


def cmd_calibrate(args):
    pattern = _parse_pattern(args.pattern)
    paths = detect.list_images(args.img_dir)
    if not paths:
        print(f"no images found in {args.img_dir}", file=sys.stderr)
        return 2
    detections = detect.detect_corners(paths, pattern)
    usable = [i for i, d in enumerate(detections) if d["ok"]]
    for i, d in enumerate(detections):
        tag = "ok" if d["ok"] else f"SKIP ({d['reason']})"
        print(f"  [{i:2d}] {os.path.basename(d['path'])}: {tag}")
    excluded = set(args.exclude or [])
    keep = [i for i in usable if i not in excluded]
    if excluded:
        print(f"excluded by user: {sorted(excluded)}")
    if len(keep) < 2:
        print("ERROR: fewer than 2 usable views; cannot calibrate.",
              file=sys.stderr)
        return 2
    obj = detect.object_points(pattern, args.square)
    img_pts = [detections[i]["corners"] for i in keep]
    h, w = detections[keep[0]]["shape"]
    result = calib.fit(obj, img_pts, (w, h), rational=args.rational)
    report = calib.degeneracy_report(len(keep), result["rvecs"],
                                     result["tvecs"])
    print(f"\nfit over {len(keep)} views, overall RMS reprojection "
          f"error: {result['rms']:.4f} px")
    print("per-image reprojection error / undistorted-grid straightness:")
    straight = []
    for slot, i in enumerate(keep):
        pv = result["per_view"][slot]
        st = grid_straightness(detections[i]["corners"], pattern,
                               result["kmat"], result["dist"])
        straight.append(st)
        name = os.path.basename(detections[i]["path"])
        print(f"  [{i:2d}] {name}: reproj rms={pv['rms']:.4f} "
              f"max={pv['max']:.4f} px | grid dev rms={st['rms']:.4f} "
              f"max={st['max']:.4f} px")

    if report["degenerate"]:
        print("\n*** UNRELIABLE CALIBRATION ***")
        print("the observation set does not constrain the camera model:")
        for p in report["problems"]:
            print(f"  - {p}")
        print("refusing to emit precise-looking parameters. Add views "
              "with varied tilts/positions, or rerun with --force to "
              "see the (untrustworthy) raw fit.")
        if not args.force:
            return 3
        print("--force given; raw fit below is NOT trustworthy:")
    print("\ncalibrated parameters:")
    _print_params(result)
    truth = _load_truth(args.img_dir)
    if truth is not None and not report["degenerate"]:
        _compare_with_truth(result, truth)
    if args.out and not (report["degenerate"] and not args.force):
        payload = {
            "image_size": [w, h],
            "kmat": result["kmat"].tolist(),
            "dist": result["dist"].tolist(),
            "rms": result["rms"],
            "views_used": [int(i) for i in keep],
            "degenerate": report["degenerate"],
        }
        with open(args.out, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"parameters written to {args.out}")
    return 0


def cmd_undistort(args):
    with open(args.params) as fh:
        params = json.load(fh)
    kmat = np.array(params["kmat"], dtype=np.float64)
    dist = np.array(params["dist"], dtype=np.float64)
    os.makedirs(args.out_dir, exist_ok=True)
    paths = detect.list_images(args.img_dir)
    pattern = _parse_pattern(args.pattern) if args.pattern else None
    for path in paths:
        img = cv2.imread(path)
        if img is None:
            continue
        und = cv2.undistort(img, kmat, dist)
        name = os.path.basename(path)
        cv2.imwrite(os.path.join(args.out_dir, name), und)
        line = f"  {name}: undistorted"
        if pattern is not None:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            ok, corners = cv2.findChessboardCorners(gray, pattern)
            if ok:
                st = grid_straightness(corners, pattern, kmat, dist)
                line += (f" | grid dev rms={st['rms']:.4f} "
                         f"max={st['max']:.4f} px")
        print(line)
    print(f"undistorted images written to {args.out_dir}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="calibtool",
        description="Action-camera lens distortion calibration tool")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="render synthetic board images")
    gen.add_argument("out_dir")
    gen.add_argument("--count", type=int, default=12)
    gen.add_argument("--seed", type=int, default=0)
    gen.add_argument("--pattern", default="9x6")
    gen.add_argument("--square", type=float, default=0.025)
    gen.add_argument("--degenerate", action="store_true",
                     help="emit near-identical poses (must be rejected "
                          "by calibration)")
    gen.set_defaults(func=cmd_generate)

    cal = sub.add_parser("calibrate", help="fit intrinsics from boards")
    cal.add_argument("img_dir")
    cal.add_argument("--pattern", default="9x6")
    cal.add_argument("--square", type=float, default=0.025)
    cal.add_argument("--exclude", type=int, nargs="*", default=None,
                     help="image indices to drop before refitting")
    cal.add_argument("--out", default=None, help="write params JSON")
    cal.add_argument("--rational", action="store_true",
                     help="use 8-coefficient rational distortion model")
    cal.add_argument("--force", action="store_true",
                     help="show raw fit even when constraints are "
                          "insufficient")
    cal.set_defaults(func=cmd_calibrate)

    und = sub.add_parser("undistort", help="undistort images")
    und.add_argument("img_dir")
    und.add_argument("--params", required=True)
    und.add_argument("--out-dir", required=True)
    und.add_argument("--pattern", default=None,
                     help="also report grid straightness, e.g. 9x6")
    und.set_defaults(func=cmd_undistort)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)
