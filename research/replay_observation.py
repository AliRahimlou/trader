"""Verify a private captured observation against the identical installed engine."""
import argparse
import json
from pathlib import Path
from pivot.observations import ObservationArchive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--id', required=True, type=int)
    args = parser.parse_args(argv)
    if not args.archive.is_file() or args.id <= 0:
        parser.error('Select an existing observation archive and positive observation ID')
    try:
        result = ObservationArchive(args.archive).verify_replay(args.id)
    except (ValueError, OSError):
        parser.error('Replay unavailable: verify the archived ID, input integrity and exact engine version')
    print(json.dumps(result, indent=2))
    return 0 if result['matches'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
