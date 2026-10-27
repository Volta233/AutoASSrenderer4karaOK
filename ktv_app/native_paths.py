"""Keep Nagisa's Windows native file reader compatible with Unicode installs."""

from contextlib import contextmanager
import importlib
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .hashing import file_sha256


@contextmanager
def ascii_model_file(source):
    """Copy only native-reader parameters to a private, short-lived ASCII path."""
    source = Path(source).resolve()
    if str(source).isascii():
        yield str(source)
        return
    candidates = [os.getenv("KTV_NATIVE_TEMP"), tempfile.gettempdir()]
    if os.getenv("SystemRoot"):
        candidates.append(str(Path(os.environ["SystemRoot"]) / "Temp"))
    temporary = None
    for candidate in candidates:
        if not candidate:
            continue
        base = Path(candidate).expanduser().resolve()
        if not str(base).isascii() or not base.is_dir():
            continue
        try:
            temporary = tempfile.TemporaryDirectory(prefix="aiktv-nagisa-", dir=base)
            assert Path(temporary.name).resolve().is_relative_to(base)
            break
        except OSError:
            continue
    if temporary is None:
        raise RuntimeError(
            "Nagisa 需要可写的 ASCII 临时目录；请将 KTV_NATIVE_TEMP 设置为不含中文的现有目录"
        )
    with temporary:
        target = Path(temporary.name) / "parameters.model"
        shutil.copyfile(source, target)
        if file_sha256(source) != file_sha256(target):
            raise RuntimeError("Nagisa 临时参数文件校验失败")
        yield str(target)


def prepare_nagisa():
    """Load the unmodified package with an adapted default Tagger constructor.

    DyNet38's populate() cannot open Unicode filenames on Windows. Python's
    vocabulary/hyperparameter readers work normally. No package files, weights,
    tokenizer behavior or virtual environments are changed or relocated.
    """
    if "nagisa" in sys.modules:
        return sys.modules["nagisa"]
    spec = importlib.util.find_spec("nagisa")
    if spec is None or not spec.origin:
        raise ImportError("nagisa is not installed")
    model = Path(spec.origin).parent / "data/nagisa_v001.model"
    if os.name != "nt" or str(model).isascii():
        return importlib.import_module("nagisa")
    package = importlib.util.module_from_spec(spec)
    existing = set(sys.modules)
    sys.modules["nagisa"] = package
    try:
        tagger = importlib.import_module("nagisa.tagger")
        original = tagger.Tagger

        class PortableTagger(original):
            def __init__(
                self, vocabs=None, params=None, hp=None, single_word_list=None
            ):
                with ascii_model_file(
                    params if params is not None else model
                ) as native_params:
                    super().__init__(vocabs, native_params, hp, single_word_list)

        tagger.Tagger = PortableTagger
        spec.loader.exec_module(package)
        return package
    except Exception:
        for name in set(sys.modules) - existing:
            if name == "nagisa" or name.startswith("nagisa."):
                del sys.modules[name]
        raise
