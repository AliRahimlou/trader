"""Compare the original deployed pure analyzer with the frozen v1 research analyzer."""
import argparse
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from .data import load, audit, at_time, expected_ends
from .offline import disconnected
from research.baseline_v1 import analyze

BASELINE='facc507ae2761a76916031994bcf9f956f9369fe'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    source=subprocess.run(['git','show',BASELINE+':pivot/strategy.py'],check=True,capture_output=True,text=True).stdout
    namespace={'__name__':'pivot._deployed_baseline','__package__':'pivot'}
    with disconnected():
        exec(compile(source,'deployed-baseline-strategy.py','exec'),namespace)
        _,sessions,stocks,vix,dataset_hash=load(args.dataset)
        count=0;differences=[]
        for day in audit(sessions,stocks,vix)['complete_sessions']:
            for end in expected_ends(sessions[day]):
                at=end+timedelta(seconds=60)
                markets,index=at_time(sessions,stocks,vix,at)
                old=namespace['analyze'](markets['QQQ'],markets,index,at)
                new=analyze(markets['QQQ'],markets,index,at)
                count+=1
                if old!=new:differences.append({'at':at.isoformat(),'old':old,'new':new})
    result={'baseline_revision':BASELINE,'baseline_strategy_sha256':sha256(source.encode()).hexdigest(),
            'dataset_sha256':dataset_hash,'checkpoints':count,'difference_count':len(differences),'differences':differences}
    Path(args.out).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='differences'}))
    if differences:raise SystemExit(1)


if __name__=='__main__':main()
