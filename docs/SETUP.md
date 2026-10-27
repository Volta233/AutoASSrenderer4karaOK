# 安装与迁移

安装要求为 Windows 10/11 x64、Python 3.10–3.14 x64 和 NVIDIA 显卡驱动。本版使用 PyTorch CUDA 12.8 wheels，CPU 推理不在本版支持范围。基本安装步骤见根目录 [README](../README.md)，本文说明安装选项、资源目录和迁移方式。

安装与下载入口共用版本检测，并按版本选择独立校验的 PyTorch 安装包，不会同时下载多个 Python 版本对应的安装包。已存在的各运行环境必须与本次选定版本一致；切换版本时请先备份任务，再重新创建 `.runtime` 中的运行环境，模型文件可继续复用。

使用标准 CPython Windows x64 安装包；PyPy、ARM64 和自由线程构建的二进制接口不同，不属于本发布包的模型 wheel 范围。Python 3.10 所需的 TOML 兼容库、3.13 及以上的音频兼容库由安装程序自动处理。包管理器优先从清华镜像下载固定版本并校验，失败后回退官方 PyPI，避免较早 Python 自带的旧 pip 无法联网更新。

## 入口

- `install.cmd`：验证 Python/驱动，下载并校验全部资源，创建四个隔离环境，配置词典，执行自检。
- `download-models.cmd`：仅下载模型与 FFmpeg，供提前预下载；不安装 Python 依赖。
- `start.cmd`：启动本地应用并打开浏览器。
- `stop.cmd`：停止本实例及模型子进程，保留已保存数据；关闭浏览器不会停止后台。使用自定义端口时传入相同的 `-Port` 参数。
- `check.cmd`：检查资源、依赖、实际 CUDA 小张量计算，结果写入 `.runtime/doctor.json`。

如果 Python 没有注册 Launcher，可用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Python C:\Python312\python.exe`，换成实际路径。

进阶修复支持 `install.ps1 -Component app|locator|tifa|kim`；正常使用不需要指定。`-TorchIndex` 只接受官方 CUDA 索引；切换索引时须确保所选构建与驱动兼容。

## 目录与下载

| 目录 | 用途 |
|---|---|
| `.runtime/app` | 网页与任务服务 |
| `.runtime/locator` | Qwen3-ASR |
| `.runtime/tifa` | TIFA |
| `.runtime/separation` | Kim |
| `models/` | 下载的源码、权重和多语言词典 |
| `tools/ffmpeg` | 下载的视频工具，包含字幕滤镜 |
| `ktv_projects/` | 用户视频、过程文件、时间轴与成片 |

资源 URL、源码 commit、模型 revision 和 SHA256 都在 `requirements/assets.lock.json`。安装器先检查 SHA256，再启用文件。下载中断保留 `.part`；解压路径会检查，完成后保存文件校验清单。成功解压的压缩包自动删除，减少重复占用。

默认 CUDA 的 PyTorch 安装包按 `requirements/wheels.lock.json` 固定版本和哈希，以 HTTP 分段续传方式下载。一份已校验的 wheel 由三个模型环境共用，避免重复下载同一个大文件。

安装默认使用国内镜像优先策略，不修改系统或用户的 pip 配置。每次安装只向一个依赖索引请求，失败再换源；所有模型、源码、视频工具与 PyTorch 文件仍按原锁文件校验 SHA256。镜像内容变化或返回错误页面不会被当作模型使用。

| 资源 | 优先下载入口 | 失败回退 |
|---|---|---|
| Python 依赖 | 清华 PyPI → 阿里 PyPI | 官方 PyPI |
| pip 引导包 | 清华 PyPI | 官方 PyPI |
| Qwen3-ASR | 魔搭 ModelScope → HF Mirror | Hugging Face |
| Kim 权重 | 魔搭 ModelScope → HF Mirror | Hugging Face |
| PyTorch / torchaudio 默认 CUDA 安装包 | 阿里 → 上海交大 | PyTorch 官方 |
| GitHub 源码、TIFA 权重、FFmpeg | ghfast.top 第三方 GitHub 下载代理 | GitHub 官方 |
| UniDic 词典 | 原 S3 下载站点 | 保留断点续传、可重试 |

GitHub 下载代理属于第三方服务，并非 GitHub 官方镜像。UniDic 暂无本项目验证过的国内镜像，仍使用原下载地址。部分镜像可能重定向到上游或对象存储，因此不能保证所有网络环境都完全不访问境外站点。Kim 使用魔搭社区仓库 `cpadyun/melband-roformer`，其权重 SHA256 已与原 Kim 权重核对一致；Kim 与 Qwen 的魔搭下载地址均固定到具体提交版本。TIFA 暂未找到可核验的同版魔搭资源，继续通过现有入口下载。魔搭不替代 Python 包索引或 PyTorch wheel 镜像。

镜像同步有延迟；只有符合原 SHA256 的内容才会启用，不会为通过下载而更换模型版本。下载过程中会显示当前来源。

正常安装直接双击 `install.cmd`。如需强制官方源：

```powershell
.\install.cmd -DownloadSource official
.\download-models.cmd -DownloadSource official
```

恢复镜像优先使用 `-DownloadSource cn`，也可不带参数重新运行。单独执行 Python 下载脚本时可用 `python scripts/download_models.py --source cn`，或进程环境变量 `LYRIFRAME_DOWNLOAD_SOURCE=cn|official`。安装入口的 `-DownloadSource` 参数决定本次安装的全部默认下载策略；自定义 `-TorchIndex` 仍直接使用指定的官方 CUDA 索引。

### 下载恢复

默认每个来源在首次请求失败后最多再重试 **10 次**，连接/读取超时为 **90 秒**。等待时间逐步增加到最多 30 秒，保留 `.part` 并继续下载。Python 依赖也使用 10 次网络重试及 10 次中断恢复尝试；每个依赖来源耗尽后自动换到下一来源。所有来源都失败时才结束本次安装，再次运行会继续恢复。

文件不存在、访问被拒绝或 SHA256 不一致时直接尝试下一来源；增加次数无法修复错误地址或不同的模型文件。界面会显示来源、失败原因和重试次数。

需要更宽松时：

```powershell
.\install.cmd -DownloadRetries 20 -DownloadTimeout 180
.\download-models.cmd -DownloadRetries 20 -DownloadTimeout 180
```

重试次数允许 0–100，超时允许 10–600 秒；参数只影响本次进程，不修改系统设置。单独运行 Python 脚本可使用 `--retries 20 --timeout 180`，或环境变量 `LYRIFRAME_DOWNLOAD_RETRIES`、`LYRIFRAME_DOWNLOAD_TIMEOUT`。

网络代理可使用操作系统或标准 HTTPS_PROXY 设置，密钥不要写入公开脚本。上述镜像仅用于公开安装文件，不转发视频或歌词。

镜像服务文档：[清华 PyPI](https://mirror.tuna.tsinghua.edu.cn/help/pypi/)、[上海交大 PyTorch](https://mirrors.sjtug.sjtu.edu.cn/docs/pytorch-wheels)。

`.env` 由模板生成，不随源码发行；其中 API Key 属于私人数据。安装会保留已有 Key。所有默认资源路径相对项目目录，TIFA 的本机词典路径在安装时生成。更换目录后重新运行安装以重建路径。
