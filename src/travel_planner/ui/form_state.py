from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class RangeFieldSpec:
    label: str
    minimum: int
    maximum: int
    step: int
    unit: str | None = None


def build_range_error(spec: RangeFieldSpec) -> str:
    suffix = f" {spec.unit}" if spec.unit else " 天"
    return f"{spec.label}必須介於 {spec.minimum:,} 到 {spec.maximum:,}{suffix}。"


def coerce_range_value(raw: str | int | float, spec: RangeFieldSpec) -> int | Decimal:
    try:
        numeric = Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(build_range_error(spec)) from error

    if numeric < spec.minimum or numeric > spec.maximum:
        raise ValueError(build_range_error(spec))

    if spec.step == 1 and spec.unit is None:
        return int(numeric)
    return numeric
