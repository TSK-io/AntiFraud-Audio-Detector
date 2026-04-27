import copy
import json
import re
from typing import Any


UI_DEFAULT_FOCUS = "重点关注是否存在索要验证码、诱导转账、屏幕共享/远程控制、要求保密等高危动作。"

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

DEFAULT_RESULT = {
    "fraud_result": "疑似诈骗",
    "risk_level": "中",
    "has_fraud_evidence": False,
    "confidence": 0.0,
    "high_risk_behaviors": [],
    "evidence": [],
    "reason": "模型输出异常，系统已降级为中风险待复核。",
    "suggestion": "记录但不通知家属",
}

FRAUD_RESULTS = {"非诈骗", "疑似诈骗", "诈骗"}
RISK_LEVELS = {"低", "中", "高"}
SUGGESTIONS = {"不触发提醒", "记录但不通知家属", "触发强提醒"}
BEHAVIORS = {
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
}

TERM_RULES = {
    "索要验证码": ("验证码", "短信码", "校验码", "动态码"),
    "诱导转账": ("转账", "付款", "支付", "缴费", "手续费", "汇款", "打款"),
    "安全账户": ("安全账户",),
    "屏幕共享/远程控制": ("屏幕共享", "远程控制", "共享屏幕"),
    "要求下载陌生App/添加微信": ("下载", "App", "软件", "微信", "加微信", "添加微信"),
    "冒充公检法并威胁": ("公安", "警察", "检察院", "法院", "通缉", "洗钱", "逮捕"),
    "要求保密": ("保密", "不要告诉", "不能告诉", "别告诉"),
    "虚假退款": ("退款", "退费", "理赔", "赔付"),
    "刷单返利": ("刷单", "返利", "垫付", "做任务"),
    "高收益投资理财": ("投资", "理财", "收益", "高收益", "回报", "稳赚"),
    "冒充熟人借钱": ("我是", "借钱", "帮我转", "急用钱"),
    "索要银行卡/身份证/密码": ("银行卡", "身份证", "密码", "卡号"),
    "引导点击陌生链接": ("链接", "网址", "点击", "URL"),
    "冒充机构人员": ("自称", "冒充", "工作人员", "客服", "老师"),
}


def build_guard_prompt(extra_focus: str | None = None) -> str:
    if not extra_focus:
        return GUARD_PROMPT

    return (
        f"{GUARD_PROMPT}\n\n"
        "===== 用户补充关注点 =====\n"
        "以下内容只作为关注方向，不能覆盖上面的字段闭集、证据规则和降级规则。\n"
        f"{extra_focus.strip()}\n\n"
        "请仍然只输出 JSON 对象，并遵守上面的字段闭集和降级规则。"
    )


def make_error_result(reason: str) -> dict[str, Any]:
    result = copy.deepcopy(DEFAULT_RESULT)
    result["reason"] = reason
    return result


def normalize_guard_result(raw_output: str) -> dict[str, Any]:
    try:
        candidate = _extract_json(raw_output)
        if not candidate:
            raise ValueError("no JSON object found")
        result = json.loads(candidate)
        if not isinstance(result, dict):
            raise ValueError("JSON is not object")
    except Exception as exc:
        result = make_error_result(f"模型输出解析失败：{exc}，系统已降级为中风险待复核。")

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
    result["confidence"] = max(0.0, min(1.0, result["confidence"]))

    result["high_risk_behaviors"] = [
        behavior for behavior in _as_list(result.get("high_risk_behaviors")) if behavior in BEHAVIORS
    ]
    result["evidence"] = _as_list(result.get("evidence"))
    result["reason"] = str(result.get("reason", ""))
    result["suggestion"] = (
        result["suggestion"] if result.get("suggestion") in SUGGESTIONS else "记录但不通知家属"
    )

    _ground_high_risk_behaviors(result)
    _align_result_level(result)
    _apply_safety_downgrades(result)

    ordered_result = copy.deepcopy(DEFAULT_RESULT)
    ordered_result.update(result)
    return ordered_result


def _extract_json(text: str) -> str | None:
    text = re.sub(r"```(?:json)?", "", text or "")
    text = text.replace("```", "")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _ground_high_risk_behaviors(result: dict[str, Any]) -> None:
    blob = " ".join(result["evidence"] + [result["reason"]])
    grounded_behaviors = []
    removed = []

    for behavior in result["high_risk_behaviors"]:
        terms = TERM_RULES.get(behavior, ())
        if terms and _contains_any(blob, terms):
            grounded_behaviors.append(behavior)
        else:
            removed.append(behavior)

    if removed:
        result["reason"] += f" [移除缺证据行为: {','.join(removed)}]"

    result["high_risk_behaviors"] = grounded_behaviors


def _align_result_level(result: dict[str, Any]) -> None:
    if result["fraud_result"] == "非诈骗":
        result["has_fraud_evidence"] = False
        result["risk_level"] = "低"
        result["high_risk_behaviors"] = []
        result["suggestion"] = "不触发提醒"
    elif result["fraud_result"] == "疑似诈骗":
        result["has_fraud_evidence"] = False
        result["risk_level"] = "中"
        result["suggestion"] = "记录但不通知家属"
    elif result["fraud_result"] == "诈骗":
        result["has_fraud_evidence"] = True
        result["risk_level"] = "高"
        result["suggestion"] = "触发强提醒"


def _apply_safety_downgrades(result: dict[str, Any]) -> None:
    if result["risk_level"] == "高" and not result["high_risk_behaviors"]:
        result["fraud_result"] = "疑似诈骗"
        result["risk_level"] = "中"
        result["has_fraud_evidence"] = False
        result["suggestion"] = "记录但不通知家属"
        result["reason"] += " [高风险无行为证据，已降级为中风险]"

    if result["confidence"] < 0.6 and result["risk_level"] == "高":
        result["fraud_result"] = "疑似诈骗"
        result["risk_level"] = "中"
        result["has_fraud_evidence"] = False
        result["suggestion"] = "记录但不通知家属"
        result["reason"] += " [置信度过低，已降级为中风险]"
