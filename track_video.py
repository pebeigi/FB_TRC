#!/usr/bin/env python3
"""Run Ultralytics YOLO tracking on a recorded camera video."""

import argparse

from ultralytics import YOLO


def parse_classes(value):
    if value is None:
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        default="recordings/shenkman-1_20261001_215413.mp4",
        help="Input video path",
    )
    parser.add_argument("--model", default="yolo26x.pt", help="YOLO model checkpoint")
    parser.add_argument("--tracker", default="bytetrack.yaml", help="Tracker config")
    parser.add_argument("--confidence", type=float, default=0.20)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument(
        "--classes",
        default=None,
        help="Comma-separated COCO class IDs; e.g. 0,2,5,7 for person/car/bus/truck",
    )
    parser.add_argument("--device", default="mps", help="Inference device: mps, cpu, or cuda")
    parser.add_argument("--project", default="analysis_runs")
    parser.add_argument("--name", default="tracked_video")
    args = parser.parse_args()

    model = YOLO(args.model)
    model.track(
        source=args.source,
        tracker=args.tracker,
        conf=args.confidence,
        imgsz=args.imgsz,
        classes=parse_classes(args.classes),
        device=args.device,
        save=True,
        project=args.project,
        name=args.name,
        exist_ok=True,
    )


if __name__ == "__main__":
    main()
