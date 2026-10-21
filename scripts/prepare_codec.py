"""Create an Opus round trip of an existing five-minute development WAV.

Audio stays local; reference labels may be reused only after timing checks pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from scribeloop_core.evaluation.codec import check_alignment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path("data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/corpus-v1/codec-dev"))
    parser.add_argument("--bitrate-kbps", type=int, choices=[12, 16, 24, 32], default=24)
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        parser.error("ffmpeg with libopus is required")
    info = sf.info(args.source)
    if (info.channels != 1 or info.samplerate != 16000 or info.subtype != "PCM_16"
            or not 0 < info.duration <= 300):
        parser.error("expected mono 16 kHz PCM16 audio no longer than five minutes")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_dir / f"{args.source.stem}-opus-{args.bitrate_kbps}k"
    encoded, decoded, metadata = prefix.with_suffix(".opus"), prefix.with_suffix(".wav"), prefix.with_suffix(".json")
    if any(path.exists() for path in (encoded, decoded, metadata)):
        parser.error("outputs already exist; choose a fresh directory")
    commands = [
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(args.source),
         "-c:a", "libopus", "-application", "voip", "-b:a", f"{args.bitrate_kbps}k", str(encoded)],
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(encoded),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(decoded)],
    ]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, text=True)
    reference, _ = sf.read(args.source, dtype="float32")
    waveform, sr = sf.read(decoded, dtype="float32")
    alignment = check_alignment(reference, waveform, sr)
    report = {"codec": "opus", "bitrate_kbps": args.bitrate_kbps, "application": "voip",
              "source": str(args.source.resolve()), "decoded": str(decoded.resolve()),
              "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
              "decoded_sha256": hashlib.sha256(decoded.read_bytes()).hexdigest(),
              "encoded_sha256": hashlib.sha256(encoded.read_bytes()).hexdigest(),
              "ffmpeg": subprocess.run([ffmpeg, "-version"], check=True, capture_output=True,
                                       text=True).stdout.splitlines()[0],
              "commands": commands, "alignment": alignment,
              "limitations": ["Three local waveform checks, not a human annotation review.",
                               "Codec compression only; no packet loss, network jitter, or echo simulation."]}
    with metadata.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({"decoded": str(decoded), "provenance": str(metadata),
                      "alignment": alignment}, indent=2))
    if not alignment["passed"]:
        raise SystemExit("Timing checks failed; do not reuse the source labels for this variant.")


if __name__ == "__main__":
    main()
