from constant import ORDER_URL, HEADER
import requests

class Bot:
    def __init__(self):
        return
    
    def post_order(self):
        response = requests.post(
            ORDER_URL,
            headers=HEADER,
            json={"symbol": "MERI", "action": "BUY", "quantity": 10}
        )
        print("ordre passé avec succès" if response.status_code==200 else "erreur dans l'ordre")
     
