# AntiFraud-SFT 反诈检测 API 文档

本服务提供两类反诈检测能力：

- 语音反诈检测：上传通话录音，可附带可选转写文本，返回结构化风险判断。
- 聊天文本检测：输入聊天记录文本或 `messages` JSON，返回同一套结构化风险判断。

两个接口都会返回统一的 Guard JSON，适合 App、即时通讯程序或后端风控服务直接接入。

## 基础信息

- **服务形态**: Hugging Face Space / Gradio
- **Space**: `xsssqqqqxx/AntiFraud-Audio-Detector`
- **通信协议**: HTTP / WebSocket，由 Gradio Client 封装
- **返回格式**: JSON 对象
- **推荐接入方式**: Python `gradio_client` 或 Node.js `@gradio/client`

## 接口一：语音反诈检测

### 端点

- **API 名称**: `/analyze`
- **Gradio 路径**: `/run/analyze`

使用 Gradio Client 时，指定 `api_name="/analyze"`。

### 请求参数

按顺序接收两个参数：

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `audio_input` | `File` / `filepath` | 是 | 待检测音频文件。推荐 `.wav`、`.mp3`、`.m4a` 等常见可解码格式。 |
| `text_input` | `String` | 否 | 已知辅助转写文本。没有转写时传空字符串 `""`。 |

### 音频限制

默认限制如下，部署时可通过环境变量调整：

- 最短时长：`0.2` 秒，对应 `AUDIO_GUARD_MIN_SECONDS`
- 最长时长：`180` 秒，对应 `AUDIO_GUARD_MAX_SECONDS`
- 最大文件大小：`80MB`，对应 `AUDIO_GUARD_MAX_MB`
- 模型采样率：`16000Hz`

### 返回结果

返回一个 Guard JSON 对象。字段、类型和枚举见下方《Guard JSON 返回结构》。

## 接口二：聊天文本检测

### 端点

- **API 名称**: `/chat_analyze`
- **Gradio 路径**: `/run/chat_analyze`

使用 Gradio Client 时，指定 `api_name="/chat_analyze"`。

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `chat_input` | `String` | 是 | 聊天记录文本。支持普通多行文本，也支持序列化后的 JSON 字符串。 |

### 支持的聊天 JSON 形式

接口会尝试读取 `messages`、`chat`、`records`、`items` 这几类消息数组。每条消息支持常见字段：

- 内容字段：`content`、`text`、`message`、`body`、`msg`
- 发送方字段：`sender`、`from`、`speaker`、`role`、`name`、`nickname`

示例：

```json
{
  "messages": [
    {
      "sender": "客服",
      "content": "账户异常，请打开 http://fake.example 填写银行卡号和支付密码。"
    },
    {
      "sender": "用户",
      "content": "为什么需要银行卡？"
    }
  ]
}
```

### 文本限制

- 默认最多读取 `200` 条消息。
- 默认最多分析 `12000` 个字符。
- 超出部分会被截断后再分析。

## Guard JSON 返回结构

语音检测和聊天文本检测都返回同一套字段，字段顺序固定如下：

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

### 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `fraud_result` | `String` | 判定结论。 |
| `risk_level` | `String` | 风险等级。 |
| `has_fraud_evidence` | `Boolean` | 是否检测到涉诈证据。 |
| `confidence` | `Float` | 置信度，范围为 `0.0` 到 `1.0`。 |
| `high_risk_behaviors` | `List[String]` | 命中的高危行为标签。未命中时返回空数组 `[]`。 |
| `evidence` | `List[String]` | 触发判定的关键证据，如原音近似转写或聊天片段。 |
| `reason` | `String` | 判定原因，说明为什么是低、中、高或未知风险。 |
| `suggestion` | `String` | 建议 App 执行的提醒动作。 |

### 字段枚举

`fraud_result` 枚举：

- `非诈骗`
- `疑似诈骗`
- `诈骗`
- `无法判断`

`risk_level` 枚举：

- `低`
- `中`
- `高`
- `未知`

`suggestion` 枚举：

- `不触发提醒`
- `记录但不通知家属`
- `触发强提醒`

`high_risk_behaviors` 只会返回以下闭集标签：

- `索要验证码`
- `诱导转账`
- `安全账户`
- `屏幕共享/远程控制`
- `要求下载陌生App/添加微信`
- `冒充公检法并威胁`
- `要求保密`
- `虚假退款`
- `刷单返利`
- `高收益投资理财`
- `冒充熟人借钱`
- `索要银行卡/身份证/密码`
- `引导点击陌生链接`
- `冒充机构人员`

### 结果联动规则

| `fraud_result` | `risk_level` | `suggestion` |
| --- | --- | --- |
| `非诈骗` | `低` | `不触发提醒` |
| `疑似诈骗` | `中` | `记录但不通知家属` |
| `诈骗` | `高` | `触发强提醒` |
| `无法判断` | `未知` | `记录但不通知家属` |

当模型输出不完整或无法解析时，接口不会擅自判定为诈骗，会返回 `无法判断`：

```json
{
  "fraud_result": "无法判断",
  "risk_level": "未知",
  "has_fraud_evidence": false,
  "confidence": 0.0,
  "high_risk_behaviors": [],
  "evidence": [],
  "reason": "模型未给出可用判断。",
  "suggestion": "记录但不通知家属"
}
```

## 调用示例：Python

安装依赖：

```bash
pip install gradio_client
```

语音检测：

```python
from gradio_client import Client, file

client = Client("xsssqqqqxx/AntiFraud-Audio-Detector")

result = client.predict(
    audio_input=file("/path/to/local/call.m4a"),
    text_input="",
    api_name="/analyze",
)

print(result)
```

聊天文本检测：

```python
import json
from gradio_client import Client

client = Client("xsssqqqqxx/AntiFraud-Audio-Detector")

chat_text = json.dumps(
    {
        "messages": [
            {"sender": "客服", "content": "账户异常，请打开链接填写银行卡号。"},
            {"sender": "用户", "content": "为什么需要银行卡？"},
        ]
    },
    ensure_ascii=False,
)

result = client.predict(
    chat_input=chat_text,
    api_name="/chat_analyze",
)

print(result)
```

## 调用示例：Node.js

安装依赖：

```bash
npm install @gradio/client
```

语音检测和聊天文本检测：

```javascript
import { Client } from "@gradio/client";

const client = await Client.connect("xsssqqqqxx/AntiFraud-Audio-Detector");

const audioBlob = await (await fetch("https://example.com/call.m4a")).blob();

const audioResult = await client.predict("/analyze", [
  audioBlob,
  ""
]);

console.log("语音检测结果:", audioResult.data[0]);

const chatText = JSON.stringify({
  messages: [
    { sender: "客服", content: "账户异常，请打开链接填写银行卡号。" },
    { sender: "用户", content: "为什么需要银行卡？" }
  ]
});

const chatResult = await client.predict("/chat_analyze", [
  chatText
]);

console.log("聊天检测结果:", chatResult.data[0]);
```

## App 接入建议

- App 侧优先读取 `fraud_result`、`risk_level`、`confidence` 和 `suggestion` 做提醒决策。
- 展示给用户或家属时，可使用 `reason` 和 `evidence` 解释命中原因。
- `high_risk_behaviors` 是闭集标签，适合做埋点、统计和规则分流。
- `无法判断` 不等于安全，只表示本次音频或文本不足以完成判断。
- `confidence` 是模型或规则置信度，不建议单独作为唯一风控条件，应结合 `risk_level` 和 `suggestion` 使用。
