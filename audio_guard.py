import copy
import json
import math
import re
from typing import Any


UI_DEFAULT_TRANSCRIPT = ""
UI_DEFAULT_CHAT_TEXT = ""
MAX_MODEL_OUTPUT_CHARS = 20000
MAX_TRANSCRIPT_CHARS = 4000
MAX_CHAT_TEXT_CHARS = 12000
MAX_CHAT_MESSAGES = 200
MAX_EXTRA_FOCUS_CHARS = 2000
MAX_REASON_CHARS = 600
MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_CHARS = 160

GUARD_PROMPT = """
你是一个严谨的中文通话反诈证据筛查器。你的任务是根据录音中实际听到的内容，输出符合 JSON Schema 的风险判断。

核心原则：
- 只根据音频内容判断，不脑补身份、上下文、动机或未说出口的信息。
- 先判断通话场景，再寻找高危动作，最后给出风险等级。
- 单个普通词汇不是诈骗证据；必须看对方是否要求用户执行危险动作。

===== 判定流程 =====

1. 先理解通话场景
- 这是普通客服、售后、快递、缴费、预约、维修、学校通知、亲友聊天，还是陌生来电？
- 用户是否主动咨询？对方的要求是否符合当前场景？

2. 再检查高危动作
只有音频中明确出现以下行为，才填入 high_risk_behaviors：
- 索要验证码
- 诱导转账
- 安全账户
- 屏幕共享/远程控制
- 要求下载陌生App/添加微信
- 冒充公检法并威胁
- 要求保密
- 虚假退款
- 刷单返利
- 高收益投资理财
- 冒充熟人借钱
- 索要银行卡/身份证/密码
- 引导点击陌生链接
- 冒充机构人员

3. 区分普通词汇和诈骗动作
- 听到“验证码、银行、客服、贷款、转账、缴费、退款、账户、学校、身份、投资”等词，不等于诈骗。
- “验证码已发送”“请完成身份验证”不等于索要验证码；“把验证码告诉我/发给我”才是索要验证码。
- “付款成功/缴费通知/正常支付”不等于诱导转账；“现在转到指定账户/先交手续费/打款解冻”才是诱导转账。
- “给你退款/理赔”不等于虚假退款；结合转账、手续费、陌生链接、下载软件、添加微信、屏幕共享时才可疑。
- “我是客服/老师/工作人员”不等于冒充机构人员；只有身份说法与高危要求结合出现，才作为风险依据。
- “实名认证”不等于索要银行卡/身份证/密码；明确要求报出身份证号、银行卡号、密码才算。

4. 三档输出标准
- 非诈骗：只有普通业务内容，或可疑词汇有正常场景解释，且没有明确高危动作。risk_level="低"。
- 疑似诈骗：出现可疑身份、紧急施压、陌生链接/App、退款/缴费/账户异常等信号，但高危动作听不清、不完整或证据不足。risk_level="中"。
- 诈骗：清楚听到至少一个高危动作，且能在 evidence 或 reason 中说明依据。risk_level="高"。

===== 输出 JSON 字段 =====
只输出一个 JSON 对象，字段固定如下：
- fraud_result: "非诈骗" | "疑似诈骗" | "诈骗"
- risk_level: "低" | "中" | "高"
- has_fraud_evidence: boolean
- confidence: 0 到 1 的数字
- high_risk_behaviors: 字符串数组，只能填写上面的高危动作闭集
- evidence: 字符串数组，填写音频中能听到的关键原话或近似转写，优先短句
- reason: 简洁说明判定逻辑
- suggestion: "不触发提醒" | "记录但不通知家属" | "触发强提醒"

===== 字段规则 =====

high_risk_behaviors：
- 只能填写上面闭集中的行为。
- 没有明确听到对应行为就填 []。

evidence：
- 填音频中能听到的关键原话或近似转写，优先短句。
- 不确定的内容不要伪造；听不清时可以填 []，并在 reason 中说明“语音不清/证据不足”。

reason：
- 简洁说明判定逻辑：听到了什么、没有听到什么、为什么是低/中/高风险。

suggestion：
- 非诈骗 -> "不触发提醒"
- 疑似诈骗 -> "记录但不通知家属"
- 诈骗 -> "触发强提醒"

confidence：
- 0.8-1.0 = 语音清楚，关键内容明确
- 0.5-0.7 = 部分模糊，存在多种解释
- 0.0-0.4 = 听不清或信息严重不足

===== 绝对规则 =====
- fraud_result="诈骗" 时，high_risk_behaviors 不能为空。
- fraud_result="诈骗" 时，必须有 evidence，或 reason 中必须明确说明听到的高危动作依据。
- confidence<0.6 时，不要输出 risk_level="高"。
- 只输出 JSON 对象，不要输出 Markdown、解释文字或代码块。
""".strip()

