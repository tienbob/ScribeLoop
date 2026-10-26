# Meeting summaries and Markdown export

Summaries default to **OpenAI · gpt-5.4-mini**, independently of ASR. Add to the
ignored project-root `.env`, or set the equivalent environment variable:

```dotenv
OPENAI_API_KEY=your-local-key
```

The default endpoint is `https://api.openai.com/v1` and uses Responses, bounded
output and `store:false`. Model/API details were checked against the official
[model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
and [text generation guide](https://developers.openai.com/api/docs/guides/text).
No startup probe or paid validation request is made.

To override the default, use explicit compatible settings:

```dotenv
SUMMARY_BASE_URL=https://your-provider.example/v1
SUMMARY_MODEL=your-model-id
SUMMARY_API_KEY=your-local-key
SUMMARY_PROVIDER=Your provider name
```

Custom endpoints use Chat Completions. `SUMMARY_PROVIDER` is optional. Environment
values take precedence; restart after changes. `OPENAI_API_KEY` is used only for the
exact official OpenAI endpoint, never implicitly for another provider. See the
[safe template](../.env.example). Do not replace existing ASR credentials.

For a local server, use a loopback URL such as `http://127.0.0.1:8080/v1`. Its key
may be empty. Remote URLs require HTTPS and a key. URLs with embedded credentials,
queries or fragments are rejected. The backend does not follow redirects, use
ambient HTTP proxies, probe the provider, download a model or make startup requests.

## Detected-call flow

The default notebook watches app audio activity. A detected episode shows a single
Record / Not now choice that identifies app/browser scope, microphone, ASR backend and
configured summary destination/model. The first-use app agreement covers recording permission and cloud processing.
**Record & take notes** starts this call and one post-call text summary. No repeated
processing-consent checkbox is needed. Declining suppresses the episode.
One-time microphone/Screen Recording permissions and provider setup still apply.

The backend ends capture after 60 continuous seconds of fresh, complete app
inactivity evidence. A countdown offers **Keep recording** to restart the grace
period; **Stop** is always available. Unknown/stale activity keeps capture running.
This is an activity heuristic, not an official Teams/Slack/browser call-state API.
Media playback can trigger suggestions and mute/helper behavior can confuse end
inference. Browser capture includes all tabs. Five minutes is the development preset; recording preferences also offer one
and three hours with displayed shared audio caps. Detection is macOS-only and requires the native observer; the UI
remains a browser development host, without native background notification UX.

After successful transcription completion, the backend starts one automatic final-notes workflow, reusing confirmed
section notes with bounded section/reduction requests to the destination/model shown in the call prompt, while the
first-use agreement remains accepted. The workflow can
finish while the browser is disconnected, provided the backend is still running.
A cited topic title replaces the automatically created app/time placeholder; the
original session time and source remain in metadata. Missing summary setup keeps
the dated transcript; failures keep saved audio/text and expose manual generation.
There are no automatic paid retries or alternate-provider calls. Quitting the
backend interrupts processing rather than silently resuming uploads at next launch.

## Manual summary and export

Open a completed, failed or interrupted saved meeting with confirmed transcript
text. The **Meeting notes** section shows the configured destination and model.
After accepting the first-use app agreement, select **Generate summary**. Only selected transcript text, timestamps, segment IDs and
speaker labels are sent, with the summary schema and instructions; no audio or
other saved meetings are sent. Each click starts one bounded notes workflow, which may use multiple
section/reduction requests without automatic retry/fallback. Choose a text model that can follow the JSON schema in the prompt.
Compatible Chat Completions requests use `messages`, `model`, `temperature` and
`max_tokens`. OpenAI Responses uses `input`, `model`, `max_output_tokens` and
`store:false`, omitting legacy sampling fields. No tool execution is enabled.

Each request has a 100 KB input limit, 4,000 output-token limit, 200 KB HTTP
response limit and a 70-second overall deadline. Unsupported model parameters,
quota errors and invalid JSON/citations fail while preserving the previous draft.
These bounds are not a provider billing guarantee; prices depend on your settings.

The draft contains overview, key points, decisions, action items, open questions
and uncertainties. Every item requires known source segment IDs. Source buttons
jump to the transcript. Owners and due dates are requested only when explicitly
stated. **Citation membership validation cannot prove a claim is accurate or stop
all prompt injection**: review the source text. Transcript and generated output
are treated as inert data, with no tool execution or automatic follow-up actions.

Saved drafts record the provider, model, revision and corrected-text fingerprint.
Editing a speaker or replacing/resuming a transcript makes earlier notes outdated.
A result arriving after the transcript changes is rejected. Reconnect restores the
saved draft; session deletion removes it. Summary generation does not block the
recording/Stop command loop and never triggers another ASR pass.

**Download Markdown · plaintext** exports the current corrected transcript,
timestamps, revision/provider/model details, citation anchors and the matching
current summary. Outdated summaries are omitted with an explanatory line. Export
works without a configured summary provider and makes no model request. A unique
filename is proposed through the browser's normal download/save flow; the backend
accepts no destination path. The file is plaintext and follows your browser's
save settings; it is outside encrypted app storage and later app deletion does
not delete an exported copy. Markdown/HTML metacharacters in content are escaped.

Local verification uses mocked endpoints and invented text. No live summary model
quality, provider-specific compatibility or private meeting has been accepted yet.

## Long conversations and durable section notes

Confirmed long-call sections are summarized incrementally using 15-minute timeline
buckets, split further by payload size. Closed sections require confirmed progress
on every selected source. A durable fingerprint/revision/provider/model/endpoint
cache reuses section notes during final hierarchical reduction, preserving original
segment citations. Reconnect restores the latest matching section draft. No ASR
rerun is involved. Editing names/text invalidates affected source fingerprints.

The first-use agreement covers multiple text requests. Per saved meeting, limits
are 128 requests and 8 MB total request text across actions/retries. Failed or
ambiguous dispatches are not retried on restart or later automatic completion;
**Generate summary** explicitly retries failed work and reuses successful sections.
The UI exposes request usage and the configured destination/model. Real summary
quality and latency still require real-meeting review.
