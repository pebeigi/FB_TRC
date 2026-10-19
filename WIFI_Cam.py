
import os
import cv2
import getpass
from urllib.parse import quote

# Force RTSP over TCP (required for your port forwarding)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|timeout;5000000"
)

# Network settings
HOST = "161.253.2.2"
PORT = 8554

# Ask for credentials
username = input("Camera username [admin]: ").strip() or "admin"
password = getpass.getpass("Camera password: ")

# Encode special characters in username/password
username = quote(username, safe="")
password = quote(password, safe="")

# Common RTSP paths for different camera manufacturers
RTSP_PATHS = [
    # Hikvision
    "Streaming/Channels/101",
    "Streaming/Channels/102",
    "h264/ch1/main/av_stream",
    "h264/ch1/sub/av_stream",

    # Dahua
    "cam/realmonitor?channel=1&subtype=0",
    "cam/realmonitor?channel=1&subtype=1",

    # Axis
    "axis-media/media.amp",
    "axis-media/media.amp?videocodec=h264",

    # Reolink
    "h264Preview_01_main",
    "h264Preview_01_sub",

    # Amcrest / Foscam / generic
    "videoMain",
    "videoSub",
    "video",
    "live",
    "live/ch00_0",
    "live/ch00_1",
    "stream1",
    "stream2",
    "onvif1",
    "onvif2",

    # Generic RTSP paths
    "media/video1",
    "media/video2",
    "mpeg4",
    "11",
    "12",
    "",
]

def make_rtsp_url(path):
    base = f"rtsp://{username}:{password}@{HOST}:{PORT}"
    return f"{base}/{path}" if path else base + "/"


def find_working_stream():
    print("\nSearching for your camera's RTSP stream...\n")

    for i, path in enumerate(RTSP_PATHS, 1):
        print(f"[{i}/{len(RTSP_PATHS)}] Testing: /{path}", flush=True)

        url = make_rtsp_url(path)

        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            cap.release()
            continue

        # Verify actual video frames can be decoded
        success = False

        for _ in range(5):
            ret, frame = cap.read()

            if ret and frame is not None:
                success = True
                break

        if success:
            print("\nSUCCESS!")
            print(f"Working stream path: /{path}")
            print(f"Camera address: {HOST}:{PORT}")
            return cap, path

        cap.release()

    return None, None


def main():
    cap, path = find_working_stream()

    if cap is None:
        print("\nNo working RTSP stream found.")
        print("Check the camera credentials and RTSP settings.")
        print("Your camera may use a manufacturer-specific path.")
        return

    print("\nDisplaying camera stream.")
    print("Press Q to quit.\n")

    try:
        while True:
            ret, frame = cap.read()

            if not ret:
                print("Stream disconnected.")
                break

            cv2.imshow("IP Camera - Live RTSP", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