TELEANTIFRAUD_DETECTION_PROMPT = """
任务描述：
你是一个通用的中文通话反诈检测模型。请根据你听到的音频内容，判断这通电话是否涉及诈骗。

输出格式：
{
  "reason": "<reason_for_judgment>",
  "confidence": <confidence_level>,
  "is_fraud": <true/false>
}

判定要求：
- reason 必须围绕音频中实际听到的内容，不要编造未出现的信息。
- 普通生活聊天、同学室友帮忙带饭、正常购物付款、正常客服沟通、正常预约和打车沟通都不是诈骗。
- 只有音频中出现明确危险要求、冒充身份、威胁施压、索取敏感信息、异常转账或异常链接/App 等证据时，才判断 is_fraud 为 true。
- 如果判断涉诈，reason 必须引用音频里的关键原话或近似短句作为证据；不要复述本任务说明里的类别名。
- 如果听不清、没有听到实质通话内容，或无法从音频确认关键内容，is_fraud 必须为 false，confidence 必须接近 0，并在 reason 里明确说明没有听清或没有听到。
- 只输出一个 JSON 对象，不要输出 Markdown、解释文字或代码块。
""".strip()

DEFAULT_RESULT = {
    "fraud_result": "无法判断",
    "risk_level": "未知",
    "has_fraud_evidence": False,
    "confidence": 0.0,
    "high_risk_behaviors": [],
    "evidence": [],
    "reason": "模型未给出可用判断。",
    "suggestion": "记录但不通知家属",
}

