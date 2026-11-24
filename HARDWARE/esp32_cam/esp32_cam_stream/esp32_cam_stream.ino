/*
 * =============================================================================
 * esp32_cam_stream.ino — on-board camera for the RC car (ESP32-CAM, AI Thinker)
 * =============================================================================
 * Project : AI-Enabled RISC-V Motorsport Electronics Platform (1:10 RC car)
 *
 * ROLE
 *   Streams Motion-JPEG over Wi-Fi straight to the dashboard's Live Camera
 *   page. It is an INDEPENDENT device: no CAN, no LoRa, no connection to the
 *   VCU or the Raspberry Pi. Video never travels over the LoRa link (5 Hz of
 *   telemetry is the entire budget there).
 *
 *   FW 2.2: the camera ANNOUNCES itself to the engineer-station backend every 10 s
 *   (POST http://BACKEND_HOST:8000/api/camera/announce). The backend relay then connects
 *   by itself and follows a new DHCP address — nothing to copy from the serial monitor.
 *   Manual fallback (Camera page):   http://<this-ip>:81/stream
 *   Status / single frame:    http://<this-ip>/          and  /capture
 *
 * WHY NOT THE STOCK CameraWebServer EXAMPLE
 *   This is the same idea, trimmed to what the project needs, plus the things
 *   that actually bite on a moving vehicle:
 *     * one stream client at a time (a second viewer is refused rather than
 *       silently starving the first)
 *     * Wi-Fi auto-reconnect, since the car will drive out of range
 *     * settings chosen for latency over resolution (VGA, ~20 fps)
 *     * the white flash LED is forced OFF at boot (it wastes 300 mA and blinds
 *       nothing useful at 1:10 scale)
 *     * /status returns JSON (uptime, RSSI, frames, drops) for debugging
 *
 * HARDWARE
 *   Board: AI Thinker ESP32-CAM + ESP32-CAM-MB programmer board.
 *   Flash: seat the CAM on the MB board, micro-USB to the laptop, upload.
 *   On the car: 5V and GND only, from the logic buck (Buck #2).
 *   [IMPORTANT] Put 470-1000 uF across 5V/GND close to the module. Brown-out
 *   during a transmission burst is the usual cause of random reboots.
 *
 * BUILD
 *   Arduino IDE: Board "AI Thinker ESP32-CAM", PSRAM enabled (default),
 *   Partition Scheme "Huge APP (3MB No OTA)" if the sketch does not fit.
 *   No external libraries: esp_camera and esp_http_server ship with the core.
 *
 * NOT HARDWARE VALIDATED — compiles clean, never run on a camera.
 * =============================================================================
 */
#include <Arduino.h>
#include <WiFi.h>
#include <esp_camera.h>
#include <esp_http_server.h>
#include <esp_timer.h>
#include <HTTPClient.h>
#include <esp_system.h>
#include "lwip/sockets.h"   // TCP_NODELAY

// ----------------------------------------------------------------- settings
static const char *WIFI_SSID = "CTRL";         // same 2.4 GHz network as the laptop and the Pi
static const char *WIFI_PASSWORD = "thugbro5"; // do NOT push this file to a public repo
static const char *HOSTNAME = "rc-car-cam";    // try http://rc-car-cam.local/
// The laptop running the backend (uvicorn ... --host 0.0.0.0 --port 8000). "" = do not announce
// (then paste the stream URL on the dashboard's Camera page). Laptop 1 on the CTRL network:
static const char *BACKEND_HOST = "10.56.191.128";
static const uint16_t BACKEND_PORT = 8000;
static const uint32_t ANNOUNCE_PERIOD_MS = 10000;

// Latency beats resolution on a 1:10 car. FRAMESIZE_VGA = 640x480.
// Drop to FRAMESIZE_QVGA (320x240) if the link is congested or Wi-Fi is weak.
static const framesize_t FRAME_SIZE = FRAMESIZE_VGA;
static const int JPEG_QUALITY = 15;            // 10 (best) .. 63 (worst). 15 = ~25 % smaller frames than 12
static const int FRAME_BUFFERS = 2;            // needs PSRAM; 1 without

// ------------------------------------------------------- AI Thinker pin map
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22
#define FLASH_LED_GPIO 4                       // the bright white LED

static const char *STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=vcuframe";
static const char *STREAM_BOUNDARY = "\r\n--vcuframe\r\n";
static const char *STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static httpd_handle_t g_stream_server = NULL;
static httpd_handle_t g_web_server = NULL;
static volatile bool g_streaming = false;
static uint32_t g_frames_sent = 0, g_frames_dropped = 0, g_stream_sessions = 0;
static uint32_t g_announce_ok = 0, g_announce_fail = 0;
static float g_fps = 0;                 // frames actually sent per second (last 30 s window)

