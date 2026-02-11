from threading import Thread
from queue import Queue
from bot import Bot
from constant import HEADER, SIMULATION_URL, TEAM_CODE
import time
import requests
import json
import websocket
import ssl


# Variables globales pour contrôler l'état de la simulation entre les threads
simulation_running = True
simulation_paused = False
price_queue = Queue()

# ---------------- WebSocket ----------------
def on_message(ws, message):
    if message == "PING":
        ws.send("PONG")
        return
    else:
        data = json.loads(message)
        if data is not None:
            price_queue.put(data)

def on_open(ws):
    print("✅ Connexion ouverte")

def on_error(ws, error):
    print("Erreur websocket :", error)

def on_close(ws, code, msg):
    print(f"Connexion fermée ({code}) {msg}")

def ws_client():
    ws = websocket.WebSocketApp(
        f"wss://hkt25.codeontime.fr/ws/simulation?code={TEAM_CODE}",
        on_message=on_message,
        on_open=on_open,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

# ---------------- Bot ----------------
def trading_bot(bot: Bot):
    global simulation_running, simulation_paused
    while simulation_running:
        if simulation_paused:
            time.sleep(0.2)
            continue

        try:
            msg = price_queue.get(timeout=1)
        except Exception:
            continue

        # Filtre: on veut seulement les ticks
        if isinstance(msg, dict) and msg.get("type") == "TICK":
            bot.on_tick(msg)

# ---------------- Clavier ----------------
def keyboard_listener(sim: "Simulation"):
    """
    Touche 'p' pour pause/reprise, 'q' pour arrêter
    """
    global simulation_paused, simulation_running
    while simulation_running:
        key = input("Appuyez sur 'p' pour pause/reprise, 'q' pour arrêter : \n").lower()
        if key == "p":
            if (simulation_paused):
                sim.resume()
            else: 
                sim.pause()
        elif key == "q":
            simulation_running = False
            print("❌ Arrêt demandé par l'utilisateur")
            break

# ---------------- Simulation ----------------
class Simulation:
    def __init__(self, bot=None):
        self.stop() # Pour bien tout nettoyer
        self.bot = bot

    def start(self):
        global simulation_running, simulation_paused
        simulation_running = True
        simulation_paused = False

        response = requests.post(SIMULATION_URL + "/start", headers=HEADER)
        if response.status_code == 200:
            print("Simulation démarrée avec succès.")
        else:
            print("Erreur lors du démarrage de la simulation.")

        # Lancer les threads
        ws_thread = Thread(target=ws_client, daemon=True)
        bot_thread = Thread(target=trading_bot, args=[self.bot],daemon=True)
        kb_thread = Thread(target=keyboard_listener, args=[self], daemon=True)

        ws_thread.start()
        bot_thread.start()
        kb_thread.start()

        # Boucle principale pour rester actif
        while simulation_running:
            time.sleep(1)

        # Stop simulation via API
        self.stop()

    def pause(self):
        global simulation_paused
        simulation_paused = True
        response = requests.post(SIMULATION_URL + "/pause", headers=HEADER)
        print("Simulation mise en pause" if response.status_code == 200 else "Erreur lors de la mise en pause")

    def resume(self):
        global simulation_paused
        simulation_paused = False
        response = requests.post(SIMULATION_URL + "/start", headers=HEADER)
        print("Simulation reprise" if response.status_code == 200 else "Erreur lors de la reprise")

    def stop(self):
        response = requests.post(SIMULATION_URL + "/stop", headers=HEADER)
        print("Simulation arrêtée" if response.status_code == 200 else "Erreur lors de l'arrêt")