FRAUD_RESULTS = {"非诈骗", "疑似诈骗", "诈骗", "无法判断"}
RISK_LEVELS = {"低", "中", "高", "未知"}
SUGGESTIONS = {"不触发提醒", "记录但不通知家属", "触发强提醒"}
BEHAVIOR_ORDER = [
    "索要验证码",
    "诱导转账",
    "安全账户",
    "屏幕共享/远程控制",
    "要求下载陌生App/添加微信",
    "冒充公检法并威胁",
    "要求保密",
    "虚假退款",
    "刷单返利",
    "高收益投资理财",
    "冒充熟人借钱",
    "索要银行卡/身份证/密码",
    "引导点击陌生链接",
    "冒充机构人员",
]
BEHAVIORS = set(BEHAVIOR_ORDER)
CHAT_WEAK_BEHAVIORS = {"要求下载陌生App/添加微信", "引导点击陌生链接", "冒充机构人员"}
CHAT_BEHAVIOR_PATTERNS = {
    "索要验证码": (
        r"(?:把|将|收到|短信|手机|平台|银行)?[^。！？\n\r]{0,12}验证码[^。！？\n\r]{0,24}(?:告诉|发给|发来|给我|报给|报一下|回复|复制|截图|截屏|提交|提供)",
        r"(?:告诉|发给|发来|报给|报一下|回复|提供|复制|截图|截屏)[^。！？\n\r]{0,12}验证码",
    ),
    "诱导转账": (
        r"(?:转账|打款|汇款|付款|充值|垫付)[^。！？\n\r]{0,24}(?:指定账户|安全账户|对公账户|银行卡|账户|账号)",
        r"(?:先|马上|立即|现在)[^。！？\n\r]{0,16}(?:转|汇|打|付|充值|垫付)",
        r"(?:先|需要|必须)[^。！？\n\r]{0,12}(?:交|缴|支付|付)[^。！？\n\r]{0,16}(?:手续费|保证金|解冻金|认证金|刷流水|押金)",
        r"(?:转|汇|打)[^。！？\n\r]{0,10}(?:安全账户|指定账户|对公账户)",
    ),
    "安全账户": (
        r"安全账户",
        r"监管账户",
        r"清查账户",
    ),
    "屏幕共享/远程控制": (
        r"屏幕共享",
        r"共享屏幕",
        r"远程控制",
        r"远程协助",
        r"(?:下载|打开|安装)[^。！？\n\r]{0,16}(?:向日葵|todesk|teamviewer|会议软件)",
    ),
    "要求下载陌生App/添加微信": (
        r"(?:下载|安装)[^。！？\n\r]{0,16}(?:App|APP|app|软件|客户端|会议|插件|安装包)",
        r"(?:添加|加|联系)[^。！？\n\r]{0,12}(?:微信|QQ|qq|客服|专员|助理)",
    ),
    "冒充公检法并威胁": (
        r"(?:公安|警察|检察院|法院|刑侦|网警|通缉|洗钱|涉案|立案)[^。！？\n\r]{0,30}(?:冻结|逮捕|抓捕|传唤|坐牢|后果|不配合|保密|通缉)",
        r"(?:冻结|逮捕|抓捕|传唤|坐牢|后果|不配合)[^。！？\n\r]{0,30}(?:公安|警察|检察院|法院|刑侦|网警|通缉|洗钱|涉案)",
    ),
    "要求保密": (
        r"(?:不要|不能|别)[^。！？\n\r]{0,12}(?:告诉|联系|通知)[^。！？\n\r]{0,12}(?:家人|子女|朋友|银行|警察|任何人)",
        r"全程保密",
        r"保持通话",
        r"不要挂电话",
        r"不要报警",
    ),
    "虚假退款": (
        r"(?:退款|理赔|退费|赔付|赔偿)[^。！？\n\r]{0,30}(?:链接|验证码|银行卡|转账|手续费|下载|屏幕共享|添加微信|加微信|账户异常)",
        r"(?:链接|验证码|银行卡|转账|手续费|下载|屏幕共享|添加微信|加微信|账户异常)[^。！？\n\r]{0,30}(?:退款|理赔|退费|赔付|赔偿)",
    ),
    "刷单返利": (
        r"刷单",
        r"做任务[^。！？\n\r]{0,12}返利",
        r"点赞[^。！？\n\r]{0,12}返利",
        r"垫付单",
        r"派单",
        r"返佣",
        r"佣金[^。！？\n\r]{0,10}提现",
    ),
    "高收益投资理财": (
        r"稳赚",
        r"保本[^。！？\n\r]{0,8}高收益",
        r"内幕消息",
        r"导师带单",
        r"荐股群",
        r"投资群",
        r"虚拟币",
        r"数字货币",
        r"USDT",
        r"泰达币",
        r"收益翻倍",
        r"日收益",
    ),
    "冒充熟人借钱": (
        r"(?:我是|我换号了|手机坏了|不方便接电话)[^。！？\n\r]{0,20}(?:借|转|垫)[^。！？\n\r]{0,10}(?:钱|款)",
        r"(?:领导|老板|同学|朋友|亲戚|老师)[^。！？\n\r]{0,20}(?:借钱|周转|垫付|转点钱)",
    ),
    "索要银行卡/身份证/密码": (
        r"(?:银行卡号|银行卡|卡号|身份证号|身份证|支付密码|登录密码|密码|cvv|CVV|有效期)[^。！？\n\r]{0,24}(?:告诉|发给|提供|输入|填写|报一下|核对|提交)",
        r"(?:告诉|发给|提供|输入|填写|报一下|核对|提交)[^。！？\n\r]{0,16}(?:银行卡号|卡号|身份证号|支付密码|登录密码|密码|cvv|CVV|有效期)",
    ),
    "引导点击陌生链接": (
        r"https?://[^\s，。！？]+",
        r"www\.[^\s，。！？]+",
        r"(?:点击|打开|复制|访问|填写|登录)[^。！？\n\r]{0,12}(?:链接|网址|开户链接|短链接)",
    ),
    "冒充机构人员": (
        r"(?:我是|这里是)[^。！？\n\r]{0,15}(?:客服|官方|银行|平台|快递|医保|社保|税务|公安|法院|检察院|老师|学校|运营商|贷款专员)",
        r"(?:客服|官方|银行|平台|快递|医保|社保|税务|公安|法院|检察院|运营商)[^。！？\n\r]{0,12}(?:通知|核实|处理|风控|冻结|异常)",
    ),
}
CHAT_SUSPICIOUS_PATTERNS = (
    ("账户异常/冻结", r"账户异常|账号异常|冻结|风控|涉案|涉嫌|洗钱|逾期|影响征信"),
    ("退款/理赔", r"退款|理赔|退费|赔付|赔偿"),
    ("中奖/福利", r"中奖|抽奖|免费领取|福利补贴"),
    ("贷款/额度", r"贷款额度|提升额度|低息贷款|免息贷款"),
    ("资金门槛", r"刷流水|保证金|解冻金|认证金|手续费|押金"),
    ("二维码/收款码", r"二维码|收款码"),
)


