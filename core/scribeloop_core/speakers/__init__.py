"""Speakers: embeddings + opt-in registry (Flow 3, §6).

Learning is OPT-IN per person; no embeddings are persisted for unmapped
"Unknown-N" speakers (§3.1/§6). Even transient in-memory embeddings of unnamed
speakers are biometric processing under GDPR — counsel review covers this.
"""
