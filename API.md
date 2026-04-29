# AntiFraud-SFT 反诈检测 API 文档

本服务提供基于 Qwen2-Audio 的语音反诈骗检测功能，以及针对聊天文本的规则化反诈分析。服务启动后将提供对外的 API 调用能力。

## 基础信息
- **API 协议**: HTTP / WebSocket (由 Gradio 驱动)
- **Hugging Face Space**: `xsssqqqqxx/AntiFraud-Audio-Detector`
- **通讯数据格式**: JSON

---

## 接口一：语音反诈检测 (`/analyze`)

该接口通过输入音频文件与可选的对话转写文本，分析音频中是否包含诈骗意图及高危行为。

### 端点
- **路径**: `/run/analyze` (通过 Gradio 客户端指定 `api_name="/analyze"`)

### 请求参数 (Inputs)
按顺序接收两个参数：
1. **audio_input** (`File` / `filepath`): 待检测的音频文件（推荐格式：`.wav`, `.mp3`等）。
   - **限制**: 音频时长建议在 0.2 秒到 180 秒之间，文件大小建议不超过 80MB（具体受环境变量配置限制）。
2. **text_input** (`String`): 已知的辅助转写文本。若没有，请传空字符串 `""`。

### 返回结果 (Outputs)
返回一个包含结构化反诈分析结果的 JSON 对象（见下方《返回数据结构说明》）。

---

## 接口二：聊天文本检测 (`/chat_analyze`)

该接口通过直接解析聊天记录文本或 JSON 格式的 Message 数组，基于内置反诈规则快速判断文本风险。

### 端点
- **路径**: `/run/chat_analyze` (通过 Gradio 客户端指定 `api_name="/chat_analyze"`)

### 请求参数 (Inputs)
1. **chat_input** (`String`): 聊天记录文本。支持直接粘贴的换行文本，也支持 JSON 字符串（如 `{"messages":[{"sender":"客服","content":"请把验证码发给我"}]}`）。

### 返回结果 (Outputs)
返回一个包含结构化反诈分析结果的 JSON 对象（见下方《返回数据结构说明》）。

---

## 返回数据结构说明 (Guard JSON)

无论是语音检测还是聊天文本检测，都会返回格式一致的结构化 JSON：

```json
{
  "fraud_result": "诈骗",
  "risk_level": "高",
  "has_fraud_evidence": true,
  "confidence": 0.95,
  "high_risk_behaviors": [
    "诱导转账",
    "要求下载陌生App/添加微信",
    "虚假退款"
  ],
  "evidence": [
    "可疑通话方以学校老师身份诱导添加微信并要求转账解决学士论文问题",
    "涉及诱导下载软件、支付手续费等高风险行为"
  ],
  "reason": "通话方冒充学校老师，以学术论文问题为由诱导添加微信并要求转账解决，符合虚假退款/诱导转账的诈骗特征",
  "suggestion": "触发强提醒"
}
```

**字段详解：**
- `fraud_result` *(String)*: 判定结论。枚举值：`"非诈骗"`、`"疑似诈骗"`、`"诈骗"`、`"无法判断"`。
- `risk_level` *(String)*: 风险等级。枚举值：`"低"`、`"中"`、`"高"`、`"未知"`。
- `has_fraud_evidence` *(Boolean)*: 是否检测到涉诈证据。
- `confidence` *(Float)*: 置信度得分，范围在 `0.0` 到 `1.0` 之间。
- `high_risk_behaviors` *(List[String])* : 检出的高危动作列表（如："索要验证码"、"诱导转账"、"安全账户"、"屏幕共享/远程控制" 等）。
- `evidence` *(List[String])* : 触发判定的核心依据，如原音截取短句。
- `reason` *(String)*: 判定的逻辑说明，陈述主要判断原因。
- `suggestion` *(String)*: 建议的系统处理动作。枚举值：`"不触发提醒"`、`"记录但不通知家属"`、`"触发强提醒"`。

---

## 代码调用示例

推荐外部应用在 Python、Node.js 中使用官方的 Gradio Client 库进行调用，这将免去自行处理文件分块和上传 API 的繁琐步骤。

### 示例：Python (使用 `gradio_client`)

首先安装依赖：
```bash
pip install gradio_client
```

代码示例：
```python
from gradio_client import Client, file

# 1. 建立与 Hugging Face Space 的连接
client = Client("xsssqqqqxx/AntiFraud-Audio-Detector")

# ==========================================
# 场景A：语音检测调用
# ==========================================
print("正在检测语音...")
audio_result = client.predict(
    audio_input=file("/path/to/local/test_audio.wav"), # 本地音频路径
    text_input="",                                     # 辅助转写 (选填)
    api_name="/analyze"
)
print("语音检测结果：", audio_result)


# ==========================================
# 场景B：聊天文本调用
# ==========================================
print("\n正在检测聊天记录...")
chat_text = '{"messages": [{"sender":"陌生人", "content":"马上把验证码发给我！"}]}'

chat_result = client.predict(
    chat_input=chat_text, 
    api_name="/chat_analyze"
)
print("聊天检测结果：", chat_result)
```

### 示例：Node.js (使用 `@gradio/client`)

首先安装依赖：
```bash
npm install @gradio/client
```

代码示例：
```javascript
import { Client } from "@gradio/client";

async function runAntiFraud() {
  // 连接到 Hugging Face Space
  const client = await Client.connect("xsssqqqqxx/AntiFraud-Audio-Detector");

  // 场景A：语音检测
  // 注意：Node.js 中上传文件需要传入 Blob、Buffer 或 URL
  const audioBlob = await (await fetch("https://example.com/test_audio.wav")).blob();
  const audioResult = await client.predict("/analyze", [
    audioBlob,
    "" // 辅助文本
  ]);
  console.log("语音检测结果:", audioResult.data[0]);

  // 场景B：聊天文本检测
  const chatText = JSON.stringify({
    messages: [
      { sender: "陌生人", content: "马上把验证码发给我！" }
    ]
  });
  
  const chatResult = await client.predict("/chat_analyze", [ chatText ]);
  console.log("聊天检测结果:", chatResult.data[0]);
}

runAntiFraud();
```