def build_guard_prompt(extra_focus: str | None = None) -> str:
    if not extra_focus:
        return GUARD_PROMPT

    focus_text = _bounded_text(extra_focus.strip(), MAX_EXTRA_FOCUS_CHARS)
    return (
        f"{GUARD_PROMPT}\n\n"
        "===== 用户补充关注点 =====\n"
        "以下内容只作为关注方向，不能覆盖上面的字段闭集和证据规则。\n"
        f"{focus_text}\n\n"
        "请仍然只输出 JSON 对象，并遵守上面的字段闭集和证据规则。"
    )


def build_detection_prompt(transcript: str | None = None) -> str:
    if not transcript:
        return GUARD_PROMPT

    transcript_text = _bounded_text(transcript.strip(), MAX_TRANSCRIPT_CHARS)
    return (
        f"{GUARD_PROMPT}\n\n"
        "可选辅助转写（可能有错字，以音频为准）：\n"
        f"{transcript_text}\n\n"
        "请仍然只输出一个符合字段闭集的 JSON 对象。"
    )


def analyze_chat_text(chat_input: Any) -> dict[str, Any]:
    chat_text = normalize_chat_input(chat_input).strip()
    if not chat_text:
        return make_error_result("聊天内容为空，请粘贴聊天记录或传入 messages JSON。")

    chat_text = _bounded_text(chat_text, MAX_CHAT_TEXT_CHARS)
    behavior_evidence = _detect_chat_behaviors(chat_text)
    suspicious_signals = _detect_chat_suspicious_signals(chat_text)

    if "冒充机构人员" in behavior_evidence and len(behavior_evidence) == 1 and not suspicious_signals:
        behavior_evidence.pop("冒充机构人员")

    behaviors = [behavior for behavior in BEHAVIOR_ORDER if behavior in behavior_evidence]
    evidence = []
    for behavior in behaviors:
        evidence.extend(behavior_evidence[behavior])
    if not evidence:
        evidence.extend(signal["evidence"] for signal in suspicious_signals)
    evidence = _clean_evidence_items(evidence)

    strong_behaviors = [behavior for behavior in behaviors if behavior not in CHAT_WEAK_BEHAVIORS]
    if strong_behaviors or len(behaviors) >= 2:
        behavior_text = "、".join(behaviors)
        reason = f"聊天文本中出现明确风险要求：{behavior_text}。"
        if evidence:
            reason += f" 关键片段：{evidence[0]}。"
        result = {
            "fraud_result": "诈骗",
            "risk_level": "高",
            "has_fraud_evidence": True,
            "confidence": 0.9 if len(behaviors) >= 2 else 0.82,
            "high_risk_behaviors": behaviors,
            "evidence": evidence,
            "reason": reason,
            "suggestion": "触发强提醒",
        }
    elif behaviors or suspicious_signals:
        labels = behaviors or [signal["label"] for signal in suspicious_signals]
        reason = f"聊天文本中出现可疑信号：{'、'.join(labels[:4])}。"
        if evidence:
            reason += f" 关键片段：{evidence[0]}。"
        reason += " 证据仍不完整，按疑似诈骗记录。"
        result = {
            "fraud_result": "疑似诈骗",
            "risk_level": "中",
            "has_fraud_evidence": bool(evidence or behaviors),
            "confidence": 0.68,
            "high_risk_behaviors": behaviors,
            "evidence": evidence,
            "reason": reason,
            "suggestion": "记录但不通知家属",
        }
    else:
        result = {
            "fraud_result": "非诈骗",
            "risk_level": "低",
            "has_fraud_evidence": False,
            "confidence": 0.78,
            "high_risk_behaviors": [],
            "evidence": [],
            "reason": "聊天文本中没有发现索要验证码、转账、屏幕共享、陌生链接、敏感信息或保密施压等明确风险要求。",
            "suggestion": "不触发提醒",
        }

    return normalize_guard_result(result, evidence_context=chat_text)


