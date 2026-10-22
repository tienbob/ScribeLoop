"""Convert AMI manual XML annotations without extracting the upstream ZIP."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

NITE = "{http://nite.sourceforge.net/}"


def convert_annotations(archive: ZipFile, meeting: str) -> tuple[str, list[dict]]:
    """Serialize lexical words in onset order; retain overlapping speaker turns.

    Turns use the manual transcription segment boundaries, restricted to segments
    containing lexical speech. These are not an official AMI diarization recipe.
    Punctuation and nonlexical vocal sounds are excluded from the WER reference.
    """
    words = []
    turns = []
    files = sorted(name for name in archive.namelist()
                   if re.fullmatch(rf"words/{re.escape(meeting)}\.[A-Z]\.words\.xml", name))
    if not files:
        raise ValueError(f"No AMI word annotations for {meeting}")
    for name in files:
        speaker = name.split(".")[-3]
        root = ET.fromstring(archive.read(name))
        nodes = list(root)
        indices = {node.attrib.get(f"{NITE}id"): i for i, node in enumerate(nodes)}
        for node in nodes:
            if node.tag == "w" and node.get("punc") != "true" and (node.text or "").strip():
                if "starttime" not in node.attrib or "endtime" not in node.attrib:
                    raise ValueError(f"Untimed lexical word in {name}")
                words.append((float(node.attrib["starttime"]), speaker,
                              node.attrib[f"{NITE}id"], node.text.strip()))
        segments = ET.fromstring(archive.read(f"segments/{meeting}.{speaker}.segments.xml"))
        for segment in segments:
            lexical = False
            for child in segment:
                ids = re.findall(r"id\(([^)]+)\)", child.get("href", ""))
                if not ids:
                    continue
                selected = nodes[indices[ids[0]]:indices[ids[-1]] + 1]
                lexical |= any(node.tag == "w" and node.get("punc") != "true"
                               and (node.text or "").strip() for node in selected)
            if lexical:
                start = round(float(segment.attrib["transcriber_start"]) * 1000)
                end = round(float(segment.attrib["transcriber_end"]) * 1000)
                if start < end:
                    turns.append({"speaker": speaker, "start_ms": start, "end_ms": end})
    if not words or not turns:
        raise ValueError(f"No lexical speech found for {meeting}")
    return " ".join(word[3] for word in sorted(words)), sorted(
        turns, key=lambda turn: (turn["start_ms"], turn["speaker"])
    )
