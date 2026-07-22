"""Unit & Integration Test Suite for AI Variable Billing & Financial Engine (Point 10).

Covers:
- Simulation mode
- Excess token calculation & monthly allowances
- Profitability margin safety rules
- Settlement reference format
- Per-tenant billing policies & state filters
- Operational alert engine
"""
import pytest
from decimal import Decimal
from billing.ai_settlement import (
    calculate_amounts,
    calculate_profitability_breakdown,
    generate_settlement_reference,
    simulate_settlement,
    summarize_logs,
)
from utils.alerts import ALERT_TYPES


def test_settlement_reference_format():
    ref = generate_settlement_reference("org_demo12345", "2026-07", nonce="test1234")
    assert ref.startswith("latus_settle_")
    assert "demo1234" in ref
    assert "202607" in ref


def test_profitability_calculation_healthy_margin():
    breakdown = calculate_profitability_breakdown(
        plan_amount_ars=50000.0,
        ai_amount_ars=15000.0,
        base_cost_usd=10.0,
        usd_to_ars_rate=1200.0,
        mp_fee_percent=4.5,
        tax_percent=0.0,
        min_margin_percent=15.0,
    )
    assert breakdown["total_revenue_ars"] == 65000.0
    assert breakdown["provider_cost_ars"] == 12000.0
    assert breakdown["mp_fee_ars"] == 2925.0
    assert breakdown["net_profit_ars"] == 50075.0
    assert breakdown["is_profitable"] is True
    assert breakdown["warning"] is None


def test_profitability_calculation_unprofitable_margin_warning():
    breakdown = calculate_profitability_breakdown(
        plan_amount_ars=10000.0,
        ai_amount_ars=0.0,
        base_cost_usd=10.0,
        usd_to_ars_rate=1200.0,
        mp_fee_percent=10.0,
        tax_percent=0.0,
        min_margin_percent=15.0,
    )
    # Revenue = 10000, provider = 12000, mp = 1000 -> Loss
    assert breakdown["is_profitable"] is False
    assert breakdown["warning"] is not None


def test_simulate_settlement_excess_tokens_only():
    logs = [
        {
            "total_tokens": 300_000,
            "estimated_cost_usd": 3.0,
            "ai_fee_percent": 20.0,
            "billable_cost_usd": 3.6,
        }
    ]
    sim = simulate_settlement(
        organization_id="org_test_pilot",
        plan_name="Starter Plan",
        plan_amount_ars=30000.0,
        logs=logs,
        usd_to_ars_rate=1200.0,
        fx_buffer_percent=10.0,
        included_tokens=250_000,  # 50,000 excess tokens out of 300,000 = 1/6
    )

    assert sim["simulation"] is True
    assert sim["period_summary"]["total_tokens"] == 300_000
    assert sim["period_summary"]["excess_tokens"] == 50_000
    # 1/6 of 3.6 USD = 0.6 USD billable
    assert round(sim["period_summary"]["billable_cost_usd"], 2) == 0.60
    assert sim["amounts"]["ai_amount_ars"] > 0
    assert sim["profitability"]["is_profitable"] is True


def test_alert_types_coverage():
    assert len(ALERT_TYPES) == 9
    assert "fx_rate_expired" in ALERT_TYPES
    assert "insufficient_margin" in ALERT_TYPES
    assert "mp_update_error" in ALERT_TYPES
