# F1/F2 acceptance evidence — 16 November 2026

These files contain synthetic data only. No private workspace, audio, API key or
provider error body is included.

| Artifact | Meaning |
| --- | --- |
| `encrypted-scale.json` | Fresh real-SQLCipher 1,000-meeting/100,000-segment keyword benchmark; passes p95 ≤500 ms |
| `local-retrieval-summary.json` | Existing developer-authored Local Granite 40-query recall rerun; not Gemini accuracy |
| `live-attempt-blocked.json` | Invalid initial harness attempt; 15 cases and 75 counted calls, ending in Gemini HTTP 429 |
| `human-review-template.csv` | Blank 120-case worksheet for 40 questions × three passes; not completed reviews |

The versioned fixture and frozen expected facts are in
`tests/fixtures/meeting_workspace_acceptance_v1.json`. An independent reviewer
must first approve/correct the labels. A provider budget wrapper metadata bug
invalidated the early live attempt; the corrected harness passes offline tests.
Do not calculate production grounding accuracy from its error rows. No live
retry was made after the quota response.

## Completing the review

1. Review the fixture facts independently from model answers. Record label reviewer
   and approval; version the fixture again if labels change.
2. Resolve Gemini quota, then explicitly execute the documented three-run command
   into a new directory. Preserve errors and partial runs; do not silently retry.
3. Use the resulting populated `human-review.csv`, with full evidence in its paired
   `results.json`. Fill `human_grounding_pass` for each answerable question and
   `abstention_or_conflict_pass` for all negative/conflicting questions, with a
   reviewer name and notes. The values should be `true` or `false`; blanks are unreviewed.
4. Report each run separately: at least 27/30 answerable questions must pass human
   grounding; all 10 negative/conflicting cases must avoid invented certainty.
   Report citation existence separately. A valid source ID or relevant retrieved
   passage does not prove the answer is supported by it.
5. Finish native keyboard/filter/playback/expired-audio/recovery/status-timing
   checks in the delivery plan. Do not mark release accepted from test counts alone.

The new code/tools passed **932 tests**, with 37 optional skips, plus Ruff and
whitespace checks on this development machine. The benchmark is a single synthetic
machine result and excludes native transport and cloud latency.