// ------------------------------------------------------------------ camera
static bool cameraInit() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;  c.pin_d1 = Y3_GPIO_NUM;  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;  c.pin_d4 = Y6_GPIO_NUM;  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;   c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;   c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.grab_mode = CAMERA_GRAB_LATEST;   // always the newest frame: lowest latency
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.frame_size = psramFound() ? FRAME_SIZE : FRAMESIZE_QVGA;
  c.jpeg_quality = JPEG_QUALITY;
  c.fb_count = psramFound() ? FRAME_BUFFERS : 1;

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("[CAM] init failed: 0x%x — check the ribbon latch and the 5V supply\n", err);
    return false;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s != NULL) {
    // Mounted upside down? set these to 1.
    s->set_vflip(s, 0);
    s->set_hmirror(s, 0);
    s->set_brightness(s, 1);      // outdoor track light is harsh
    s->set_saturation(s, 0);
  }
  Serial.printf("[CAM] ready: %s, quality %d, PSRAM %s\n",
                psramFound() ? "VGA" : "QVGA (no PSRAM)", JPEG_QUALITY,
                psramFound() ? "yes" : "no");
  return true;
}

// ------------------------------------------------------------- HTTP handlers
static esp_err_t streamHandler(httpd_req_t *req) {
  if (g_streaming) {  // one viewer at a time — two clients halve everyone's rate
    httpd_resp_set_status(req, "503 Service Unavailable");
    return httpd_resp_send(req, "camera already streaming to another client", HTTPD_RESP_USE_STRLEN);
  }
  if (httpd_resp_set_type(req, STREAM_CONTENT_TYPE) != ESP_OK) return ESP_FAIL;
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");  // dashboard is on another origin
  httpd_resp_set_hdr(req, "X-Framerate", "20");

  g_streaming = true;
  g_stream_sessions++;
  // 2026-11-14: TCP_NODELAY. Each frame goes out as several small writes; with Nagle on (lwIP
  // default) and Windows' 200 ms delayed ACK, every frame could wait ~200 ms -> 1-4 fps.
  int fd = httpd_req_to_sockfd(req), one = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
  Serial.println("[CAM] stream client connected");

  esp_err_t res = ESP_OK;
  char head[96];
  while (res == ESP_OK) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      g_frames_dropped++;
      Serial.println("[CAM] frame grab failed");
      break;
    }
    // boundary + part header in ONE write (was two)
    int hl = snprintf(head, sizeof(head), "%s", STREAM_BOUNDARY);
    hl += snprintf(head + hl, sizeof(head) - hl, STREAM_PART, (unsigned)fb->len);
    res = httpd_resp_send_chunk(req, head, hl);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (res == ESP_OK) g_frames_sent++;
  }

  g_streaming = false;
  Serial.println("[CAM] stream client disconnected");
  return res;
}

static esp_err_t captureHandler(httpd_req_t *req) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return httpd_resp_send_500(req);
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=frame.jpg");
  esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

