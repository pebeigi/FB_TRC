#!/usr/bin/env python3
"""Local map server. RTSP is converted to MJPEG so the browser can show it."""

import json
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import cv2

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMES_DIR = os.path.join(os.path.dirname(WEB_DIR), "camera_frames")
COVERAGE_PATH = os.path.join(WEB_DIR, "coverage.json")
LAYOUT_PATH = os.path.join(WEB_DIR, "cameras.json")
STATUS_PATH = os.path.join(WEB_DIR, "camera_status.json")

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
        "frame_prefix": "Shenkman_Hall",
        "cameras": cameras(38.8978927, -77.0504203, {1, 2, 3}),
    },
    {
        "id": "fulbright",
        "name": "Fullbright Hall",
        "ip": "161.253.27.245",
        "lat": 38.8998309,
        "lng": -77.0498463,
        "frame_prefix": "Fullbright_Hall",
        "cameras": cameras(38.8998309, -77.0498463, set()),
    },
    {
        "id": "himmelfarb",
        "name": "Himmelfarb Library",
        "ip": "128.164.87.101",
        "lat": 38.9003000,
        "lng": -77.0506000,
        "frame_prefix": "Himmelfarb_Library",
        "cameras": cameras(38.9003000, -77.0506000, {1, 2}),
    },
    {
        "id": "jbko",
        "name": "JBKO Hall",
        "ip": "161.253.27.247",
        "lat": 38.90056,
        "lng": -77.04972,
        "frame_prefix": "JBKO_Hall",
        "cameras": cameras(38.90056, -77.04972, {2, 4}),
    },
    {
        "id": "gelman",
        "name": "Gelman Library",
        "ip": "161.253.43.212",
        "lat": 38.8992259,
        "lng": -77.0483775,
        "frame_prefix": "Gelman_Library1",
        "cameras": cameras(38.8992259, -77.0483775, {1, 2, 3, 4}),
    },
    {
        "id": "duques",
        "name": "Duques Hall",
        "ip": "128.164.16.236",
        "lat": 38.8989201,
        "lng": -77.0492137,
        "frame_prefix": "Duques_Hall",
        "cameras": cameras(38.8989201, -77.0492137, {1, 2}),
    },
    {
        "id": "police",
        "name": "Police",
        "ip": "128.164.39.71",
        "lat": 38.8985790,
        "lng": -77.0486403,
        "frame_prefix": "Police",
        "cameras": cameras(38.8985790, -77.0486403, {1, 2}),
    },
    {
        "id": "mfa",
        "name": "MFA",
        "ip": None,
        "lat": 38.9012537,
        "lng": -77.0485077,
        "frame_prefix": "MFA",
        "cameras": cameras(38.9012537, -77.0485077, set()),
    },
]

SITES_BY_ID = {site["id"]: site for site in SITES}

stream_lock = threading.Lock()
active_streams = set()
next_stream_id = {"value": 0}


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
    if not os.path.isfile(LAYOUT_PATH):
        return {"sites": {}}
    with open(LAYOUT_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("sites", {})
    return data


def write_layout(data):
    with open(LAYOUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


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
                "stream": bool(site.get("ip")),
                "still": f"/frames/{site['frame_prefix']}_ch{channel}.jpg"
                if working
                else None,
            }
        )
    return items


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
        if channel not in {1, 2, 3, 4} or channel in seen:
            raise ValueError("Each camera must be a unique channel from 1 to 4")
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
        if channel not in {1, 2, 3, 4}:
            raise ValueError("Channel must be 1 to 4")
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


def open_capture(site, channel, timeout_ms=8000):
    password = session["password"]
    if not site.get("ip") or not password:
        return None, None
    path = f"unicast/c{channel}/s0/live"
    url = (
        f"rtsp://{quote(USERNAME, safe='')}:"
        f"{quote(password, safe='')}@{site['ip']}:554/{path}"
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
        blank = cv2.zeros((360, 640, 3), dtype="uint8")
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
            if not os.path.isfile(file_path):
                self.send_error(404)
                return
            with open(file_path, "rb") as handle:
                data = handle.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
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
            if site is None or not site.get("ip") or channel not in {1, 2, 3, 4}:
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