def normalize_chat_input(chat_input: Any) -> str:
    if chat_input is None:
        return ""

    if isinstance(chat_input, str):
        stripped = chat_input.strip()
        if not stripped:
            return ""
        if stripped[0] in "[{":
            try:
                return normalize_chat_input(json.loads(stripped))
            except json.JSONDecodeError:
                return stripped
        return stripped

    if isinstance(chat_input, list):
        return _format_chat_messages(chat_input[:MAX_CHAT_MESSAGES])

    if isinstance(chat_input, dict):
        for key in ("messages", "chat", "records", "items"):
            value = chat_input.get(key)
            if isinstance(value, list):
                return _format_chat_messages(value[:MAX_CHAT_MESSAGES])

        direct_text = _first_text_value(chat_input, ("content", "text", "message", "body", "msg"))
        if direct_text:
            sender = _first_text_value(chat_input, ("sender", "from", "speaker", "role", "name", "nickname"))
            return f"{sender}: {direct_text}" if sender else direct_text

        parts = []
        for key, value in chat_input.items():
            if isinstance(value, (dict, list)):
                nested = normalize_chat_input(value)
                if nested:
                    parts.append(nested)
            elif value not in (None, ""):
                parts.append(f"{key}: {value}")
        return "\n".join(parts)

    return str(chat_input)


def make_error_result(reason: str) -> dict[str, Any]:
    result = copy.deepcopy(DEFAULT_RESULT)
    result["reason"] = _bounded_text(reason, MAX_REASON_CHARS)
    return result


def normalize_guard_result(raw_output: Any, evidence_context: str | None = None) -> dict[str, Any]:
    if isinstance(raw_output, dict):
        result = copy.deepcopy(raw_output)
        raw_text = _bounded_text(json.dumps(raw_output, ensure_ascii=False), MAX_MODEL_OUTPUT_CHARS)
    else:
        raw_text = _bounded_text(raw_output, MAX_MODEL_OUTPUT_CHARS)
        try:
            objects = _extract_json_objects(raw_text)
            if not objects:
                raise ValueError("no JSON object found")
            result = _select_json_object(objects)
        except Exception as exc:
            result = make_error_result(f"模型输出解析失败：{exc}。本次不做兜底判断。")

    result = _coerce_native_result(result, raw_text, evidence_context)
    normalized = copy.deepcopy(DEFAULT_RESULT)
    normalized.update(result)
    result = normalized

    result["fraud_result"] = (
        result["fraud_result"] if result.get("fraud_result") in FRAUD_RESULTS else "疑似诈骗"
    )
    result["risk_level"] = result["risk_level"] if result.get("risk_level") in RISK_LEVELS else "中"

    try:
        result["confidence"] = float(result.get("confidence", 0.0))
    except Exception:
        result["confidence"] = 0.0
    if not math.isfinite(result["confidence"]):
        result["confidence"] = 0.0
    result["confidence"] = max(0.0, min(1.0, result["confidence"]))

    result["high_risk_behaviors"] = [
        behavior for behavior in _as_list(result.get("high_risk_behaviors")) if behavior in BEHAVIORS
    ]
    result["evidence"] = _clean_evidence_items(_as_list(result.get("evidence")))
    result["reason"] = _clean_reason(result.get("reason", ""))
    result["has_fraud_evidence"] = _coerce_bool(result.get("has_fraud_evidence"))
    if result["has_fraud_evidence"] is None:
        result["has_fraud_evidence"] = bool(
            result["high_risk_behaviors"] or result["evidence"] or result["fraud_result"] == "诈骗"
        )
    result["suggestion"] = (
        result["suggestion"] if result.get("suggestion") in SUGGESTIONS else "记录但不通知家属"
    )

    _align_result_level(result)

    return {key: result.get(key, DEFAULT_RESULT[key]) for key in DEFAULT_RESULT}


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    text = re.sub(r"```(?:json)?", "", text or "")
    text = text.replace("```", "")
    decoder = json.JSONDecoder()
    objects = []

    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)

    return objects


