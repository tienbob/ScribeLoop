# Pricing and customer-provided models

Product decision recorded October 28, 2026.

## Purchase model

ScribeLoop costs **US$1 as a one-time app purchase**. After purchase, there is
no recurring ScribeLoop subscription or ScribeLoop usage fee. Distribution is
planned outside the Mac App Store; the storefront has not been selected.

The purchase pays for the app. It does not include hosted transcription or LLM
credits. Customers choose how to run both speech recognition (ASR) and the LLM
used for notes and summaries.

| Mode | How it works | Ongoing cost |
| --- | --- | --- |
| Self-hosted | Connect the app to customer-operated ASR and LLM models. | No ScribeLoop usage fee or external API bill when both models run entirely on the customer's own infrastructure. Hardware, electricity and any hosting costs remain the customer's responsibility. |
| Own API keys | Configure the customer's own ASR and LLM provider accounts and API keys. | The customer pays those providers directly under their pricing. ScribeLoop adds no usage charge. |
| Mixed | Self-host one component and use a paid API for the other. | The customer pays only the applicable provider and infrastructure costs. |

Self-hosted use must support both ASR and LLM processing without requiring a
ScribeLoop-operated model service. Provider availability and model licenses still
apply to the customer's chosen setup.

## Intended customer-facing description

> Buy ScribeLoop once for $1. Use it without an ongoing ScribeLoop fee. Connect
> your own self-hosted speech and language models, or bring your own ASR and LLM
> API keys and pay your providers directly. AI services are not included in the
> purchase price.

## Required implementation before release

This document defines the intended commercial model; it is not a claim that all
three modes are available in the current app.

- Done for internal build 0.1.1: remove the bundled operator transcription
  credential and add customer-owned Deepgram API-key configuration alongside LLM
  configuration. Other ASR providers and self-hosted ASR remain future work.
- Add and validate supported self-hosted ASR and LLM connections, including
  endpoint configuration and any required authentication.
- Allow ASR and LLM choices to be configured independently for mixed use.
- Store customer credentials securely and keep them out of logs and exports.
- Make setup, connection failures and provider charges clear before processing.
- Verify that fully self-hosted processing does not require an external model
  service; explain where audio and transcript data go for each selected mode.

The current native app uses Deepgram cloud ASR and configurable cloud notes
providers. Local-model experiments in the repository do not establish complete
self-hosted support in the packaged app. General release requirements remain in
the [current acceptance checklist](CURRENT_STATUS.md).
