import os
from simulation import Simulation
from bot import Bot
from process_data import load_data

if __name__ == '__main__':
    b = Bot()
    simu = Simulation(b)
    simu.start()