def _select_json_object(objects: list[dict[str, Any]]) -> dict[str, Any]:
    key_priority = (
        {"fraud_result", "risk_level"},
        {"high_risk_behaviors"},
        {"is_fraud"},
        {"fraud_type"},
        {"scene"},
    )
    for keys in key_priority:
        for obj in objects:
            if keys & obj.keys():
                return obj
    return objects[0]


def _format_chat_messages(messages: list[Any]) -> str:
    lines = []
    for message in messages:
        if isinstance(message, dict):
            content = _first_text_value(message, ("content", "text", "message", "body", "msg"))
            sender = _first_text_value(message, ("sender", "from", "speaker", "role", "name", "nickname"))
            if content:
                lines.append(f"{sender}: {content}" if sender else content)
                continue
        text = normalize_chat_input(message).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _first_text_value(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return str(value).strip()
    return ""


def _detect_chat_behaviors(text: str) -> dict[str, list[str]]:
    evidence_by_behavior = {}
    for behavior, patterns in CHAT_BEHAVIOR_PATTERNS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                if _is_negated_chat_match(text, match.start(), match.end()):
                    continue
                evidence_by_behavior.setdefault(behavior, []).append(
                    _extract_chat_evidence(text, match.start(), match.end())
                )
                break
            if behavior in evidence_by_behavior:
                break
    return evidence_by_behavior


def _detect_chat_suspicious_signals(text: str) -> list[dict[str, str]]:
    signals = []
    for label, pattern in CHAT_SUSPICIOUS_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match or _is_negated_chat_match(text, match.start(), match.end()):
            continue
        signals.append(
            {
                "label": label,
                "evidence": _extract_chat_evidence(text, match.start(), match.end()),
            }
        )
    return signals


def _is_negated_chat_match(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 8) : start]
    if re.search(r"(不要|不能|别|切勿|请勿|无需|不需要|不可)[^。！？\n\r]{0,3}$", prefix):
        return True

    line = _extract_chat_evidence(text, start, end)
    anti_leak_warning = re.search(
        r"(不要|不能|别|切勿|请勿)[^。！？\n\r]{0,10}(告诉|发给|提供|泄露)[^。！？\n\r]{0,10}(任何人|别人|他人|客服|工作人员)",
        line,
    )
    return bool(anti_leak_warning and not re.search(r"(给我|发我|报给我|告诉我)", line))


