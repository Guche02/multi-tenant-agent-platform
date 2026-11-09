import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # Predictable constraint names, so migrations can refer to them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _id_column() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _org_id_column() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("organizations.id"), nullable=False)


def _created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _id_column()
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at_column()


class User(Base):
    """The org and role of a Supabase Auth user. Supabase holds the password and the sessions."""

    __tablename__ = "users"
    __table_args__ = (
        # Supabase Auth allows one account per email, so an email belongs to one org only.
        UniqueConstraint("email"),
        UniqueConstraint("org_id", "id"),
        CheckConstraint("role IN ('customer', 'admin')", name="valid_role"),
    )

    # The Supabase Auth user id, never generated here.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    org_id: Mapped[uuid.UUID] = _org_id_column()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at_column()


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "user_id"], ["users.org_id", "users.id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)  # First message, shortened.
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "user_id", "conversation_id"],
            ["conversations.org_id", "conversations.user_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("role IN ('user', 'assistant')", name="valid_role"),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # clock_timestamp, not now(): messages saved in one transaction must still sort in order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )


# ---------------------------------------------------------------------------
# Himali Tea House
# ---------------------------------------------------------------------------


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("org_id", "sku"),
        UniqueConstraint("org_id", "id"),
        CheckConstraint("price >= 0", name="price_not_negative"),
        # The database itself refuses to oversell.
        CheckConstraint("stock >= 0", name="stock_not_negative"),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = _org_id_column()
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(["org_id", "user_id"], ["users.org_id", "users.id"]),
        # Deleting a chat keeps the order and clears only this link.
        ForeignKeyConstraint(
            ["org_id", "user_id", "conversation_id"],
            ["conversations.org_id", "conversations.user_id", "conversations.id"],
            ondelete="SET NULL (conversation_id)",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')",
            name="valid_status",
        ),
        CheckConstraint("total >= 0", name="total_not_negative"),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # The chat that led to this order, for the conversion rate metric.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = _created_at_column()


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id"),
        ForeignKeyConstraint(["org_id", "order_id"], ["orders.org_id", "orders.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["org_id", "product_id"], ["products.org_id", "products.id"]),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_not_negative"),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # The price when ordered: later price changes must not alter past orders.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "order_id"], ["orders.org_id", "orders.id"], ondelete="CASCADE"),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)  # Empty for the first entry.
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )


# ---------------------------------------------------------------------------
# Lotus Wellness Studio
# ---------------------------------------------------------------------------


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("org_id", "name"),
        UniqueConstraint("org_id", "id"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        CheckConstraint("price >= 0", name="price_not_negative"),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = _org_id_column()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class Practitioner(Base):
    __tablename__ = "practitioners"
    __table_args__ = (UniqueConstraint("org_id", "id"),)

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = _org_id_column()
    name: Mapped[str] = mapped_column(Text, nullable=False)


class OpeningHours(Base):
    __tablename__ = "opening_hours"
    __table_args__ = (
        UniqueConstraint("org_id", "weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="valid_weekday"),
        CheckConstraint("closes_at > opens_at", name="closes_after_opening"),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = _org_id_column()
    # 0 is Monday. A day with no row is a day the studio is closed.
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Local studio time, not exact moments.
    opens_at: Mapped[time] = mapped_column(Time, nullable=False)
    closes_at: Mapped[time] = mapped_column(Time, nullable=False)


class Holiday(Base):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("org_id", "date"),)

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = _org_id_column()
    date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "user_id"], ["users.org_id", "users.id"]),
        ForeignKeyConstraint(["org_id", "service_id"], ["services.org_id", "services.id"]),
        ForeignKeyConstraint(["org_id", "practitioner_id"], ["practitioners.org_id", "practitioners.id"]),
        # Deleting a chat keeps the booking and clears only this link.
        ForeignKeyConstraint(
            ["org_id", "user_id", "conversation_id"],
            ["conversations.org_id", "conversations.user_id", "conversations.id"],
            ondelete="SET NULL (conversation_id)",
        ),
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="valid_status"),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        CheckConstraint("price >= 0", name="price_not_negative"),
        CheckConstraint("cancellation_fee >= 0", name="fee_not_negative"),
        # The database refuses overlapping bookings, even if two requests arrive at once.
        # Cancelled bookings don't count, and back-to-back bookings are allowed.
        ExcludeConstraint(
            ("practitioner_id", "="),
            (text("tstzrange(starts_at, ends_at)"), "&&"),
            where=text("status = 'confirmed'"),
            using="gist",
            name="no_practitioner_overlap",
        ),
        ExcludeConstraint(
            ("user_id", "="),
            (text("tstzrange(starts_at, ends_at)"), "&&"),
            where=text("status = 'confirmed'"),
            using="gist",
            name="no_customer_overlap",
        ),
    )

    id: Mapped[uuid.UUID] = _id_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    practitioner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # The chat that led to this booking, for the conversion rate metric.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="confirmed")
    # The price when booked: later price changes must not alter past bookings.
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    cancellation_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at_column()
