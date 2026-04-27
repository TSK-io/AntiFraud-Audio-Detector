import copy
import json
import unittest

from audio_guard import (
    DEFAULT_RESULT,
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


if __name__ == "__main__":
    unittest.main()
