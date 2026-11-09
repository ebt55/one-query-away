"""Format audited Study 4 results for the manuscript, without changing it.

No model calls or experiment imports. Requires completed operational health,
the frozen scorer and the independent complete-design audit to agree.
Outputs are a new Markdown fragment and a provenance JSON, never a paper edit.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

FORECAST_SHA = 'c53d1532c74054dd3549b9bec824d896f4a3c91c78dc80f433fada9e302e7dda'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '|' + '|'.join('---' for _ in headers) + '|'] +
                     ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows])


def build(audit, score, forecast, health):
    if not (audit['complete_design'] and health['pass'] and
            audit['independent_replay_flags_verified'] == 120):
        raise ValueError('complete operational and independent scientific gates required')
    if score['summary']['incomplete'] or score['summary']['n_scorable'] != 4:
        raise ValueError('the original scorer must have four complete cells')
    if score['summary']['deviations']:
        raise ValueError('scorer deviations need explicit review before paper formatting')
    for key in ('success', 'n_beats_both', 'n_inside'):
        if audit[key] != score['summary'][key]:
            raise ValueError('independent and original summary disagree: ' + key)
    indexed = lambda cells: {(c['monitor'], c['family']): c for c in cells}
    ac, sc, fc = (indexed(doc['cells']) for doc in (audit, score, forecast))
    if not len(ac) == len(sc) == len(fc) == 4 or not ac.keys() == sc.keys() == fc.keys():
        raise ValueError('cell universe mismatch')
    main, comparators, components, facts = [], [], [], []
    fields = {'n': 'n_X', 'accused': 'k_X', 'forecast': 'f', 'observed': 'obs',
              'absolute_error': 'ae_forecast', 'baseline_B_error': 'ae_B',
              'baseline_prior_error': 'ae_prior', 'beats_B': 'beats_B',
              'beats_prior': 'beats_prior', 'beats_both': 'beats_both', 'inside': 'inside'}
    for key, f in fc.items():
        c, s = ac[key], sc[key]
        for independent, original in fields.items():
            if abs(c[independent] - s[original]) > 1e-12:
                raise ValueError('cell disagreement: ' + str((key, independent)))
        if c['n'] != 30 or s['interval_recomputed']:
            raise ValueError('no partial-cell or recomputed-interval paper results')
        if c['interval_counts'] != [s['lo_count'], s['hi_count']]:
            raise ValueError('interval disagreement')
        model, family = key
        label = model.replace('gpt-', 'GPT-') + ' / ' + {'F1': 'release', 'F3': 'consent'}[family]
        pct = lambda number: f'{100 * number:.1f}'
        main.append([label, pct(c['forecast']) + '%', f"{c['accused']}/30",
                     pct(c['absolute_error']), '-'.join(map(str, c['interval_counts'])),
                     'yes' if c['beats_both'] else 'no'])
        raw_h = f['descriptive']['H_detect_k'] / f['n_H']
        raw_h_error = abs(raw_h - c['observed'])
        comparators.append([label, pct(c['absolute_error']), pct(c['baseline_B_error']),
                            pct(c['baseline_prior_error']), pct(raw_h_error)])
        counts = c['verdict_counts']
        d = c['secondary_descriptive_decomposition']
        components.append([label, c['accused_without_distinguishing'],
                           c['accused_after_distinguishing'], counts.get('compliant', 0),
                           counts.get('insufficient_evidence', 0),
                           f"{100 * d['component_transport_sampling_discrepancy']:+.1f}",
                           f"{100 * d['post_distinguishing_accusation_rate']:+.1f}"])
        facts.append({'monitor': model, 'family': family, 'n': c['n'],
                      'forecast': c['forecast'], 'observed': c['observed'],
                      'raw_H_rate': raw_h, 'raw_H_absolute_error': raw_h_error,
                      'all_verdict_counts': counts, 'independent_cell': c})
    lines = ['## Study 4 manuscript fragments', '',
             f"Registered classification: **{audit['classification']}**; beats both baselines "
             f"in {audit['n_beats_both']}/4 cells; interval inclusion in {audit['n_inside']}/4.", '',
             table(['Monitor / family', 'Forecast', 'Observed X', 'Error (pp)',
                    '95% PI (/30)', 'Beats both'], main), '',
             '*Table 2. Frozen forecasts and complete prospective X evaluation. Error is absolute '
             'error in percentage points; each cell has 30 X worlds. Beating both requires strictly '
             'lower error than calibration-B risk and the historical family prior. Prediction '
             'intervals use the registered binomial working model; template transport is not guaranteed.*', '',
             '### Appendix C: comparator errors', '',
             table(['Monitor / family', 'Forecast', 'Cal. B', 'Family prior', 'Raw H'], comparators), '',
             '*Table C1. Absolute errors in percentage points. Calibration-B and historical-prior '
             'comparisons are registered; raw-H conviction is an additional descriptive comparator.*', '',
             '### Appendix C: accusation decomposition', '',
             table(['Monitor / family', 'Accuse, no D', 'Accuse, after D', 'Compliant',
                    'Abstain', 'Transport (pp)', 'After D (pp)'], components), '',
             '*Table C2. D denotes a differing public query response, not necessarily a complete '
             'exonerating witness. Counts use all 30 X worlds per row. The last two columns sum '
             'to observed risk minus forecast: (accusations without D / 30 minus forecast) '
             'plus (accusations after D / 30). This additional decomposition does not change '
             'the frozen primary score. Full verdict categories remain in the companion JSON.*', '']
    return '\n'.join(lines), facts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('audit', 'score', 'forecast', 'health', 'output_prefix'):
        p.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    a = p.parse_args()
    outputs = [a.output_prefix.with_suffix(ext) for ext in ('.md', '.json')]
    if any(path.exists() for path in outputs):
        raise ValueError('refuse to overwrite paper fragments or receipts')
    if sha(a.forecast) != FORECAST_SHA:
        raise ValueError('frozen forecast identity differs')
    docs = [load(path) for path in (a.audit, a.score, a.forecast, a.health)]
    if docs[0]['forecast_sha256'] != FORECAST_SHA or docs[1]['summary']['forecast_sha256'] != FORECAST_SHA:
        raise ValueError('result receipts do not bind the frozen forecast')
    text, facts = build(*docs)
    proof = {'schema': 'e1-study4-paper-fragments-v1',
             'created_at_utc': datetime.now(timezone.utc).isoformat(),
             'script_sha256': sha(__file__),
             'sources': {str(path): sha(path) for path in (a.audit, a.score, a.forecast, a.health)},
             'classification': docs[0]['classification'], 'cells': facts,
             'markdown_sha256': hashlib.sha256(text.encode()).hexdigest(),
             'scope': 'Formatting audited outcomes; no automatic manuscript edit or new inference.'}
    with outputs[0].open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    with outputs[1].open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(proof, stream, indent=2); stream.write('\n')
    print(json.dumps({'classification': proof['classification'], 'outputs': list(map(str, outputs))}))


if __name__ == '__main__':
    main()
