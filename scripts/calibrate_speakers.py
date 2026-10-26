"""Offline calibration of consented cloud voice-embedding scores; never records/uploads."""
import argparse
import hashlib
import json
from pathlib import Path

from scribeloop_core.speakers.embeddings import calibrate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path,help='Private JSON with consent_confirmed, model/version, training and heldout pairs')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    data=args.input.read_bytes()
    if len(data)>10_000_000:
        raise ValueError('Calibration input exceeds bounds')
    value=json.loads(data)
    if value.get('consent_confirmed') is not True:
        raise ValueError('Use consented, pseudonymous calibration data only')
    report=calibrate(value['training'],value['heldout'],model=value['model'],version=value['version'])
    report['evidence_sha256']=hashlib.sha256(data).hexdigest()
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print('Calibration report saved; automatic naming remains disabled.')


if __name__=='__main__':
    main()
