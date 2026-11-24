/*
 * =============================================================================
 * ground_radio_bridge.ino — USB-serial <-> SX1278 LoRa bridge (SLIP framed)
 * =============================================================================
 * Project : AI-Enabled RISC-V Motorsport Electronics Platform (1:10 RC car)
 *
 * ROLE
 *   The laptop has no SPI bus, so the ground-station end of the LoRa link is
 *   an ESP32 dev board + SX1278 module (Ai-Thinker Ra-02) on USB. A dumb modem:
 *     USB  -> SLIP frame -> LoRa TX (one packet per frame)
 *     LoRa RX -> SLIP frame -> USB, followed by a LINK_STATS frame
 *               [0xB5, rssi_dBm*10 (i16 LE), snr_dB*4 (i8)]
 *   All protocol meaning (envelope, CRC16, sequence numbers, message types)
 *   lives in Python (Ground Station/bridge.py, --radio serial). The same
 *   firmware can also sit on the Pi's USB port as an alternative to the SPI
 *   SX1278 (raspberry_pi/main.py --lora-backend serial).
 *
 * AIR SETTINGS — must match raspberry_pi/main.py LoRaConfig exactly:
 *   433.0 MHz, SF7, BW 250 kHz, CR 4/5, sync 0x12 (private), preamble 8,
 *   explicit header, CRC on, 10 dBm via PA_BOOST (both ends).
 *   433 MHz is the band the Ra-02 ships on (SX1278 covers 410-525 MHz). In
 *   India the 433-434.79 MHz band is de-licensed only at very low power, with
 *   conditions — verify the current rule before raising the power. Both
 *   antennas must be 433 MHz types.
 *
 * WIRING (defaults for a classic ESP32 dev module / WROOM-32, VSPI):
 *   SX1278  NSS->GPIO5   SCK->GPIO18  MISO->GPIO19  MOSI->GPIO23
 *           NRST->GPIO14 DIO0->GPIO26              3V3, GND
 *   GPIO6-11 are the flash pins and GPIO34-39 are input-only — avoid both.
 *   The SX1278 has no BUSY pin and switches its own RF path, so there is
 *   nothing else to wire.
 *   Antenna MUST be connected before power-up (TX into no antenna damages PA).
 *
 * BUILD
 *   Library: RadioLib 7.x (Arduino Library Manager: "RadioLib" by jgromes)
 *   Board  : ESP32 Dev Module (WROOM-32). The pin defaults below are for that
 *            board; change PIN_* for any other ESP32.
 *   NOT HARDWARE VALIDATED.
 * =============================================================================
 */
#include <Arduino.h>
#include <RadioLib.h>

// ---------------- configuration ----------------
static const int PIN_NSS = 5, PIN_DIO0 = 26, PIN_NRST = 14;
static const int PIN_SCK = 18, PIN_MISO = 19, PIN_MOSI = 23;

static const float LORA_FREQ_MHZ = 433.0f;            // must match the Pi (Ra-02 is a 433 MHz module)
static const float LORA_BW_KHZ = 250.0f;
static const uint8_t LORA_SF = 7;
static const uint8_t LORA_CR = 5;                     // 4/5
static const uint8_t LORA_SYNC = 0x12;                // RadioLib private -> register 0x1424
static const int8_t LORA_POWER_DBM = 10;              // match the Pi (main.py LoRaConfig). 433 MHz is only
                                                      // de-licensed in India at very low power — verify first
static const uint16_t LORA_PREAMBLE = 8;

static const uint32_t SERIAL_BAUD = 115200;
static const size_t MAX_PACKET = 80;                  // LoRa envelope max = 9 + 64 + 2 = 75

// ---------------- SLIP ----------------
static const uint8_t SLIP_END = 0xC0, SLIP_ESC = 0xDB, SLIP_ESC_END = 0xDC, SLIP_ESC_ESC = 0xDD;
static const uint8_t LINK_STATS_MAGIC = 0xB5;

static void slipWrite(const uint8_t *data, size_t len) {
  Serial.write(SLIP_END);
  for (size_t i = 0; i < len; ++i) {
    if (data[i] == SLIP_END) { Serial.write(SLIP_ESC); Serial.write(SLIP_ESC_END); }
    else if (data[i] == SLIP_ESC) { Serial.write(SLIP_ESC); Serial.write(SLIP_ESC_ESC); }
    else Serial.write(data[i]);
  }
  Serial.write(SLIP_END);
}

static uint8_t g_rxSlip[MAX_PACKET];
static size_t g_rxSlipLen = 0;
static bool g_rxSlipEsc = false, g_rxSlipOverflow = false;

/* returns true when a complete frame is in g_rxSlip[0..g_rxSlipLen) */
static bool slipFeed(uint8_t b) {
  if (b == SLIP_END) {
    bool complete = g_rxSlipLen > 0 && !g_rxSlipOverflow;
    if (!complete) g_rxSlipLen = 0;
    g_rxSlipEsc = false; g_rxSlipOverflow = false;
    return complete;
  }
  if (g_rxSlipEsc) {
    b = (b == SLIP_ESC_END) ? SLIP_END : (b == SLIP_ESC_ESC) ? SLIP_ESC : b;
    g_rxSlipEsc = false;
  } else if (b == SLIP_ESC) {
    g_rxSlipEsc = true;
    return false;
  }
  if (g_rxSlipLen < MAX_PACKET) g_rxSlip[g_rxSlipLen++] = b;
  else g_rxSlipOverflow = true;  // drop the whole frame at the next END
  return false;
}

