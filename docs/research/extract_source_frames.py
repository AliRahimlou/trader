#!/usr/bin/env python3
"""Rebuild local, unaltered video evidence; no network or trading imports."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            result.update(chunk)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--output-root', type=Path,
                        default=Path(__file__).resolve().parents[2] / 'runtime/research/video-evidence')
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name('source-evidence.json').read_text())
    args.output_root.mkdir(parents=True, exist_ok=True)
    observations = []
    for source in manifest['sources']:
        video = args.source_root / source['filename']
        if digest(video) != source['sha256']:
            raise ValueError(f"Source hash mismatch: {source['id']}; refuse to relabel different media")
        for second in source['sample_seconds']:
            name = f"{source['id']}-{second:03d}s.png"
            command = ['ffmpeg', '-hide_banner', '-loglevel', 'info', '-ss', str(second),
                       '-copyts', '-i', str(video), '-frames:v', '1', '-vf', 'showinfo',
                       '-y', str(args.output_root / name)]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            match = re.search(r'n:\s*0\s+pts:\s*(\d+)\s+pts_time:([\d.]+)', result.stderr)
            if not match:
                raise ValueError(f'Unable to verify decoded source timestamp: {name}')
            observations.append({'source': source['id'], 'seek_seconds': second,
                                 'source_pts': int(match.group(1)),
                                 'source_pts_seconds': float(match.group(2)),
                                 'frame': name, 'frame_sha256': digest(args.output_root / name)})
    (args.output_root / 'extraction-manifest.json').write_text(json.dumps(observations, indent=2) + '\n')
    rows = []
    for item in manifest['examples']:
        found = next(row for row in observations
                     if row['source'] == item['source'] and row['seek_seconds'] == item['second'])
        esc = html.escape
        sections = ''.join(f'<h3>{esc(label)}</h3><p>{esc(item[key])}</p>' for key, label in
                           [('observed', 'Observed'), ('interpretation', 'Interpretation'),
                            ('not_observable', 'Not established by this frame')])
        rows.append(f'<article id="{esc(item["id"])}"><header><small>{esc(item["classification"])}'
                    f'</small><h2>{esc(item["title"])}</h2><p>{esc(item["source"].upper())} · '
                    f'{found["source_pts_seconds"]:.6f} seconds from recording start</p></header>'
                    f'<div class="example"><a href="{esc(found["frame"])}"><img '
                    f'src="{esc(found["frame"])}" alt="Original source frame: {esc(item["title"])}"></a>'
                    f'<section>{sections}</section></div></article>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Primary video evidence — September 17, 2026</title><style>
body{font:17px/1.55 system-ui,sans-serif;color:#172534;background:#f3f5f7;margin:0 auto;max-width:1150px;padding:36px}
h1,h2,h3{line-height:1.2}h1{font-size:34px}h2{font-size:25px}h3{font-size:18px;color:#364861}
article{margin:30px 0;padding:26px;background:white;border:1px solid #d2dce4;border-radius:12px}
.example{display:grid;grid-template-columns:minmax(200px,360px) 1fr;gap:28px}.example img{width:100%;height:auto}
small{font-weight:700;color:#365c72;text-transform:uppercase}a{color:#195b86}
@media(max-width:650px){body{padding:16px}.example{display:block}.example img{max-width:360px}}
</style><h1>What the primary recordings actually establish</h1>
<p>Unaltered frames with separate researcher annotations. These are source examples, not broker fills or measured returns.
Exact decoded source timestamps are recorded below. Chart times, phone-clock times and recording times are different clocks.</p>
<p>Source SHA-256 validation passed before extraction. Images remain private, local and excluded from Git.
Click a frame for full resolution. This page can be regenerated from docs/research/extract_source_frames.py.</p>'''
    (args.output_root / 'annotated-examples.html').write_text(page + ''.join(rows) + '</html>\n')
    print(json.dumps({'sources_verified': len(manifest['sources']), 'frames_extracted': len(observations),
                      'annotated_examples': len(rows), 'output_root': str(args.output_root)}, indent=2))


if __name__ == '__main__':
    main()
