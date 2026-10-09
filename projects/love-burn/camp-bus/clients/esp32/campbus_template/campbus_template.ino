/*
 * Camp Bus - ESP32 reference sketch (M1)
 *
 * Does the three things every bus node does, per docs/SCHEMA.md:
 *   1. connects to camp WiFi + the broker, with a retained node/<id>/status and a last-will
 *   2. SUBSCRIBES: flashes the onboard LED on every audio/beat (a crystal blinking on the kick drum)
 *   3. PUBLISHES: a toy/<id> press event when the button is pressed, and bumps node/<id>/count
 *   4. SERVES ITS OWN CONSOLE on http://<its ip>/ - health, last traffic, and an Identify button -
 *      and advertises it as `ui` in its status, so the Devices page can link to it (SCHEMA, 2026-09-24)
 *
 * Copy this folder, change NODE_ID / TOY_ID / pins, and you have a toy or a responder.
 *
 * Board: any ESP32 (Arduino core 2.x or 3.x).  Libraries (Library Manager):
 *   - PubSubClient (Nick O'Leary)
 *   - ArduinoJson (Benoit Blanchon) v7
 *
 * Deep sleep (battery toys): see the DEEP_SLEEP block at the bottom. A pure responder
 * (crystal) stays awake; a pure toy (button) can sleep between presses.
 */
#include <WiFi.h>
#include <WebServer.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ---------------------------------------------------------------- configure me
#define WIFI_SSID     "YOUR_SSID"    
#define WIFI_PASS     "YOUR_PASSWORD"
#define BROKER_HOST   "10.42.0.1"        // static IP of the booth Pi; ESP32 mDNS is flaky, use the IP
#define BROKER_PORT   1883
#define NODE_ID       "toy_demo_01"      // lowercase snake_case, type prefix (see README)
#define TOY_ID        "demo_button"
#define FW_VERSION    "esp32-template-0.1"

#define UI_PORT       80                 // this node's own console; published as `ui` in status
#define LED_PIN       2                  // onboard LED on most dev boards
#define BUTTON_PIN    0                  // BOOT button on most dev boards (active low)
#define BEAT_FLASH_MS 40
#define STATUS_EVERY_MS 30000
// -----------------------------------------------------------------------------

WiFiClient   wifi;
PubSubClient mqtt(wifi);
WebServer    http(UI_PORT);

const char* TOPIC_STATUS = "node/" NODE_ID "/status";
const char* TOPIC_COUNT  = "node/" NODE_ID "/count";
const char* TOPIC_TOY    = "toy/" TOY_ID;

uint32_t interactions   = 0;
uint32_t bootMillis     = 0;
char     lastIn[96]     = "";            // newest topic heard, for the console
char     lastOut[96]    = "";            // newest topic sent
uint32_t lastInAt       = 0;
uint32_t identifyUntil  = 0;             // console "Identify": hold the LED on
uint32_t lastStatus     = 0;
uint32_t ledOffAt       = 0;
bool     lastButton     = true;          // pull-up: true = released
uint32_t lastButtonEdge = 0;

// ---- helpers -----------------------------------------------------------------
void publishJson(const char* topic, JsonDocument& doc, bool retain = false) {
  char buf[256];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  mqtt.publish(topic, (const uint8_t*)buf, n, retain);   // fire and forget; a dead bus breaks nothing
}

void publishStatus() {
  JsonDocument d;
  d["online"] = true;
  d["ip"]     = WiFi.localIP().toString();
  d["fw"]     = FW_VERSION;
  d["uptime"] = (millis() - bootMillis) / 1000;
  d["role"]   = "input";                   // input | instrument | responder | bridge | booth
  d["ui"]     = UI_PORT;                   // "my console is at http://<ip>:80/"
  d["ts"]     = 0;                         // no real clock on this node
  publishJson(TOPIC_STATUS, d, true);
  lastStatus = millis();
}

// ---- this node's console -----------------------------------------------------
// Everything a person standing in front of the device needs, and nothing else. Self
// contained on purpose: the camp LAN has no internet, so no CDN, no fonts, no library.
void handleRoot() {
  char b[1900];
  snprintf(b, sizeof(b),
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>%s console</title><style>"
    ":root{--ground:#0b0f12;--panel:#131a1f;--rule:#1f2a31;--ink:#e6edf1;--mute:#7d8f9a;--live:#4dffa6;--down:#ff5252;--act:#4de0ff}"
    "body{margin:0;padding:16px;background:var(--ground);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,sans-serif}"
    "h1{font-size:24px;margin:0 0 2px;letter-spacing:-.02em}p.v{color:var(--mute);margin:0 0 18px}"
    "section{background:var(--panel);border:1px solid var(--rule);border-radius:12px;padding:14px 16px;margin-bottom:12px}"
    "h2{font-size:14px;margin:0 0 10px}dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:0}"
    "dt{color:var(--mute);font-size:12px}dd{margin:0;font:16px ui-monospace,Consolas,monospace;font-variant-numeric:tabular-nums}"
    "a.b{display:inline-block;color:var(--act);border:1px solid var(--act);border-radius:8px;padding:7px 14px;text-decoration:none}"
    "a.b:hover{background:var(--act);color:var(--ground)}</style>"
    "<h1>%s</h1><p class=v>%s &middot; broker %s</p>"
    "<section><h2>Health</h2><dl>"
    "<div><dt>Bus</dt><dd style='color:%s'>%s</dd></div>"
    "<div><dt>WiFi</dt><dd>%d dBm</dd></div>"
    "<div><dt>Uptime</dt><dd>%lus</dd></div>"
    "<div><dt>Interactions</dt><dd>%lu</dd></div>"
    "<div><dt>Address</dt><dd>%s</dd></div>"
    "<div><dt>Free heap</dt><dd>%lu</dd></div>"
    "</dl></section>"
    "<section><h2>Traffic</h2><dl>"
    "<div><dt>Last heard</dt><dd>%s</dd></div>"
    "<div><dt>Last sent</dt><dd>%s</dd></div>"
    "</dl></section>"
    "<section><h2>Controls</h2><a class=b href='/identify'>Identify</a></section>"
    "<script>setTimeout(()=>location.reload(),2000)</script>",
    NODE_ID, NODE_ID, FW_VERSION, BROKER_HOST,
    mqtt.connected() ? "#4dffa6" : "#ff5252", mqtt.connected() ? "connected" : "not connected",
    WiFi.RSSI(), (millis() - bootMillis) / 1000, interactions,
    WiFi.localIP().toString().c_str(), (unsigned long)ESP.getFreeHeap(),
    lastIn[0] ? lastIn : "nothing yet", lastOut[0] ? lastOut : "nothing yet");
  http.send(200, "text/html; charset=utf-8", b);
}

