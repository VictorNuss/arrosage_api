"""Serveur HTTP minimal simulant l'endpoint OTA d'un ESP32, pour tester le
relais du dashboard (onglet Firmware) sans matériel réel.

Usage :
    python mock_ota_server.py [port]   (8090 par défaut)

Comportement, pour se rapprocher du contrat réel (voir esp32/README.md >
"Mise à jour firmware") :
  - POST /api/ota, corps = .bin brut -> 200 "OK, redemarrage en cours..."
  - Si le corps est vide ou commence par b"FAIL" (pratique pour tester le
    chemin d'erreur du dashboard sans avoir un vrai firmware invalide sous
    la main) -> 400 avec un message d'erreur.
  - Un court délai est simulé avant la réponse, pour se rapprocher d'une
    vraie écriture flash et vérifier que le bouton "Confirmer" du dashboard
    reste désactivé pendant ce temps.

Ce script ne dépend que de la bibliothèque standard : il tourne directement
sur l'hôte (pas besoin de Docker) via `python esp32/mock_ota_server.py`.
"""

import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

SIMULATED_FLASH_DELAY_SECONDS = 2


class OtaHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/ota":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        print(f"[mock-ota] Reçu {len(body)} octets sur {self.path}")

        time.sleep(SIMULATED_FLASH_DELAY_SECONDS)

        if not body or body.startswith(b"FAIL"):
            message = "Image invalide (mock_ota_server: corps vide ou préfixé FAIL)"
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(message.encode("utf-8"))
            print(f"[mock-ota] Rejeté: {message}")
            return

        message = "OK, redemarrage en cours..."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))
        print(f"[mock-ota] Accepté ({len(body)} octets)")

    def log_message(self, format, *args):
        pass  # évite le double-log, on a déjà nos propres print() ci-dessus


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    server = HTTPServer(("0.0.0.0", port), OtaHandler)
    print(f"Serveur OTA simulé sur http://0.0.0.0:{port}/api/ota (Ctrl+C pour arrêter)")
    server.serve_forever()


if __name__ == "__main__":
    main()
