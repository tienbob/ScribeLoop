# Meeting summaries and Markdown export

Summaries use an **OpenAI-compatible chat/completions endpoint**, independently of
the audio transcription model. Configure these literal assignments in the ignored
`.env` file at the project root, or set equivalent environment variables:

```dotenv
SUMMARY_BASE_URL=https://your-provider.example/v1
SUMMARY_MODEL=your-model-id
SUMMARY_API_KEY=your-local-key
SUMMARY_PROVIDER=Your provider name
```

Use your provider's actual API prefix, without `/chat/completions`; the app appends
that path. `SUMMARY_PROVIDER` is an optional display name. Environment values take
precedence. Restart the app after changing settings. See [safe template](../.env.example).
Do not replace your existing ASR credentials when adding these assignments.

For a local server, use a loopback URL such as `http://127.0.0.1:8080/v1`. Its key
may be empty. Remote URLs require HTTPS and a key. URLs with embedded credentials,
queries or fragments are rejected. The backend does not follow redirects, use
ambient HTTP proxies, probe the provider, download a model or make startup requests.

Open a completed, failed or interrupted saved meeting with confirmed transcript
text. The **Meeting notes** section shows the configured destination and model.
For a remote endpoint, check the separate text-upload consent, then select
**Generate summary**. Only selected transcript text, timestamps, segment IDs and
speaker labels are sent, with the summary schema and instructions; no audio or
other saved meetings are sent. Each click makes one request, without automatic
retry/fallback. Choose a text model that can follow the JSON schema in the prompt.
The generic request uses `messages`, `model`, `temperature` and `max_tokens`.
Provider-specific reasoning parameters, tools and streaming are not enabled.

The request has a 100 KB transcript limit, 4,000 output-token limit, 200 KB HTTP
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
