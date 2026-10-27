"""Generate local absolute resource paths after third-party assets are installed."""

import argparse
from pathlib import Path

import yaml


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=root / "models/forced_alignment/tifa/TIFA-1.0-ST",
    )
    parser.add_argument(
        "--unidic",
        type=Path,
        default=root / "models/forced_alignment/tifa/resources/UniDic/unidic",
    )
    args = parser.parse_args()
    model, dictionary = args.model_dir.resolve(), args.unidic.resolve()
    required = [
        model / "model.pt",
        model / "config.yaml",
        model / "vocabulary.json",
        dictionary / "mecabrc",
        model / "assets/LstmG2p-Eng",
        *[
            model / "dictionaries" / name
            for name in (
                "ds-zh-pinyin-lite.txt",
                "japanese_dict_full.txt",
                "jyutping_dict.txt",
                "ds_cmudict-07b.txt",
            )
        ],
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit("Missing model resources:\n" + "\n".join(missing))

    def words(name):
        return str(model / "dictionaries" / name)

    converters = [
        dict(
            id="chinese-pinyin", kwargs=dict(dict_path=words("ds-zh-pinyin-lite.txt"))
        ),
        dict(
            id="japanese-mecab",
            kwargs=dict(
                dict_path=words("japanese_dict_full.txt"),
                nbest=32,
                double_written_sokuon=False,
                unidic_dir=str(dictionary),
            ),
        ),
        dict(id="yue-jyutping", kwargs=dict(dict_path=words("jyutping_dict.txt"))),
        dict(
            id="lstm",
            language="en",
            kwargs=dict(
                dict_path=words("ds_cmudict-07b.txt"),
                model_path=str(model / "assets/LstmG2p-Eng"),
                beam_size=16,
            ),
        ),
        *[
            dict(id="dictionary", language=lang, kwargs=dict(dict_path=words(name)))
            for lang, name in [
                ("zh", "ds-zh-pinyin-lite.txt"),
                ("ja", "japanese_dict_full.txt"),
                ("yue", "jyutping_dict.txt"),
            ]
        ],
        dict(id="passthrough"),
    ]
    result = dict(
        preprocessors=[
            dict(id=name)
            for name in ("filter-punctuation", "strip-whitespace", "lowercase")
        ],
        converters=converters,
    )
    target = model / "g2p.local.yaml"
    target.write_text(
        yaml.safe_dump(result, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(target)


if __name__ == "__main__":
    main()
