import copy
import json
import re
from typing import Any


UI_DEFAULT_TRANSCRIPT = ""

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
- 只输出一个 JSON 对象，不要输出 Markdown、解释文字或代码块。
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

INFERENCE_PATTERNS = {
    "索要验证码": (
        r"(验证码|短信码|校验码|动态码).{0,16}(告诉|提供|发给|报给|给我|继续下一步)",
        r"(告诉|提供|发给|报给).{0,16}(验证码|短信码|校验码|动态码)",
    ),
    "诱导转账": (
        r"(转账|汇款|打款|打钱).{0,20}(指定账户|安全账户|个人账户|解冻|保证金|手续费)",
        r"(手续费|解冻金|保证金|刷流水|验资|保证账户安全)",
    ),
    "安全账户": (r"安全账户",),
    "屏幕共享/远程控制": (r"(屏幕共享|共享屏幕|远程控制|远程协助)",),
    "要求下载陌生App/添加微信": (
        r"(下载|安装).{0,12}(APP|App|app|软件|客户端)",
        r"(加|添加).{0,8}(微信|好友)",
        r"微信.{0,8}(手机号|号码|添加|加你)",
    ),
    "冒充公检法并威胁": (
        r"(公安|警号|户政科|检察官|检察院|法院|专案组|通缉令|通缉|洗钱|冻结.{0,10}(资产|账户)|刑事责任|国家机密)",
    ),
    "要求保密": (r"(保密|不能告诉|不要告诉|别告诉|泄露国家机密|周边环境.{0,10}安全)",),
    "虚假退款": (r"(退款|退费|理赔|赔付).{0,20}(链接|App|APP|验证码|银行卡|手续费|转账)",),
    "刷单返利": (r"(刷单|返利|垫付|做任务|佣金).{0,20}(转账|付款|返还|提现)",),
    "高收益投资理财": (r"(投资|理财).{0,20}(高收益|稳赚|回报|保本|内部消息)",),
    "冒充熟人借钱": (r"(我是|这里是).{0,12}(朋友|同学|亲戚|领导).{0,20}(借钱|转钱|急用钱)",),
    "索要银行卡/身份证/密码": (
        r"(提供|告诉|报出|索要|填写).{0,16}(银行卡|银行卡号|卡号|身份证|身份证号|密码)",
        r"(银行卡号|身份证号|登录密码|支付密码)",
    ),
    "引导点击陌生链接": (
        r"(点击|打开|发送|发).{0,16}(链接|网址)",
        r"(短信|信息).{0,16}(链接|网址)",
        r"(陌生链接|不明链接)",
    ),
    "冒充机构人员": (
        r"(自称|冒充|假扮).{0,16}(公安|警察|检察官|法院|客服|老师|工作人员|银行)",
        r"我是.{0,16}(公安|警察|检察官|户政科|银行|客服|工作人员)",
    ),
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


def build_detection_prompt(transcript: str | None = None) -> str:
    if not transcript:
        return TELEANTIFRAUD_DETECTION_PROMPT

    return (
        f"{TELEANTIFRAUD_DETECTION_PROMPT}\n\n"
        "可选辅助转写（可能有错字，以音频为准）：\n"
        f"{transcript.strip()}\n\n"
        "请仍然只输出一个 JSON 对象。"
    )


def make_error_result(reason: str) -> dict[str, Any]:
    result = copy.deepcopy(DEFAULT_RESULT)
    result["reason"] = reason
    return result


def normalize_guard_result(raw_output: str, evidence_context: str | None = None) -> dict[str, Any]:
    try:
        objects = _extract_json_objects(raw_output)
        if not objects:
            raise ValueError("no JSON object found")
        result = _select_json_object(objects)
    except Exception as exc:
        result = make_error_result(f"模型输出解析失败：{exc}，系统已降级为中风险待复核。")

    result = _coerce_native_result(result, raw_output, evidence_context)
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

    _ground_high_risk_behaviors(result, raw_output, evidence_context)
    _align_result_level(result)
    _apply_safety_downgrades(result, evidence_context)

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
    if "has_fraud_evidence" not in result and is_fraud is not None:
        result["has_fraud_evidence"] = is_fraud
    if "suggestion" not in result and is_fraud is not None:
        result["suggestion"] = "触发强提醒" if is_fraud else "不触发提醒"

    try:
        confidence = float(result.get("confidence", 0.0))
        if confidence > 1 and confidence <= 100:
            result["confidence"] = confidence / 100
    except Exception:
        pass

    evidence = _clean_evidence_items(_as_list(result.get("evidence")))
    if not evidence:
        evidence = _extract_evidence_candidates(str(result.get("reason", "")))
    if not evidence:
        evidence = _extract_evidence_candidates(evidence_context or "")
    if not evidence:
        evidence = _extract_evidence_candidates(raw_output or "")
    result["evidence"] = evidence

    blob = _evidence_blob(evidence, str(result.get("reason", "")), evidence_context)
    declared_behaviors = [b for b in _as_list(result.get("high_risk_behaviors")) if b in BEHAVIORS]
    inferred_behaviors = _infer_high_risk_behaviors(blob)
    result["high_risk_behaviors"] = _ordered_unique(declared_behaviors + inferred_behaviors)

    return result


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _ground_high_risk_behaviors(
    result: dict[str, Any],
    raw_output: str,
    evidence_context: str | None = None,
) -> None:
    blob = _evidence_blob(result["evidence"], result["reason"], evidence_context)
    inferred = set(_infer_high_risk_behaviors(blob))
    grounded_behaviors = []
    removed = []

    for behavior in result["high_risk_behaviors"]:
        if behavior in inferred:
            grounded_behaviors.append(behavior)
        else:
            removed.append(behavior)

    if removed:
        result["reason"] += f" [移除缺证据行为: {','.join(removed)}]"

    result["high_risk_behaviors"] = _ordered_unique(grounded_behaviors + list(inferred))


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


def _apply_safety_downgrades(result: dict[str, Any], evidence_context: str | None = None) -> None:
    if result["risk_level"] == "高" and not result["high_risk_behaviors"]:
        suspicious = _has_suspicious_signal(_evidence_blob(result["evidence"], result["reason"], evidence_context))
        result["fraud_result"] = "疑似诈骗" if suspicious else "非诈骗"
        result["risk_level"] = "中" if suspicious else "低"
        result["has_fraud_evidence"] = False
        result["suggestion"] = "记录但不通知家属" if suspicious else "不触发提醒"
        cleaned_reason = _remove_prompt_artifacts(result["reason"]).strip()
        if not cleaned_reason:
            result["reason"] = "未提取到音频中的明确诈骗高危行为证据。"
        result["reason"] += " [未提取到明确高危行为证据，已按证据强度降级]"

    if result["confidence"] < 0.6 and result["risk_level"] == "高":
        result["fraud_result"] = "疑似诈骗"
        result["risk_level"] = "中"
        result["has_fraud_evidence"] = False
        result["suggestion"] = "记录但不通知家属"
        result["reason"] += " [置信度过低，已降级为中风险]"


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "fraud", "诈骗", "是"}:
            return True
        if normalized in {"false", "no", "0", "normal", "非诈骗", "否"}:
            return False
    return None


