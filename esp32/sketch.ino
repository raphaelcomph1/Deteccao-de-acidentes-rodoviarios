#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#define WIFI_SSID   "Wokwi-GUEST"
#define WIFI_PASS   ""
#define BROKER_HOST "broker.hivemq.com"
#define BROKER_PORT 1883
#define CLIENT_ID   "motiva-rm563567-esp32"

#define T_CMD       "motiva/rm563567/comandos"
#define T_STATUS    "motiva/rm563567/status"
#define T_CENTRAL   "motiva/rm563567/central"

#define PIN_R   25
#define PIN_G   26
#define PIN_B   27
#define PIN_BUZ 14

#define HEARTBEAT_ESP_MS    5000
#define CENTRAL_TIMEOUT_MS  30000

enum Estado { NORMAL, SUSPEITA, CONFIRMADO };
const char* NOMES[] = {"NORMAL", "SUSPEITA", "CONFIRMADO"};
volatile Estado estado = NORMAL;
volatile bool falha = true;
volatile bool centralOnline = true;
char eventoAtivo[48] = "";
uint64_t ultimoSeq = 0;
unsigned long tCentral = 0, tHb = 0, tWifi = 0, tRetry = 0, retryMs = 1000;

WiFiClient net;
PubSubClient mqtt(net);

const char* estadoAtual() { return falha ? "FALHA" : NOMES[estado]; }

