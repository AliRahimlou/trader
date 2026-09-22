"""Owner-controlled runtime. Hosted access uses an authenticated local reverse proxy."""
import argparse
import logging
import os
from pathlib import Path
from dotenv import dotenv_values
import uvicorn
from .api import Hosting, create_app
from .feeds import ReadOnlyFeeds
from .service import Service
from .store import Store


def main():
    parser = argparse.ArgumentParser(description='Run the video-only Pivot app')
    parser.add_argument('--host', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8011)
    args = parser.parse_args()
    # Worker and execution failures were previously invisible in container logs.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    root = Path(__file__).resolve().parent.parent
    # Only explicit provider keys are consumed. Legacy LIVE_* flags cannot enable orders.
    config = {**dotenv_values(root / '.env'), **os.environ}
    if config.get('PIVOT_ALERT_WEBHOOK_URL'):
        # Alerts read the environment at call time; the private .env is the owner's configuration surface.
        os.environ.setdefault('PIVOT_ALERT_WEBHOOK_URL', config['PIVOT_ALERT_WEBHOOK_URL'])
    if config.get('PIVOT_CRYPTO_PAUSED') is not None:
        # The crypto pause flag is read at call time by every worker; .env is the owner's override surface.
        os.environ.setdefault('PIVOT_CRYPTO_PAUSED', config['PIVOT_CRYPTO_PAUSED'])
    hosting = Hosting.from_values(config)
    if args.host == '0.0.0.0' and not hosting.public_origin:
        parser.error('--host 0.0.0.0 requires PUBLIC_ORIGIN and an authenticated reverse proxy')
    feeds = ReadOnlyFeeds(config)
    from .broker import AlpacaBroker
    import fcntl
    state_dir = root / 'runtime' / 'pivot-v2'
    state_dir.mkdir(parents=True, exist_ok=True)
    process_lock = (state_dir / 'worker.lock').open('a')
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another Pivot backend already owns this runtime') from None
    service = Service(feeds, Store(root / 'runtime' / 'pivot-v2' / 'audit.sqlite3'), broker=AlpacaBroker(feeds))
    uvicorn.run(create_app(service, hosting=hosting, deployment_lock=state_dir / 'deployment.lock',
                          deployment_status_path=state_dir / 'deployment-status.json'), host=args.host, port=args.port,
                log_level='warning', proxy_headers=True, forwarded_allow_ips='127.0.0.1')


if __name__ == '__main__':
    main()
