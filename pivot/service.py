"""Account, analysis and order loops with separate locks and durable execution permission."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone, timedelta
from threading import Event, Lock, Thread
from .rulebook import rulebook
from .strategy import analyze
from .sizing import decimal
from .data_health import stock_health, vix_health, quote_health, expire_health


class Service:
    def __init__(self, feeds, store, broker=None):
        self.feeds, self.store = feeds, store
        self.lock, self.stop_event = Lock(), Event()
        self.threads = []
        from .execution import Executor
        self.executor = Executor(broker, store) if broker else None
        self.state = {'account':None, 'positions':[], 'orders':[], 'clock':None, 'account_at':None,
                      'analysis_at':None, 'setup':None, 'account_error':None, 'data_errors':[], 'observations':[], 'feeds':{},
                      'live_enabled':False, 'execution_available':False, 'data_health':None, 'data_valid_until':None, 'quote':None, 'quote_at':None, 'quote_error':None}

    def start(self):
        loops = [('account', self.refresh_account, 15), ('data', self.refresh_analysis, 60)]
        if self.executor: loops.append(('execution', self.refresh_execution, 5))
        for name, function, delay in loops:
            thread=Thread(target=self._loop,args=(function,delay),name=f'pivot-{name}',daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=1)

    def _loop(self,function,delay):
        while not self.stop_event.is_set():
            try:
                function()
            except Exception:
                with self.lock:
                    self.state['data_errors']=['Data update failed; waiting for a validated snapshot']
            self.stop_event.wait(delay)

    def refresh_account(self):
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                quote_future = pool.submit(self.feeds.quote) if hasattr(self.feeds, 'quote') else None
                values=list(pool.map(lambda fn:fn(),[self.feeds.account,self.feeds.positions,self.feeds.orders,self.feeds.clock]))
                quote, quote_error = None, None
                if quote_future:
                    try: quote = quote_future.result()
                    except Exception: quote_error = 'Latest QQQ quote could not be refreshed'
            with self.lock:
                self.state.update(account=values[0],positions=values[1],orders=values[2],clock=values[3],
                                  account_at=datetime.now(timezone.utc).isoformat(),account_error=None,
                                  quote=quote, quote_at=datetime.now(timezone.utc).isoformat() if quote else None, quote_error=quote_error)
        except Exception:
            with self.lock:
                self.state['account_error']='Account update unavailable; showing the last confirmed snapshot'

    def refresh_analysis(self):
        now=datetime.now(timezone.utc)
        errors,markets,vix=[],{},None
        stock_error=vix_error=None
        from .feeds import FeedError
        with ThreadPoolExecutor(max_workers=2) as pool:
            sf = pool.submit(self.feeds.stocks,now)
            insight = getattr(self.feeds, 'vix_provider', None) == 'insightsentry'
            vf = None if insight else pool.submit(self.feeds.vix,now)
            try: markets=sf.result()
            except Exception as exc:
                stock_error=str(exc) if isinstance(exc,FeedError) else 'Stock data could not be validated'
                errors.append(stock_error)
            if insight:
                # The actual exchange calendar is a dependency of VIX coverage.
                vf = pool.submit(self.feeds.vix,datetime.now(timezone.utc))
            try: vix=vf.result()
            except Exception as exc:
                vix_error=str(exc) if isinstance(exc,FeedError) else 'data could not be validated'
                errors.append('VIX: '+vix_error)
        observations=[]
        for symbol,market in markets.items():
            bars=[b for b in market.bars.get(15,[]) if b.end<=now]
            if bars:
                observations.append({'symbol':symbol,'price':bars[-1].close,'at':bars[-1].end.isoformat(),
                                     'kind':'15-minute close; observation only'})
        checked_at=datetime.now(timezone.utc)
        stocks=stock_health(markets, getattr(self.feeds, 'stock_sessions', {}), checked_at, error=stock_error,
            fetch_seconds=getattr(self.feeds, 'stock_fetch_seconds', None), refresh_mode=getattr(self.feeds, 'stock_refresh_mode', None),
            full_at=getattr(self.feeds, 'stock_last_full_at', None))
        index=vix_health(vix, checked_at, error=vix_error, details=getattr(self.feeds, 'vix_diagnostics', None))
        if markets and stocks['status'] != 'current' and not stock_error:
            errors.append('One or more required stock histories are missing, incomplete or stale')
        qqq=markets.get('QQQ')
        ready=stocks['status']=='current' and index['status']=='current'
        deadlines=[now+timedelta(seconds=90), *[m.observed_at+timedelta(seconds=90) for m in markets.values()]]
        if index['valid_until']: deadlines.append(datetime.fromisoformat(index['valid_until']))
        with self.lock:
            self.state.update(data_health={'stocks':stocks, 'vix':index, 'ready':ready}, data_valid_until=min(deadlines).isoformat() if ready else None, analysis_at=checked_at.isoformat(),setup=analyze(qqq,markets,vix,checked_at) if qqq else None, observations=observations,data_errors=errors,
                feeds={'stocks':qqq.source if qqq else 'unavailable',
                       'vix':'current' if index['status']=='current' else 'unavailable or delayed'})

    def refresh_execution(self):
        with self.lock: state = deepcopy(self.state)
        self.executor.tick(state)

    def set_live(self, payload):
        if not self.executor:
            raise ValueError('Broker execution is not configured')
        self.executor.set_live(payload)
        return self.snapshot()

    def snapshot(self):
        with self.lock: result=deepcopy(self.state)
        expire_health(result['data_health'], datetime.now(timezone.utc))
        if result['data_health'] and result['data_health']['vix']['status'] != 'current':
            result['feeds']['vix']='unavailable or delayed'
        result['quote_health'] = quote_health(result['quote'], datetime.now(timezone.utc),
            market_open=bool((result.get('clock') or {}).get('is_open')), error=result['quote_error'])
        from .policy import POLICY
        if self.executor: result.update(self.executor.snapshot())
        result.update(settings=self.store.settings(), rulebook=rulebook(), events=self.store.events(),
                      trade_results=self.store.trade_results(),
                      execution_policy=POLICY, version='video-execution-v3', runtime='Video strategy · owner-controlled execution', legacy_loaded=False)
        return result

    def save_settings(self,payload):
        if set(payload)!={'sizing_mode','target_dollars'}:
            raise ValueError('Only the purchase target can be changed')
        target=decimal(payload['target_dollars'])
        if payload['sizing_mode']!='target':
            raise ValueError('Automatic allocation is not defined by the recordings yet')
        if target<1 or target>decimal('1000000000000') or target!=target.quantize(decimal('.01')):
            raise ValueError('Target must be at least $1 with up to two decimal places')
        with self.executor.entry_lock if self.executor else nullcontext():
            if self.executor and (self.executor.enabled() or self.store.active_trade()):
                raise ValueError('Turn Live money off and wait for the current trade to finish before changing size')
            with self.lock:
                at=self.state['account_at']
                if self.state['account_error'] or not at or (datetime.now(timezone.utc)-datetime.fromisoformat(at)).total_seconds()>60:
                    raise ValueError('Wait for a current account balance')
                if self.state['positions'] or self.state['orders']:
                    raise ValueError('Existing positions and orders must finish before changing size')
                if target>decimal(self.state['account']['buying_power']):
                    raise ValueError('Target exceeds current buying power')
                normalized={'sizing_mode':'target','target_dollars':str(target.quantize(decimal('.01')))}
                self.store.save(normalized)
        return normalized