void handleIdentify() {
  identifyUntil = millis() + 3000;         // a real responder would go bright white here
  digitalWrite(LED_PIN, HIGH);
  http.sendHeader("Location", "/");
  http.send(303, "text/plain", "identifying");
}

void publishCount() {
  JsonDocument d;
  d["interactions"] = interactions;
  d["ts"] = 0;
  publishJson(TOPIC_COUNT, d, true);
}

void publishPress() {
  JsonDocument d;
  d["toy"]   = TOY_ID;
  d["event"] = "press";
  d["value"] = 1;
  d["ts"]    = 0;
  publishJson(TOPIC_TOY, d);
  snprintf(lastOut, sizeof(lastOut), "%s", TOPIC_TOY);
  interactions++;
  publishCount();
}

// ---- subscriber --------------------------------------------------------------
void onMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument d;
  if (deserializeJson(d, payload, len)) return;   // not JSON; ignore
  snprintf(lastIn, sizeof(lastIn), "%s", topic);
  lastInAt = millis();

  if (strcmp(topic, "audio/beat") == 0) {
    // Flash on every beat; brighter/longer on the downbeat (n % 4 == 0).
    int n = d["n"] | 0;
    digitalWrite(LED_PIN, HIGH);
    ledOffAt = millis() + (n % 4 == 0 ? BEAT_FLASH_MS * 3 : BEAT_FLASH_MS);
  }
  // Responders also handle cmd/<my_target>/<effect> here, e.g.:
  // if (strncmp(topic, "cmd/", 4) == 0) { ... d["color"], d["speed"] ... }
}

// ---- connection --------------------------------------------------------------
void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                    // lower latency for responders; enable for battery toys
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < 10000) delay(100);
}

void ensureMqtt() {
  if (mqtt.connected()) return;
  static uint32_t lastTry = 0;
  if (millis() - lastTry < 2000) return;   // don't hammer; keep the loop() responsive
  lastTry = millis();
  // last will: mark ourselves offline (retained) if we vanish
  if (mqtt.connect(NODE_ID, TOPIC_STATUS, 0, true, "{\"online\":false,\"ts\":0}")) {
    publishStatus();
    publishCount();
    mqtt.subscribe("audio/beat");
    // mqtt.subscribe("cmd/" TARGET "/#");  mqtt.subscribe("cmd/all/#");   // for responders
  }
}

// ---- arduino -----------------------------------------------------------------
void setup() {
  pinMode(LED_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  bootMillis = millis();
  mqtt.setServer(BROKER_HOST, BROKER_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(20);
  http.on("/", handleRoot);
  http.on("/identify", handleIdentify);
  http.begin();                            // the console is up even when the broker is not
}

void loop() {
  ensureWifi();
  ensureMqtt();
  mqtt.loop();
  http.handleClient();

  // LED off after the flash (or after an Identify from the console)
  if (identifyUntil && millis() >= identifyUntil) { identifyUntil = 0; digitalWrite(LED_PIN, LOW); }
  if (!identifyUntil && ledOffAt && millis() >= ledOffAt) { digitalWrite(LED_PIN, LOW); ledOffAt = 0; }

  // Button with 50 ms debounce; the LOCAL effect (if any) happens here first, then the bus hears about it
  bool b = digitalRead(BUTTON_PIN);
  if (b != lastButton && millis() - lastButtonEdge > 50) {
    lastButtonEdge = millis();
    lastButton = b;
    if (b == LOW) { Serial.println("press"); if (mqtt.connected()) publishPress(); }
  }

  if (millis() - lastStatus > STATUS_EVERY_MS && mqtt.connected()) publishStatus();

  /* DEEP_SLEEP (battery toys only):
   * after publishing, disconnect cleanly (mqtt.disconnect(); WiFi.disconnect(true);),
   * then esp_sleep_enable_ext0_wakeup((gpio_num_t)BUTTON_PIN, 0); esp_deep_sleep_start();
   * Wake-to-publish is ~1-2 s with DHCP; use WiFi.config(static ip) + a cached BSSID/channel
   * in RTC memory to get it under ~400 ms.
   */
}