// ---------------- radio ----------------
static SPIClass g_spi(VSPI);
// SX127x Module(): cs, irq (DIO0), reset, gpio (DIO1, unused here)
static SX1278 g_radio = new Module(PIN_NSS, PIN_DIO0, PIN_NRST, RADIOLIB_NC, g_spi);
static volatile bool g_rxFlag = false;
static uint32_t g_txCount = 0, g_rxCount = 0, g_rxErr = 0, g_txErr = 0;

#if defined(ESP32)
IRAM_ATTR
#endif
static void onDio0() { g_rxFlag = true; }

// Stage 2d FIX: human-readable status is wrapped in SLIP END bytes. Printed bare, it
// sat in the host's SLIP buffer until the next END — i.e. until the first packet
// arrived — so "rx=0" never showed exactly when a dead link needed debugging.
#define STATUS_BEGIN() Serial.write(SLIP_END)
#define STATUS_END() Serial.write(SLIP_END)

static void fatal(const char *what, int16_t code) {
  for (;;) {
    STATUS_BEGIN();
    Serial.printf("# FATAL %s (RadioLib code %d) — check wiring/pins, see header", what, code);
    STATUS_END();
    delay(2000);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(300);
  g_spi.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_NSS);
  // SX127x begin(): freq, bw, sf, cr, syncWord, power, preambleLength, gain
  int16_t st = g_radio.begin(LORA_FREQ_MHZ, LORA_BW_KHZ, LORA_SF, LORA_CR, LORA_SYNC, LORA_POWER_DBM,
                             LORA_PREAMBLE);
  if (st != RADIOLIB_ERR_NONE) fatal("SX1278 begin", st);
  g_radio.setCRC(true);
  g_radio.setCurrentLimit(100);  // RadioLib default 60 mA starves PA_BOOST above ~14 dBm
  g_radio.setDio0Action(onDio0, RISING);
  st = g_radio.startReceive();
  if (st != RADIOLIB_ERR_NONE) fatal("startReceive", st);
  // Status lines are their own END-delimited frames starting with '#'; the Python
  // side recognises and logs them immediately instead of treating them as packets.
  STATUS_BEGIN();
  Serial.printf("# ground_radio_bridge ready: %.1f MHz SF%u BW%.0f CR4/%u %d dBm", LORA_FREQ_MHZ, LORA_SF,
                LORA_BW_KHZ, LORA_CR, LORA_POWER_DBM);
  STATUS_END();
}

static void handleRadioRx() {
  g_rxFlag = false;
  size_t len = g_radio.getPacketLength();
  uint8_t buf[MAX_PACKET];
  if (len == 0 || len > sizeof(buf)) { g_rxErr++; g_radio.startReceive(); return; }
  int16_t st = g_radio.readData(buf, len);
  if (st == RADIOLIB_ERR_NONE) {
    g_rxCount++;
    slipWrite(buf, len);
    int16_t rssi10 = static_cast<int16_t>(g_radio.getRSSI() * 10.0f);
    int8_t snr4 = static_cast<int8_t>(constrain(g_radio.getSNR() * 4.0f, -128.0f, 127.0f));
    uint8_t stats[4] = {LINK_STATS_MAGIC, static_cast<uint8_t>(rssi10 & 0xFF),
                        static_cast<uint8_t>((rssi10 >> 8) & 0xFF), static_cast<uint8_t>(snr4)};
    slipWrite(stats, sizeof(stats));
  } else {
    g_rxErr++;  // CRC error etc. — dropped here, never forwarded
  }
  g_radio.startReceive();
}

void loop() {
  if (g_rxFlag) handleRadioRx();

  while (Serial.available() > 0) {
    if (slipFeed(static_cast<uint8_t>(Serial.read()))) {
      g_rxFlag = false;  // DIO0 also fires on TX_DONE; don't misread it as RX
      int16_t st = g_radio.transmit(g_rxSlip, g_rxSlipLen);  // blocking, ~40-50 ms at SF7/BW250
      g_rxSlipLen = 0;
      if (st == RADIOLIB_ERR_NONE) g_txCount++; else g_txErr++;
      g_rxFlag = false;
      g_radio.startReceive();
      break;  // service RX between transmissions
    }
  }

  static uint32_t lastStatus = 0;
  if (millis() - lastStatus > 10000) {
    lastStatus = millis();
    STATUS_BEGIN();
    Serial.printf("# tx=%lu rx=%lu rx_err=%lu tx_err=%lu", (unsigned long)g_txCount, (unsigned long)g_rxCount,
                  (unsigned long)g_rxErr, (unsigned long)g_txErr);
    STATUS_END();
  }
}
