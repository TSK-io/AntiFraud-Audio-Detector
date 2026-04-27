#!/bin/bash
set -euo pipefail

WAV_AUDIO="/tmp/antifraud_guard_input_$$.wav"
SCHEMA_FILE="/tmp/antifraud_guard_schema_$$.json"
RAW_OUTPUT="/tmp/antifraud_guard_raw_$$.json"
FINAL_OUTPUT="/tmp/antifraud_guard_final_$$.json"
LLAMA_LOG="/tmp/antifraud_guard_llama_log_$$.txt"

# Keep stdout clean for system JSON integration
exec 3>&1
exec 1>&2

MODEL="/Users/liyonghao/.lmstudio/models/mradermacher/AntiFraud-SFT-GGUF/AntiFraud-SFT.Q4_K_M.gguf"
MMPROJ="/Users/liyonghao/Downloads/AntiFraud-SFT.mmproj-Q8_0.gguf"
LLAMA_MTMD="$HOME/llama.cpp/build/bin/llama-mtmd-cli"
NGL="${NGL:-auto}"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  BOLD="$(tput bold || true)"
  DIM="$(tput dim || true)"
  RED="$(tput setaf 1 || true)"
  GREEN="$(tput setaf 2 || true)"
  YELLOW="$(tput setaf 3 || true)"
  BLUE="$(tput setaf 4 || true)"
  RESET="$(tput sgr0 || true)"
else
  BOLD=""
  DIM=""
  RED=""
  GREEN=""
  YELLOW=""
  BLUE=""
  RESET=""
fi

print_header() {
  echo
  echo "${BOLD}${BLUE}========================================${RESET}"
  echo "${BOLD}${BLUE}桑榆智盾：AI反电诈守护系统 (improved)${RESET}"
  echo "${BOLD}${BLUE}========================================${RESET}"
}

info() {
  echo "${BLUE}▶${RESET} $1"
}

spinner() {
  local pid=$1
  local log_file=$2
  local delay=0.1
  local spinstr='|/-\'
  
  printf "\033[?25l"
  while [ "$(ps -p $pid -o state=)" ]; do
    local temp=${spinstr#?}
    local last_line=""
    if [ -f "$log_file" ]; then
      last_line=$(tail -n 1 "$log_file" | tr -d '\r\n' | cut -c1-60)
    fi
    
    printf "\r${BLUE} [%c] ${RESET} ${DIM}%-60s${RESET}" "$spinstr" "$last_line"
    local spinstr=$temp${spinstr%"$temp"}
    sleep $delay
  done
  printf "\r\033[K\033[?25h"
}

success() {
  echo "${GREEN}✓${RESET} $1"
}

warn() {
  echo "${YELLOW}!${RESET} $1"
}

fail() {
  echo "${RED}✗ 错误：${RESET}$1" >&2
  exit 1
}

usage() {
  print_header
  echo "${BOLD}用法${RESET}"
  echo "  ./read_audio_guard_improved.sh <音频文件路径>"
  echo
  echo "${BOLD}示例${RESET}"
  echo "  ./read_audio_guard_improved.sh '/Users/liyonghao/Desktop/call/xxx.m4a'"
  echo
  echo "${DIM}证据优先 + 多维度检查 + JSON Schema 约束${RESET}"
}

write_schema() {
  cat > "$SCHEMA_FILE" <<'JSON'
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "fraud_result",
    "risk_level",
    "has_fraud_evidence",
    "confidence",
    "high_risk_behaviors",
    "evidence",
    "reason",
    "suggestion"
  ],
  "properties": {
    "fraud_result": {
      "type": "string",
      "enum": ["非诈骗", "疑似诈骗", "诈骗"]
    },
    "risk_level": {
      "type": "string",
      "enum": ["低", "中", "高"]
    },
    "has_fraud_evidence": {
      "type": "boolean"
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "high_risk_behaviors": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": [
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
          "冒充机构人员"
        ]
      }
    },
    "evidence": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "reason": {
      "type": "string"
    },
    "suggestion": {
      "type": "string",
      "enum": [
        "不触发提醒",
        "记录但不通知家属",
        "触发强提醒"
      ]
    }
  }
}
JSON
}

