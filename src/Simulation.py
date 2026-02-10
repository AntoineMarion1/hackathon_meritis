import requests

ORDER_URL = "https://hkt25.codeontime.fr/api/order"
SIMULATION_URL = "https://hkt25.codeontime.fr/api/simulation"

class Simulation:
    def __init__(self, team_code: str, data):
        self.team_code = team_code
        self.headers = {"X-Team-Code": self.team_code}

    def start(self)->None:
        """
        Démarrer la simulation.
        """
        response = requests.post(
            SIMULATION_URL + "/start",
            headers=self.headers
        )
        if response.status_code == 200:
            print("Simulation démarrée avec succès.")
        else:
            print("Erreur lors du démarrage de la simulation.")

    def pause(self)->None:
        """
        Mettre en pause la simulation.
        """
        response = requests.post(
            SIMULATION_URL + "/pause",
            headers=self.headers
        )
        if response.status_code == 200:
            print("Simulation mise en pause avec succès.")
        else:
            print("Erreur lors de la mise en pause de la simulation.")
    
    def stop(self)->None:
        """
        Arrêter la simulation et la réinitialiser.
        """
        response = requests.post(
            SIMULATION_URL + "/stop",
            headers=self.headers
        )
        if response.status_code == 200:
            print("Simulation arrêtée avec succès.")
        else:
            print("Erreur lors de l'arrêt de la simulation.")
            
    def post_order(self, symbol: str, order: str, quantity: int)->None:
        """
        Poster un ordre sur la simulation.
        """
        response = requests.post(
            ORDER_URL,
            headers=self.headers,
            json={"symbol": symbol, "action": order, "quantity": quantity}
        )
        if response.status_code == 200:
            print("Ordre exécuté avec succès.")
        else:
            print("Erreur lors du passage de l'ordre :", response.status_code)