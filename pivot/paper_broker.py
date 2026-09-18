"""Fixed-destination adapter for explicit, isolated broker-paper commissioning.

This adapter never accepts a configured trading host. Production does not import
or instantiate it unless the separate commissioning command is explicitly used.
"""
from .broker import AlpacaBroker
from .feeds import ReadOnlyFeeds

PAPER_HOST = 'https://paper-api.alpaca.markets'


class PaperAlpacaBroker(AlpacaBroker):
    def __init__(self, api_key, secret, *, session=None):
        if any(not isinstance(value, str) or not value.strip() or '\r' in value or '\n' in value
               for value in (api_key, secret)):
            raise ValueError('Separate paper API credentials are required')
        feeds = ReadOnlyFeeds({'APCA_API_BASE_URL': PAPER_HOST,
                               'APCA_API_KEY_ID': api_key, 'APCA_API_SECRET_KEY': secret,
                               'APCA_API_FEED': 'iex', 'VIX_PROVIDER': 'massive'}, session=session)
        super().__init__(feeds, session=session or feeds.session)

    def assert_paper_only(self):
        if self.feeds.broker_url != PAPER_HOST:
            raise ValueError('Paper commissioning refuses any other broker destination')

    @property
    def requires_vix_entry_quote(self):
        # This explicit workflow commissions order handling, not a strategy or VIX feed.
        return False

    def _request(self, *args, **kwargs):
        self.assert_paper_only()
        return super()._request(*args, **kwargs)

    def account(self):
        self.assert_paper_only()
        return super().account()

    def positions(self):
        self.assert_paper_only()
        return super().positions()

    def orders(self):
        self.assert_paper_only()
        return super().orders()

    def clock(self):
        self.assert_paper_only()
        return super().clock()

    def asset(self, symbol):
        self.assert_paper_only()
        return super().asset(symbol)

    def quote(self, symbol):
        self.assert_paper_only()
        return super().quote(symbol)
