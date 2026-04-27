---
title: AntiFraud Audio Detector
emoji: 🐢
colorFrom: indigo
colorTo: blue
sdk: gradio
sdk_version: 6.13.0
python_version: '3.12'
app_file: app.py
pinned: false
license: apache-2.0
---

Check out the configuration reference at https://huggingface.co/docs/hub/spaces-config-reference

这是一个huggingface的space仓库,运行的是Zero GPU.

## Web API

本 Space 通过 Gradio 暴露外部 API。Space 部署后，可以在页面底部点击 **Use via API** 查看实时生成的调用示例。

- `/analyze`：语音反诈检测，输入为音频文件和可选转写。
- `/chat_analyze`：聊天文本反诈检测，输入为聊天记录文本。即时通讯程序可以直接传普通文本，也可以传序列化后的 JSON：

```json
{
  "messages": [
    {"sender": "客服", "content": "账户异常，请打开链接填写银行卡号。"},
    {"sender": "用户", "content": "为什么需要银行卡？"}
  ]
}
```

两个接口都会返回同一套稳定结构：`fraud_result`、`risk_level`、`has_fraud_evidence`、`confidence`、`high_risk_behaviors`、`evidence`、`reason`、`suggestion`。

聊天文本接口目前在本地进行规则识别，不依赖 GPU 模型，适合即时通讯程序先做实时初筛；命中高风险时建议结合业务侧人工复核和用户确认。

## Improved Audio Guard

已将 `read_audio_guard_improved.sh` 中的“证据优先 + 多维度检查 + JSON 约束”逻辑接入 Space：

- `audio_guard.py` 保存反诈判定提示词、字段闭集和 TeleAntiFraud 原生 `is_fraud` 输出兼容逻辑，不再做本地兜底判定或证据推断。
- `app.py` 继续使用 Zero GPU 上的 `JimmyMa99/AntiFraud-SFT` 模型推理，使用当前 Transformers 版本的 `audio=` 参数传入音频，并在日志中打印音频特征编码结果。
- `/analyze` 和 `/chat_analyze` 返回稳定结构：`fraud_result`、`risk_level`、`has_fraud_evidence`、`confidence`、`high_risk_behaviors`、`evidence`、`reason`、`suggestion`。

如果处理器没有生成音频特征，或模型输出无法解析，接口会返回 `fraud_result="无法判断"`、`risk_level="未知"`，不会自动改写成非诈骗或疑似诈骗。

前端的第二个输入框默认留空；如果已有 ASR/人工转写，可以粘贴进去辅助判断。不要把“重点关注某类风险”的提示词当作默认输入，否则模型可能把提示词误当成音频证据。

## Stability Guards

- 应用启动时会尝试加载模型；如果模型加载失败，Gradio 页面仍会启动，并在后续请求中重试加载。
- 上传音频会先检查文件是否存在、是否为空、大小、时长、解码结果和非有限采样值，再进入模型推理。
- 默认限制：`AUDIO_GUARD_MAX_SECONDS=180`、`AUDIO_GUARD_MAX_MB=80`、`AUDIO_GUARD_MIN_SECONDS=0.2`。环境变量格式错误时会退回默认值。
- 提示词现在直接要求完整 Guard JSON 字段；仍兼容模型返回旧版 `{"is_fraud": true/false}` 的情况。
- 规范化层会裁剪过长输出、清洗证据字段、限制证据数量、过滤非闭集高危行为，并确保低置信度结果不会被返回为高风险。
- 聊天文本检测支持普通多行文本，以及 `messages`、`chat`、`records`、`items` 形式的 JSON 消息数组。默认最多读取 200 条消息、12000 个字符。

## Local Tests

```bash
python -B -m unittest discover -s tests -v
```
