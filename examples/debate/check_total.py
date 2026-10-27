"""Example independent task verifier. Copy into the selected project before use."""
import argparse
import csv
import io
import json
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--artifacts',type=Path,required=True)
p.add_argument('--evidence',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
source=next(s for s in json.loads(a.evidence.read_text(encoding='utf-8')) if s['id']=='sales')
expected=sum(int(r['amount']) for r in csv.DictReader(io.StringIO(source['content'])))
report=json.loads((a.artifacts/'report').read_text(encoding='utf-8'))
passed=type(report.get('total')) is int and report['total']==expected
result={'passed':passed,'summary':'Sales total agrees with supplied rows' if passed else 'Sales total disagrees with supplied rows',
        'findings':[] if passed else ['Recompute the sum using every provided row.']}
a.output.write_text(json.dumps(result),encoding='utf-8')
