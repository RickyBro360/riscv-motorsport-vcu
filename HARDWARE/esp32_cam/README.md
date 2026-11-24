# ESP32-CAM — on-board video

`esp32_cam_stream/esp32_cam_stream.ino` streams Motion-JPEG over Wi-Fi to the dashboard's Live Camera page. The camera is an independent device: no CAN, no LoRa, no wiring to the VCU or the Raspberry Pi.

## Flashing

1. Seat the ESP32-CAM on the ESP32-CAM-MB programmer board, micro-USB to the laptop.
2. `WIFI_SSID` / `WIFI_PASSWORD` are already set to the project network (`CTRL`). Change them there if the network changes, and keep this file out of public repos.
3. Arduino IDE → Board **AI Thinker ESP32-CAM**, Partition Scheme **Huge APP (3MB No OTA)** if it does not fit.
4. Upload, then open Serial Monitor at 115200 and press RST. It prints the stream URL.

## Using it

| URL | What |
|---|---|
| `http://<ip>:81/stream` | MJPEG stream — paste this into the dashboard |
| `http://<ip>/` | Test page with the stream embedded |
| `http://<ip>/capture` | One JPEG frame |
| `http://<ip>/status` | JSON: RSSI, uptime, frames sent/dropped, free heap |

## On the car

5V and GND from the logic buck only. Fit 470–1000 µF across its supply close to the module — brown-out during a transmission burst is the usual cause of random reboots.

## Tuning (lag)

The stream is Wi-Fi-bound. In order of effect: close every other viewer/tab; `FRAME_SIZE = FRAMESIZE_QVGA`; `JPEG_QUALITY` 15-20; keep the phone hotspot within a few metres. Expect 100-300 ms glass-to-glass even when tuned — MJPEG over a phone hotspot is not a low-latency FPV link.


- `FRAME_SIZE` — `FRAMESIZE_VGA` (640×480) by default; drop to `FRAMESIZE_QVGA` on a weak link.
- `JPEG_QUALITY` — 10 (best) to 63 (worst); higher numbers mean smaller frames and less Wi-Fi load.
- `s->set_vflip` / `set_hmirror` in `cameraInit()` — set to 1 if the camera is mounted upside down.

Only one viewer at a time is accepted; a second client gets a 503 rather than halving the first one's frame rate. Video is the heaviest traffic in the system — if telemetry gets choppy, close the camera page while checking telemetry rates.

**Not hardware validated.** It compiles (31 % flash) but has never run on a camera.
