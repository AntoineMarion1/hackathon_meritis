import websocket
from threading import Thread
from queue import Queue
import time
import json
import json
import ssl
import os
from dotenv import load_dotenv

def on_message(ws, message):
    # Filtrer les heartbeat
    if message == "PING":
        ws.send("PONG")
        print("ping recu")
        return
    else:
        # Traiter les messages JSON normalement
        data = json.loads(message)
        print(f"[{data['type']}] {data.get('date', '')}")
        price = data.get("marketData")  # dépend de ton message
        if price is not None:
            price_queue.put(price)  # envoi du prix au bot

def on_open(ws):
    print("✅ Connexion ouverte")

def on_error(ws, error):
    print("Erreur websocket :", error)

def on_close(ws, code, msg):
    print(f"Connexion fermée ({code}) {msg}")

# Thread 1 : client WebSocket
def ws_client():

    ws = websocket.WebSocketApp(
        f"wss://hkt25.codeontime.fr/ws/simulation?code={TEAM_CODE}",
        on_message=on_message,
        on_open=on_open,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}) # A RETIRER ET A FAIRE PROPREMENT POUR METTRE EN PROD

# Thread 2 : bot de trading
def trading_bot():
    while True:
        if (not price_queue.empty()):
            # Récupère les prix du WebSocket, avec timeout pour ne pas bloquer
            price = price_queue.get(timeout=1)
            print(f"Bot reçoit le prix : {price}")
            # Ici tu peux ajouter ta logique de trading
        else:
            continue

if __name__ == "__main__":
    load_dotenv()  # charge le .env
    TEAM_CODE = os.getenv("TEAM_CODE")
    price_queue = Queue()
    print("Démarrage du client WS…")
    # Lancer les threads
    ws_thread = Thread(target=ws_client, daemon=True)
    bot_thread = Thread(target=trading_bot, daemon=True)

    ws_thread.start()
    bot_thread.start()

    # Boucle principale pour ne pas quitter immédiatement
    while True:
        time.sleep(1)
