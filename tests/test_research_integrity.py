"""Regression cases for incorrect financial validation and audit release."""
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import financial_rigor as F
import report_audit as R


class FinancialIntegrity(unittest.TestCase):
    def test_decimal_expression_preserves_literal_precision(self):
        for expression, expected in [
            ("0.1 + 0.2", "0.3"),
            ("9007199254740993 + 0.1 - 9007199254740993", "0.1"),
            ("-(2.5e2 / 5) + 1", "-49"),
        ]:
            with self.subTest(expression=expression):
                self.assertEqual(F.exact_calc(expression), Decimal(expected))

    def test_calculator_rejects_unsupported_or_invalid_operations(self):
        for expression in ["2 ** 1000000", "3 // 2", "1 / 0", "True", "abs(-1)"]:
            with self.subTest(expression=expression):
                self.assertIsNone(F.exact_calc(expression))

    def test_invalid_market_cap_inputs_cannot_pass(self):
        for price, shares, reported in [(100, 1000000, 0), (0, 1, 1), (-1, -1, 1),
                                        (1, 0, 1), (1, 1, "NaN"), (1, 1, "Infinity")]:
            with self.subTest(values=(price, shares, reported)):
                self.assertFalse(F.verify_market_cap(price, shares, reported))

    def test_market_cap_over_one_percent_requires_reconciliation(self):
        self.assertFalse(F.verify_market_cap(103, 1, 100))
        self.assertTrue(F.verify_market_cap(101, 1, 100))

    def test_inconsistent_cross_sources_cannot_pass(self):
        for values in [{"A": -100, "B": -200}, {"A": -100, "B": 100},
                       {"A": 0, "B": 1}, {"A": 100, "B": 102},
                       {"A": 100, "B": "NaN"}, {"A": 100} , {}]:
            with self.subTest(values=values):
                self.assertFalse(F.cross_validate("profit", values)["all_consistent"])

    def test_zero_and_matching_negative_sources_can_pass(self):
        for values in [{"A": 0, "B": 0}, {"A": -100, "B": -100}, {"A": 100, "B": 101}]:
            with self.subTest(values=values):
                self.assertTrue(F.cross_validate("profit", values)["all_consistent"])

    def test_financial_cli_failure_is_nonzero_without_traceback(self):
        for args in [
            ["verify-market-cap", "--price", "100", "--shares", "1000000", "--reported", "0"],
            ["cross-validate", "--field", "profit", "--values", '{"A":-100,"B":-200}'],
            ["cross-validate", "--field", "profit", "--values", '{}'],
            ["calc", "--expr", "1 / 0"],
        ]:
            with self.subTest(args=args):
                proc = subprocess.run([sys.executable, str(TOOLS / "financial_rigor.py"), *args],
                                      capture_output=True, text=True, encoding="utf-8")
                self.assertNotEqual(proc.returncode, 0)
                self.assertNotIn("Traceback", proc.stderr)

    def test_valuation_cli_rejects_nonpositive_price(self):
        for price in ("-100", "0"):
            proc = subprocess.run([sys.executable, str(TOOLS / "financial_rigor.py"),
                                   "verify-valuation", "--price", price, "--dividend", "5"],
                                  capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn("Traceback", proc.stderr)

    def test_scenario_rejects_invalid_domain(self):
        for option, value in [("--price", "-1"), ("--years", "-1"), ("--shares", "0")]:
            args = ["three-scenario", "--price", "10", "--eps", "1", "--shares", "1",
                    "--growth", "0.1", "0", "-0.1", "--pe", "20", "10", "5", "--years", "3"]
            args[args.index(option) + 1] = value
            proc = subprocess.run([sys.executable, str(TOOLS / "financial_rigor.py"), *args],
                                  capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn("Traceback", proc.stderr)


def audit_item(**changes):
    item = dict(id=1, label="营业收入", reported_value=100, unit="亿元",
                fetched_value=100, fetched_source="原始年报",
                fetched_value2=100, fetched_source2="富途")
    item.update(changes)
    return item


class AuditIntegrity(unittest.TestCase):
    def test_empty_audit_is_incomplete(self):
        self.assertEqual(R.render_verdict([])["verdict"], "INCOMPLETE")

    def test_missing_or_duplicate_sources_are_incomplete(self):
        for changes in [dict(fetched_value=None), dict(fetched_value2=None),
                        dict(fetched_source=""), dict(fetched_source2=" 原始年报 "),
                        dict(reported_value=None), dict(fetched_value="NaN"),
                        dict(fetched_value=True), dict(fetched_value="Infinity")]:
            with self.subTest(changes=changes):
                self.assertEqual(R.render_verdict([audit_item(**changes)])["verdict"], "INCOMPLETE")

    def test_any_observed_disagreement_blocks_release(self):
        for changes in [dict(fetched_value=200, fetched_value2=None),
                        dict(fetched_value=200), dict(fetched_value2=102)]:
            with self.subTest(changes=changes):
                result = R.render_verdict([audit_item(**changes)])
                self.assertEqual(result["verdict"], "FAIL")
                self.assertEqual(result["fail_count"], 1)

    def test_all_items_must_be_completed(self):
        result = R.render_verdict([audit_item(), audit_item(id=2, fetched_value=None)])
        self.assertEqual(result["verdict"], "INCOMPLETE")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["pass_count"], 1)

    def test_signed_and_zero_matches_pass(self):
        for value in [0, -100, "100.00000000000000001"]:
            with self.subTest(value=value):
                result = R.render_verdict([audit_item(reported_value=value, fetched_value=value,
                                                     fetched_value2=value)])
                self.assertEqual(result["verdict"], "PASS")

    def test_zero_report_with_nonzero_source_fails(self):
        self.assertEqual(R.render_verdict([audit_item(reported_value=0)])["verdict"], "FAIL")

    def test_zero_data_points_are_included_in_extraction(self):
        points = R.extract_data_points("| 项目 | 数值 |\n|---|---|\n| 净利润 | 0 |\n")
        self.assertIn(0, [p["reported_value"] for p in points])

    def test_cli_json_output_is_parseable_and_failure_is_nonzero(self):
        for items, expected in [([], "INCOMPLETE"), ([audit_item()], "PASS"),
                                ([audit_item(fetched_value=200)], "FAIL")]:
            with self.subTest(expected=expected):
                proc = subprocess.run([sys.executable, str(TOOLS / "report_audit.py"), "verdict",
                                       "--results", json.dumps(items), "--output-json"],
                                      capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(proc.returncode == 0, expected == "PASS")
                self.assertEqual(json.loads(proc.stdout)["verdict"], expected)

    def test_audit_module_invocation_returns_structured_result(self):
        proc = subprocess.run([sys.executable, "-m", "tools.report_audit", "verdict",
                               "--results", json.dumps([audit_item()]), "--output-json"],
                              cwd=TOOLS.parent, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
