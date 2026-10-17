-- Version du firmware en cours d'exécution sur le device, publiée par le
-- device lui-même (clé MQTT "fw_version", à chaque connexion et sur
-- get_status, comme "ip") : permet d'afficher la version avant/après une
-- mise à jour OTA et de vérifier qu'elle a bien été appliquée.
ALTER TABLE devices ADD COLUMN IF NOT EXISTS fw_version TEXT;
