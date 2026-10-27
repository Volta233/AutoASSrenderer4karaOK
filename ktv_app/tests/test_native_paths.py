from pathlib import Path

import pytest

from ktv_app.native_paths import ascii_model_file


def test_unicode_native_parameter_copy_preserves_bytes_and_is_temporary(
    tmp_path, monkeypatch
):
    source = tmp_path / "中文目录" / "parameters.model"
    source.parent.mkdir()
    source.write_bytes(bytes(range(256)) * 100)
    monkeypatch.setenv("KTV_NATIVE_TEMP", str(tmp_path))
    with ascii_model_file(source) as filename:
        copied = Path(filename)
        assert filename.isascii() and copied != source
        assert copied.read_bytes() == source.read_bytes()
    assert not copied.exists()
    assert source.exists()


def test_ascii_native_parameters_are_used_in_place(tmp_path):
    source = tmp_path / "parameters.model"
    source.write_bytes(b"parameters")
    if not str(source).isascii():
        pytest.skip("Test temporary directory must be ASCII")
    with ascii_model_file(source) as filename:
        assert Path(filename) == source.resolve()
    assert source.exists()


def test_native_parameter_copy_is_cleaned_up_after_reader_failure(
    tmp_path, monkeypatch
):
    source = tmp_path / "中文参数.model"
    source.write_bytes(b"parameters")
    monkeypatch.setenv("KTV_NATIVE_TEMP", str(tmp_path))
    with pytest.raises(RuntimeError, match="reader failed"):
        with ascii_model_file(source) as filename:
            raise RuntimeError("reader failed")
    assert not Path(filename).exists()
