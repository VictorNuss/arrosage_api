-- IP locale fixe du device (réseau local), utilisée pour relayer les mises
-- à jour de firmware (OTA) depuis le dashboard vers l'ESP32 en HTTP, sans
-- passer par MQTT ni USB. Configurée une fois par device depuis le dashboard.
ALTER TABLE devices ADD COLUMN IF NOT EXISTS ip_address TEXT;