static esp_err_t statusHandler(httpd_req_t *req) {
  char json[320];
  snprintf(json, sizeof(json),
           "{\"device\":\"esp32-cam\",\"ip\":\"%s\",\"rssi\":%d,\"fps\":%.1f,\"uptimeS\":%lu,"
           "\"streaming\":%s,\"framesSent\":%lu,\"framesDropped\":%lu,\"sessions\":%lu,"
           "\"heapFree\":%lu,\"psram\":%s,\"announceOk\":%lu,\"announceFail\":%lu}",
           WiFi.localIP().toString().c_str(), WiFi.RSSI(), g_fps, (unsigned long)(millis() / 1000),
           g_streaming ? "true" : "false", (unsigned long)g_frames_sent,
           (unsigned long)g_frames_dropped, (unsigned long)g_stream_sessions,
           (unsigned long)ESP.getFreeHeap(), psramFound() ? "true" : "false", (unsigned long)g_announce_ok,
           (unsigned long)g_announce_fail);
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t indexHandler(httpd_req_t *req) {
  char page[640];
  snprintf(page, sizeof(page),
           "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
           "<body style='margin:0;background:#111;color:#eee;font-family:sans-serif'>"
           "<p style='padding:8px'>RC car camera &mdash; paste this into the dashboard: "
           "<code>http://%s:81/stream</code></p>"
           "<img src='http://%s:81/stream' style='width:100%%;max-width:960px'>"
           "</body>",
           WiFi.localIP().toString().c_str(), WiFi.localIP().toString().c_str());
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
}

static void startServers() {
  // Port 80: page, snapshot, status. Port 81: the MJPEG stream only, so a
  // stuck stream cannot block the other endpoints.
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.ctrl_port = 32768;
  cfg.max_uri_handlers = 8;
  httpd_uri_t index_uri = {};
  index_uri.uri = "/"; index_uri.method = HTTP_GET; index_uri.handler = indexHandler;
  httpd_uri_t capture_uri = {};
  capture_uri.uri = "/capture"; capture_uri.method = HTTP_GET; capture_uri.handler = captureHandler;
  httpd_uri_t status_uri = {};
  status_uri.uri = "/status"; status_uri.method = HTTP_GET; status_uri.handler = statusHandler;
  if (httpd_start(&g_web_server, &cfg) == ESP_OK) {
    httpd_register_uri_handler(g_web_server, &index_uri);
    httpd_register_uri_handler(g_web_server, &capture_uri);
    httpd_register_uri_handler(g_web_server, &status_uri);
  }

  httpd_config_t scfg = HTTPD_DEFAULT_CONFIG();
  scfg.server_port = 81;
  scfg.ctrl_port = 32769;
  httpd_uri_t stream_uri = {};
  stream_uri.uri = "/stream"; stream_uri.method = HTTP_GET; stream_uri.handler = streamHandler;
  if (httpd_start(&g_stream_server, &scfg) == ESP_OK) {
    httpd_register_uri_handler(g_stream_server, &stream_uri);
  }
}

// ---------------------------------------------------------- announce (FW 2.2)
// Tells the backend relay where the stream is. Runs in loop(), never in the stream task, so a
// slow or absent backend cannot stall the video. 1.5 s timeout; failures are only counted.
static void announceToBackend() {
  if (!BACKEND_HOST[0] || WiFi.status() != WL_CONNECTED) return;
  char url[96], body[256];
  snprintf(url, sizeof(url), "http://%s:%u/api/camera/announce", BACKEND_HOST, BACKEND_PORT);
  snprintf(body, sizeof(body),
           "{\"url\":\"http://%s:81/stream\",\"ip\":\"%s\",\"rssi\":%d,\"name\":\"%s\","
           "\"framesSent\":%lu,\"streaming\":%s}",
           WiFi.localIP().toString().c_str(), WiFi.localIP().toString().c_str(), WiFi.RSSI(), HOSTNAME,
           (unsigned long)g_frames_sent, g_streaming ? "true" : "false");
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(1500);
  if (!http.begin(url)) { g_announce_fail++; return; }
  http.addHeader("Content-Type", "application/json");
  int code = http.POST((uint8_t *)body, strlen(body));
  http.end();
  if (code == 200) {
    if (g_announce_ok++ == 0) Serial.printf("[CAM] announced to the backend at %s:%u — the dashboard connects by itself\n",
                                            BACKEND_HOST, BACKEND_PORT);
  } else {
    if (g_announce_fail++ % 6 == 0)
      Serial.printf("[CAM] backend %s:%u not reachable (HTTP %d) — is uvicorn running with --host 0.0.0.0? "
                    "Paste the stream URL on the Camera page meanwhile.\n", BACKEND_HOST, BACKEND_PORT, code);
  }
}

// ------------------------------------------------------------------- Wi-Fi
static void wifiConnect() {
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);            // sleep adds hundreds of ms of latency
  WiFi.setHostname(HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[WIFI] connecting to %s", WIFI_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    const int rssi = WiFi.RSSI();
    Serial.printf("[WIFI] connected, RSSI %d dBm (%s)\n", rssi,
                  rssi > -60 ? "good" : rssi > -70 ? "ok" : "WEAK: expect a few fps; move the hotspot closer / external antenna");
    Serial.printf("\n  Dashboard URL:  http://%s:81/stream\n", WiFi.localIP().toString().c_str());
    Serial.printf("  Browser test:   http://%s/\n\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("[WIFI] failed — check SSID/password and that it is a 2.4 GHz network");
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nESP32-CAM stream (project: RISC-V motorsport platform) 2026-11-14");
  const esp_reset_reason_t rr = esp_reset_reason();
  Serial.printf("[BOOT] reset reason %d%s\n", (int)rr,
                rr == ESP_RST_BROWNOUT ? "  <-- BROWNOUT: the 5 V supply sagged. Fix the power, not the code." :
                rr == ESP_RST_PANIC ? "  (crash)" : rr == ESP_RST_POWERON ? "  (power on / serial monitor reset)" : "");

  pinMode(FLASH_LED_GPIO, OUTPUT);
  digitalWrite(FLASH_LED_GPIO, LOW);   // flash LED off: 300 mA saved

  if (!cameraInit()) {
    Serial.println("[CAM] halted — fix the camera before continuing");
    for (;;) delay(1000);
  }
  wifiConnect();
  startServers();
  Serial.println("[HTTP] servers up on ports 80 and 81");
  announceToBackend();
}

void loop() {
  // Reconnect when the car drives out of range and comes back.
  static uint32_t last_check = 0, last_report = 0, last_announce = 0;
  if (millis() - last_announce > ANNOUNCE_PERIOD_MS) {
    last_announce = millis();
    announceToBackend();
  }
  if (millis() - last_check > 5000) {
    last_check = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[WIFI] link lost — reconnecting");
      WiFi.disconnect();
      wifiConnect();
    }
  }
  if (millis() - last_report > 30000) {
    static uint32_t last_frames = 0;
    g_fps = (g_frames_sent - last_frames) / ((millis() - last_report) / 1000.0f);
    last_frames = g_frames_sent;
    last_report = millis();
    Serial.printf("[CAM] fps=%.1f frames=%lu dropped=%lu sessions=%lu rssi=%d heap=%lu\n", g_fps,
                  (unsigned long)g_frames_sent, (unsigned long)g_frames_dropped,
                  (unsigned long)g_stream_sessions, WiFi.RSSI(), (unsigned long)ESP.getFreeHeap());
  }
  delay(100);
}
