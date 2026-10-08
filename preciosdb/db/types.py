"""Custom column types shared by the ORM models."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import DateTime, Dialect, Numeric
from sqlalchemy import Enum as SAEnum
from sqlalchemy.types import TypeDecorator, TypeEngine


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores datetimes as naive UTC and always returns timezone-aware UTC values.

    Keeps behaviour identical across SQLite (no timezone support) and MySQL.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use timezone-aware UTC values.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class FixedDecimal(TypeDecorator[Decimal]):
    """DECIMAL(p, s) that always comes back as a `Decimal` with exactly `s` decimals.

    MySQL stores it as an exact DECIMAL. SQLite has no decimal type, so there it is read as float and
    rounded back to `s` decimals (exact for amounts of this size).
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int) -> None:
        super().__init__(precision=precision, scale=scale)
        self.precision = precision
        self.scale = scale

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        asdecimal = dialect.name != "sqlite"
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=asdecimal))

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-self.scale))


def money() -> FixedDecimal:
    return FixedDecimal(12, 2)


def enum_type(enum_cls: type[Enum]) -> SAEnum:
    """Portable enum column: stored as VARCHAR holding the enum *value*."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=20,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )
