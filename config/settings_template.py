# Configurações do Bot BTC/USDT - Binance
# COPIE este arquivo para settings.py e preencha com suas chaves reais
# NUNCA commite settings.py no git!

# === CHAVES DE API BINANCE ===
API_KEY = "SUA_API_KEY_AQUI"
API_SECRET = "SEU_API_SECRET_AQUI"

# === PAR DE TRADING ===
SYMBOL = "BTCUSDT"
SYMBOL_WS = "btcusdt"

# === FILTROS DE MONITORAMENTO ===
MIN_BTC_VOLUME = 0.5
WHALE_BTC_VOLUME = 5.0

# === ENDPOINTS ===
REST_BASE_URL = "https://api.binance.com"
WS_BASE_URL = "wss://stream.binance.com:9443"

# === TELEGRAM (opcional) ===
TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# === BANCO DE DADOS ===
DB_PATH = "data/btc_data.db"
