#!/usr/bin/env python3
"""Local map server. RTSP is converted to MJPEG so the browser can show it."""

import json
import math
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import cv2
import numpy as np

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(WEB_DIR)
FRAMES_DIR = os.path.join(ROOT_DIR, "camera_frames")
COVERAGE_PATH = os.path.join(WEB_DIR, "coverage.json")
LAYOUT_PATH = os.path.join(WEB_DIR, "cameras.json")
STATUS_PATH = os.path.join(WEB_DIR, "camera_status.json")
HOMOGRAPHY_PATH = os.path.join(WEB_DIR, "homographies.json")
HOMOGRAPHY_FRAMES_DIR = os.path.join(ROOT_DIR, "homography_frames")
REFERENCE_PATH = os.path.join(ROOT_DIR, "Map.tif")
GENERATED_DIR = os.path.join(WEB_DIR, "generated")
REFERENCE_PREVIEW_PATH = os.path.join(GENERATED_DIR, "map_preview.jpg")
REFERENCE_PREVIEW_MAX_DIMENSION = 6000
REFERENCE_TILE_DIR = os.path.join(GENERATED_DIR, "map_reference_tiles")
REFERENCE_TILE_SIZE = 512
REFERENCE_TILE_MIN_ZOOM = 5
REFERENCE_TILE_MAX_ZOOM = 6
CAMERA_CALIBRATION_PATH = os.path.join(
    ROOT_DIR,
    "calibration_frames",
    "uniview_tilted",
    "calibration_output",
    "camera_calibration.json",
)

COLORS = {
    "shenkman": "#3b6fe0",
    "fulbright": "#e07a3b",
    "himmelfarb": "#2a9d5c",
    "jbko": "#8b5cc7",
    "gelman": "#d4a017",
    "duques": "#1aa3a3",
    "police": "#d6456a",
    "mfa": "#3d5a80",
}

os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;8000000|rw_timeout;8000000|fflags;nobuffer|flags;low_delay|max_delay;500000",
)

STREAM_MAX_WIDTH = 480
STREAM_JPEG_QUALITY = 40

USERNAME = "admin"
session = {"password": ""}


def offset(lat, lng, heading_deg, meters):
    heading = math.radians(heading_deg)
    lat2 = lat + (meters * math.cos(heading)) / 111320.0
    lng2 = lng + (meters * math.sin(heading)) / (
        111320.0 * math.cos(math.radians(lat))
    )
    return round(lat2, 7), round(lng2, 7)


def cameras(lat, lng, working):
    # Placeholder aim: c1 north, c2 east, c3 south, c4 west.
    layout = ((1, 0), (2, 90), (3, 180), (4, 270))
    items = []
    for channel, heading in layout:
        cam_lat, cam_lng = offset(lat, lng, heading, 22)
        items.append(
            {
                "channel": channel,
                "lat": cam_lat,
                "lng": cam_lng,
                "heading": heading,
                "fov": 70,
                "range_m": 55,
                "working": channel in working,
            }
        )
    return items


SITES = [
    {
        "id": "shenkman",
        "name": "Shenkman Hall",
        "ip": "128.164.58.173",
        "lat": 38.8978927,
        "lng": -77.0504203,
        "height_ft": 98.4,
        "frame_prefix": "Shenkman_Hall",
        "cameras": cameras(38.8978927, -77.0504203, {1, 2, 3}),
    },
    {
        "id": "fulbright",
        "name": "Fullbright Hall",
        "ip": "161.253.27.245",
        "lat": 38.8998309,
        "lng": -77.0498463,
        "height_ft": 86.2,
        "frame_prefix": "Fullbright_Hall",
        "cameras": cameras(38.8998309, -77.0498463, set()),
    },
    {
        "id": "himmelfarb",
        "name": "Himmelfarb Library",
        "ip": "128.164.87.101",
        "lat": 38.9003000,
        "lng": -77.0506000,
        "height_ft": 39.1,
        "frame_prefix": "Himmelfarb_Library",
        "cameras": cameras(38.9003000, -77.0506000, {1, 2}),
    },
    {
        "id": "jbko",
        "name": "JBKO Hall",
        "ip": "161.253.27.247",
        "lat": 38.90056,
        "lng": -77.04972,
        "height_ft": 91.7,
        "frame_prefix": "JBKO_Hall",
        "cameras": cameras(38.90056, -77.04972, {2, 4}),
    },
    {
        "id": "gelman",
        "name": "Gelman Library",
        "ip": "161.253.43.212",
        "lat": 38.8992259,
        "lng": -77.0483775,
        "height_ft": None,
        "frame_prefix": "Gelman_Library1",
        "cameras": cameras(38.8992259, -77.0483775, {1, 2, 3, 4})
        + [
            {
                "channel": 5,
                "lat": 38.8992259,
                "lng": -77.0483775,
                "heading": 0,
                "fov": 70,
                "range_m": 55,
                "working": True,
            }
        ],
        # This Wi-Fi camera is separate from Gelman's four-channel recorder.
        "stream_overrides": {
            5: {
                "host": "161.253.2.2",
                "port": 8554,
                "path": "Streaming/Channels/101",
            }
        },
    },
    {
        "id": "duques",
        "name": "Duques Hall",
        "ip": "128.164.16.236",
        "lat": 38.8989201,
        "lng": -77.0492137,
        "height_ft": 79.4,
        "frame_prefix": "Duques_Hall",
        "cameras": cameras(38.8989201, -77.0492137, {1, 2}),
    },
    {
        "id": "police",
        "name": "Police",
        "ip": "128.164.39.71",
        "lat": 38.8985790,
        "lng": -77.0486403,
        "height_ft": 29.7,
        "frame_prefix": "Police",
        "cameras": cameras(38.8985790, -77.0486403, {1, 2}),
    },
    {
        "id": "mfa",
        "name": "MFA",
        "ip": None,
        "lat": 38.9012537,
        "lng": -77.0485077,
        "height_ft": None,
        "frame_prefix": "MFA",
        "cameras": cameras(38.9012537, -77.0485077, set()),
    },
]

