import importlib.util
from pathlib import Path


def checker():
    path = Path(__file__).resolve().parents[2] / "scripts/check_release.py"
    spec = importlib.util.spec_from_file_location("release_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_possible_keys_are_reported_without_returning_the_value(tmp_path):
    module = checker()
    module.ROOT = tmp_path
    file = tmp_path / "config.py"
    secret = "sk-" + "x" * 32
    file.write_text(secret)
    issues = module.inspect([file])
    assert issues == [dict(file="config.py", reason="possible_secret")]
    assert secret not in str(issues)


def test_media_and_private_configuration_never_pass_as_public_source(tmp_path):
    module = checker()
    module.ROOT = tmp_path
    model = tmp_path / "models/model.ckpt"
    model.parent.mkdir()
    model.write_bytes(b"not a real model")
    settings = tmp_path / ".env"
    settings.write_text("private=true")
    example = tmp_path / ".env.example"
    example.write_text("KTV_LLM_API_KEY=")
    assert len(module.inspect([model, settings])) >= 3
    assert module.inspect([example]) == []


def test_downloaded_volumes_and_interrupted_restore_are_excluded_without_git(tmp_path):
    module = checker()
    module.ROOT = tmp_path
    public = tmp_path / "app.py"
    public.write_text("pass")
    for name in (
        "models.zip",
        "models.zip.001",
        "models.zip.parts.json",
        "aiktv-restore-old/resource.tmp",
    ):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"private download")
    assert module.source_files() == [public]