def _infer_high_risk_behaviors(text: str) -> list[str]:
    text = _remove_prompt_artifacts(text)
    matched = []
    for behavior in BEHAVIOR_ORDER:
        patterns = INFERENCE_PATTERNS.get(behavior, ())
        if any(_has_non_negated_match(text, pattern) for pattern in patterns):
            matched.append(behavior)
    return matched


def _extract_evidence_candidates(text: str) -> list[str]:
    cleaned = _remove_prompt_artifacts(text or "")
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"[{}\[\]\"']", " ", cleaned)
    parts = re.split(r"[。！？；;\n\r]+", cleaned)
    candidates = []

    for part in parts:
        sentence = re.sub(r"\s+", " ", part).strip(" ,，:：")
        if len(sentence) < 4:
            continue
        if _infer_high_risk_behaviors(sentence):
            candidates.append(sentence[:120])
        if len(candidates) >= 5:
            break

    return _ordered_unique(candidates)


def _clean_evidence_items(items: list[str]) -> list[str]:
    cleaned = []
    for item in items:
        text = _remove_prompt_artifacts(str(item)).strip(" ,，:：。")
        if text:
            cleaned.append(text)
    return _ordered_unique(cleaned)


def _evidence_blob(evidence: list[str], reason: str, evidence_context: str | None = None) -> str:
    parts = evidence + [reason]
    if _looks_like_transcript(evidence_context):
        parts.append(evidence_context or "")
    return _remove_prompt_artifacts(" ".join(parts))


def _looks_like_transcript(text: str | None) -> bool:
    if not text or len(text.strip()) < 20:
        return False
    return bool(re.search(r"[\n\r]|[：:]", text))


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


def _has_suspicious_signal(text: str) -> bool:
    suspicious_patterns = (
        r"(公安|警号|检察|法院|通缉|洗钱|冻结|刑事责任|国家机密)",
        r"(验证码|短信码|校验码).{0,16}(告诉|提供|发给|报给)",
        r"(安全账户|屏幕共享|远程控制|陌生链接|不明链接)",
        r"(下载|安装).{0,12}(App|APP|app|软件)",
        r"(银行卡号|身份证号|登录密码|支付密码)",
        r"(刷单|返利|高收益|稳赚|解冻金|保证金|手续费)",
    )
    return any(_has_non_negated_match(text, pattern) for pattern in suspicious_patterns)


def _has_non_negated_match(text: str, pattern: str) -> bool:
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        if not _is_negated_match(text, match.start()):
            return True
    return False


def _is_negated_match(text: str, start: int) -> bool:
    prefix = text[max(0, start - 18) : start]
    return bool(re.search(r"(没有|无|未|未见|未听到|不涉及|不存在|不是|并非|没有要求|没有索要)", prefix))


def _ordered_unique(values: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
