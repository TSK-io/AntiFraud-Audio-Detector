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

Python 调用示例：

```python
from gradio_client import Client, handle_file

client = Client("你的用户名/AntiFraud-Audio-Detector")

result = client.predict(
    audio_path=handle_file("test.wav"),
    text_prompt="请判断这段录音是否存在电信诈骗风险，并说明理由。",
    api_name="/analyze",
)

print(result)
```

如果 Space 是私有仓库，需要传入 Hugging Face Token：

```python
from gradio_client import Client, handle_file

client = Client(
    "你的用户名/AntiFraud-Audio-Detector",
    hf_token="hf_xxx",
)

result = client.predict(
    audio_path=handle_file("test.wav"),
    text_prompt="请判断这段录音是否存在电信诈骗风险，并说明理由。",
    api_name="/analyze",
)

print(result)
```
