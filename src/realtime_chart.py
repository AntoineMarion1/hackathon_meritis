import dash
from dash import dcc, html, Input, Output
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
from collections import deque
from threading import Thread
from queue import Queue
import json
import websocket
import ssl
from constant import TEAM_CODE

# Variables globales
price_queue = Queue()
chart_running = False

class RealtimeWebSocketChart:
    def __init__(self, symbol="MERI", candle_interval=5, ma_periods=None, donchian_period=20):
        """
        Interface graphique pour visualiser les prix du websocket en temps réel.
        
        Args:
            symbol: Symbole à afficher (MERI ou TIS)
            candle_interval: Durée d'une bougie en secondes (par défaut 5s)
            ma_periods: Périodes des moyennes mobiles (défaut: [30, 50])
            donchian_period: Période du canal de Donchian (défaut: 20)
        """
        self.symbol = symbol
        self.candle_interval = candle_interval
        self.update_interval = 1  # Rafraîchissement de l'interface (1 seconde)
        self.ma_periods = ma_periods or [30, 50]
        self.donchian_period = donchian_period
        
        # Stockage des données
        self.price_data = deque(maxlen=1000)  # Historique des prix (close)
        self.candles = {}  # Bougies par date
        self.last_price = None
        self.last_update_time = None
        self.portfolio_info = None
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        
        # Initialisation de Dash
        self.app = dash.Dash(__name__)
        self.setup_layout()
        self.setup_callbacks()
    
    def setup_layout(self):
        """Configure l'interface utilisateur"""
        self.app.layout = html.Div([
            html.H1(f'Chart Temps Réel - {self.symbol}', 
                   style={'textAlign': 'center', 'marginBottom': '20px'}),
            
            html.Div([
                html.Button('Démarrer', id='start-stop-btn', n_clicks=0,
                           style={'padding': '10px 30px', 'fontSize': '16px'})
            ], style={'textAlign': 'center', 'marginBottom': '20px'}),
            
            html.Div([
                html.Div(id='current-price', 
                        style={'fontSize': '24px', 'fontWeight': 'bold', 'color': '#2196F3'}),
                html.Div(id='last-update', 
                        style={'fontSize': '14px', 'color': 'gray', 'marginTop': '5px'}),
                html.Div(id='status', 
                        style={'fontSize': '14px', 'marginTop': '10px'}),
                html.Div(id='portfolio-info', 
                        style={'fontSize': '14px', 'marginTop': '5px', 'color': '#666'})
            ], style={'textAlign': 'center', 'marginBottom': '30px'}),
            
            dcc.Graph(id='price-chart', style={'height': '600px'}),
            
            dcc.Interval(
                id='interval-component',
                interval=self.update_interval * 1000,
                n_intervals=0,
                disabled=True
            )
        ])
    
    def setup_callbacks(self):
        """Configure les callbacks Dash"""
        
        @self.app.callback(
            [Output('price-chart', 'figure'),
             Output('current-price', 'children'),
             Output('last-update', 'children'),
             Output('status', 'children'),
             Output('start-stop-btn', 'children'),
             Output('portfolio-info', 'children')],
            [Input('interval-component', 'n_intervals'),
             Input('start-stop-btn', 'n_clicks')]
        )
        def update_chart(n_intervals, n_clicks):
            # Déterminer si démarré
            started = (n_clicks is not None and n_clicks % 2 == 1)
            
            # Traiter les nouveaux prix de la queue
            if started:
                queue_size = price_queue.qsize()
                if queue_size > 0:
                    print(f"🔄 Traitement de {queue_size} éléments dans la queue...")
                self.process_price_queue()
            
            # Créer le graphique
            fig = self.create_chart()
            
            # Informations de prix
            current_price = "En attente de données..."
            last_update = ""
            status = ""
            
            portfolio = ""
            if self.last_price is not None:
                current_price = f"Prix de clôture: {self.last_price:.5f}"
                if self.last_update_time:
                    last_update = f"Dernière mise à jour: {self.last_update_time.strftime('%H:%M:%S')}"
                status = f"📊 {len(self.price_data)} ticks reçus | {len(self.candles)} bougies"
            
            if self.portfolio_info:
                cash = self.portfolio_info.get('cash', 0)
                valuation = self.portfolio_info.get('valuation', 0)
                portfolio = f"💰 Cash: {cash:,.2f} | Valorisation: {valuation:,.2f}"
            
            button_label = 'Arrêter' if started else 'Démarrer'
            
            return fig, current_price, last_update, status, button_label, portfolio
        
        @self.app.callback(
            Output('interval-component', 'disabled'),
            [Input('start-stop-btn', 'n_clicks')]
        )
        def toggle_interval(n_clicks):
            global chart_running
            started = n_clicks % 2 == 1 if n_clicks else False
            
            if started and not chart_running:
                self.start_websocket()
                chart_running = True
            elif not started and chart_running:
                self.stop_websocket()
                chart_running = False
            
            return not started
    
    def process_price_queue(self):
        """Traite les prix de la queue websocket"""
        while not price_queue.empty():
            try:
                data = price_queue.get_nowait()
                print(f"📩 Données reçues du WS: type={data.get('type')}")
                
                # Vérifier que c'est un TICK
                if not isinstance(data, dict) or data.get('type') != 'TICK':
                    continue
                
                # Extraire les données du portfolio
                if 'portfolio' in data:
                    self.portfolio_info = {
                        'cash': data['portfolio'].get('cash', 0),
                        'valuation': data.get('valuation', 0)
                    }
                
                # Extraire les données de marché
                market_data = data.get('marketData', [])
                if not market_data:
                    print("⚠️ Aucune donnée de marché")
                    continue
                
                # Chercher les données pour notre symbole
                for item in market_data:
                    if item.get('symbol') == self.symbol:
                        date = item.get('date')
                        candle_data = {
                            'date': date,
                            'open': float(item.get('open', 0)),
                            'high': float(item.get('high', 0)),
                            'low': float(item.get('low', 0)),
                            'close': float(item.get('close', 0)),
                            'volume': int(item.get('volume', 0))
                        }
                        self.add_candle(candle_data)
                        print(f"✅ Bougie ajoutée pour {self.symbol}: {date} - Close: {candle_data['close']:.5f}")
                        break
                else:
                    print(f"⚠️ Symbole {self.symbol} non trouvé dans les données")
                    
            except Exception as e:
                print(f"❌ Erreur lors du traitement: {e}")
                import traceback
                traceback.print_exc()
    
    def add_candle(self, candle_data):
        """Ajoute une nouvelle bougie complète"""
        date = candle_data['date']
        close_price = candle_data['close']
        
        # Ajouter le prix de clôture à l'historique
        self.price_data.append(close_price)
        self.last_price = close_price
        self.last_update_time = datetime.now()
        
        # Stocker ou mettre à jour la bougie
        self.candles[date] = candle_data
        
        # Nettoyer les anciennes bougies (garder les 200 dernières)
        if len(self.candles) > 200:
            sorted_dates = sorted(self.candles.keys())
            for old_date in sorted_dates[:-200]:
                del self.candles[old_date]
    
    def create_chart(self):
        """Crée le graphique avec candlesticks, moyennes mobiles, Donchian et volume"""
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.7, 0.3],
            vertical_spacing=0.05,
            subplot_titles=(f'{self.symbol} - Prix', 'Volume')
        )
        
        # Graphique 1: Candlesticks
        if self.candles:
            df_candles = pd.DataFrame(list(self.candles.values()))
            df_candles = df_candles.sort_values('date')
            
            fig.add_trace(
                go.Candlestick(
                    x=df_candles['date'],
                    open=df_candles['open'],
                    high=df_candles['high'],
                    low=df_candles['low'],
                    close=df_candles['close'],
                    name=self.symbol
                ),
                row=1, col=1
            )
            
            # Moyennes mobiles simples (SMA)
            ma_colors = ['#FF9800', '#9C27B0', '#4CAF50', '#E91E63']
            for idx, period in enumerate(self.ma_periods):
                if len(df_candles) >= period:
                    ma = df_candles['close'].rolling(window=period).mean()
                    fig.add_trace(
                        go.Scatter(
                            x=df_candles['date'],
                            y=ma,
                            mode='lines',
                            name=f'MA{period}',
                            line=dict(color=ma_colors[idx % len(ma_colors)], width=2),
                            hovertemplate=f'MA{period}: %{{y:.5f}}<br>%{{x}}'
                        ),
                        row=1, col=1
                    )
            
            # Canal de Donchian
            if len(df_candles) >= self.donchian_period:
                donchian_high = df_candles['high'].rolling(window=self.donchian_period).max()
                donchian_low = df_candles['low'].rolling(window=self.donchian_period).min()
                donchian_mid = (donchian_high + donchian_low) / 2
                
                # Ligne haute du canal
                fig.add_trace(
                    go.Scatter(
                        x=df_candles['date'],
                        y=donchian_high,
                        mode='lines',
                        name=f'Donchian High ({self.donchian_period})',
                        line=dict(color='rgba(33, 150, 243, 0.6)', width=1.5, dash='dash'),
                        hovertemplate='High: %{y:.5f}<br>%{x}'
                    ),
                    row=1, col=1
                )
                
                # Ligne basse du canal
                fig.add_trace(
                    go.Scatter(
                        x=df_candles['date'],
                        y=donchian_low,
                        mode='lines',
                        name=f'Donchian Low ({self.donchian_period})',
                        line=dict(color='rgba(33, 150, 243, 0.6)', width=1.5, dash='dash'),
                        hovertemplate='Low: %{y:.5f}<br>%{x}'
                    ),
                    row=1, col=1
                )
                
                # Ligne médiane du canal
                fig.add_trace(
                    go.Scatter(
                        x=df_candles['date'],
                        y=donchian_mid,
                        mode='lines',
                        name='Donchian Mid',
                        line=dict(color='rgba(33, 150, 243, 0.3)', width=1, dash='dot'),
                        hovertemplate='Mid: %{y:.5f}<br>%{x}'
                    ),
                    row=1, col=1
                )
            
            # Graphique 2: Volume
            colors = ['red' if close < open else 'green' 
                     for close, open in zip(df_candles['close'], df_candles['open'])]
            
            fig.add_trace(
                go.Bar(
                    x=df_candles['date'],
                    y=df_candles['volume'],
                    name='Volume',
                    marker_color=colors,
                    showlegend=False
                ),
                row=2, col=1
            )
        
        # Configuration
        fig.update_layout(
            template='plotly_white',
            height=650,
            showlegend=True,
            xaxis_rangeslider_visible=False,
            uirevision='constant',  # Préserve le zoom
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )
        
        fig.update_xaxes(title_text='Date', row=2, col=1)
        fig.update_yaxes(title_text='Prix', row=1, col=1)
        fig.update_yaxes(title_text='Volume', row=2, col=1)
        
        return fig
    
    def start_websocket(self):
        """Démarre la connexion websocket"""
        if self.ws_thread is None or not self.ws_thread.is_alive():
            self.ws_thread = Thread(target=self._ws_client, daemon=True)
            self.ws_thread.start()
            print("✅ WebSocket démarré")
    
    def stop_websocket(self):
        """Arrête la connexion websocket"""
        if self.ws:
            self.ws.close()
            self.ws = None
        print("❌ WebSocket arrêté")
    
    def _ws_client(self):
        """Client websocket (s'exécute dans un thread)"""
        def on_message(ws, message):
            if message == "PING":
                ws.send("PONG")
                return
            try:
                print(f"📨 Message WS brut: {message}")
                data = json.loads(message)
                if data is not None:
                    price_queue.put(data)
                    print(f"➡️ Données ajoutées à la queue: {data}")
            except json.JSONDecodeError as e:
                print(f"❌ Erreur JSON: {e} - Message: {message}")
            except Exception as e:
                print(f"❌ Erreur on_message: {e}")
        
        def on_open(ws):
            print("✅ Connexion WebSocket ouverte")
        
        def on_error(ws, error):
            print(f"❌ Erreur WebSocket: {error}")
        
        def on_close(ws, code, msg):
            print(f"⛔ Connexion fermée ({code}) {msg}")
        
        self.ws = websocket.WebSocketApp(
            f"wss://hkt25.codeontime.fr/ws/simulation?code={TEAM_CODE}",
            on_message=on_message,
            on_open=on_open,
            on_error=on_error,
            on_close=on_close
        )
        
        print(f"🔗 Connexion au websocket: wss://hkt25.codeontime.fr/ws/simulation?code={TEAM_CODE}")
        self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
    
    def run(self, debug=False, port=8050):
        """Lance l'application Dash"""
        print(f"\n🚀 Démarrage du serveur de visualisation")
        print(f"📊 Ouvrez http://localhost:{port} dans votre navigateur\n")
        self.app.run(debug=debug, port=port, use_reloader=False)


if __name__ == "__main__":
    import sys
    # Permettre de choisir le symbole via argument
    symbol = sys.argv[1] if len(sys.argv) > 1 else "MERI"
    print(f"\n📊 Affichage du symbole: {symbol}")
    print(f"📈 Moyennes mobiles: MA30, MA50")
    print(f"📉 Canal de Donchian: 20 périodes\n")
    chart = RealtimeWebSocketChart(symbol=symbol, ma_periods=[30, 50], donchian_period=20)
    chart.run(debug=False, port=8050)
