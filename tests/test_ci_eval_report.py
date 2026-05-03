import sys
import os

# Add scripts directory to path to allow importing
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.ci_eval_report import generate_report

def test_generate_report_pass():
    md, passed = generate_report(current_score=0.98, baseline_score=1.0)
    assert passed is True
    assert "Passed" in md
    assert "98.00%" in md

def test_generate_report_fail_regression():
    md, passed = generate_report(current_score=0.90, baseline_score=1.0)
    assert passed is False
    assert "Regression Detected" in md
    assert "90.00%" in md
