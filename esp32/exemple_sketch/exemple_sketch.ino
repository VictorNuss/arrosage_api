// Exemple ESP32 : lit des capteurs, publie un état JSON sur
// arrosage/<device_id>/etat toutes les 60s, ET écoute des commandes
// d'ouverture/fermeture (avec minuteur d'auto-fermeture géré localement) sur
// arrosage/<device_id>/commande. Voir esp32/README.md pour le contrat complet.
//
// Dépendances (Arduino Library Manager) :
//   - PubSubClient (Nick O'Leary)
//   - ArduinoJson (Benoit Blanchon)

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

const char *WIFI_SSID = "VOTRE_SSID";
const char *WIFI_PASSWORD = "VOTRE_MOT_DE_PASSE";

const char *MQTT_HOST = "192.168.1.100";  // IP du serveur hébergeant Mosquitto
const int MQTT_PORT = 1883;
const char *DEVICE_ID = "jardin-1";

const unsigned long PUBLISH_INTERVAL_MS = 60000;

// Une entrée par vanne : nom (doit contenir "vanne", voir contrat MQTT),
// pin de commande du relais. Ajouter une ligne suffit pour une 4e/5e vanne.
struct Valve {
  const char *name;
  int pin;
  bool isOpen;
  unsigned long autoCloseAtMs;  // 0 = pas de minuteur en cours
};

Valve valves[] = {
  {"vanne_1", 25, false, 0},
  {"vanne_2", 26, false, 0},
  {"vanne_3", 27, false, 0},
};
const int NUM_VALVES = sizeof(valves) / sizeof(valves[0]);

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
unsigned long lastPublish = 0;

String stateTopic() { return String("arrosage/") + DEVICE_ID + "/etat"; }
String commandTopic() { return String("arrosage/") + DEVICE_ID + "/commande"; }

void connectWifi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }
}

// Remplacez ces fonctions par la lecture réelle de vos capteurs.
float readWaterLevelCm() { return 0.0; }
float readHumidityPct() { return 0.0; }
float readTemperatureC() { return 0.0; }

void applyValveState(Valve &valve, bool open) {
  valve.isOpen = open;
  digitalWrite(valve.pin, open ? HIGH : LOW);
}

Valve *findValve(const String &name) {
  for (int i = 0; i < NUM_VALVES; i++) {
    if (name == valves[i].name) return &valves[i];
  }
  return nullptr;
}

void publishState() {
  StaticJsonDocument<256> doc;
  doc["water_level_cm"] = readWaterLevelCm();
  doc["humidity_pct"] = readHumidityPct();
  doc["temperature_c"] = readTemperatureC();
  for (int i = 0; i < NUM_VALVES; i++) {
    doc[valves[i].name] = valves[i].isOpen ? "open" : "closed";
  }

  char payload[256];
  size_t len = serializeJson(doc, payload);
  mqttClient.publish(stateTopic().c_str(), payload, len);
}

// Republie un message "close" retenu sur le topic de commande pour que l'état
// retenu par le broker reflète toujours la commande courante et non une
// commande "open"+durée périmée (sinon un redémarrage rejouerait une
// ouverture déjà terminée). Limite connue : si l'ESP32 redémarre PENDANT
// qu'un minuteur est en cours, la durée restante n'est pas persistée et la
// commande "open" retenue reprendra pour la durée complète d'origine.
void publishRetainedClose(const char *valveName) {
  StaticJsonDocument<64> doc;
  doc[valveName] = "close";
  char payload[64];
  size_t len = serializeJson(doc, payload);
  mqttClient.publish(commandTopic().c_str(), payload, len, true /* retain */);
}

void onMqttMessage(char *topic, byte *message, unsigned int length) {
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, message, length) != DeserializationError::Ok) return;

  long durationS = doc["duration_s"] | 0;

  for (JsonPair kv : doc.as<JsonObject>()) {
    String key = kv.key().c_str();
    Valve *valve = findValve(key);
    if (valve == nullptr) continue;  // ignore "duration_s" et clés inconnues

    String action = kv.value().as<const char *>();
    if (action == "open") {
      applyValveState(*valve, true);
      valve->autoCloseAtMs = durationS > 0 ? millis() + (unsigned long)durationS * 1000UL : 0;
    } else if (action == "close") {
      applyValveState(*valve, false);
      valve->autoCloseAtMs = 0;
    }
  }
  publishState();
}

void checkValveTimers() {
  unsigned long now = millis();
  for (int i = 0; i < NUM_VALVES; i++) {
    if (valves[i].autoCloseAtMs != 0 && now >= valves[i].autoCloseAtMs) {
      applyValveState(valves[i], false);
      valves[i].autoCloseAtMs = 0;
      publishRetainedClose(valves[i].name);
      publishState();
    }
  }
}

void connectMqtt() {
  while (!mqttClient.connected()) {
    String clientId = String("esp32-") + DEVICE_ID;
    if (mqttClient.connect(clientId.c_str())) {
      mqttClient.subscribe(commandTopic().c_str());
    } else {
      delay(2000);
    }
  }
}

void setup() {
  for (int i = 0; i < NUM_VALVES; i++) {
    pinMode(valves[i].pin, OUTPUT);
    digitalWrite(valves[i].pin, LOW);
  }

  connectWifi();
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(onMqttMessage);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  if (!mqttClient.connected()) {
    connectMqtt();
  }
  mqttClient.loop();

  checkValveTimers();

  unsigned long now = millis();
  if (now - lastPublish >= PUBLISH_INTERVAL_MS) {
    lastPublish = now;
    publishState();
  }
}
