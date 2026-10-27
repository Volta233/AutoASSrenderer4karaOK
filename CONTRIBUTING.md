# 开发与 PR

使用 Python 3.10–3.14。以下以 3.12 为例，源码及单元测试不依赖大型模型下载：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m ruff check ktv_app scripts
.\.venv\Scripts\python -m ruff format --check ktv_app scripts
.\.venv\Scripts\python -m pytest -q
```

GitHub Actions 对 Windows 和 Linux 运行源码检查；产品安装入口主要面向 Windows。需要 FFmpeg 的真实媒体测试用 `KTV_LIVE_TESTS=1` 显式启用。大型模型功能验证需另备资源及 CUDA 环境，单元测试通过不能代替精度评估。

资源升级集中修改 `requirements/assets.lock.json`，附带资源哈希、兼容性及推理验证。不要提交 `.env`、模型、环境、用户任务或实验视频。服务、模型 worker、时间轴和渲染保持分层，避免把任务实现塞进 API 处理函数。

发布无模型包：

```powershell
py -3.12 scripts/check_release.py
py -3.12 scripts/package_source.py --output dist/LyriFrame-Kim-Source.zip
```

压缩包先生成临时文件，逐文件验证后才提升为正式 ZIP，并输出 `.sha256` 和校验记录。提交 PR 时说明问题、最终行为、测试结果及尚未验证的范围。应用源码授权应由仓库所有者确定；本包不擅自替所有者指定开源许可证。
