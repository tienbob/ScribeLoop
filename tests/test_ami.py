"""AMI XML conversion: chronology, overlapping turns, and nonlexical sounds."""
from __future__ import annotations

from zipfile import ZipFile

import pytest
from scribeloop_core.evaluation.ami import convert_annotations


def test_manual_annotation_conversion(tmp_path):
    path = tmp_path / "annotations.zip"
    with ZipFile(path, "w") as archive:
        for speaker, start, end, text in (("A", 2, 4, "later"), ("B", 1, 3, "earlier")):
            archive.writestr(f"words/TEST.{speaker}.words.xml", f'''
<nite:root xmlns:nite="http://nite.sourceforge.net/">
  <w nite:id="w0" starttime="{start}" endtime="{end}">{text}</w>
  <w nite:id="w1" starttime="{end}" endtime="{end}" punc="true">.</w>
  <vocalsound nite:id="w2" starttime="5" endtime="6" type="laugh"/>
</nite:root>''')
            archive.writestr(f"segments/TEST.{speaker}.segments.xml", f'''
<nite:root xmlns:nite="http://nite.sourceforge.net/">
  <segment transcriber_start="{start}" transcriber_end="{end}">
    <nite:child href="TEST.{speaker}.words.xml#id(w0)..id(w1)"/>
  </segment>
  <segment transcriber_start="5" transcriber_end="6">
    <nite:child href="TEST.{speaker}.words.xml#id(w2)"/>
  </segment>
</nite:root>''')
    with ZipFile(path) as archive:
        text, turns = convert_annotations(archive, "TEST")
    assert text == "earlier later"
    assert turns == [{"speaker": "B", "start_ms": 1000, "end_ms": 3000},
                     {"speaker": "A", "start_ms": 2000, "end_ms": 4000}]
    assert turns[0]["end_ms"] > turns[1]["start_ms"]  # overlap survives
    with ZipFile(path) as archive, pytest.raises(ValueError, match="No AMI word"):
        convert_annotations(archive, "MISSING")
