"""Local consistency checks only; never import Torch or execute model code."""
import hashlib

import pytest
from scribeloop_core.pipeline.moss_worker import _verify_model_integrity


def model(tmp_path, lines=None):
    root=tmp_path/'model'
    root.mkdir()
    (root/'config.json').write_bytes(b'{"model_type":"test"}')
    (root/'modeling.py').write_bytes(b'# synthetic code; not executed\n')
    if lines is None:
        lines=[hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name
               for path in sorted(root.iterdir())]
    (root/'manifest.sha256').write_text('\n'.join(lines)+'\n')
    return root


def test_complete_manifest_and_binary_sha256sum_marker(tmp_path):
    root=model(tmp_path)
    _verify_model_integrity(root)
    manifest=root/'manifest.sha256'
    manifest.write_text(manifest.read_text().replace('  modeling.py',' *modeling.py'))
    _verify_model_integrity(root)


@pytest.mark.parametrize('lines',[
    [], ['# no entries'], ['garbled'], ['not-a-hash config.json'],
    ['0'*64+'  ../outside'], ['0'*64+'  /outside'],
    ['0'*64+'  sub/../../outside'], ['0'*64+'  sub\\outside'],
    ['0'*64+'  manifest.sha256'], ['0'*64+'  config.json']*2,
])
def test_empty_malformed_and_unsafe_manifests_rejected(tmp_path,lines):
    with pytest.raises(RuntimeError):
        _verify_model_integrity(model(tmp_path,lines))


def test_missing_directory_or_manifest_rejected(tmp_path):
    with pytest.raises(RuntimeError): _verify_model_integrity(tmp_path/'absent')
    root=model(tmp_path); (root/'manifest.sha256').unlink()
    with pytest.raises(RuntimeError): _verify_model_integrity(root)


@pytest.mark.parametrize('change',['modified','missing','unlisted'])
def test_model_contents_must_match_exactly(tmp_path,change):
    root=model(tmp_path)
    if change=='modified': (root/'modeling.py').write_bytes(b'# changed\n')
    elif change=='missing': (root/'modeling.py').unlink()
    else: (root/'extra.py').write_bytes(b'# unreviewed\n')
    with pytest.raises(RuntimeError): _verify_model_integrity(root)


@pytest.mark.parametrize('target',['directory','manifest','file'])
def test_symlinked_inputs_rejected(tmp_path,target):
    root=model(tmp_path)
    if target=='directory':
        alias=tmp_path/'alias'; alias.symlink_to(root,target_is_directory=True); root=alias
    elif target=='manifest':
        manifest=root/'manifest.sha256'; content=manifest.read_bytes(); manifest.unlink()
        outside=tmp_path/'manifest'; outside.write_bytes(content); manifest.symlink_to(outside)
    else:
        file=root/'modeling.py'; content=file.read_bytes(); file.unlink()
        outside=tmp_path/'code'; outside.write_bytes(content); file.symlink_to(outside)
    with pytest.raises(RuntimeError): _verify_model_integrity(root)


def test_manifest_size_is_bounded(tmp_path):
    root=model(tmp_path)
    (root/'manifest.sha256').write_bytes(b'#'*(1_048_576+1))
    with pytest.raises(RuntimeError): _verify_model_integrity(root)


def test_large_weights_are_hashed_without_read_bytes(tmp_path,monkeypatch):
    root=model(tmp_path)
    weights=root/'weights.bin'; weights.write_bytes(b'x'*2_100_000)
    with (root/'manifest.sha256').open('a') as manifest:
        manifest.write(hashlib.sha256(weights.read_bytes()).hexdigest()+'  weights.bin\n')
    def no_whole_file_read(_path): raise AssertionError('unbounded read')
    monkeypatch.setattr(type(root),'read_bytes',no_whole_file_read)
    _verify_model_integrity(root)
