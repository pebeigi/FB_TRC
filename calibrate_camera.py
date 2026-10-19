#!/usr/bin/env python3
"""Calibrate a camera from checkerboard images and save review artifacts.

The default target is one 5 x 5 internal-corner quadrant of the calibration
board used by this project.  The physical square size only affects estimated
translations; the camera matrix and distortion coefficients are independent
of the chosen unit.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


@dataclass
class FrameResult:
    path: Path
    status: str
    reason: str = ""
    corners: np.ndarray | None = None
    detection_method: str = ""
    center_x: float | None = None
    center_y: float | None = None
    coverage_percent: float | None = None
    grid_fit_error_percent: float | None = None
    reprojection_error: float | None = None
    tilt_degrees: float | None = None
    annotated_path: Path | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect checkerboards, calibrate the camera, and save review images."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        default=Path("calibration_frames/uniview_tilted"),
        help="folder containing calibration images",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="result folder (default: <input_dir>/calibration_output)",
    )
    parser.add_argument(
        "--pattern-cols",
        type=int,
        default=5,
        help="number of INTERNAL checkerboard corners across (default: 5)",
    )
    parser.add_argument(
        "--pattern-rows",
        type=int,
        default=5,
        help="number of INTERNAL checkerboard corners down (default: 5)",
    )
    parser.add_argument(
        "--square-size",
        type=float,
        default=1.0,
        help="physical checker square side length (default: 1.0)",
    )
    parser.add_argument(
        "--square-unit",
        default="square",
        help="unit for --square-size, such as mm or inches (default: square)",
    )
    parser.add_argument(
        "--model",
        choices=("standard", "rational"),
        default="standard",
        help="distortion model (default: standard, k1 k2 p1 p2 k3)",
    )
    parser.add_argument(
        "--max-reprojection-error",
        type=float,
        default=3.0,
        help="reject strong calibration outliers above this RMS error (default: 3.0 px)",
    )
    parser.add_argument(
        "--max-grid-fit-error",
        type=float,
        default=0.08,
        help="reject detections whose grid-to-homography RMS exceeds this fraction of a square (default: 0.08)",
    )
    parser.add_argument(
        "--min-views",
        type=int,
        default=12,
        help="minimum detected views required (default: 12)",
    )
    parser.add_argument(
        "--detection-max-width",
        type=int,
        default=1920,
        help="downscale only during initial detection for speed; 0 disables",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="search for images recursively below input_dir",
    )
    parser.add_argument(
        "--keep-output",
        action="store_true",
        help="do not replace a previous calibration_output folder",
    )
    return parser.parse_args()


def list_images(input_dir: Path, recursive: bool, output_dir: Path) -> list[Path]:
    iterator = input_dir.rglob("*") if recursive else input_dir.glob("*")
    output_resolved = output_dir.resolve()
    images = []
    for path in iterator:
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            path.resolve().relative_to(output_resolved)
            continue
        except ValueError:
            pass
        images.append(path)
    return sorted(images, key=lambda path: path.name.lower())


def make_object_points(cols: int, rows: int, square_size: float) -> np.ndarray:
    points = np.zeros((rows * cols, 3), np.float32)
    points[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    points[:, :2] *= square_size
    return points


def detect_corners(
    image: np.ndarray,
    pattern_size: tuple[int, int],
    max_width: int,
    max_grid_fit_error: float,
) -> tuple[np.ndarray | None, str]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    scale = 1.0
    detection_gray = gray

    if max_width > 0 and width > max_width:
        scale = max_width / width
        detection_gray = cv2.resize(
            gray,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA,
        )

    flags = (
        cv2.CALIB_CB_NORMALIZE_IMAGE
        | cv2.CALIB_CB_EXHAUSTIVE
        | cv2.CALIB_CB_ACCURACY
    )
    invalid_candidates: list[tuple[float, np.ndarray, str]] = []

    def try_region(
        source: np.ndarray,
        method: str,
        offset_x: int = 0,
        offset_y: int = 0,
        source_scale: float = scale,
    ) -> np.ndarray | None:
        found, candidate = cv2.findChessboardCornersSB(source, pattern_size, flags)
        if not found or candidate is None:
            return None
        candidate = candidate.astype(np.float32)
        candidate[:, 0, 0] += offset_x
        candidate[:, 0, 1] += offset_y
        candidate /= source_scale
        fit = grid_fit_error(candidate, pattern_size[0], pattern_size[1])
        if fit <= max_grid_fit_error:
            return candidate
        invalid_candidates.append((fit, candidate, method))
        return None

    # Fast first pass, identical to the original detector.
    candidate = try_region(detection_gray, "SB scaled/full frame")
    if candidate is not None:
        return candidate, "SB scaled/full frame"

    # Downscaling occasionally merges narrow white gaps. Retry the original
    # pixels before using more expensive tiled searches.
    if scale < 1.0:
        candidate = try_region(gray, "SB native/full frame", source_scale=1.0)
        if candidate is not None:
            return candidate, "SB native/full frame"

    def tiled_search(source: np.ndarray, label: str) -> tuple[np.ndarray | None, str]:
        source_height, source_width = source.shape
        for tile_fraction, positions_per_axis in ((0.62, 3), (0.52, 5)):
            tile_width = round(source_width * tile_fraction)
            tile_height = round(source_height * tile_fraction)
            x_positions = np.linspace(
                0, source_width - tile_width, positions_per_axis
            ).astype(int)
            y_positions = np.linspace(
                0, source_height - tile_height, positions_per_axis
            ).astype(int)
            valid: list[np.ndarray] = []
            for y in y_positions:
                for x in x_positions:
                    tile = source[y : y + tile_height, x : x + tile_width]
                    method = (
                        f"{label} {tile_fraction:.2f} tile x={x} y={y}"
                    )
                    corners = try_region(tile, method, int(x), int(y))
                    if corners is not None:
                        valid.append(corners)
            if valid:
                # Prefer the largest well-formed grid; larger corner spacing
                # normally gives the most accurate subpixel result.
                best = max(
                    valid, key=lambda item: cv2.contourArea(cv2.convexHull(item))
                )
                return best, f"{label} {tile_fraction:.2f}"
        return None, ""

    candidate, method = tiled_search(detection_gray, "SB tiled")
    if candidate is not None:
        return candidate, method

    # Final retry for uneven lighting or low local contrast.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    normalized = clahe.apply(detection_gray)
    candidate = try_region(normalized, "SB CLAHE/full frame")
    if candidate is not None:
        return candidate, "SB CLAHE/full frame"
    candidate, method = tiled_search(normalized, "SB CLAHE/tiled")
    if candidate is not None:
        return candidate, method

    # Preserve the best invalid candidate for an informative red overlay.
    if invalid_candidates:
        _, candidate, method = min(invalid_candidates, key=lambda item: item[0])
        return candidate, f"{method} (invalid geometry)"
    return None, "all retry methods failed"


def corner_metrics(corners: np.ndarray, image_size: tuple[int, int]) -> tuple[float, float, float]:
    width, height = image_size
    points = corners.reshape(-1, 2)
    center = points.mean(axis=0)
    hull = cv2.convexHull(points.astype(np.float32))
    coverage = 100.0 * cv2.contourArea(hull) / (width * height)
    return float(center[0] / width), float(center[1] / height), float(coverage)


def grid_fit_error(corners: np.ndarray, cols: int, rows: int) -> float:
    """Return homography-fit RMS as a fraction of the median grid spacing.

    This rejects false grids that jump across the gaps between the four
    checkerboard quadrants while remaining independent of target scale.
    """
    ideal = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2).astype(np.float32)
    observed = corners.reshape(-1, 2).astype(np.float32)
    homography, _ = cv2.findHomography(ideal, observed, method=0)
    if homography is None:
        return math.inf
    projected = cv2.perspectiveTransform(ideal.reshape(-1, 1, 2), homography).reshape(-1, 2)
    rms = float(np.sqrt(np.mean(np.sum((observed - projected) ** 2, axis=1))))
    grid = observed.reshape(rows, cols, 2)
    horizontal = np.linalg.norm(np.diff(grid, axis=1), axis=2).ravel()
    vertical = np.linalg.norm(np.diff(grid, axis=0), axis=2).ravel()
    spacing = float(np.median(np.concatenate((horizontal, vertical))))
    return rms / spacing if spacing > 1e-9 else math.inf


def safe_output_name(path: Path, input_dir: Path, index: int) -> str:
    try:
        relative = path.relative_to(input_dir)
        stem = "__".join(relative.with_suffix("").parts)
    except ValueError:
        stem = path.stem
    return f"{index:04d}_{stem}.jpg"


def annotated_image(
    image: np.ndarray,
    pattern_size: tuple[int, int],
    frame: FrameResult,
) -> np.ndarray:
    output = image.copy()
    detected = frame.corners is not None
    if detected:
        cv2.drawChessboardCorners(output, pattern_size, frame.corners, True)
        points = frame.corners.reshape(-1, 2)
        for number, point in ((1, points[0]), (len(points), points[-1])):
            location = tuple(np.rint(point).astype(int))
            cv2.circle(output, location, 14, (0, 0, 255), 3, cv2.LINE_AA)
            cv2.putText(
                output,
                str(number),
                (location[0] + 16, location[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 0, 255),
                3,
                cv2.LINE_AA,
            )

    color = (40, 210, 40) if frame.status == "accepted" else (30, 30, 230)
    detail = frame.status.upper()
    if frame.reason:
        detail += f": {frame.reason}"
    lines = [frame.path.name, detail]
    if frame.detection_method:
        lines.append(f"Detection: {frame.detection_method}")
    if frame.reprojection_error is not None:
        lines.append(f"Reprojection RMS: {frame.reprojection_error:.3f} px")
    if frame.coverage_percent is not None:
        lines.append(f"Grid coverage: {frame.coverage_percent:.2f}%")
    if frame.grid_fit_error_percent is not None:
        lines.append(f"Grid fit error: {frame.grid_fit_error_percent:.2f}% of one square")

    font_scale = max(0.7, min(output.shape[1], output.shape[0]) / 1700.0)
    thickness = max(2, round(font_scale * 2))
    line_height = round(42 * font_scale)
    box_height = 20 + line_height * len(lines)
    overlay = output.copy()
    cv2.rectangle(overlay, (0, 0), (output.shape[1], box_height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.65, output, 0.35, 0, output)
    for line_number, line in enumerate(lines):
        cv2.putText(
            output,
            line,
            (18, 32 + line_number * line_height),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color if line_number == 1 else (255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )
    return output


def per_view_errors(
    object_points: list[np.ndarray],
    image_points: list[np.ndarray],
    rvecs: tuple[np.ndarray, ...],
    tvecs: tuple[np.ndarray, ...],
    camera_matrix: np.ndarray,
    distortion: np.ndarray,
) -> np.ndarray:
    errors = []
    for obj, img, rvec, tvec in zip(object_points, image_points, rvecs, tvecs):
        projected, _ = cv2.projectPoints(obj, rvec, tvec, camera_matrix, distortion)
        delta = img.reshape(-1, 2) - projected.reshape(-1, 2)
        errors.append(float(np.sqrt(np.mean(np.sum(delta * delta, axis=1)))))
    return np.asarray(errors, dtype=np.float64)


def run_calibration(
    frames: list[FrameResult],
    object_template: np.ndarray,
    image_size: tuple[int, int],
    model: str,
) -> tuple[float, np.ndarray, np.ndarray, tuple[np.ndarray, ...], tuple[np.ndarray, ...], np.ndarray]:
    object_points = [object_template.copy() for _ in frames]
    image_points = [frame.corners for frame in frames]
    flags = cv2.CALIB_RATIONAL_MODEL if model == "rational" else 0
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER,
        100,
        1e-9,
    )
    rms, matrix, distortion, rvecs, tvecs = cv2.calibrateCamera(
        object_points,
        image_points,
        image_size,
        None,
        None,
        flags=flags,
        criteria=criteria,
    )
    errors = per_view_errors(
        object_points, image_points, rvecs, tvecs, matrix, distortion
    )
    return rms, matrix, distortion, rvecs, tvecs, errors


def reject_outliers(
    detected: list[FrameResult],
    object_template: np.ndarray,
    image_size: tuple[int, int],
    model: str,
    max_error: float,
    min_views: int,
) -> tuple[list[FrameResult], float, np.ndarray, np.ndarray, tuple[np.ndarray, ...], tuple[np.ndarray, ...], np.ndarray]:
    active = detected.copy()
    while True:
        rms, matrix, distortion, rvecs, tvecs, errors = run_calibration(
            active, object_template, image_size, model
        )
        median = float(np.median(errors))
        mad = float(np.median(np.abs(errors - median)))
        robust_limit = median + 3.0 * 1.4826 * mad if mad > 1e-9 else max_error
        # A frame must be both worse than the requested absolute limit and a
        # statistical outlier. This avoids pruning an entire internally
        # consistent 4K data set merely because its typical error is > 1 px.
        rejection_limit = max(max_error, robust_limit)
        worst_index = int(np.argmax(errors))
        if errors[worst_index] <= rejection_limit or len(active) <= min_views:
            return active, rms, matrix, distortion, rvecs, tvecs, errors

        rejected = active.pop(worst_index)
        rejected.status = "rejected"
        rejected.reason = (
            f"calibration outlier ({errors[worst_index]:.3f} px; "
            f"limit {rejection_limit:.3f} px)"
        )
        rejected.reprojection_error = float(errors[worst_index])


def rotation_tilt_degrees(rvec: np.ndarray) -> float:
    rotation, _ = cv2.Rodrigues(rvec)
    board_normal = rotation[:, 2]
    cosine = float(np.clip(abs(board_normal[2]), 0.0, 1.0))
    return math.degrees(math.acos(cosine))


def save_yaml(
    path: Path,
    image_size: tuple[int, int],
    pattern_size: tuple[int, int],
    square_size: float,
    square_unit: str,
    model: str,
    rms: float,
    matrix: np.ndarray,
    distortion: np.ndarray,
) -> None:
    storage = cv2.FileStorage(str(path), cv2.FILE_STORAGE_WRITE)
    if not storage.isOpened():
        raise RuntimeError(f"Could not write {path}")
    try:
        storage.write("image_width", image_size[0])
        storage.write("image_height", image_size[1])
        storage.write("pattern_columns", pattern_size[0])
        storage.write("pattern_rows", pattern_size[1])
        storage.write("square_size", square_size)
        storage.write("square_unit", square_unit)
        storage.write("distortion_model", model)
        storage.write("rms_reprojection_error", rms)
        storage.write("camera_matrix", matrix)
        storage.write("distortion_coefficients", distortion)
    finally:
        storage.release()


def json_array(array: np.ndarray) -> list:
    return np.asarray(array).reshape(array.shape).tolist()


def write_frame_report(path: Path, frames: list[FrameResult], input_dir: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "image",
                "status",
                "reason",
                "detection_method",
                "center_x_fraction",
                "center_y_fraction",
                "grid_coverage_percent",
                "grid_fit_error_percent_of_square",
                "reprojection_rms_pixels",
                "board_tilt_degrees",
                "annotated_image",
            ]
        )
        for frame in frames:
            try:
                image_name = str(frame.path.relative_to(input_dir))
            except ValueError:
                image_name = str(frame.path)
            writer.writerow(
                [
                    image_name,
                    frame.status,
                    frame.reason,
                    frame.detection_method,
                    "" if frame.center_x is None else f"{frame.center_x:.6f}",
                    "" if frame.center_y is None else f"{frame.center_y:.6f}",
                    "" if frame.coverage_percent is None else f"{frame.coverage_percent:.6f}",
                    "" if frame.grid_fit_error_percent is None else f"{frame.grid_fit_error_percent:.6f}",
                    "" if frame.reprojection_error is None else f"{frame.reprojection_error:.6f}",
                    "" if frame.tilt_degrees is None else f"{frame.tilt_degrees:.3f}",
                    "" if frame.annotated_path is None else str(frame.annotated_path),
                ]
            )


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = (args.output_dir or input_dir / "calibration_output").resolve()
    pattern_size = (args.pattern_cols, args.pattern_rows)

    if not input_dir.is_dir():
        raise RuntimeError(f"Input directory does not exist: {input_dir}")
    if args.pattern_cols < 3 or args.pattern_rows < 3:
        raise RuntimeError("Checkerboard pattern must have at least 3 x 3 internal corners.")
    if args.square_size <= 0:
        raise RuntimeError("--square-size must be greater than zero.")
    if args.min_views < 3:
        raise RuntimeError("--min-views must be at least 3.")
    if args.max_reprojection_error <= 0:
        raise RuntimeError("--max-reprojection-error must be greater than zero.")
    if args.max_grid_fit_error <= 0:
        raise RuntimeError("--max-grid-fit-error must be greater than zero.")

    images = list_images(input_dir, args.recursive, output_dir)
    if not images:
        raise RuntimeError(f"No supported images found in {input_dir}")

    if output_dir.exists() and not args.keep_output:
        shutil.rmtree(output_dir)
    accepted_dir = output_dir / "detections" / "accepted"
    rejected_dir = output_dir / "detections" / "rejected"
    accepted_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(images)} image(s) in {input_dir}")
    print(f"Detecting a {pattern_size[0]} x {pattern_size[1]} internal-corner grid...")

    frames: list[FrameResult] = []
    image_size: tuple[int, int] | None = None
    for index, path in enumerate(images, start=1):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            frame = FrameResult(path, "rejected", "could not read image")
            frames.append(frame)
            print(f"[{index:03d}/{len(images):03d}] REJECT read failure: {path.name}")
            continue

        current_size = (image.shape[1], image.shape[0])
        if image_size is None:
            image_size = current_size
        if current_size != image_size:
            frame = FrameResult(
                path,
                "rejected",
                f"resolution {current_size[0]}x{current_size[1]} does not match "
                f"{image_size[0]}x{image_size[1]}",
            )
        else:
            corners, detection_method = detect_corners(
                image,
                pattern_size,
                args.detection_max_width,
                args.max_grid_fit_error,
            )
            if corners is None:
                frame = FrameResult(
                    path,
                    "rejected",
                    "checkerboard not detected after retries",
                    detection_method=detection_method,
                )
            else:
                center_x, center_y, coverage = corner_metrics(corners, image_size)
                fit_error = grid_fit_error(corners, args.pattern_cols, args.pattern_rows)
                if fit_error > args.max_grid_fit_error:
                    frame = FrameResult(
                        path,
                        "rejected",
                        f"false/nonuniform grid ({100.0 * fit_error:.2f}% fit error)",
                        corners=corners,
                        detection_method=detection_method,
                        center_x=center_x,
                        center_y=center_y,
                        coverage_percent=coverage,
                        grid_fit_error_percent=100.0 * fit_error,
                    )
                else:
                    frame = FrameResult(
                        path,
                        "accepted",
                        corners=corners,
                        detection_method=detection_method,
                        center_x=center_x,
                        center_y=center_y,
                        coverage_percent=coverage,
                        grid_fit_error_percent=100.0 * fit_error,
                    )
        frames.append(frame)
        if frame.status == "accepted":
            state = "FOUND"
        elif frame.corners is not None:
            state = "FALSE"
        else:
            state = "MISS "
        print(f"[{index:03d}/{len(images):03d}] {state}: {path.name}")

    detected = [frame for frame in frames if frame.status == "accepted"]
    if image_size is None:
        raise RuntimeError("None of the image files could be read.")
    if len(detected) < args.min_views:
        # Still save annotated failures before returning an actionable error.
        for index, frame in enumerate(frames, start=1):
            image = cv2.imread(str(frame.path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            target = accepted_dir if frame.status == "accepted" else rejected_dir
            target_path = target / safe_output_name(frame.path, input_dir, index)
            cv2.imwrite(str(target_path), annotated_image(image, pattern_size, frame))
            frame.annotated_path = target_path.relative_to(output_dir)
        write_frame_report(output_dir / "frame_report.csv", frames, input_dir)
        raise RuntimeError(
            f"Only {len(detected)} checkerboards were detected; at least "
            f"{args.min_views} are required. Review {rejected_dir}"
        )

    object_template = make_object_points(
        args.pattern_cols, args.pattern_rows, args.square_size
    )
    (
        accepted,
        rms,
        matrix,
        distortion,
        rvecs,
        tvecs,
        errors,
    ) = reject_outliers(
        detected,
        object_template,
        image_size,
        args.model,
        args.max_reprojection_error,
        args.min_views,
    )

    for frame, error, rvec in zip(accepted, errors, rvecs):
        frame.status = "accepted"
        frame.reason = "used for calibration"
        frame.reprojection_error = float(error)
        frame.tilt_degrees = rotation_tilt_degrees(rvec)

    # Draw every frame only after final outlier classification so the labels are final.
    for index, frame in enumerate(frames, start=1):
        image = cv2.imread(str(frame.path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        target = accepted_dir if frame.status == "accepted" else rejected_dir
        target_path = target / safe_output_name(frame.path, input_dir, index)
        rendered = annotated_image(image, pattern_size, frame)
        if not cv2.imwrite(str(target_path), rendered, [cv2.IMWRITE_JPEG_QUALITY, 92]):
            raise RuntimeError(f"Could not write annotated image: {target_path}")
        frame.annotated_path = target_path.relative_to(output_dir)

    width, height = image_size
    fx, fy = float(matrix[0, 0]), float(matrix[1, 1])
    cx, cy = float(matrix[0, 2]), float(matrix[1, 2])
    horizontal_fov = math.degrees(2.0 * math.atan(width / (2.0 * fx)))
    vertical_fov = math.degrees(2.0 * math.atan(height / (2.0 * fy)))
    warnings = []
    if not (0.2 <= fx / fy <= 5.0):
        warnings.append("fx/fy ratio is implausible; capture more varied board tilts")
    if not (0.1 * width <= fx <= 10.0 * width and 0.1 * height <= fy <= 10.0 * height):
        warnings.append("focal length is outside the expected range")
    if not (-0.25 * width <= cx <= 1.25 * width and -0.25 * height <= cy <= 1.25 * height):
        warnings.append("principal point is far outside the image")
    if rms > args.max_reprojection_error:
        warnings.append("overall reprojection error exceeds the requested maximum")

    accepted_x = [frame.center_x for frame in accepted if frame.center_x is not None]
    accepted_y = [frame.center_y for frame in accepted if frame.center_y is not None]
    accepted_tilts = [
        frame.tilt_degrees for frame in accepted if frame.tilt_degrees is not None
    ]
    notes = []
    if accepted_x and (min(accepted_x) > 0.15 or max(accepted_x) < 0.85):
        notes.append(
            "accepted checker grids do not reach both far left and far right image regions; "
            "edge distortion accuracy can be improved with additional edge views"
        )
    if accepted_y and (min(accepted_y) > 0.15 or max(accepted_y) < 0.85):
        notes.append(
            "accepted checker grids do not reach both top and bottom image regions; "
            "edge distortion accuracy can be improved with additional edge views"
        )
    if accepted_tilts and max(accepted_tilts) < 15.0:
        warnings.append("board pose diversity is low; add views tilted by at least 20 degrees")

    coefficient_count = 5 if args.model == "standard" else 8
    saved_distortion = np.asarray(distortion).ravel()[:coefficient_count]
    result = {
        "format_version": 1,
        "image_width": width,
        "image_height": height,
        "pattern_internal_columns": args.pattern_cols,
        "pattern_internal_rows": args.pattern_rows,
        "square_size": args.square_size,
        "square_unit": args.square_unit,
        "distortion_model": args.model,
        "distortion_coefficient_order": (
            ["k1", "k2", "p1", "p2", "k3"]
            if args.model == "standard"
            else ["k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6"]
        ),
        "camera_matrix": json_array(matrix),
        "distortion_coefficients": saved_distortion.tolist(),
        "rms_reprojection_error_pixels": float(rms),
        "mean_per_view_error_pixels": float(np.mean(errors)),
        "median_per_view_error_pixels": float(np.median(errors)),
        "maximum_per_view_error_pixels": float(np.max(errors)),
        "horizontal_field_of_view_degrees": horizontal_fov,
        "vertical_field_of_view_degrees": vertical_fov,
        "input_image_count": len(frames),
        "detected_image_count": len(detected),
        "accepted_image_count": len(accepted),
        "rejected_image_count": len(frames) - len(accepted),
        "accepted_center_x_fraction_range": [min(accepted_x), max(accepted_x)],
        "accepted_center_y_fraction_range": [min(accepted_y), max(accepted_y)],
        "accepted_tilt_degrees_range": [min(accepted_tilts), max(accepted_tilts)],
        "notes": notes,
        "warnings": warnings,
    }
    with (output_dir / "camera_calibration.json").open("w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    save_yaml(
        output_dir / "camera_calibration.yaml",
        image_size,
        pattern_size,
        args.square_size,
        args.square_unit,
        args.model,
        rms,
        matrix,
        saved_distortion,
    )
    np.savez_compressed(
        output_dir / "camera_calibration.npz",
        camera_matrix=matrix,
        distortion_coefficients=saved_distortion,
        image_size=np.asarray(image_size),
        rms_reprojection_error=np.asarray(rms),
        rvecs=np.asarray(rvecs),
        tvecs=np.asarray(tvecs),
    )
    write_frame_report(output_dir / "frame_report.csv", frames, input_dir)

    best_index = int(np.argmin(errors))
    best_frame = accepted[best_index]
    sample = cv2.imread(str(best_frame.path), cv2.IMREAD_COLOR)
    if sample is not None:
        new_matrix, roi = cv2.getOptimalNewCameraMatrix(
            matrix, distortion, image_size, 0.0, image_size
        )
        undistorted = cv2.undistort(sample, matrix, distortion, None, new_matrix)
        cv2.imwrite(
            str(output_dir / "undistorted_sample.jpg"),
            undistorted,
            [cv2.IMWRITE_JPEG_QUALITY, 92],
        )
        result["undistorted_sample_source"] = best_frame.path.name
        result["undistorted_valid_roi"] = [int(value) for value in roi]
        with (output_dir / "camera_calibration.json").open("w", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")

    print("\nCalibration complete")
    print(f"  Resolution:           {width} x {height}")
    print(f"  Used views:           {len(accepted)} / {len(frames)}")
    print(f"  RMS error:            {rms:.4f} px")
    print(f"  Mean view error:      {np.mean(errors):.4f} px")
    print(f"  Focal length:         fx={fx:.3f}, fy={fy:.3f} px")
    print(f"  Principal point:      cx={cx:.3f}, cy={cy:.3f} px")
    print(f"  Approximate FOV:      {horizontal_fov:.2f} x {vertical_fov:.2f} degrees")
    print(f"  Results:              {output_dir}")
    print(f"  Detection previews:   {output_dir / 'detections'}")
    if warnings:
        print("  WARNING:")
        for warning in warnings:
            print(f"    - {warning}")
        return 2
    if notes:
        print("  Note:")
        for note in notes:
            print(f"    - {note}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, cv2.error) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
