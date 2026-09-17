from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import stat
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .version import APP_VERSION


@dataclass(frozen=True)
class Hosting:
    """Explicit reverse-proxy settings; these do not replace proxy authentication."""
    public_origin: str = ''
    url_prefix: str = ''
    runtime_name: str = ''
    revision: str = ''

    def __post_init__(self):
        origin = self.public_origin
        if origin:
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError:
                raise ValueError('PUBLIC_ORIGIN must be one HTTPS origin without a path') from None
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment or '*' in origin
                    or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', parsed.hostname)
                    or any(not part or part.startswith('-') or part.endswith('-') for part in parsed.hostname.split('.'))
                    or origin != f'https://{parsed.hostname}' + (f':{port}' if port is not None else '')):
                raise ValueError('PUBLIC_ORIGIN must be one HTTPS origin without a path')
        if self.url_prefix and not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)+', self.url_prefix):
            raise ValueError('URL_PREFIX must be a path such as /pivot without a trailing slash')
        if self.runtime_name and (len(self.runtime_name) > 80 or any(ord(c) < 32 for c in self.runtime_name)):
            raise ValueError('RUNTIME_NAME must be a short display name')
        if self.revision and not re.fullmatch(r'[a-f0-9]{40}', self.revision):
            raise ValueError('PIVOT_REVISION must be the full Git commit identifier')

    @classmethod
    def from_values(cls, values):
        return cls(values.get('PUBLIC_ORIGIN') or '', values.get('URL_PREFIX') or '',
                   values.get('PIVOT_RUNTIME_NAME') or values.get('RUNTIME_NAME') or '',
                   values.get('PIVOT_REVISION') or '')

    def describe(self):
        hosted = bool(self.public_origin)
        name = self.runtime_name or ('the hosted server' if hosted else 'this Mac')
        return {'mode': 'hosted' if hosted else 'local', 'name': name,
                'message': (f'Running on {name} · The server must stay online. Your laptop can disconnect.' if hosted
                            else f'Running on {name} · Keep it awake and connected for automated trading.')}


@contextmanager
def live_enable_guard(path, enabled):
    """Coordinate enabling with the updater's exclusive, cross-process deployment lock."""
    if path is None or enabled is not True:
        yield
        return
    import fcntl
    with Path(path).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409, detail='Update in progress; wait before enabling Live money') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


UPDATE_MESSAGES = {
    'checking': 'Checking GitHub for an update.',
    'building': 'Preparing an update.',
    'built': 'Update prepared; installation is pending.',
    'waiting_off': 'Update waiting: turn Live money Off to install.',
    'blocked_exposure': 'Update waiting: the account must be current with no open positions or orders.',
    'deploying': 'Installing an update.',
    'current': 'Running the latest checked version.',
    'updated': 'Update installed.',
    'rolled_back': 'Update did not pass checks; the previous version was restored.',
    'error': 'Update checks need attention. The running app has not been confirmed as current.',
}


def deployment_status(path, now=None):
    """Read only bounded, known display fields; operator errors never enter the API."""
    unavailable = {'state': 'unavailable', 'message': 'Update status is unavailable.'}
    if path is None:
        return unavailable
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return unavailable
            raw = source.read(4097)
        if len(raw) > 4096:
            return unavailable
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('state') not in UPDATE_MESSAGES:
            return unavailable
        checked = datetime.fromisoformat(payload['checked_at'])
        if checked.tzinfo is None:
            return unavailable
        age = ((now or datetime.now(timezone.utc)) - checked).total_seconds()
        if age < -60:
            return unavailable
        result = {'state': payload['state'], 'message': UPDATE_MESSAGES[payload['state']],
                  'checked_at': checked.astimezone(timezone.utc).isoformat()}
        for key in ('active_revision', 'candidate_revision'):
            revision = payload.get(key)
            if isinstance(revision, str) and re.fullmatch(r'[a-f0-9]{40}', revision):
                result[key] = revision
        # A tested image build can take twenty minutes; the updater's service
        # timeout is twenty-five. Ordinary checks happen every five minutes.
        if age > (1800 if payload['state'] in ('building', 'deploying') else 600):
            result.update(state='overdue', message='Update checks are overdue; the running version may be behind GitHub.')
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return unavailable


def create_app(service, background=True, hosting=None, deployment_lock=None, deployment_status_path=None):
    hosting = hosting or Hosting()
    if service.executor is not None:
        service.executor.revision = hosting.revision
    def decorate_snapshot(payload):
        result = {**payload, 'hosting': hosting.describe(), 'revision': hosting.revision, 'app_version': APP_VERSION}
        if hosting.public_origin:
            result['deployment'] = deployment_status(deployment_status_path)
        return result
    @asynccontextmanager
    async def lifespan(app):
        if background:
            service.start()
        yield
        service.stop()
    app = FastAPI(title='Pivot · Video strategies', version=APP_VERSION, lifespan=lifespan, root_path=hosting.url_prefix)
    allowed_hosts = ['127.0.0.1', 'localhost'] + ([] if background else ['testserver'])
    if hosting.public_origin:
        allowed_hosts.append(urlsplit(hosting.public_origin).hostname)
    allowed_origins = ([hosting.public_origin] if hosting.public_origin else
                       ['http://127.0.0.1:5173', 'http://localhost:5173'])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins,
                       allow_methods=['GET', 'PUT'], allow_headers=['Content-Type', 'X-Pivot-Intent'])

    @app.middleware('http')
    async def protect(request: Request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            allowed = set(allowed_origins)
            if not hosting.public_origin and request.url.hostname in allowed_hosts:
                # Use the origin, not base_url: root_path may contain a proxy prefix.
                allowed.add(f'{request.url.scheme}://{request.url.netloc}')
            route_path = request.scope['path']
            if hosting.url_prefix and route_path.startswith(hosting.url_prefix + '/'):
                route_path = route_path[len(hosting.url_prefix):]
            if origin not in allowed or request.headers.get('x-pivot-intent') != ('live-control' if route_path == '/api/live' else 'settings'):
                return JSONResponse({'detail': 'Use the app from its configured address to change settings'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.get('/api/health')
    def health():
        return {'ok': True, 'version': 'video-execution-v3', 'live_enabled': service.executor.enabled() if service.executor else False,
                'legacy_loaded': False, 'revision': hosting.revision, 'app_version': APP_VERSION}

    @app.get('/api/snapshot')
    def snapshot():
        return decorate_snapshot(service.snapshot())

    @app.get('/api/decisions')
    def decisions(limit: int = 50, before_id: int | None = None):
        try:
            return service.store.decision_history(limit, before_id)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from None

    @app.get('/api/execution-checks')
    def execution_checks(limit: int = 50, before_id: int | None = None):
        try:
            return service.store.execution_history(limit, before_id)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from None

    @app.put('/api/settings')
    async def settings(request: Request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError('Invalid settings')
            return service.save_settings(payload)
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            raise HTTPException(422, detail=str(exc)) from None

    @app.put('/api/live')
    def live(payload: dict):
        from .feeds import FeedError
        try:
            with live_enable_guard(deployment_lock, payload.get('enabled')):
                return decorate_snapshot(service.set_live(payload))
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from None
        except FeedError:
            raise HTTPException(503, detail='Cannot verify the Alpaca account. Try again when the connection returns.') from None

    # No generic order-submission or legacy controls route.
    root = Path(__file__).resolve().parent / 'web'
    app.mount('/', StaticFiles(directory=root, html=True), name='web')
    return app
