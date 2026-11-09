"""Primary read-only source/record verification of the qualitative audit."""
import collections
import hashlib
import json
from pathlib import Path

folder = Path(__file__).resolve().parent
path = folder / 'STUDY2-RATIONALE-EVIDENCE-AUDIT.json'
out_path = folder / 'STUDY2-RATIONALE-primary-verification.json'
assert not out_path.exists(), 'Do not overwrite an audit receipt'
data = json.loads(path.read_text(encoding='utf-8'))
sha = lambda b: hashlib.sha256(b).hexdigest()
assert len(data['cases']) == 30
assert collections.Counter(c['rationale_category'] for c in data['cases']) == data['counts']
cache = {}
for receipt in data['source_receipts']:
    content = Path(receipt['path']).read_bytes()
    assert sha(content) == receipt['sha256'] and len(content) == receipt['bytes']
    cache[receipt['path']] = content.splitlines()
for case in data['cases'] + [data['Haiku_verify_exception']]:
    assert sha(case['rationale'].encode('utf-8')) == case['rationale_sha256_utf8']
    for key in ('monitor_result', 'judge_raw_record'):
        receipt = case[key]
        line = cache[receipt['path']][receipt['line_1based'] - 1]
        assert sha(line) == receipt['line_sha256_excluding_newline']
        row = json.loads(line)
        if key == 'monitor_result':
            assert row['parsed']['rationale'] == case['rationale']
            assert row['parsed']['verdict'] == case['verdict']
        else:
            assert row['call_id'] == receipt['call_id']
    if 'rationale_excerpt' in case:
        assert case['rationale_excerpt'] in case['rationale']
out = {'audit_sha256': sha(path.read_bytes()),
       'verifier_sha256': sha(Path(__file__).read_bytes()),
       'source_files_verified': len(data['source_receipts']),
       'rationale_records_verified': 31, 'posthoc_categories': data['counts'],
       'verified': True,
       'scope': 'Exact source and record binding. Interpretive categories remain model-assisted qualitative coding.'}
with out_path.open('x', encoding='utf-8') as f:
    f.write(json.dumps(out, indent=2) + '\n')
print(json.dumps(out))
