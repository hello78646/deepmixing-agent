# Deepmixing · 零氪星球 Agent

这是本机 `8765` 的原版 DJ 工作台与 Python 混音后端，不是浏览器双轨试玩。静音开场结束后直接进入工作台。

## 本地运行

```powershell
python -m pip install -r requirements-audio.txt
.\start-deepmixing.ps1
```

浏览器打开 `http://127.0.0.1:8765/`。FFmpeg 需支持 `rubberband` 滤镜；可用 `DEEPMIXING_FFMPEG` 指定。分轨和识别运行依赖可分别使用 `tools/setup_separation.py`、`tools/setup_lyrics.py` 安装。

部署者需另行配置模型服务（`DEEPMIXING_AGENT_API_KEY`、`DEEPMIXING_AGENT_BASE_URL`、`DEEPMIXING_AGENT_PROVIDER`、`DEEPMIXING_AGENT_MODEL`）和私有的 `DEEPMIXING_AGENT_PROMPT_FILE`（包含 `SYSTEM` 与 `SMART_DIALOGUE` 两个字符串），并单独安装零氪声线运行资产。密钥、原始训练音色、素材、历史任务和内部对话配置不在仓库中。

## 观众体验

本仓库不是在线体验网址。GitHub Pages 不运行 Python。公网需要独立托管并隔离访客素材、限制上传和用量；不要将本机 8765 直接暴露给互联网。
