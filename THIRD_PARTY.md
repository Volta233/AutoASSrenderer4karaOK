# 第三方组件与资源

本源码包不包含模型、第三方源码或 FFmpeg。安装器从以下原始来源下载固定版本，按原发行许可使用，不为第三方资源重新授权。下载后的 LICENSE、README 和词典许可保留在资源目录。使用或再分发前请阅读各来源条款。

| 组件 | 来源与版本 |
|---|---|
| Kim Mel-Band Roformer | [官方源码](https://github.com/KimberleyJensen/Mel-Band-Roformer-Vocal-Model)，commit `25f44ffb55ee3c301281bba21b2d6d311cb69ae2`；[权重](https://huggingface.co/KimberleyJSN/melbandroformer)，revision `ac9b0614ab3cd7f77219e18ba494dfd93956c348`。源码仓库未见独立 LICENSE 文件，不将其声明为本项目授权；权重及其使用条件请检查原模型卡 |
| Qwen3-ASR-1.7B | [模型卡](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)，revision `7278e1e70fe206f11671096ffdd38061171dd6e5`，Apache-2.0；`qwen-asr==0.0.6` |
| TIFA | [官方源码](https://github.com/openvpi/TIFA)，MIT，commit `614a277d2580efe5e4b84faf4c34753dbc5a062c`；[TIFA-1.0-ST v1.0.0](https://github.com/openvpi/TIFA/releases/tag/v1.0.0)，权重与配套资源以发行说明为准 |
| UniDic/G2P | [UniDic](https://clrd.ninjal.ac.jp/unidic/)，3.1.0；TIFA 配套词典和英文 LSTM G2P，许可见各资源 |
| FFmpeg/libass | [GyanD FFmpeg](https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2)，9.0.2 essentials；实际适用许可见构建随附 LICENSE 与构建配置 |
| PyTorch | [PyTorch](https://pytorch.org/)，torch/torchaudio 2.11.0 CUDA 12.8，许可随 pip 发行包 |
| OpenCC/Nagisa | [OpenCC Python](https://github.com/yichen0831/opencc-python)、Nagisa/DyNet 发行包；用于名称检索与日语处理 |
| 歌词来源 | LRCLIB、网易云、UtaTen、J-Lyric 及网页检索，保留来源；歌词版权不随程序授权 |

Kim 权重 SHA256：`87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e`。所有下载条目的 URL 和 SHA256 见 `requirements/assets.lock.json`。源码包自身的文件清单见包内 `RELEASE_MANIFEST.json`。
