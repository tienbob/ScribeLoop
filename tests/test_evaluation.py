"""Corpus safety, metric edge cases, and baseline reports without ML downloads."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError
from scribeloop_core.evaluation.corpus import Corpus, load_corpus, readiness
from scribeloop_core.evaluation.metrics import normalize, score_words


@pytest.fixture()
def manifest(tmp_path):
    sf.write(tmp_path / "sample.wav", np.zeros(2400), 16000)
    (tmp_path / "reference.txt").write_text("hello world", encoding="utf-8")
    (tmp_path / "speakers.json").write_text(json.dumps([
        {"speaker": "speaker-1", "start_ms": 0, "end_ms": 150}
    ]))
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps({"version": 1, "recordings": [{
        "id": "fixture", "source": "synthetic", "audio": "sample.wav",
        "reference": "reference.txt", "speaker_labels": "speakers.json",
        "provenance": "generated test fixture"
    }]}))
    return path


def test_word_errors_and_unicode():
    assert normalize("Xin CHÀO, thế giới!") == ["xin", "chào", "thế", "giới"]
    score = score_words("one two three", "one four three five")
    assert (score.errors, score.reference_words) == (2, 3)
    assert score.wer == pytest.approx(2 / 3)
    assert score_words("", "inserted words").errors == 2
    assert score_words("", "inserted words").wer is None
    assert score_words("one", "").wer == 1
    assert score_words("one", "two three four").wer == 3


def test_manifest_paths_and_readiness(manifest, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path.parent)
    corpus = load_corpus(manifest)
    assert corpus.recordings[0].audio.is_absolute()
    status = readiness(corpus)
    assert status["real_meetings"] == 0
    assert not status["corpus_ready"]


def test_real_recording_requires_explicit_consent(manifest):
    data = json.loads(manifest.read_text())
    data["recordings"][0]["source"] = "real"
    with pytest.raises(ValidationError, match="consent"):
        Corpus.model_validate(data)


def test_duplicate_ids_and_invalid_variants(manifest):
    data = json.loads(manifest.read_text())
    data["recordings"].append(dict(data["recordings"][0]))
    with pytest.raises(ValidationError, match="unique"):
        Corpus.model_validate(data)
    data["recordings"].pop()
    data["recordings"][0].update(variant_of="missing", codec="opus")
    with pytest.raises(ValidationError, match="original"):
        Corpus.model_validate(data)


def test_invalid_speaker_timing_and_missing_files(manifest):
    labels = manifest.parent / "speakers.json"
    labels.write_text('[{"speaker":"s", "start_ms":0,"end_ms":200}]')
    with pytest.raises(ValueError, match="invalid speaker turn"):
        load_corpus(manifest)
    labels.unlink()
    with pytest.raises(ValueError, match="missing speaker_labels"):
        load_corpus(manifest)


def test_baseline_report_and_no_overwrite(manifest, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_corpus.py"
    spec = importlib.util.spec_from_file_location("evaluate_corpus", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'version',lambda name:'mock-backend-version')
    monkeypatch.setitem(sys.modules, "bakeoff", SimpleNamespace(
        run_backend=lambda *args: ([{"text": "hello", "is_final": True}], 0.1, 0.01, 0.15)
    ))
    output = manifest.parent / "report.json"
    monkeypatch.setattr(sys, "argv", [str(script), str(manifest), "--machine", "test CPU",
                                      "--output", str(output)])
    module.main()
    report = json.loads(output.read_text())
    assert report["groups"]["synthetic/original"]["wer"] == 0.5
    assert report["recordings"][0]["audio_s"] == 0.15
    assert report["der"] is None and report["live_latency"] is None
    assert report['backend_version']=='mock-backend-version'
    assert not report["targets_signed_off"]
    assert "hello" not in output.read_text()
    with pytest.raises(SystemExit):
        module.main()
