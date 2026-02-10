import os
from dotenv import load_dotenv
from Simulation import Simulation


if __name__ == '__main__':
    load_dotenv()  # charge le .env
    TEAM_CODE = os.getenv("TEAM_CODE")
    simu = Simulation(TEAM_CODE)
    simu.start()
    simu.post_order("MERI", "BUY", 10)
    simu.stop()