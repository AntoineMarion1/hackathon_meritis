from dotenv import load_dotenv
import os

load_dotenv()

TEAM_CODE = os.getenv("TEAM_CODE")
HEADER = {"X-Team-Code": TEAM_CODE}
ORDER_URL = "https://hkt25.codeontime.fr/api/order"
SIMULATION_URL = "https://hkt25.codeontime.fr/api/simulation"