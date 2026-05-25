from decimal import Decimal

import pytest

from travel_planner.ui.form_state import (
    RangeFieldSpec,
    build_range_error,
    coerce_range_value,
)


def test_coerce_range_value_accepts_in_bounds_integer():
    spec = RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)

    assert coerce_range_value("5", spec) == 5


def test_coerce_range_value_rejects_out_of_bounds_integer():
    spec = RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)

    with pytest.raises(ValueError, match="旅遊天數必須介於 1 到 10 天。"):
        coerce_range_value("11", spec)


def test_coerce_range_value_accepts_decimal_budget():
    spec = RangeFieldSpec(label="總預算", minimum=1000, maximum=300000, step=1000, unit="NTD")

    assert coerce_range_value("25000", spec) == Decimal("25000")


def test_build_range_error_uses_currency_unit():
    spec = RangeFieldSpec(label="住宿預算", minimum=0, maximum=150000, step=1000, unit="NTD")

    assert build_range_error(spec) == "住宿預算必須介於 0 到 150,000 NTD。"


def test_budget_specs_match_product_bounds():
    from travel_planner.ui.app import DAYS_SPEC, LODGING_BUDGET_SPEC, TOTAL_BUDGET_SPEC

    assert DAYS_SPEC == RangeFieldSpec(label="旅遊天數", minimum=1, maximum=10, step=1)
    assert TOTAL_BUDGET_SPEC.minimum == 1000
    assert TOTAL_BUDGET_SPEC.maximum == 300000
    assert LODGING_BUDGET_SPEC.minimum == 0
    assert LODGING_BUDGET_SPEC.maximum == 150000
