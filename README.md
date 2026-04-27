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

本 Space 通过 Gradio 暴露了外部 API，接口名为 `/analyze`。Space 部署后，可以在页面底部点击 **Use via API** 查看实时生成的调用示例。

## Improved Audio Guard

已将 `read_audio_guard_improved.sh` 中的“证据优先 + 多维度检查 + JSON 约束”逻辑接入 Space：

- `audio_guard.py` 保存反诈判定提示词、字段闭集和 TeleAntiFraud 原生 `is_fraud` 输出兼容逻辑，不再做本地兜底判定或证据推断。
- `app.py` 继续使用 Zero GPU 上的 `JimmyMa99/AntiFraud-SFT` 模型推理，使用当前 Transformers 版本的 `audio=` 参数传入音频，并在日志中打印音频特征编码结果。
- `/analyze` 返回稳定结构：`fraud_result`、`risk_level`、`has_fraud_evidence`、`confidence`、`high_risk_behaviors`、`evidence`、`reason`、`suggestion`。

如果处理器没有生成音频特征，或模型输出无法解析，接口会返回 `fraud_result="无法判断"`、`risk_level="未知"`，不会自动改写成非诈骗或疑似诈骗。

前端的第二个输入框默认留空；如果已有 ASR/人工转写，可以粘贴进去辅助判断。不要把“重点关注某类风险”的提示词当作默认输入，否则模型可能把提示词误当成音频证据。
