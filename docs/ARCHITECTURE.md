# 代码结构与流程

`ktv_app/backend/app.py` 定义 HTTP API，`service.py` 组织应用服务，`repository.py` 和 `storage.py` 负责落盘，`tasks/` 分离媒体、对齐与渲染任务。`frontend/` 是无需 Node 构建的静态界面。`workflow.py` 定义四个步骤的完成条件。

音频与模型分工：

1. `media.py` 提取音频并记录原始时钟；`separation.py` 核验输入、权重和缓存，独立 `separation_worker.py` 调用 Kim 官方 Mel-Band Roformer，完整人声等长保留。
2. `recognition.py` 运行两遍短窗口 Qwen3-ASR，识别真实演唱内容。ASR 短窗口的粗时间来自音频窗口，ASR 本身不生成词级时间戳。
3. TIFA 为 ASR 窗口提供时间定位，再与确认歌词顺序匹配，寻找分块切点。逐步放宽候选要求，正式输入保持小于 90 秒；无法形成安全切点时明确提示处理失败。
4. `aligners/` 用 TIFA 对正式歌词块对齐，优先保留原生词边界；局部失败重试，缺词用有边界约束的补齐并标记估算。不会把估算计为模型定位成功。没有加载 Qwen FA。
5. `timeline.py` 保留原词、时间与来源，`completion.py` 完成缺失展示数据，`rendering.py` 生成 ASS 与双行滚动视频。最终音轨来自原视频。

模型环境相互隔离，顺序执行；应用服务不直接导入 torch。缓存身份包含输入、配置、源码及权重，替换模型不会静默复用旧人声。

`requirements/assets.lock.json` 是资源锁定清单，`scripts/download_models.py` 只负责下载/校验/安装资源，`install.ps1` 只负责安装编排，`scripts/package_source.py` 只发布公开源码。模型、缓存和任务均不入版本控制。

`serve.py` 启动本机服务并在 Windows Job Object 中管理模型子进程。关闭浏览器不影响服务；`stop.ps1` 根据应用目录、端口、解释器和进程创建时间验证实例，再终止该实例的进程树。服务退出时 Windows 回收同组子进程，避免模型继续占用显存。强制退出后，下一次启动会将处理中任务标记为中断，保留已保存文件。

歌词检索只读取公开歌词来源，用户确认后进入对齐；没有 LLM 调用或密钥配置。第四步仅提供完整视频导出，时间轴的视频播放与字幕叠加仍用于校对。
