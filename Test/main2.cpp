#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include "esp_http_server.h"
#include "Firebase_ESP_Client.h"

// ==========================================
// 1. ตั้งค่า WiFi และ Firebase
// ==========================================
const char* ssid = "AiMaSiKhon_2.4G";
const char* password = "07151524";

#define DATABASE_SECRET "BpnnISY14yoxVPIb894jRmnxLkTCDM5Z23TJnxUA"
#define DATABASE_URL "preserving-fall-detector-default-rtdb.firebaseio.com"

FirebaseData fbdo;
FirebaseAuth auth;
FirebaseJson json;
FirebaseConfig configFirebase;

// ==========================================
// 2. ตั้งค่าขา Pin (ESP32-S3-WROOM-CAM)
// ==========================================
#define PWDN_GPIO_NUM     38
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM     15
#define SIOD_GPIO_NUM     4
#define SIOC_GPIO_NUM     5
#define Y9_GPIO_NUM       16
#define Y8_GPIO_NUM       17
#define Y7_GPIO_NUM       18
#define Y6_GPIO_NUM       12
#define Y5_GPIO_NUM       10
#define Y4_GPIO_NUM       8
#define Y3_GPIO_NUM       9
#define Y2_GPIO_NUM       11
#define VSYNC_GPIO_NUM    6
#define HREF_GPIO_NUM     7
#define PCLK_GPIO_NUM     13

#define Pirpin 1

String Path = "/hospital_system/wards/ward_A/room_301/motion";
// motion state
bool motionLatched = false;
unsigned long lastMotionMs = 0;
int lastSentMotion = -1;

// camera/server state
httpd_handle_t stream_httpd = NULL;
bool cameraOn = false;

// ==========================================
// 3. ฟังก์ชันส่งรูปภาพ (Capture Handler)
// ==========================================
static esp_err_t capture_handler(httpd_req_t *req) {
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Camera capture failed");
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }

  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

static void startCameraServer() {
  if (stream_httpd != NULL) return; // already running

  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.lru_purge_enable = true;
  config.max_uri_handlers = 4;
  config.stack_size = 8192;

  httpd_uri_t capture_uri = {
    .uri       = "/capture",
    .method    = HTTP_GET,
    .handler   = capture_handler,
    .user_ctx  = NULL
  };

  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &capture_uri);
    Serial.println("HTTP server started");
  } else {
    Serial.println("Failed to start HTTP server");
    stream_httpd = NULL;
  }
}

static void stopCameraServer() {
  if (stream_httpd) {
    httpd_stop(stream_httpd);
    stream_httpd = NULL;
    Serial.println("HTTP server stopped");
  }
}

// Build camera config each time we start the camera
static camera_config_t buildCameraConfig() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 12;
  config.fb_count = 2;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  return config;
}

static bool ensureWifiConnected() {
  if (WiFi.status() == WL_CONNECTED) return true;
  Serial.print("Connecting WiFi");
  WiFi.begin(ssid, password);
  WiFi.setSleep(false);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
    if (millis() - start > 3000) {
      Serial.println("\nWiFi connect timeout, restarting...");
      ESP.restart();
    }
  }
  Serial.println("\nWiFi connected");
  return true;
}

static bool startCamera() {
  if (cameraOn) return true;
  camera_config_t config = buildCameraConfig();
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    return false;
  }
  startCameraServer();
  cameraOn = true;
  Serial.println("Camera started");
  return true;
}

static void stopCamera() {
  if (!cameraOn) return;
  stopCameraServer();
  esp_camera_deinit();
  cameraOn = false;
  Serial.println("Camera stopped");
}

void setup() {
  Serial.begin(115200);
  pinMode(Pirpin, INPUT);
  Serial.setDebugOutput(true);
  Serial.println();

  ensureWifiConnected();

  // เริ่มต้น Firebase หลัง WiFi พร้อม
  configFirebase.database_url = DATABASE_URL;
  configFirebase.signer.tokens.legacy_token = DATABASE_SECRET;
  configFirebase.timeout.wifiReconnect = 10000;
  Firebase.begin(&configFirebase, &auth);
  Firebase.reconnectWiFi(true);

  Serial.println("System ready, waiting for PIR motion to start camera");
}

static void sendMotionToFirebase(int state) {
  if (!Firebase.ready()) return;
  json.set("val", state);
  if (Firebase.RTDB.setJSON(&fbdo, Path, &json)) {
    Serial.printf("Motion %d -> Firebase ok\n", state);
  } else {
    Serial.printf("Firebase update failed: %s\n", fbdo.errorReason().c_str());
  }
}

void loop() {
  ensureWifiConnected();

  int pir = digitalRead(Pirpin);
  unsigned long now = millis();

  if (pir == HIGH) {
    motionLatched = true;
    lastMotionMs = now;
    if (!cameraOn) {
      if (startCamera()) {
        Serial.println("Camera enabled due to motion");
      }
    }
  }

  if (motionLatched && (now - lastMotionMs >= 5000)) {
    motionLatched = false;
    if (cameraOn) {
      stopCamera();
      Serial.println("Camera disabled due to inactivity");
    }
  }

  int state = motionLatched ? 1 : 0;
  if (state != lastSentMotion) {
    sendMotionToFirebase(state);
    lastSentMotion = state;
  }

  delay(100);
}