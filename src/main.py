import os
from Simulation import Simulation
from bot import Bot
from process_data import load_data

if __name__ == '__main__':
    b = Bot(
        mom=20,
        vol=40,
        thresh=2.0,
        max_gross=0.4,
        target_vol_annual=0.15,
        dd_stop=0.03,
        min_trade_qty=5,
        short=True
    )
    simu = Simulation(b)
    simu.start()