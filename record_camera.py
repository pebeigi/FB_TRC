#!/usr/bin/env python3
"""Record one minute from a GW RTSP camera without storing credentials."""

import argparse
import getpass
import os
import time
from datetime import datetime
from urllib.parse import quote

import cv2


CAMERAS = {
    "shenkman-1": ("Shenkman Hall camera 1", "128.164.58.173", 1),
    "shenkman-2": ("Shenkman Hall camera 2", "128.164.58.173", 2),
    "shenkman-3": ("Shenkman Hall camera 3", "128.164.58.173", 3),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", choices=sorted(CAMERAS), default="shenkman-1")
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()

    label, ip, channel = CAMERAS[args.camera]
    username = "admin"
    password = getpass.getpass(f"GW camera password for {label}: ")
    rtsp_url = (
        f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}"
        f"@{ip}:554/unicast/c{channel}/s0/live"
    )

    capture = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
    capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
    if not capture.isOpened():
        raise SystemExit("Could not open the camera stream.")

    ok, frame = capture.read()
    if not ok or frame is None:
        capture.release()
        raise SystemExit("The camera opened but did not provide a frame.")

    height, width = frame.shape[:2]
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not fps or fps < 1 or fps > 120:
        fps = 15.0

    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"{args.camera}_{timestamp}.mp4")
    writer = cv2.VideoWriter(
        output_path,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise SystemExit("Could not create the MP4 output file.")

    deadline = time.monotonic() + args.seconds
    frames = 0
    try:
        while time.monotonic() < deadline:
            if frames:
                ok, frame = capture.read()
            if not ok or frame is None:
                print("Stream ended before the recording finished.")
                break
            writer.write(frame)
            frames += 1
            if frames % max(1, int(fps)) == 0:
                remaining = max(0, int(deadline - time.monotonic()))
                print(f"Recording {label}: {remaining}s remaining", end="\r", flush=True)
    finally:
        writer.release()
        capture.release()

    print(f"\nSaved {frames} frames to {output_path}")


if __name__ == "__main__":
    main()
