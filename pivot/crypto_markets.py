"""Bounded research watch universe; inclusion never grants trading permission."""

MARKETS = (
    {'symbol': 'BTC/USD', 'name': 'Bitcoin', 'video_market': True},
    {'symbol': 'ETH/USD', 'name': 'Ethereum', 'video_market': False},
    {'symbol': 'SOL/USD', 'name': 'Solana', 'video_market': False},
    {'symbol': 'LINK/USD', 'name': 'Chainlink', 'video_market': False},
    {'symbol': 'XRP/USD', 'name': 'XRP', 'video_market': False},
)
SYMBOLS = tuple(row['symbol'] for row in MARKETS)
ALIASES = {symbol.replace('/', ''): symbol for symbol in SYMBOLS}
