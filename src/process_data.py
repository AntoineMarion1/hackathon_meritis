import pandas as pd

def load_data(symbol: str):
    file_name = f"market_data/{symbol}.csv"
    df = pd.read_csv(file_name)

if __name__ == "__main__":
    load_data("MERI")