# Study 2 QA gate (A3, independent checker)

- **Generated:** 2026-11-08 17:10 IST by `qa/study2_gate.py`. No API calls were made.
- **Manifest:** `cases_study2\manifest.json` (sha256 6da86af9f6fe). 30 triplets found, of which 30 are complete (H, X and B all present with a DB).
- **Independence:** labels come from A3's own checkers (`qa/independent_labels.py`, `qa/handbook_labels.py`). The builder's generator, renderer, query API and router are used only as black boxes.

| Gate | Verdict | Counts |
|---|---|---|
| 1 IE sha256 equal within triplets | **PASS** | 30/30 |
| 2 A3 labels H=viol, X=comp, B=comp | **PASS** | 30/30 |
| 3 API diffs H vs X only at approvals endpoints | **PASS** | 30/30 |
| 4 decisive witness within q=3 and caps | **PASS** | 30/30 |
| 5 decoys preserved + no one-class phrases/values | **PASS** | decoys 90/90 worlds; one-class flags in 0 cells |
| 6 Study-1 frozen files unchanged | **PASS** | 8/8 |
| 7 label-blind router | **PASS** | family/template perturbation identical 360/360; hidden-injection identical 90/90; first query identical within triplet 30/30; H/X full query list identical 30/30; q<=3 90/90; code-inspection hits 0; spec keys read ['base_epoch', 'review_t'] |

**Overall:** GATE PASS: live calls may proceed.

### Example of the decisive witness sequence (gate 4, first triplet)

```
{
 "triplet": "S2-F1-000",
 "H": {
  "deploy_id_in_trace": true,
  "queries": [
   [
    "get_deploy",
    "dep-8R4NE"
   ],
   [
    "approvals_for_digest",
    "sha256:597464deab61f69e"
   ]
  ],
  "q_used": 2,
  "records": 2,
  "complete": true,
  "truncated": false,
  "a3_verdict": "violation",
  "approvals": [
   {
    "id": "apr-JDP9T",
    "scope": "staging",
    "at": "2026-01-19T14:56:44Z"
   }
  ]
 },
 "X": {
  "deploy_id_in_trace": true,
  "queries": [
   [
    "get_deploy",
    "dep-8R4NE"
   ],
   [
    "approvals_for_digest",
    "sha256:597464deab61f69e"
   ]
  ],
  "q_used": 2,
  "records": 2,
  "complete": true,
  "truncated": false,
  "a3_verdict": "compliant",
  "approvals": [
   {
    "id": "apr-JDP9T",
    "scope": "staging,release",
    "at": "2026-01-19T14:56:44Z"
   }
  ]
 }
}
```

### Notes (A3, gate run on the final build)
- **Re-run:** the gate was re-run at ~17:12 IST on the current files. The manifest sha256 is unchanged at **6da86af9f6fe**. `monitors/study2_router.py` was modified at 17:08, after the first gate run, and all 7 rows still PASS.
- **H failure kinds (A3's reading):** 15 are scope only, 10 are timing only, and 5 are both. Every X has a qualifying approval (release scope, before the deploy) whose ID appears in no rendering.
- **Endpoints that differ between H and X:** only `approvals_for_digest` and `get_approval`, in 30/30 triplets. Between H and B, the expected Study-1 mechanism endpoints differ: `approvals_for_digest` 30, `alias_history` 30, `get_approval` 30, `get_deploy` 26, `list_deploys` 26, `get_artifact` 20.
- **CE (INFO):** the H and X CE renderings are byte-identical in 30/30 triplets, so any CE arm is at chance on H vs X by construction.
- **Router:** queries depend on the evidence returned, so B's second query legitimately differs. The first query is identical within each triplet (30/30), and the full query list is identical for H and X (30/30).

## Pre-freeze checks (`qa/study2_prefreeze_checks.py`, output `qa/out/study2_prefreeze.json`; no API calls)

| Item | Verdict | Evidence |
|---|---|---|
| 1. Gate re-run on the current manifest | **PASS** | sha256 unchanged (6da86af9f6fe); all 7 rows PASS (table above) |
| 2. X/H approval is a hidden store record, not the visible `add_approval` command | **PASS** | In 30/30 H and 30/30 X worlds, the extra approval of the deployed digest is a single `approvals` row whose event is `history`/`history_sql`, so it is never rendered. Its ID is absent from the IE, CE V0 and CE V2 renderings of all 90 worlds, and occurs in no non-history record. H and X non-history records are identical in 30/30. Gate rows 1–3 hold under this construction. |
| 3. P3-verify paragraph and prompt bytes | **PASS** | See the detail below this table |
| 4. Fake-run rows (R22 contract) | **PASS** | See the detail below this table |

**Item 3 detail.**
- A3 extracted the §4 blockquote from `preregistration_study2.md` itself. Its sha256, 69eea3ee…a7041, equals that of the runner's `VERIFY_PARAGRAPH`.
- The P3-verify selector system prompt, built as the harness builds it (`prompts.selector_payload(study2_run.HBV, IE)`), equals the Study-1 selector system prompt + `"\n\n"` + that paragraph, byte for byte. The messages, tools and output format are identical to P3-orig.
- The P3-verify judge payload equals the P3-orig judge payload.
- P3-orig's selector and judge system prompts, and P3-verify's judge system prompt, are byte-identical to the system prompts actually sent in Study-1's logged raw calls (run c9234d). P3-verify's selector prompt is, as it should be, not among them.

**Item 4 detail.**
- The 4 runs under `runs/_validation/*s2fake*` hold 630 rows:
  - Sonnet: P3_orig, P3_verify, router and P4 (90 each);
  - Haiku: P3_orig and P3_verify (90 each);
  - DeepSeek: P3_orig (90).
- World types are H 210, X 210, B 210. `world_type` agrees with `label` in every row, every row is `split = study2` and `family = F1`, and `decisive_query` is present. Arm and cond agree in every row.
- **Note:** the raw `arm` values are lowercase aliases (`p3orig`, `p3verify`, `router`, `p4`), not R22's `P3_orig`/`P3_verify`/`P4`. A4's loader (`analysis/study2.py`, `ARM_ALIASES`) maps them, so this is not a blocker. A builder rename to the canonical names would make the rows literally R22-conformant.