SITES_BY_ID = {site["id"]: site for site in SITES}

stream_lock = threading.Lock()
active_streams = set()
next_stream_id = {"value": 0}
reference_preview_lock = threading.Lock()
reference_tile_lock = threading.Lock()
homography_file_lock = threading.RLock()
layout_file_lock = threading.RLock()


def load_status():
    if not os.path.isfile(STATUS_PATH):
        return {}
    with open(STATUS_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def write_status(data):
    with open(STATUS_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def refresh_cameras():
    jobs = []
    for site in SITES:
        if not site.get("ip"):
            continue
        for camera in site_cameras(site):
            jobs.append((site, camera["channel"]))

    found = {}

    def check(site, channel):
        cap, frame = open_capture(site, channel, timeout_ms=5000)
        if cap is not None:
            cap.release()
        return site["id"], str(channel), frame is not None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(check, site, channel) for site, channel in jobs]
        for future in as_completed(futures):
            site_id, channel, online = future.result()
            found.setdefault(site_id, {})[channel] = online

    for site in SITES:
        if site.get("ip"):
            continue
        found[site["id"]] = {
            str(camera["channel"]): False for camera in site_cameras(site)
        }
    write_status(found)
    return public_sites()


def load_layout():
    with layout_file_lock:
        if not os.path.isfile(LAYOUT_PATH):
            return {"sites": {}}
        with open(LAYOUT_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        data.setdefault("sites", {})
        return data


def write_layout(data):
    with layout_file_lock:
        directory = os.path.dirname(LAYOUT_PATH)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".cameras.", suffix=".tmp", dir=directory, text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, LAYOUT_PATH)
        except Exception:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise


def site_cameras(site):
    layout = load_layout().get("sites", {}).get(site["id"], {})
    chosen = layout.get("cameras")
    templates = {cam["channel"]: cam for cam in site["cameras"]}
    if chosen is None:
        chosen = [
            {"channel": cam["channel"], "lat": None, "lng": None}
            for cam in site["cameras"]
            if cam["working"]
        ]
    items = []
    for item in sorted(chosen, key=lambda cam: cam["channel"]):
        channel = int(item["channel"])
        base = templates.get(
            channel,
            {
                "channel": channel,
                "lat": site["lat"],
                "lng": site["lng"],
                "heading": 0,
                "fov": 70,
                "range_m": 55,
                "working": False,
            },
        )
        placed = item.get("lat") is not None and item.get("lng") is not None
        live = load_status().get(site["id"], {})
        if str(channel) in live:
            working = bool(live[str(channel)])
        else:
            working = bool(base.get("working"))
        items.append(
            {
                **base,
                "channel": channel,
                "lat": item["lat"] if placed else base["lat"],
                "lng": item["lng"] if placed else base["lng"],
                "placed": placed,
                "working": working,
                "stream": bool(stream_settings(site, channel)),
                "still": f"/frames/{site['frame_prefix']}_ch{channel}.jpg"
                if working
                else None,
            }
        )
    return items


def camera_channels(site):
    return {camera["channel"] for camera in site["cameras"]}


def stream_settings(site, channel):
    override = site.get("stream_overrides", {}).get(channel)
    if override:
        return {
            "host": override["host"],
            "port": override.get("port", 554),
            "path": override["path"].lstrip("/"),
        }
    if not site.get("ip"):
        return None
    return {
        "host": site["ip"],
        "port": 554,
        "path": f"unicast/c{channel}/s0/live",
    }


def save_site_cameras(payload):
    site_id = payload.get("site")
    if site_id not in SITES_BY_ID:
        raise ValueError("Unknown building")
    raw = payload.get("cameras")
    if not isinstance(raw, list):
        raise ValueError("Camera list is missing")
    clean = []
    seen = set()
    for item in raw:
        channel = int(item.get("channel"))
        if channel not in camera_channels(SITES_BY_ID[site_id]) or channel in seen:
            raise ValueError("Each camera must be a unique channel for this building")
        seen.add(channel)
        lat, lng = item.get("lat"), item.get("lng")
        if lat is None or lng is None:
            clean.append({"channel": channel, "lat": None, "lng": None})
            continue
        lat, lng = round(float(lat), 7), round(float(lng), 7)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("Camera coordinate is outside the map")
        clean.append({"channel": channel, "lat": lat, "lng": lng})
    clean.sort(key=lambda cam: cam["channel"])
    with layout_file_lock:
        data = load_layout()
        data["sites"][site_id] = {"cameras": clean}
        write_layout(data)
    return {"sites": public_sites()}


def public_sites():
    payload = []
    camera_access = access_state()["unlocked"]
    for site in SITES:
        cameras_out = site_cameras(site)
        if not camera_access:
            cameras_out = [
                {**camera, "working": False, "still": None}
                for camera in cameras_out
            ]
        working = sum(1 for cam in cameras_out if cam["working"])
        payload.append(
            {
                "id": site["id"],
                "name": site["name"],
                "color": COLORS[site["id"]],
                "lat": site["lat"],
                "lng": site["lng"],
                "height_ft": site["height_ft"],
                "stream": bool(site.get("ip")),
                "working": working,
                "total": len(cameras_out),
                "cameras": cameras_out,
            }
        )
    return payload


def load_coverage():
    if not os.path.isfile(COVERAGE_PATH):
        return {"areas": []}
    with open(COVERAGE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("areas", [])
    return data


def write_coverage(data):
    with open(COVERAGE_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def coverage_id(site_id, channel):
    return f"{site_id}-{channel if channel is not None else 'building'}"


def parse_area(payload):
    site_id = payload.get("site")
    if site_id not in SITES_BY_ID:
        raise ValueError("Unknown building")
    channel = payload.get("channel")
    if channel is not None:
        channel = int(channel)
        if channel not in camera_channels(SITES_BY_ID[site_id]):
            raise ValueError("Channel must belong to the selected building")
    ring = payload.get("ring")
    if not isinstance(ring, list) or len(ring) < 3:
        raise ValueError("A coverage area needs at least 3 points")
    clean = []
    for point in ring:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Each point must be [lat, lng]")
        lat, lng = float(point[0]), float(point[1])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("Point is outside the map")
        clean.append([round(lat, 7), round(lng, 7)])
    return {
        "id": coverage_id(site_id, channel),
        "site": site_id,
        "channel": channel,
        "ring": clean,
    }


def upsert_area(area):
    data = load_coverage()
    data["areas"] = [item for item in data["areas"] if item.get("id") != area["id"]]
    data["areas"].append(area)
    data["areas"].sort(key=lambda item: item["id"])
    write_coverage(data)
    return data


def delete_area(area_id):
    data = load_coverage()
    data["areas"] = [item for item in data["areas"] if item.get("id") != area_id]
    write_coverage(data)
    return data


def access_state():
    return {"unlocked": bool(session["password"])}


def homography_camera_id(site_id, channel):
    return f"{site_id}-{int(channel)}"


def camera_frame_path(site, channel):
    os.makedirs(HOMOGRAPHY_FRAMES_DIR, exist_ok=True)
    captured = os.path.join(
        HOMOGRAPHY_FRAMES_DIR,
        f"{site['id']}_ch{int(channel)}.jpg",
    )
    if os.path.isfile(captured):
        return captured, f"/homography-frames/{os.path.basename(captured)}"
    still = os.path.join(FRAMES_DIR, f"{site['frame_prefix']}_ch{int(channel)}.jpg")
    if os.path.isfile(still):
        return still, f"/frames/{os.path.basename(still)}"
    return None, None


def uses_shared_intrinsics(site, channel):
    # Stream overrides identify cameras that are not one of the matching
    # Uniview recorder cameras. Gelman channel 5 is currently the Wi-Fi unit.
    return int(channel) not in site.get("stream_overrides", {})


def load_camera_calibration():
    if not os.path.isfile(CAMERA_CALIBRATION_PATH):
        return None
    with open(CAMERA_CALIBRATION_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    matrix = np.asarray(data.get("camera_matrix"), dtype=np.float64)
    distortion = np.asarray(data.get("distortion_coefficients"), dtype=np.float64)
    if matrix.shape != (3, 3) or distortion.size < 4:
        raise ValueError("Camera calibration file has invalid matrix dimensions")
    return data, matrix, distortion


def homography_frame(site, channel):
    raw_path, _ = camera_frame_path(site, channel)
    if raw_path is None:
        raise ValueError("No frame is available for this camera")
    frame = cv2.imread(raw_path, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("The saved camera frame could not be read")
    coordinate_space = "raw"
    if uses_shared_intrinsics(site, channel):
        calibration = load_camera_calibration()
        if calibration is not None:
            data, matrix, distortion = calibration
            calibration_width = int(data["image_width"])
            calibration_height = int(data["image_height"])
            height, width = frame.shape[:2]
            scaled_matrix = matrix.copy()
            scaled_matrix[0, 0] *= width / calibration_width
            scaled_matrix[0, 2] *= width / calibration_width
            scaled_matrix[1, 1] *= height / calibration_height
            scaled_matrix[1, 2] *= height / calibration_height
            new_matrix, _ = cv2.getOptimalNewCameraMatrix(
                scaled_matrix,
                distortion,
                (width, height),
                0.0,
                (width, height),
            )
            frame = cv2.undistort(
                frame,
                scaled_matrix,
                distortion,
                None,
                new_matrix,
            )
            coordinate_space = "undistorted"
    return frame, raw_path, coordinate_space


def homography_cameras():
    items = []
    for site in SITES:
        for camera in sorted(site["cameras"], key=lambda item: item["channel"]):
            channel = int(camera["channel"])
            frame_path, _ = camera_frame_path(site, channel)
            frame_url = None
            if frame_path:
                version = int(os.path.getmtime(frame_path))
                frame_url = (
                    f"/homography/frame/{site['id']}/{channel}.jpg?v={version}"
                )
            coordinate_space = (
                "undistorted"
                if frame_path
                and uses_shared_intrinsics(site, channel)
                and os.path.isfile(CAMERA_CALIBRATION_PATH)
                else "raw"
            )
            items.append(
                {
                    "id": homography_camera_id(site["id"], channel),
                    "site": site["id"],
                    "site_name": site["name"],
                    "channel": channel,
                    "label": f"{site['name']} - Camera {channel}",
                    "frame_url": frame_url,
                    "frame_name": os.path.basename(frame_path) if frame_path else None,
                    "coordinate_space": coordinate_space,
                    "can_capture": stream_settings(site, channel) is not None,
                }
            )
    return items


def reference_metadata():
    if not os.path.isfile(REFERENCE_PATH):
        raise ValueError(f"Reference image is missing: {REFERENCE_PATH}")
    try:
        import rasterio
    except ImportError as exc:
        raise ValueError("rasterio is required to read the reference TIFF") from exc
    with rasterio.open(REFERENCE_PATH) as dataset:
        transform = dataset.transform
        cache_version = int(os.path.getmtime(REFERENCE_PATH))
        return {
            "name": os.path.basename(REFERENCE_PATH),
            "width": dataset.width,
            "height": dataset.height,
            "bands": dataset.count,
            "crs": str(dataset.crs) if dataset.crs else None,
            "transform": [
                transform.a,
                transform.b,
                transform.c,
                transform.d,
                transform.e,
                transform.f,
            ],
            "preview_url": f"/homography/reference.jpg?v={cache_version}",
            "tile_url_template": (
                "/homography/reference-tile/{z}/{x}/{y}.jpg"
                f"?v={cache_version}"
            ),
            "tile_size": REFERENCE_TILE_SIZE,
            "tile_min_zoom": REFERENCE_TILE_MIN_ZOOM,
            "tile_max_zoom": REFERENCE_TILE_MAX_ZOOM,
            "tile_activation_scale": 0.22,
        }


def ensure_reference_preview():
    if os.path.isfile(REFERENCE_PREVIEW_PATH):
        return REFERENCE_PREVIEW_PATH
    with reference_preview_lock:
        if os.path.isfile(REFERENCE_PREVIEW_PATH):
            return REFERENCE_PREVIEW_PATH
        try:
            import rasterio
            from rasterio.enums import Resampling
        except ImportError as exc:
            raise ValueError("rasterio is required to create the TIFF preview") from exc
        os.makedirs(GENERATED_DIR, exist_ok=True)
        with rasterio.open(REFERENCE_PATH) as dataset:
            scale = min(
                1.0,
                REFERENCE_PREVIEW_MAX_DIMENSION / max(dataset.width, dataset.height),
            )
            output_width = max(1, round(dataset.width * scale))
            output_height = max(1, round(dataset.height * scale))
            band_indexes = list(range(1, min(dataset.count, 3) + 1))
            pixels = dataset.read(
                band_indexes,
                out_shape=(len(band_indexes), output_height, output_width),
                resampling=Resampling.bilinear,
            )
        if pixels.shape[0] == 1:
            rgb = np.repeat(pixels, 3, axis=0)
        else:
            rgb = pixels[:3]
        bgr = np.transpose(rgb[[2, 1, 0]], (1, 2, 0))
        if not cv2.imwrite(
            REFERENCE_PREVIEW_PATH,
            bgr,
            [int(cv2.IMWRITE_JPEG_QUALITY), 90],
        ):
            raise ValueError("Could not write the reference preview")
    return REFERENCE_PREVIEW_PATH


def reference_tile_geometry(reference, zoom):
    if not (REFERENCE_TILE_MIN_ZOOM <= zoom <= REFERENCE_TILE_MAX_ZOOM):
        raise ValueError("Reference tile zoom is outside the supported range")
    source_pixels_per_tile_pixel = 2 ** (REFERENCE_TILE_MAX_ZOOM - zoom)
    source_tile_span = REFERENCE_TILE_SIZE * source_pixels_per_tile_pixel
    columns = math.ceil(reference["width"] / source_tile_span)
    rows = math.ceil(reference["height"] / source_tile_span)
    return source_pixels_per_tile_pixel, source_tile_span, columns, rows


def reference_tile_path(zoom, tile_x, tile_y):
    return os.path.join(
        REFERENCE_TILE_DIR,
        f"z{zoom}",
        f"x{tile_x}_y{tile_y}.jpg",
    )


def ensure_reference_tile(zoom, tile_x, tile_y):
    reference = reference_metadata()
    factor, source_span, columns, rows = reference_tile_geometry(reference, zoom)
    if not (0 <= tile_x < columns and 0 <= tile_y < rows):
        raise ValueError("Reference tile coordinate is outside the image")
    requested_path = reference_tile_path(zoom, tile_x, tile_y)
    if os.path.isfile(requested_path):
        return requested_path

    # The source GeoTIFF is stored as full-width scanline strips. Reading one
    # full tile row and caching every column avoids decompressing the same TIFF
    # rows once per horizontal tile.
    with reference_tile_lock:
        if os.path.isfile(requested_path):
            return requested_path
        try:
            import rasterio
            from rasterio.enums import Resampling
            from rasterio.windows import Window
        except ImportError as exc:
            raise ValueError("rasterio is required to create TIFF detail tiles") from exc

        source_y = tile_y * source_span
        source_height = min(source_span, reference["height"] - source_y)
        output_height = math.ceil(source_height / factor)
        output_width = math.ceil(reference["width"] / factor)
        with rasterio.open(REFERENCE_PATH) as dataset:
            band_indexes = list(range(1, min(dataset.count, 3) + 1))
            row_pixels = dataset.read(
                band_indexes,
                window=Window(0, source_y, reference["width"], source_height),
                out_shape=(len(band_indexes), output_height, output_width),
                resampling=Resampling.bilinear,
            )
        if row_pixels.shape[0] == 1:
            row_pixels = np.repeat(row_pixels, 3, axis=0)
        row_bgr = np.transpose(row_pixels[[2, 1, 0]], (1, 2, 0))
        zoom_dir = os.path.join(REFERENCE_TILE_DIR, f"z{zoom}")
        os.makedirs(zoom_dir, exist_ok=True)
        for column in range(columns):
            start_x = column * REFERENCE_TILE_SIZE
            end_x = min(start_x + REFERENCE_TILE_SIZE, output_width)
            tile = row_bgr[:, start_x:end_x]
            path = reference_tile_path(zoom, column, tile_y)
            if not cv2.imwrite(
                path,
                tile,
                [int(cv2.IMWRITE_JPEG_QUALITY), 94],
            ):
                raise ValueError(f"Could not write reference tile {column}, {tile_y}")
        if not os.path.isfile(requested_path):
            raise ValueError("Requested reference tile was not generated")
    return requested_path


def load_homographies():
    if not os.path.isfile(HOMOGRAPHY_PATH):
        return {
            "version": 2,
            "reference": reference_metadata(),
            "cameras": {},
            "point_sets": {},
        }
    with open(HOMOGRAPHY_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        data = {}
    data["version"] = 2
    data["reference"] = reference_metadata()
    data.setdefault("cameras", {})
    data.setdefault("point_sets", {})
    return data


def write_homographies(data):
    with homography_file_lock:
        temporary_path = HOMOGRAPHY_PATH + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(temporary_path, HOMOGRAPHY_PATH)


def homography_payload():
    data = load_homographies()
    return {
        "reference": data["reference"],
        "cameras": homography_cameras(),
        "calibrations": data["cameras"],
        "point_sets": data["point_sets"],
        "access": access_state(),
    }


def parse_homography_points(payload, minimum_points=0):
    camera_id = str(payload.get("camera_id", ""))
    cameras_by_id = {item["id"]: item for item in homography_cameras()}
    camera = cameras_by_id.get(camera_id)
    if camera is None:
        raise ValueError("Unknown camera")
    source_width = int(payload.get("source_width", 0))
    source_height = int(payload.get("source_height", 0))
    if source_width <= 0 or source_height <= 0:
        raise ValueError("Camera frame dimensions are invalid")
    raw_points = payload.get("points")
    if not isinstance(raw_points, list) or len(raw_points) < minimum_points:
        raise ValueError(
            f"At least {minimum_points} matched point pairs are required"
        )

    reference = reference_metadata()
    clean_points = []
    camera_points = []
    map_points = []
    for index, pair in enumerate(raw_points, start=1):
        if not isinstance(pair, dict):
            raise ValueError(f"Point pair {index} is invalid")
        camera_point = pair.get("camera")
        map_point = pair.get("map")
        if (
            not isinstance(camera_point, (list, tuple))
            or len(camera_point) != 2
            or not isinstance(map_point, (list, tuple))
            or len(map_point) != 2
        ):
            raise ValueError(f"Point pair {index} must contain camera and map [x, y]")
        camera_x, camera_y = map(float, camera_point)
        map_x, map_y = map(float, map_point)
        values = (camera_x, camera_y, map_x, map_y)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Point pair {index} contains a non-finite coordinate")
        if not (0 <= camera_x < source_width and 0 <= camera_y < source_height):
            raise ValueError(f"Camera point {index} is outside the frame")
        if not (0 <= map_x < reference["width"] and 0 <= map_y < reference["height"]):
            raise ValueError(f"Map point {index} is outside the reference image")
        camera_points.append([camera_x, camera_y])
        map_points.append([map_x, map_y])
        clean_points.append(
            {
                "camera": [round(camera_x, 3), round(camera_y, 3)],
                "map": [round(map_x, 3), round(map_y, 3)],
            }
        )

    return (
        camera_id,
        camera,
        source_width,
        source_height,
        reference,
        clean_points,
        np.asarray(camera_points, dtype=np.float64),
        np.asarray(map_points, dtype=np.float64),
    )


def parse_homography(payload):
    (
        camera_id,
        camera,
        source_width,
        source_height,
        reference,
        clean_points,
        camera_array,
        map_array,
    ) = parse_homography_points(payload, minimum_points=4)

    method = cv2.RANSAC if len(clean_points) > 4 else 0
    matrix, mask = cv2.findHomography(
        camera_array,
        map_array,
        method=method,
        ransacReprojThreshold=10.0,
    )
    if matrix is None or not np.all(np.isfinite(matrix)):
        raise ValueError("Homography could not be computed; choose better-spread points")
    projected = cv2.perspectiveTransform(
        camera_array.reshape(-1, 1, 2), matrix
    ).reshape(-1, 2)
    errors = np.linalg.norm(projected - map_array, axis=1)
    inliers = (
        mask.ravel().astype(bool)
        if mask is not None
        else np.ones(len(clean_points), dtype=bool)
    )
    if int(inliers.sum()) < 4:
        raise ValueError("Fewer than four point pairs agree; review the point ordering")
    inlier_errors = errors[inliers]
    transform = reference["transform"]
    pixel_to_crs = np.asarray(
        [
            [transform[0], transform[1], transform[2]],
            [transform[3], transform[4], transform[5]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    camera_to_crs = pixel_to_crs @ matrix
    projected_crs = cv2.perspectiveTransform(
        projected.reshape(-1, 1, 2), pixel_to_crs
    ).reshape(-1, 2)
    target_crs = cv2.perspectiveTransform(
        map_array.reshape(-1, 1, 2), pixel_to_crs
    ).reshape(-1, 2)
    crs_errors = np.linalg.norm(projected_crs - target_crs, axis=1)[inliers]
    return camera_id, {
        "camera_id": camera_id,
        "site": camera["site"],
        "site_name": camera["site_name"],
        "channel": camera["channel"],
        "source_image": payload.get("source_image") or camera.get("frame_name"),
        "source_width": source_width,
        "source_height": source_height,
        "reference_image": reference["name"],
        "reference_width": reference["width"],
        "reference_height": reference["height"],
        "points": clean_points,
        "coordinate_space": camera.get("coordinate_space", "raw"),
        "homography": matrix.tolist(),
        "homography_camera_to_reference_pixels": matrix.tolist(),
        "reference_crs": reference["crs"],
        "reference_pixel_to_crs": pixel_to_crs.tolist(),
        "homography_camera_to_crs": camera_to_crs.tolist(),
        "inlier_mask": [bool(value) for value in inliers],
        "inlier_count": int(inliers.sum()),
        "point_count": len(clean_points),
        "rmse_reference_pixels": float(
            np.sqrt(np.mean(inlier_errors * inlier_errors))
        ),
        "maximum_error_reference_pixels": float(np.max(inlier_errors)),
        "rmse_crs_units": float(np.sqrt(np.mean(crs_errors * crs_errors))),
        "maximum_error_crs_units": float(np.max(crs_errors)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_homography(payload):
    camera_id, calibration = parse_homography(payload)
    with homography_file_lock:
        data = load_homographies()
        data["cameras"][camera_id] = calibration
        data["point_sets"][camera_id] = {
            "camera_id": camera_id,
            "source_image": calibration["source_image"],
            "source_width": calibration["source_width"],
            "source_height": calibration["source_height"],
            "points": calibration["points"],
            "revision": int(payload.get("revision", 0)),
            "updated_at": calibration["updated_at"],
        }
        write_homographies(data)
    return calibration


def save_homography_points(payload):
    (
        camera_id,
        _,
        source_width,
        source_height,
        _,
        clean_points,
        _,
        _,
    ) = parse_homography_points(payload)
    revision = int(payload.get("revision", 0))
    point_set = {
        "camera_id": camera_id,
        "source_image": payload.get("source_image"),
        "source_width": source_width,
        "source_height": source_height,
        "points": clean_points,
        "revision": revision,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with homography_file_lock:
        data = load_homographies()
        existing = data["point_sets"].get(camera_id)
        if existing and revision and int(existing.get("revision", 0)) > revision:
            return existing
        data["point_sets"][camera_id] = point_set
        write_homographies(data)
    return point_set


def delete_homography(camera_id):
    with homography_file_lock:
        data = load_homographies()
        removed_calibration = data["cameras"].pop(camera_id, None)
        write_homographies(data)
    return {
        "deleted": removed_calibration is not None,
        "camera_id": camera_id,
    }


def capture_homography_frame(payload):
    if not access_state()["unlocked"]:
        raise PermissionError("Camera access is not unlocked")
    site_id = str(payload.get("site", ""))
    site = SITES_BY_ID.get(site_id)
    if site is None:
        raise ValueError("Unknown building")
    channel = int(payload.get("channel", 0))
    if channel not in camera_channels(site):
        raise ValueError("Unknown camera channel")
    capture, frame = open_capture(site, channel, timeout_ms=8000)
    if capture is not None:
        capture.release()
    if frame is None:
        raise ValueError("Could not capture a frame from this camera")
    os.makedirs(HOMOGRAPHY_FRAMES_DIR, exist_ok=True)
    path = os.path.join(HOMOGRAPHY_FRAMES_DIR, f"{site_id}_ch{channel}.jpg")
    if not cv2.imwrite(path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
        raise ValueError("Could not save the captured frame")
    return {
        "camera_id": homography_camera_id(site_id, channel),
        "frame_url": f"/homography/frame/{site_id}/{channel}.jpg?v={int(datetime.now().timestamp())}",
        "frame_name": os.path.basename(path),
        "coordinate_space": (
            "undistorted"
            if uses_shared_intrinsics(site, channel)
            and os.path.isfile(CAMERA_CALIBRATION_PATH)
            else "raw"
        ),
        "width": frame.shape[1],
        "height": frame.shape[0],
    }


def open_capture(site, channel, timeout_ms=8000):
    password = session["password"]
    settings = stream_settings(site, channel)
    if settings is None or not password:
        return None, None
    url = (
        f"rtsp://{quote(USERNAME, safe='')}:"
        f"{quote(password, safe='')}@{settings['host']}:{settings['port']}/"
        f"{settings['path']}"
    )
    passwords = [password]
    for password in passwords:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                return cap, frame
        cap.release()
    return None, None


def shrink(frame, max_width):
    height, width = frame.shape[:2]
    if width <= max_width:
        return frame
    scale = max_width / width
    return cv2.resize(
        frame,
        (max_width, max(1, int(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def latest_frame(cap):
    grabbed = False
    for _ in range(4):
        if not cap.grab():
            break
        grabbed = True
    if not grabbed:
        return None
    ok, frame = cap.retrieve()
    if not ok or frame is None:
        return None
    return frame


def begin_stream():
    with stream_lock:
        next_stream_id["value"] += 1
        token = next_stream_id["value"]
        active_streams.add(token)
        return token


def end_stream(token):
    with stream_lock:
        active_streams.discard(token)


def mjpeg(site, channel, token, max_width, quality):
    cap, frame = open_capture(site, channel)
    if cap is None:
        blank = np.zeros((360, 640, 3), dtype="uint8")
        cv2.putText(
            blank,
            "Stream unavailable",
            (150, 190),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        ok, jpg = cv2.imencode(".jpg", blank)
        if ok:
            data = jpg.tobytes()
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
            )
        return

    try:
        while True:
            with stream_lock:
                if token not in active_streams:
                    break
            if frame is None:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
            else:
                ok = True
            ok_jpg, jpg = cv2.imencode(
                ".jpg",
                shrink(frame, max_width),
                [int(cv2.IMWRITE_JPEG_QUALITY), quality],
            )
            frame = None
            if not ok_jpg:
                continue
            data = jpg.tobytes()
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
            frame = latest_frame(cap)
    finally:
        cap.release()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith("GET /stream"):
            return
        super().log_message(fmt, *args)

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode() or "{}")

    def send_file(self, file_path, content_type, cache_control="no-cache"):
        if not os.path.isfile(file_path):
            self.send_error(404)
            return
        with open(file_path, "rb") as handle:
            data = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/access":
            try:
                payload = self.read_json()
            except (TypeError, json.JSONDecodeError):
                self.send_json({"error": "Password is required"}, status=400)
                return
            password = payload.get("password", "")
            if not isinstance(password, str) or not password.strip():
                self.send_json({"error": "Password is required"}, status=400)
                return
            session["password"] = password
            self.send_json(access_state())
            return
        if path == "/api/refresh":
            if not access_state()["unlocked"]:
                self.send_json({"error": "Camera access is not unlocked"}, status=403)
                return
            self.send_json(refresh_cameras())
            return
        if path == "/api/cameras":
            try:
                self.send_json(save_site_cameras(self.read_json()))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if path == "/api/homography":
            try:
                self.send_json(save_homography(self.read_json()))
            except (ValueError, TypeError, json.JSONDecodeError, cv2.error) as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if path == "/api/homography/points":
            try:
                self.send_json(save_homography_points(self.read_json()))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if path == "/api/homography/snapshot":
            try:
                self.send_json(capture_homography_frame(self.read_json()))
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, status=403)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if path != "/api/coverage":
            self.send_error(404)
            return
        try:
            area = parse_area(self.read_json())
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json(upsert_area(area))

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/homography/"):
            camera_id = path.split("/")[-1]
            self.send_json(delete_homography(camera_id))
            return
        if not path.startswith("/api/coverage/"):
            self.send_error(404)
            return
        area_id = path.split("/")[-1]
        self.send_json(delete_area(area_id))

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/access":
            self.send_json(access_state())
            return
        if path == "/api/coverage":
            self.send_json(load_coverage())
            return
        if path == "/api/homography":
            try:
                self.send_json(homography_payload())
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, status=500)
            return

        if path == "/homography/reference.jpg":
            try:
                preview_path = ensure_reference_preview()
            except (ValueError, OSError, cv2.error) as exc:
                self.send_json({"error": str(exc)}, status=500)
                return
            self.send_file(preview_path, "image/jpeg", "public, max-age=86400")
            return

        if path.startswith("/homography/reference-tile/"):
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[:2] != ["homography", "reference-tile"]:
                self.send_error(404)
                return
            try:
                zoom = int(parts[2])
                tile_x = int(parts[3])
                tile_y = int(os.path.splitext(parts[4])[0])
                tile_path = ensure_reference_tile(zoom, tile_x, tile_y)
            except (ValueError, OSError, cv2.error) as exc:
                self.send_json({"error": str(exc)}, status=404)
                return
            self.send_file(tile_path, "image/jpeg", "public, max-age=31536000")
            return

        if path.startswith("/homography/frame/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["homography", "frame"]:
                self.send_error(404)
                return
            site = SITES_BY_ID.get(parts[2])
            try:
                channel = int(os.path.splitext(parts[3])[0])
            except ValueError:
                self.send_error(404)
                return
            if site is None or channel not in camera_channels(site):
                self.send_error(404)
                return
            try:
                frame, _, coordinate_space = homography_frame(site, channel)
                ok, encoded = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95]
                )
                if not ok:
                    raise ValueError("Could not encode the camera frame")
            except (ValueError, OSError, cv2.error) as exc:
                self.send_json({"error": str(exc)}, status=404)
                return
            data = encoded.tobytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Camera-Coordinate-Space", coordinate_space)
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/sites":
            body = json.dumps(public_sites()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/frames/"):
            name = os.path.basename(path)
            file_path = os.path.join(FRAMES_DIR, name)
            self.send_file(file_path, "image/jpeg")
            return

        if path.startswith("/homography-frames/"):
            name = os.path.basename(path)
            file_path = os.path.join(HOMOGRAPHY_FRAMES_DIR, name)
            self.send_file(file_path, "image/jpeg")
            return

        if path.startswith("/stream/"):
            if not access_state()["unlocked"]:
                self.send_json({"error": "Camera access is not unlocked"}, status=403)
                return
            parts = path.strip("/").split("/")
            if len(parts) != 3 or parts[0] != "stream":
                self.send_error(404)
                return
            site = SITES_BY_ID.get(parts[1])
            try:
                channel = int(parts[2])
            except ValueError:
                self.send_error(404)
                return
            if (
                site is None
                or stream_settings(site, channel) is None
                or channel not in camera_channels(site)
            ):
                self.send_error(404)
                return
            wall = parse_qs(urlparse(self.path).query).get("view", [""])[0] == "wall"
            max_width = 320 if wall else STREAM_MAX_WIDTH
            quality = 30 if wall else STREAM_JPEG_QUALITY
            token = begin_stream()
            self.send_response(200)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=frame"
            )
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for chunk in mjpeg(site, channel, token, max_width, quality):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                end_stream(token)
            return

        if path == "/":
            self.path = "/index.html"
        super().do_GET()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Map is at http://127.0.0.1:8765")
    server.serve_forever()


if __name__ == "__main__":
    main()