void cor(bool r, bool g, bool b) {
  digitalWrite(PIN_R, r); digitalWrite(PIN_G, g); digitalWrite(PIN_B, b);
}
void setBuz(bool on) {
  static bool atual = false;
  if (on == atual) return;
  atual = on;
  if (on) tone(PIN_BUZ, 2000); else noTone(PIN_BUZ);
}
void taskSaida(void*) {
  for (;;) {
    unsigned long t = millis();
    if (falha)                   { bool on = (t / 500) % 2 == 0; cor(on, 0, on); setBuz(false); }
    else if (estado == NORMAL)   { cor(0, 1, 0); setBuz(false); }
    else if (estado == SUSPEITA) { bool on = (t / 1000) % 2 == 0; cor(on, on, 0); setBuz(false); }
    else                         { bool on = (t / 250) % 2 == 0; cor(on, 0, 0); setBuz(on); }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void pub(JsonDocument& d, bool retain) {
  d["v"] = 1;
  d["dispositivo"] = "esp32-01";
  d["uptime_s"] = millis() / 1000;
  char buf[384];
  serializeJson(d, buf, sizeof(buf));
  mqtt.publish(T_STATUS, buf, retain);
  Serial.printf("[PUB%s] %s\n", retain ? " retained" : "", buf);
}
void idEvento(JsonDocument& d, const char* eid) {
  if (eid[0]) d["event_id"] = eid; else d["event_id"] = nullptr;
}
void status(const char* tipoMsg, const char* motivo, bool retain) {
  JsonDocument d;
  d["tipo_msg"] = tipoMsg; d["online"] = true; d["estado"] = estadoAtual();
  idEvento(d, eventoAtivo);
  d["ultimo_seq"] = ultimoSeq;
  if (motivo[0]) d["motivo"] = motivo;
  pub(d, retain);
}
void ack(const char* cid, const char* eid, const char* fase, bool dup, const char* motivo) {
  JsonDocument d;
  d["tipo_msg"] = "ACK"; d["command_id"] = cid; idEvento(d, eid);
  d["seq"] = ultimoSeq; d["fase"] = fase; d["duplicado"] = dup; d["estado"] = estadoAtual();
  if (motivo[0]) d["motivo"] = motivo;
  pub(d, false);
}

bool parseEstado(const char* s, Estado& e) {
  if (!strcmp(s, "NORMAL"))     { e = NORMAL;     return true; }
  if (!strcmp(s, "SUSPEITA"))   { e = SUSPEITA;   return true; }
  if (!strcmp(s, "CONFIRMADO")) { e = CONFIRMADO; return true; }
  return false;
}
bool acaoValida(const char* a) {
  return !strcmp(a, "SUSPEITA") || !strcmp(a, "CONFIRMAR") || !strcmp(a, "REJEITAR") ||
         !strcmp(a, "ENCERRAR") || !strcmp(a, "SINCRONIZAR");
}

void onMsg(char* topic, byte* payload, unsigned int len) {
  JsonDocument d;
  bool ehCmd = strcmp(topic, T_CMD) == 0;
  if (deserializeJson(d, (const char*)payload, len)) {
    if (ehCmd) ack("", "", "RECUSADO", false, "json_invalido");
    return;
  }
  if (!ehCmd) {
    const char* tm = d["tipo_msg"] | "";
    bool on = strcmp(tm, "LWT") != 0 && (d["online"] | true);
    centralOnline = on;
    if (on) tCentral = millis();
    return;
  }
  tCentral = millis(); centralOnline = true;

  char cid[48], eid[48], acao[16], alvo[16];
  strlcpy(cid,  d["command_id"]  | "", sizeof(cid));
  strlcpy(eid,  d["event_id"]    | "", sizeof(eid));
  strlcpy(acao, d["acao"]        | "", sizeof(acao));
  strlcpy(alvo, d["estado_alvo"] | "", sizeof(alvo));
  uint64_t seq = d["seq"].as<uint64_t>();
  int v = d["v"] | 1;

  Estado novo;
  if (cid[0] == 0 || seq == 0)   { ack(cid, eid, "RECUSADO", false, "command_id_ou_seq_ausente"); return; }
  if (v != 1)                    { ack(cid, eid, "RECUSADO", false, "versao_nao_suportada"); return; }
  if (!acaoValida(acao))         { ack(cid, eid, "RECUSADO", false, "acao_invalida"); return; }
  if (!parseEstado(alvo, novo))  { ack(cid, eid, "RECUSADO", false, "estado_alvo_invalido"); return; }

  if (seq <= ultimoSeq) {
    ack(cid, eid, "RECEBIDO", true, "seq_ja_aplicado");
    return;
  }
  ack(cid, eid, "RECEBIDO", false, "");
  ultimoSeq = seq;
  estado = novo;
  if (novo == NORMAL) eventoAtivo[0] = 0; else if (eid[0]) strlcpy(eventoAtivo, eid, sizeof(eventoAtivo));
  ack(cid, eid, "APLICADO", false, acao);
}

void rede() {
  unsigned long t = millis();
  if (WiFi.status() != WL_CONNECTED) {
    if (t - tWifi > 15000) { tWifi = t; WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASS, 6); Serial.println("[WIFI] tentando..."); }
    return;
  }
  if (mqtt.connected() || t - tRetry < retryMs) return;
  tRetry = t;
  Serial.println("[MQTT] conectando...");
  const char* lwt = "{\"v\":1,\"tipo_msg\":\"LWT\",\"dispositivo\":\"esp32-01\",\"online\":false,\"estado\":\"OFFLINE\"}";
  if (mqtt.connect(CLIENT_ID, NULL, NULL, T_STATUS, 1, true, lwt)) {
    mqtt.subscribe(T_CMD, 1);
    mqtt.subscribe(T_CENTRAL, 1);
    tCentral = millis(); tHb = millis(); retryMs = 1000;
    Serial.println("[MQTT] conectado");
    status("HEARTBEAT", "", true);
  } else {
    Serial.printf("[MQTT] falhou rc=%d\n", mqtt.state());
    retryMs = min(retryMs * 2, 15000UL);
  }
}

void avaliaFalha() {
  bool nova = !mqtt.connected() || !centralOnline || (millis() - tCentral > CENTRAL_TIMEOUT_MS);
  if (nova == falha) return;
  falha = nova;
  Serial.printf("[FALHA] %s\n", falha ? "ATIVA" : "RECUPERADA");
  if (!falha && mqtt.connected()) status("SYNC", "pedindo_estado_atual", false);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_R, OUTPUT); pinMode(PIN_G, OUTPUT); pinMode(PIN_B, OUTPUT); pinMode(PIN_BUZ, OUTPUT);
  xTaskCreatePinnedToCore(taskSaida, "saida", 3072, NULL, 1, NULL, 0);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS, 6);
  tWifi = millis();
  mqtt.setServer(BROKER_HOST, BROKER_PORT);
  mqtt.setCallback(onMsg);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(10);
  mqtt.setSocketTimeout(2);
}

void loop() {
  rede();
  mqtt.loop();
  avaliaFalha();
  if (mqtt.connected() && millis() - tHb > HEARTBEAT_ESP_MS) { tHb = millis(); status("HEARTBEAT", "", true); }
}
