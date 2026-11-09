# Manuscript numbers audit v1

Audited manuscript SHA256: `36e9c3a5b9bf60b05a03a2ad7aea9e73227be89fbf2ca0c404d47721d35ed285` (2026-11-08T21:37:18.250425+00:00).

**51/51 numerical checks passed.** Two factual precision/scope corrections are listed below. No headline count, p-value, Study-3 router count, Study-4 forecast or saved model-setting mismatch was found.

## Corrections / source qualifications

### P1: manuscript line 156

> Of the 44 additional detections, 41 occurred where that baseline lacked decisive information.

44 is a net difference, not the number of distinct P3-only detections. The component counts themselves are correct.

**Evidence:** {'P3_only': 45, 'P2G_only': 1, 'net_gain': 44, 'blind_P3_only': 41, 'blind_P2G_only': 0, 'different_evidence_P3_only': 4, 'different_evidence_P2G_only': 1}

**Concrete correction:** The net gain was 44 detections; 41 of that net gain occurred on the 44 pairs whose generic-baseline evidence was identical across twins.

### P2: manuscript line 67

> X contains an uninspected qualifying approval

If read as describing every plotted X run after monitoring, uninspected is contradicted by successful retrievals. No bar count is wrong.

**Evidence:** The approval was initially unmentioned, but4/30 Sonnet original X runs and19/30 Haiku original X runs executed the target query; four Sonnet original runs retrieved the actual qualifying record.

**Concrete correction:** X contains an initially unmentioned qualifying approval (uninspected in the accused Sonnet runs).

## Verified results

- Study 1 score flags: P3 H/B84/90,9/90; P2G40/90,3/90; P2S88/90,4/90. Explicit P3 violation verdicts82/90,4/90. Unamended sensitivity74/90,1/90. The manuscript keeps the score/verdict endpoints distinct.
- Study 2 Sonnet original/reminder/router/P4 H,X,B,triplet counts: (29,25,0,4), (30,23,0,7), (30,0,0,30), (30,0,0,30). Haiku X/B11/0; DeepSeek30/5.
- Study 2 exact X/B raw p=5.960464477539063e-8; Holm p=1.1920928955078125e-7 (printed1.19e-7). Reminder discordances4 verify-only/1 original-only, raw and Holm p=.375.
- Study 2 template profile5/5 in five templates,0/5 alias_race; all30 original Sonnet X runs spent3 queries. Across its two arms,48 accusations omit the target approval query;11 target-query runs are compliant. Saved qualitative categories19G/6V/4W/1T and all73 source hashes verified. Cases007/025 and the Haiku12:58:32-before13:15:36 error match the saved receipts.
- Study 3 Sonnet X/B7/30,7/30; discordances4/4 and exact p=1. Target-query association:7/10 versus0/20, with9 witnessed abstentions. Full witness H15 unambiguous+0 ambiguous=15; router15+2=17 (one latest_alias,one revocation,zero review_hold_lift).
- Study 4 forecast counts/PI:15/18 [16,30],2/18 [0,12],18/18 [23,30],6/18 [3,20]. Raw-H detections16,3,18,6; GPT5.4 consent H abstentions15/18. GPT4.1 forecast equals raw H in both families; GPT5.4 differs by1/18 each.
- Raw settings: Claude requested/served IDs match, medium effort/adaptive thinking; DeepSeek Flash thinking disabled. GPT5.4 calibration selectors103 with omitted effort after103 unsupported-parameter fallbacks;72 judges medium. GPT4.1 no effort parameter. Exact served calibration snapshots match the manuscript.
- No live Study4 evaluation directories exist at audit time. Live wire pinning and safeguards are implemented settings, not observed evaluation results. Frozen forecast SHA matches freeze2.

## Coverage and limits

The JSON companion contains 51 claim checks, raw summaries, the73 rationale-source rechecks, and 110 exact-byte source hashes. Manuscript unchanged at finish: **True**.

Policy/design claims were checked against frozen protocols, manifests, existing QA reports and inspected implementation. The existing independent label/replay audits support the15 policy-dependent H cases; this audit does not redefine those labels.

Bibliographic claims and author/LLM-workflow disclosures are outside this numerical audit. Rationale categories are saved posthoc coding, not fresh independent human annotations. No canonical manuscript, runtime, frozen source or raw results were modified.
