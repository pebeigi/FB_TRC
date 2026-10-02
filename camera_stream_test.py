import os
import getpass
import cv2
import time
import pandas as pd
from urllib.parse import quote

# =========================
# CONFIG
# =========================

LOCATIONS = {
    "128.164.58.173": "Shenkman Hall",
    "161.253.27.245": "Fullbright Hall",
    "128.164.87.101": "Himmelfarb Library",
    "161.253.27.247": "JBKO Hall",
    "161.253.43.212": "Gelman Library1",
    "128.164.16.236": "Duques Hall",
    "128.164.39.71": "Police",
}

username = "admin"
password = getpass.getpass("GW camera password: ")

# Try channels c1 -> c4
channels = [1, 2, 3, 4]

OUTPUT_DIR = "camera_frames"

# =========================
# RESULTS TABLE
# =========================

os.makedirs(OUTPUT_DIR, exist_ok=True)
results = []

# =========================
# TEST STREAMS
# =========================

for ip, name in LOCATIONS.items():

    file_prefix = name.replace(" ", "_")

    print("\n" + "=" * 70)
    print(f"TESTING: {name} ({ip})")
    print("=" * 70)

    for channel in channels:

        path = f"unicast/c{channel}/s0/live"
        rtsp_url = (
            f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{ip}:554/{path}"
        )

        print(f"\nTrying c{channel}")
        print("Trying camera credentials")

        stream = cv2.VideoCapture(rtsp_url)
        time.sleep(1)

        if not stream.isOpened():
            print("FAILED: Could not open stream")
            results.append({
                "Location": name,
                "IP": ip,
                "Channel": channel,
                "Path": path,
                "Status": "OPEN FAILED",
            })
            stream.release()
            continue

        ret, frame = stream.read()

        if not ret or frame is None:
            print("FAILED: No frames received")
            results.append({
                "Location": name,
                "IP": ip,
                "Channel": channel,
                "Path": path,
                "Status": "NO FRAME",
            })
            stream.release()
            continue

        frame_path = os.path.join(OUTPUT_DIR, f"{file_prefix}_ch{channel}.jpg")
        cv2.imwrite(frame_path, frame)
        print(f"SUCCESS! Saved frame to {frame_path}")

        results.append({
            "Location": name,
            "IP": ip,
            "Channel": channel,
            "Path": path,
            "Status": "WORKING",
            "Frame": frame_path,
        })

        stream.release()
        break

# =========================
# SUMMARY TABLE
# =========================

df = pd.DataFrame(results)

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)

print(df)

# Optional CSV export
df.to_csv("camera_test_summary.csv", index=False)

print(f"\nSaved frames to {OUTPUT_DIR}/")
print("Saved summary to camera_test_summary.csv")
