"""Monotonic worker progress, independent of provider readiness and market hours."""
from datetime import datetime, timezone
from threading import Lock
from time import monotonic

LIMITS = {'account': 90, 'data': 180, 'execution': 30}


class WorkerHealth:
    def __init__(self, names, *, timer=monotonic, clock=lambda: datetime.now(timezone.utc)):
        self.timer, self.clock, self.lock = timer, clock, Lock()
        self.rows = {name: {'started': None, 'finished': None, 'finished_at': None,
                            'failed': False, 'cycles': 0} for name in names}

    def begin(self, name):
        with self.lock:
            self.rows[name]['started'] = self.timer()

    def finish(self, name, failed=False):
        with self.lock:
            self.rows[name].update(finished=self.timer(), finished_at=self.clock().isoformat(), failed=failed,
                                   cycles=self.rows[name]['cycles'] + 1)

    def snapshot(self, alive=None):
        now = self.timer()
        with self.lock:
            result = []
            for name, row in self.rows.items():
                since = row['finished'] if row['finished'] is not None else row['started']
                age = max(0, now - since) if since is not None else None
                if alive is not None and name not in alive:
                    state = 'stopped'
                elif row['started'] is None:
                    state = 'not_started'
                elif age > LIMITS[name]:
                    state = 'stalled'
                elif row['finished'] is None:
                    state = 'starting'
                elif row['failed']:
                    state = 'error'
                else:
                    state = 'running'
                result.append({'name': name, 'status': state, 'age_seconds': round(age, 1) if age is not None else None,
                               'last_completed_at': row['finished_at'], 'cycles': row['cycles'],
                               'deadline_seconds': LIMITS[name]})
        return {'ready': bool(result) and all(row['status'] == 'running' for row in result),
                'workers': result, 'scope': 'worker progress; market data and broker readiness are separate'}


def next_tick(previous, interval, now):
    """Keep cadence from start times; overrun runs once promptly without a catch-up burst."""
    return previous + interval if previous + interval > now else now + min(1, interval)
