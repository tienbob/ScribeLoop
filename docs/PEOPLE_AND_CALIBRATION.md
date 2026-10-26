# People, voice-learning preference and forget

In a saved transcript, open the speaker editor and **People across meetings**.
Create/select a named person, choose an anonymous speaker and **Link selected
speaker to person**. Reuse the person in another meeting explicitly. This changes
presentation and excerpt notes without changing ASR labels or enabling learning.
A later manual speaker rename unlinks that person for the current revision.

**Allow future voice learning** stores an opt-in preference. No voice data is
collected for learning in this build: a configured cloud voice-embedding backend
and consented calibration are still required. App ASR is cloud-only; there is no
hidden local extractor or second transcription path. Recognition cannot identify
people from Deepgram speaker numbers across connections. Missing/uncertain names
stay anonymous. No automatic biometric naming is enabled.

The enrollment library refuses unnamed/unlinked/non-opted-in people, unreviewed,
short, overlapping, expired or incomplete exemplars. It keeps at most ten reviewed
exemplars, with model/version, source session/speaker/revision, time range and review
provenance. Pausing learning excludes a person from suggestions without deleting
existing exemplars. Forget is the explicit deletion action.

Confirm **Forget person** to remove the person, links/history, exemplars, derived
notes/cache/vectors and associated presentation names. Previously unlinked meetings
are included. Original transcript speech and audio are not redacted; exports and
backups remain outside this purge. Review/delete those separately if required.

## Calibration tool — evidence, not a default threshold

Prepare private, pseudonymous JSON from consented cloud embedding evaluations:

```json
{"consent_confirmed":true,"model":"your-cloud-model","version":"exact-version",
 "training":[{"score":0.9,"same_person":true,"people":["train-a","train-a"],"condition":"clean"},
             {"score":0.2,"same_person":false,"people":["train-a","train-b"],"condition":"clean"}],
 "heldout":[{"score":0.8,"same_person":true,"people":["test-a","test-a"],"condition":"clean"},
            {"score":0.1,"same_person":false,"people":["test-a","test-b"],"condition":"clean"}]}
```

This tiny example intentionally cannot pass the evidence gate. Real evaluation
must include enough independent people/calls and representative clean, overlap,
codec/device conditions; repeated correlated pairs are not independent evidence.
The report does not validate those collection assumptions for you.

```bash
.venv/bin/python scripts/calibrate_speakers.py /private/path/scores.json \
  --output /private/path/calibration.json
```

The script never records or uploads audio. It reports false matches, misses, condition
coverage, a 95% upper false-match bound and input evidence hash. Training and held-out
people must be disjoint. A suggested threshold is learned from training evidence;
insufficient/poor held-out performance leaves suggestions disabled. A passing report
still does not enable automatic naming or configure a cloud embedding service.
