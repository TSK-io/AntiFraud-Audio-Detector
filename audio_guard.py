import copy
import json
import math
import re
from typing import Any


UI_DEFAULT_TRANSCRIPT = ""
MAX_MODEL_OUTPUT_CHARS = 20000
MAX_TRANSCRIPT_CHARS = 4000
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


def make_error_result(reason: str) -> dict[str, Any]:
    result = copy.deepcopy(DEFAULT_RESULT)
    result["reason"] = _bounded_text(reason, MAX_REASON_CHARS)
    return result


def normalize_guard_result(raw_output: Any, evidence_context: str | None = None) -> dict[str, Any]:
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
