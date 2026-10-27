"""Convert Japanese spelling to dictionary readings after independent ASR."""

import json
from pathlib import Path
import sys


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = {"readings": {}, "available": False}
    try:
        import fugashi

        dictionary = Path(request["dictionary"])
        tagger = fugashi.Tagger(
            f'-r "{(dictionary / "mecabrc").as_posix()}" -d "{dictionary.as_posix()}"'
        )
        for text in request["texts"]:
            result["readings"][text] = "".join(
                (word.feature.kana or word.surface)
                if not word.surface.isascii()
                else word.surface
                for word in tagger(text)
            )
        result["available"] = True
    except (ImportError, RuntimeError, AttributeError) as error:
        result["error"] = str(error)[:600]
    Path(request["output"]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
