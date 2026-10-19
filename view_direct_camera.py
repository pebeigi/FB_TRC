#!/usr/bin/env python3
"""Display the directly connected Uniview camera's RTSP stream."""

import argparse
import getpass
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

# OpenCV passes these options to FFmpeg when it opens an RTSP stream.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000|rw_timeout;5000000",
)

import cv2


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.1.13")
    parser.add_argument("--port", type=int, default=554)
    parser.add_argument("--path", default="media/video1")
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--output-dir",
        default="calibration_frames/uniview_192_168_1_13",
        help="directory for full-resolution calibration frames",
    )
    parser.add_argument(
        "--auto-interval",
        type=float,
        default=2.0,
        help="seconds between frames while automatic capture is enabled",
    )
    return parser.parse_args()


def rtsp_url(args, password):
    username = quote(args.username, safe="")
    password = quote(password, safe="")
    path = args.path.lstrip("/")
    return f"rtsp://{username}:{password}@{args.host}:{args.port}/{path}"


def open_stream(url):
    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not capture.isOpened():
        capture.release()
        return None
    return capture


def save_frame(frame, output_dir, sequence):
    """Save the unscaled decoded frame as a lossless PNG."""
    height, width = frame.shape[:2]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    path = output_dir / f"calibration_{sequence:03d}_{timestamp}_{width}x{height}.png"
    if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise RuntimeError(f"Could not save calibration frame: {path}")
    print(f"Saved full-resolution frame: {path}")
    return path


def preview_frame(frame, saved_count, automatic):
    """Create a screen-sized preview without modifying the saved frame."""
    height, width = frame.shape[:2]
    scale = min(1.0, 1280 / width, 720 / height)
    if scale < 1.0:
        preview = cv2.resize(
            frame,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    else:
        preview = frame.copy()

    mode = "AUTO ON" if automatic else "AUTO OFF"
    lines = (
        f"Native frame: {width}x{height} | Saved: {saved_count} | {mode}",
        "Space/S: save frame   A: toggle auto capture   Q/Esc: quit",
    )
    for index, line in enumerate(lines):
        y = 30 + index * 30
        cv2.putText(
            preview,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 0),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            preview,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return preview


def main():
    args = parse_args()
    if args.auto_interval <= 0:
        raise RuntimeError("--auto-interval must be greater than zero.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    password = os.environ.get("CAMERA_PASSWORD") or getpass.getpass(
        f"Password for {args.username}@{args.host}: "
    )
    url = rtsp_url(args, password)

    print(f"Connecting to rtsp://{args.host}:{args.port}/{args.path.lstrip('/')} ...")
    print(f"Calibration frames will be saved in: {output_dir.resolve()}")
    print("Press Space or S to save a frame, A for automatic capture, Q/Esc to quit.")

    capture = None
    failures = 0
    saved_count = 0
    automatic = False
    next_automatic_capture = time.monotonic()
    reported_resolution = False

    try:
        while True:
            if capture is None:
                capture = open_stream(url)
                if capture is None:
                    failures += 1
                    if failures >= 3:
                        raise RuntimeError(
                            "Could not open the stream after 3 attempts. "
                            "Check that the camera-facing adapter is 192.168.1.10/24."
                        )
                    print("Connection failed; retrying in 2 seconds...")
                    time.sleep(2)
                    continue

            ok, frame = capture.read()
            if not ok or frame is None:
                capture.release()
                capture = None
                failures += 1
                if failures >= 3:
                    raise RuntimeError("The stream stopped returning video frames.")
                print("Stream interrupted; reconnecting...")
                time.sleep(1)
                continue

            failures = 0
            if not reported_resolution:
                height, width = frame.shape[:2]
                print(f"Receiving native {width}x{height} frames.")
                reported_resolution = True

            now = time.monotonic()
            if automatic and now >= next_automatic_capture:
                saved_count += 1
                save_frame(frame, output_dir, saved_count)
                next_automatic_capture = now + args.auto_interval

            preview = preview_frame(frame, saved_count, automatic)
            cv2.imshow("Uniview Camera - Calibration Capture", preview)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key in (ord("s"), ord(" ")):
                saved_count += 1
                save_frame(frame, output_dir, saved_count)
            elif key == ord("a"):
                automatic = not automatic
                next_automatic_capture = time.monotonic()
                state = "enabled" if automatic else "disabled"
                print(f"Automatic capture {state}.")
    finally:
        if capture is not None:
            capture.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except RuntimeError as error:
        raise SystemExit(f"Error: {error}")
