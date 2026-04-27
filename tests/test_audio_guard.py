import copy
import json
import unittest

from audio_guard import (
    DEFAULT_RESULT,
    analyze_chat_text,
    build_detection_prompt,
    make_error_result,
    normalize_guard_result,
)


class NormalizeGuardResultTest(unittest.TestCase):
    def test_returns_stable_schema_for_malformed_output(self):
        result = normalize_guard_result("模型输出不是 JSON")

        self.assertEqual(list(result), list(DEFAULT_RESULT))
        self.assertEqual(result["fraud_result"], "无法判断")
        self.assertEqual(result["risk_level"], "未知")
        self.assertFalse(result["has_fraud_evidence"])
        self.assertEqual(result["confidence"], 0.0)

    def test_native_boolean_output_is_supported(self):
        result = normalize_guard_result(
            json.dumps(
                {
                    "is_fraud": False,
                    "confidence": 90,
                    "reason": "普通客服沟通，没有异常转账或索要敏感信息。",
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(result["fraud_result"], "非诈骗")
        self.assertEqual(result["risk_level"], "低")
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["suggestion"], "不触发提醒")
        self.assertFalse(result["has_fraud_evidence"])

    def test_low_confidence_fraud_is_downgraded_to_suspicious(self):
        result = normalize_guard_result(
            json.dumps(
                {
                    "fraud_result": "诈骗",
                    "risk_level": "高",
                    "has_fraud_evidence": True,
                    "confidence": 0.5,
                    "high_risk_behaviors": ["诱导转账"],
                    "evidence": ["请马上转到安全账户"],
                    "reason": "语音较模糊。",
                    "suggestion": "触发强提醒",
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(result["fraud_result"], "疑似诈骗")
        self.assertEqual(result["risk_level"], "中")
        self.assertEqual(result["suggestion"], "记录但不通知家属")
        self.assertIn("置信度低于高风险阈值", result["reason"])

    def test_high_risk_result_keeps_closed_list_behaviors_only(self):
        result = normalize_guard_result(
            json.dumps(
                {
                    "fraud_result": "诈骗",
                    "risk_level": "高",
                    "confidence": 0.95,
                    "high_risk_behaviors": ["诱导转账", "未知行为"],
                    "evidence": ["现在转到指定账户", "现在转到指定账户"],
                    "reason": "听到明确转账要求。",
                    "suggestion": "触发强提醒",
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(result["fraud_result"], "诈骗")
        self.assertEqual(result["risk_level"], "高")
        self.assertTrue(result["has_fraud_evidence"])
        self.assertEqual(result["high_risk_behaviors"], ["诱导转账"])
        self.assertEqual(result["evidence"], ["现在转到指定账户"])

    def test_prompt_requests_full_guard_schema(self):
        prompt = build_detection_prompt("辅助转写")

        self.assertIn("fraud_result", prompt)
        self.assertIn("high_risk_behaviors", prompt)
        self.assertIn("辅助转写", prompt)
        self.assertIn("只输出一个符合字段闭集的 JSON 对象", prompt)

    def test_non_finite_confidence_is_clamped(self):
        result = normalize_guard_result(
            '{"is_fraud": 1, "confidence": NaN, "reason": "疑似要求转账。"}'
        )

        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["fraud_result"], "疑似诈骗")
        self.assertEqual(result["risk_level"], "中")

    def test_transcript_is_bounded_in_prompt(self):
        prompt = build_detection_prompt("转写" * 3000)

        self.assertLess(len(prompt), 9000)
        self.assertIn("...", prompt)

    def test_error_result_isolated_from_default(self):
        first = make_error_result("错误一")
        second = make_error_result("错误二")
        first["evidence"].append("不应污染默认值")

        self.assertEqual(second["evidence"], [])
        self.assertEqual(DEFAULT_RESULT["evidence"], [])
        self.assertNotEqual(copy.deepcopy(first), second)

    def test_normalize_guard_result_accepts_dict_input(self):
        result = normalize_guard_result(
            {
                "fraud_result": "诈骗",
                "risk_level": "高",
                "confidence": 0.9,
                "high_risk_behaviors": ["索要验证码"],
                "evidence": ["请把验证码发给我"],
                "reason": "聊天中索要验证码。",
                "suggestion": "触发强提醒",
            }
        )

        self.assertEqual(result["fraud_result"], "诈骗")
        self.assertEqual(result["risk_level"], "高")
        self.assertTrue(result["has_fraud_evidence"])

    def test_chat_text_detects_high_risk_verification_code(self):
        result = analyze_chat_text("客服: 退款需要验证，请把短信验证码发给我，随后转到安全账户解冻。")

        self.assertEqual(result["fraud_result"], "诈骗")
        self.assertEqual(result["risk_level"], "高")
        self.assertIn("索要验证码", result["high_risk_behaviors"])
        self.assertIn("安全账户", result["high_risk_behaviors"])
        self.assertTrue(result["evidence"])

    def test_chat_text_normal_conversation_is_low_risk(self):
        result = analyze_chat_text("妈妈: 晚上回家吃饭吗？\n我: 回，路上买点水果。")

        self.assertEqual(result["fraud_result"], "非诈骗")
        self.assertEqual(result["risk_level"], "低")
        self.assertFalse(result["has_fraud_evidence"])
        self.assertEqual(result["high_risk_behaviors"], [])

    def test_chat_text_does_not_flag_anti_leak_warning(self):
        result = analyze_chat_text("银行: 验证码不要告诉任何人，工作人员也不会索要验证码。")

        self.assertEqual(result["fraud_result"], "非诈骗")
        self.assertNotIn("索要验证码", result["high_risk_behaviors"])

    def test_chat_text_accepts_messages_json(self):
        result = analyze_chat_text(
            json.dumps(
                {
                    "messages": [
                        {"sender": "客服", "content": "账户异常，请打开 http://fake.example 填写银行卡号和支付密码。"}
                    ]
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(result["fraud_result"], "诈骗")
        self.assertIn("引导点击陌生链接", result["high_risk_behaviors"])
        self.assertIn("索要银行卡/身份证/密码", result["high_risk_behaviors"])

    def test_empty_chat_text_returns_unknown_result(self):
        result = analyze_chat_text("")

        self.assertEqual(result["fraud_result"], "无法判断")
        self.assertEqual(result["risk_level"], "未知")
        self.assertIn("聊天内容为空", result["reason"])


if __name__ == "__main__":
    unittest.main()