def _extract_chat_evidence(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = re.sub(r"\s+", " ", text[line_start:line_end]).strip()
    if len(line) <= MAX_EVIDENCE_CHARS:
        return line

    half_window = max(20, MAX_EVIDENCE_CHARS // 2)
    snippet_start = max(0, start - half_window)
    snippet_end = min(len(text), end + half_window)
    snippet = re.sub(r"\s+", " ", text[snippet_start:snippet_end]).strip()
    return _bounded_text(snippet, MAX_EVIDENCE_CHARS)


def _coerce_native_result(
    result: dict[str, Any],
    raw_output: str,
    evidence_context: str | None = None,
) -> dict[str, Any]:
    result = dict(result)
    is_fraud = _coerce_bool(result.get("is_fraud"))

    if "fraud_result" not in result and is_fraud is not None:
        result["fraud_result"] = "诈骗" if is_fraud else "非诈骗"
    if "risk_level" not in result and is_fraud is not None:
        result["risk_level"] = "高" if is_fraud else "低"
    if "suggestion" not in result and is_fraud is not None:
        result["suggestion"] = "触发强提醒" if is_fraud else "不触发提醒"

    try:
        confidence = float(result.get("confidence", 0.0))
        if confidence > 1 and confidence <= 100:
            result["confidence"] = confidence / 100
    except Exception:
        pass

    result["evidence"] = _clean_evidence_items(_as_list(result.get("evidence")))
    result["high_risk_behaviors"] = [
        behavior for behavior in _as_list(result.get("high_risk_behaviors")) if behavior in BEHAVIORS
    ]
    if "has_fraud_evidence" not in result:
        result["has_fraud_evidence"] = bool(
            result["evidence"] or result["high_risk_behaviors"] or is_fraud is True
        )

    return result


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _align_result_level(result: dict[str, Any]) -> None:
    if result["fraud_result"] == "诈骗" and result["confidence"] < 0.6:
        result["fraud_result"] = "疑似诈骗"
        result["reason"] = _append_reason_note(
            result["reason"],
            "模型置信度低于高风险阈值，已按疑似诈骗处理。",
        )

    if result["fraud_result"] == "非诈骗":
        result["has_fraud_evidence"] = False
        result["risk_level"] = "低"
        result["high_risk_behaviors"] = []
        result["suggestion"] = "不触发提醒"
    elif result["fraud_result"] == "疑似诈骗":
        result["has_fraud_evidence"] = bool(
            result["has_fraud_evidence"] or result["high_risk_behaviors"] or result["evidence"]
        )
        result["risk_level"] = "中"
        result["suggestion"] = "记录但不通知家属"
    elif result["fraud_result"] == "诈骗":
        result["has_fraud_evidence"] = True
        result["risk_level"] = "高"
        result["suggestion"] = "触发强提醒"
    elif result["fraud_result"] == "无法判断":
        result["has_fraud_evidence"] = False
        result["risk_level"] = "未知"
        result["high_risk_behaviors"] = []
        result["suggestion"] = "记录但不通知家属"


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "fraud", "诈骗", "是"}:
            return True
        if normalized in {"false", "no", "0", "normal", "非诈骗", "否"}:
            return False
    return None


def _clean_evidence_items(items: list[str]) -> list[str]:
    cleaned = []
    for item in items:
        text = _bounded_text(_remove_prompt_artifacts(str(item)), MAX_EVIDENCE_CHARS).strip(" ,，:：。")
        if text:
            cleaned.append(text)
    return _ordered_unique(cleaned)[:MAX_EVIDENCE_ITEMS]


def _remove_prompt_artifacts(text: str) -> str:
    lines = re.split(r"[\n\r。！？；;]+", text or "")
    kept = []
    artifact_patterns = (
        r"例如",
        r"比如",
        r"如果判断涉诈",
        r"如果存在",
        r"请在\s*reason",
        r"本任务",
        r"任务说明",
        r"类别名",
        r"关键证据",
        r"高危动作",
        r"这些都",
        r"这些属于",
        r"可能涉及诈骗",
        r"潜在受害方",
        r"音频特征中",
        r"逻辑矛盾",
    )
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(re.search(pattern, stripped, flags=re.IGNORECASE) for pattern in artifact_patterns):
            continue
        kept.append(stripped)
    return "。".join(kept)


def _ordered_unique(values: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _clean_reason(value: Any) -> str:
    text = _remove_prompt_artifacts(str(value or "")).strip()
    if not text:
        return DEFAULT_RESULT["reason"]
    return _bounded_text(text, MAX_REASON_CHARS)


def _bounded_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _append_reason_note(reason: str, note: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        return note
    if note in reason:
        return reason
    return _bounded_text(f"{reason} {note}", MAX_REASON_CHARS)