if [ $# -lt 1 ]; then
  usage
  exit 1
fi

INPUT_AUDIO="$1"

if [ ! -f "$INPUT_AUDIO" ]; then
  fail "找不到音频文件：$INPUT_AUDIO"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  fail "找不到 ffmpeg，请先安装 ffmpeg。"
fi

if [ ! -x "$LLAMA_MTMD" ]; then
  fail "找不到可执行文件：$LLAMA_MTMD"
fi

if [ ! -f "$MODEL" ]; then
  fail "找不到模型文件：$MODEL"
fi

if [ ! -f "$MMPROJ" ]; then
  fail "找不到 mmproj 文件：$MMPROJ"
fi

# 证据优先、多维度检查风格的提示词
PROMPT='<audio>
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
- 只输出 JSON 对象，不要输出 Markdown、解释文字或代码块。'

print_header
echo "${BOLD}输入文件${RESET}  $INPUT_AUDIO"
echo "${BOLD}模型版本${RESET}  AntiFraud-SFT.Q4_K_M"
echo "${BOLD}采样温度${RESET}  0.1 (原保守设置)"
echo

info "正在写入 JSON Schema 约束文件..."
write_schema
success "Schema 已写入"

info "正在转换音频为 16kHz 双声道 wav..."
if ffmpeg -y -hide_banner -loglevel error -i "$INPUT_AUDIO" -ar 16000 -ac 2 "$WAV_AUDIO"; then
  success "音频转换完成"
else
  fail "音频转换失败。"
fi

echo
info "桑榆智盾正在分析中，请稍候..."

: > "$LLAMA_LOG"

"$LLAMA_MTMD" \
  -m "$MODEL" \
  --mmproj "$MMPROJ" \
  --audio "$WAV_AUDIO" \
  -p "$PROMPT" \
  --json-schema-file "$SCHEMA_FILE" \
  --temp 0.1 \
  --top-k 1 \
  --top-p 1 \
  -ngl "$NGL" \
  -c 4096 \
  -n 350 \
  > "$RAW_OUTPUT" \
  2> "$LLAMA_LOG" &

SPIN_PID=$!
spinner $SPIN_PID "$LLAMA_LOG"
wait $SPIN_PID

echo
echo "${BOLD}${BLUE}-------------- 原始 JSON 输出 --------------${RESET}"
cat "$RAW_OUTPUT"
echo

python3 - "$RAW_OUTPUT" "$FINAL_OUTPUT" >&3 <<'PY'
import json
import re
import sys
import os
from pathlib import Path

raw_path = Path(sys.argv[1])
final_path = Path(sys.argv[2])
raw = raw_path.read_text(encoding="utf-8", errors="ignore")

DEFAULT = {
    "fraud_result": "疑似诈骗",
    "risk_level": "中",
    "has_fraud_evidence": False,
    "confidence": 0.0,
    "high_risk_behaviors": [],
    "evidence": [],
    "reason": "模型输出异常，系统已降级为中风险待复核。",
    "suggestion": "记录但不通知家属"
}

FRAUD_RESULTS = {"非诈骗", "疑似诈骗", "诈骗"}
RISK_LEVELS = {"低", "中", "高"}
BEHAVIORS = {
    "索要验证码", "诱导转账", "安全账户", "屏幕共享/远程控制",
    "要求下载陌生App/添加微信", "冒充公检法并威胁", "要求保密",
    "虚假退款", "刷单返利", "高收益投资理财", "冒充熟人借钱",
    "索要银行卡/身份证/密码", "引导点击陌生链接", "冒充机构人员"
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
    "冒充机构人员": ("自称", "冒充", "工作人员", "客服", "老师")
}

def extract_json(text: str):
    text = re.sub(r"```(?:json)?", "", text)
    text = text.replace("```", "")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]

def as_list(value):
    if isinstance(value, list):
        return [str(x) for x in value]
    if value in (None, ""):
        return []
    return [str(value)]

def contains_any(text, terms):
    return any(term in text for term in terms)

try:
    candidate = extract_json(raw)
    if not candidate:
        raise ValueError("no JSON object found")
    result = json.loads(candidate)
    if not isinstance(result, dict):
        raise ValueError("JSON is not object")
except Exception as exc:
    result = dict(DEFAULT)
    result["reason"] = f"模型输出解析失败：{exc}，系统已降级为中风险待复核。"

for key, value in DEFAULT.items():
    result.setdefault(key, value)

result["fraud_result"] = result["fraud_result"] if result.get("fraud_result") in FRAUD_RESULTS else "疑似诈骗"
result["risk_level"] = result["risk_level"] if result.get("risk_level") in RISK_LEVELS else "中"

try:
    result["confidence"] = float(result.get("confidence", 0.0))
except Exception:
    result["confidence"] = 0.0
result["confidence"] = max(0.0, min(1.0, result["confidence"]))

result["high_risk_behaviors"] = [b for b in as_list(result.get("high_risk_behaviors")) if b in BEHAVIORS]
result["evidence"] = as_list(result.get("evidence"))
result["reason"] = str(result.get("reason", ""))
result["suggestion"] = str(result.get("suggestion", ""))

blob = " ".join(result["evidence"] + [result["reason"]])

grounded_behaviors = []
removed = []
for behavior in result["high_risk_behaviors"]:
    terms = TERM_RULES.get(behavior, ())
    if terms and contains_any(blob, terms):
        grounded_behaviors.append(behavior)
    else:
        removed.append(behavior)

if removed:
    result["reason"] += f" [移除缺证据行为: {','.join(removed)}]"

result["high_risk_behaviors"] = grounded_behaviors

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

final_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

COLOR_MAP = {"非诈骗": "\033[92m", "疑似诈骗": "\033[93m", "诈骗": "\033[91m"}
RESET = "\033[0m"
BOLD = "\033[1m"

res = result["fraud_result"]
risk = result["risk_level"]
c = COLOR_MAP.get(res, "")

print(f"\n{BOLD}分析结论：{RESET}{c}{res}{RESET} (风险等级: {risk})")
print(f"{BOLD}置信度：  {RESET}{result['confidence']:.2f}")

if result["high_risk_behaviors"]:
    print(f"{BOLD}命中行为：{RESET}{', '.join(result['high_risk_behaviors'])}")

print(f"{BOLD}判定原因：{RESET}{result['reason']}")

if result["evidence"]:
    print(f"{BOLD}关键证据：{RESET}")
    for i, e in enumerate(result["evidence"], 1):
        print(f"  {i}. {e}")

print(f"{BOLD}系统建议：{RESET}{c}{result['suggestion']}{RESET}")
print("-" * 40)

sys.stdout = os.fdopen(3, 'w')
print(json.dumps(result, ensure_ascii=False, indent=2))
PY

echo "${BOLD}${BLUE}-------------- 输出结束 --------------${RESET}"
success "Guard 分析完成"
