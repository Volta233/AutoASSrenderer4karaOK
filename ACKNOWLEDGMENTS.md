# 致谢

映词 · LyriFrame Kim 的整条处理链路都建立在公开研究与开源工作之上：人声分离来自 Kim Mel-Band Roformer，识别来自 Qwen3-ASR，逐字时间来自 TIFA，另有许多词典、工具与数据来源。这里记录实际使用的模型与资源，以及参与本仓库开发的协作者。

> 本文件用于署名与致谢，不改变任何第三方许可。各组件的版本锁定、下载地址、SHA256 与许可提示见 [THIRD_PARTY.md](THIRD_PARTY.md) 和 `requirements/assets.lock.json`。
> 若你是下列项目的作者，希望更正署名、补充链接或撤下致谢，请提交 Issue，我们会尽快处理。

---

## 一、核心模型

按实际调用顺序排列：人声分离 → 语音识别 → 强制对齐。

### 1. Kim Mel-Band Roformer —— 人声分离

- **用途**：从原始歌曲中分离出干声，后续识别与对齐都在干声上进行。
- **模型**：`MelBandRoformer.ckpt`（Kim Mel-Band Roformer）
- **权重**：<https://huggingface.co/KimberleyJSN/melbandroformer>（revision `ac9b0614ab3cd7f77219e18ba494dfd93956c348`）
- **源码**：<https://github.com/KimberleyJensen/Mel-Band-Roformer-Vocal-Model>（commit `25f44ffb55ee3c301281bba21b2d6d311cb69ae2`）
- **作者**：Kimberley Jensen（模型训练与推理实现）；网络架构出自 Ju-Chiang Wang、Wei-Tsung Lu、Minz Won
- **论文**：Ju-Chiang Wang, Wei-Tsung Lu, Minz Won. *Mel-Band RoFormer for Music Source Separation.* arXiv:2310.01809, 2023. <https://arxiv.org/abs/2310.01809>
- **许可**：源码仓库未附独立 LICENSE 文件；权重使用条件以原模型卡为准。本项目按原发行条款使用，不代为授权。

### 2. Qwen3-ASR-1.7B —— 语音识别

- **用途**：识别音频中实际唱出的内容，用于校验与补全歌词、约束对齐候选。
- **模型**：`Qwen3-ASR-1.7B`
- **权重**：<https://huggingface.co/Qwen/Qwen3-ASR-1.7B>（revision `7278e1e70fe206f11671096ffdd38061171dd6e5`）
- **作者**：Qwen Team, Alibaba Group（论文署名：Xian Shi、Xiong Wang、Zhifang Guo、Yongqi Wang、Pei Zhang、Xinyu Zhang、Zishan Guo、Hongkun Hao、Yu Xi、Baosong Yang、Jin Xu、Jingren Zhou、Junyang Lin）
- **论文**：*Qwen3-ASR Technical Report.* arXiv:2601.21337, 2026.
- **推理库**：`qwen-asr==0.0.6`
- **许可**：Apache-2.0

### 3. TIFA-1.0-ST —— 强制对齐

- **用途**：在候选时间窗内定位歌词，输出逐字（音素级）时间戳，并给出可诊断的对齐质量。
- **模型**：TIFA-1.0-ST v1.0.0
- **权重**：<https://github.com/openvpi/TIFA/releases/tag/v1.0.0>
- **源码**：<https://github.com/openvpi/TIFA>（commit `614a277d2580efe5e4b84faf4c34753dbc5a062c`）
- **作者**：Team OpenVPI（<https://github.com/openvpi>，openvpi@outlook.com）
- **许可**：MIT
- **说明**：截至本项目集成时，TIFA 未见公开论文，引用以仓库与发行说明为准。

---

## 二、支撑模型运行的词典与语言资源

| 资源 | 用途 | 来源 | 作者 / 维护方 |
|---|---|---|---|
| UniDic 3.1.0 | 日语分词与读音 | <https://clrd.ninjal.ac.jp/unidic/> | 国立国語研究所（NINJAL） |
| g2pflow | 中/日/英字形到音素转换 | <https://github.com/openvpi/g2pflow> | Team OpenVPI |
| 英文 LSTM G2P | 英语音素转换 | TIFA 配套资源 | Team OpenVPI |
| OpenCC / opencc-python-reimplemented | 简繁与异体字转换 | <https://github.com/BYVoid/OpenCC>、<https://github.com/yichen0831/opencc-python> | Carbo Kuo（BYVoid）及贡献者 |
| Nagisa 0.2.11 | 日语形态素分析 | <https://github.com/taishi-i/nagisa> | Taishi Ikeda |
| soynlp 0.0.493 | 韩语处理 | <https://github.com/lovit/soynlp> | Hyunjoong Kim（lovit） |

词典与模型资源的具体许可随各发行包提供，本项目不重新授权。

---

## 三、运行环境与工具

| 组件 | 用途 | 来源 | 许可 |
|---|---|---|---|
| FFmpeg 9.0.2（essentials build） | 音视频解码、封装与导出 | <https://github.com/GyanD/codexffmpeg> | LGPL / GPL，以该构建随附 LICENSE 与构建配置为准 |
| libass | 字幕排版与烧录 | <https://github.com/libass/libass> | ISC |
| PyTorch / torchaudio 2.11.0 (CUDA 12.8) | 模型推理运行时 | <https://pytorch.org/> | BSD-3-Clause 等，以 pip 发行包为准 |
| FastAPI / Uvicorn / Pydantic | 本地 HTTP 服务与接口校验 | 各自官方仓库 | MIT / BSD / MIT |
| librosa / soundfile | 音频读写与分析 | 各自官方仓库 | ISC / BSD-3-Clause |
| transformers / accelerate / huggingface-hub | 模型加载 | Hugging Face | Apache-2.0 |
| Gradio 6.17.3 | 交互界面组件 | <https://github.com/gradio-app/gradio> | Apache-2.0 |
| ddgs | 网页检索 | 官方发行包 | MIT |

完整依赖清单见 `requirements/`、`requirements.txt` 与 `requirements/assets.lock.json`。

---

## 四、歌词与内容来源

联网查词可能检索以下站点，结果仅用于辅助定位与校对，**不改变歌词本身的版权归属**：

- LRCLIB — <https://lrclib.net/>
- 网易云音乐 — <https://music.163.com/>
- UtaTen — <https://utaten.com/>
- J-Lyric — <https://j-lyric.net/>

歌词著作权归原作者及权利人所有。使用者需自行确保对所处理歌曲与歌词拥有合法使用权。

