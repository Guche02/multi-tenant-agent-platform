"""Order rules for an online store.

The agent's tools call these, and so will the admin endpoint and the "My orders" panel, so every
way of touching an order applies the same rules. Every function is scoped to an org, and those
that touch an order to the user who placed it.

Stock is reserved when an order is placed (still pending) and released if it is cancelled.
"""

import uuid
from typing import Any
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Text, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, OrderStatusHistory, Product

STATUS_FLOW = ["pending", "confirmed", "shipped", "delivered"]
EDITABLE_STATUS = "pending"


class OrderError(Exception):
    """A request the rules refuse. The message is written to be shown to the customer."""


@dataclass(frozen=True)
class ItemRequest:
    sku: str
    quantity: int


@dataclass
class QuoteLine:
    sku: str
    name: str
    quantity: int
    unit_price: str
    line_total: str
    in_stock: int


@dataclass
class Quote:
    """What an order would look like right now, at current prices and stock."""

    lines: list[QuoteLine] = field(default_factory=list)
    total: str = "0.00"
    problems: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def order_ref(order_id: uuid.UUID) -> str:
    """The short order number customers see, e.g. "3F2A9C1B"."""
    return order_id.hex[:8].upper()


def format_time(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).strftime("%a %d %b %Y, %H:%M")


def normalise_items(items: list[ItemRequest]) -> list[ItemRequest]:
    """Merges repeated SKUs and sorts by SKU, so the same order always compares equal."""
    totals: dict[str, int] = {}
    for item in items:
        if item.quantity < 1:
            raise OrderError(f"Quantities must be at least 1 (got {item.quantity} for {item.sku}).")
        sku = item.sku.strip().upper()
        totals[sku] = totals.get(sku, 0) + item.quantity
    if not totals:
        raise OrderError("An order needs at least one item.")
    return [ItemRequest(sku, quantity) for sku, quantity in sorted(totals.items())]


def normalise_address(address: str) -> str:
    address = " ".join(address.split())
    if len(address) < 5:
        raise OrderError("Please give a full delivery address.")
    return address


async def search_products(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    stmt = select(Product).where(Product.org_id == org_id)
    # Every word must appear in the name, category, SKU or tags: "green tea" finds the Green Tea
    # category, and "elaichi chiya" finds Cardamom Chai through its tags.
    tags = func.array_to_string(Product.tags, " ")
    for word in (query or "").split():
        pattern = f"%{word}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(pattern),
                Product.category.ilike(pattern),
                Product.sku.ilike(pattern),
                tags.ilike(pattern),
            )
        )
    if category:
        stmt = stmt.where(Product.category.ilike(f"%{category.strip()}%"))
    if min_price is not None:
        stmt = stmt.where(Product.price >= Decimal(str(min_price)))
    if max_price is not None:
        stmt = stmt.where(Product.price <= Decimal(str(max_price)))
    if in_stock_only:
        stmt = stmt.where(Product.stock > 0)
    products = (await db.scalars(stmt.order_by(Product.category, Product.price).limit(limit))).all()
    return [
        {"sku": p.sku, "name": p.name, "category": p.category, "price": str(p.price), "in_stock": p.stock}
        for p in products
    ]


async def catalogue_options(db: AsyncSession, org_id: uuid.UUID) -> tuple[list[str], list[str]]:
    """The org's product categories and product names, so the agent's tool descriptions can list
    the values that actually exist. Names only: prices and stock are always looked up live."""
    rows = (await db.execute(
        select(Product.category, Product.name).where(Product.org_id == org_id).order_by(Product.category, Product.name)
    )).all()
    categories = list(dict.fromkeys(category for category, _ in rows))
    return categories, [name for _, name in rows]


async def _alternatives(db: AsyncSession, org_id: uuid.UUID, product: Product, quantity: int) -> list[dict[str, Any]]:
    """Up to three products in the same category with enough stock."""
    rows = await db.scalars(
        select(Product)
        .where(
            Product.org_id == org_id,
            Product.category == product.category,
            Product.id != product.id,
            Product.stock >= quantity,
        )
        .order_by(func.abs(Product.price - product.price))
        .limit(3)
    )
    return [{"sku": p.sku, "name": p.name, "price": str(p.price), "in_stock": p.stock} for p in rows]


async def quote_items(
    db: AsyncSession, org_id: uuid.UUID, items: list[ItemRequest], reserved: dict[str, int] | None = None
) -> Quote:
    """Prices and checks items against current prices and stock, without changing anything.

    `reserved` is stock already held by the order being changed, which it can use again.
    """
    reserved = reserved or {}
    items = normalise_items(items)
    products = {
        p.sku: p
        for p in await db.scalars(
            select(Product).where(Product.org_id == org_id, Product.sku.in_([i.sku for i in items]))
        )
    }
    quote = Quote()
    total = Decimal("0")
    for item in items:
        product = products.get(item.sku)
        if product is None:
            quote.problems.append(f"There is no product with SKU {item.sku}.")
            continue
        available_stock = product.stock + reserved.get(product.sku, 0)
        if item.quantity > available_stock:
            available = f"only {available_stock} left" if available_stock else "out of stock"
            quote.problems.append(f"{product.name} ({product.sku}): {available}, but {item.quantity} requested.")
            quote.alternatives += await _alternatives(db, org_id, product, item.quantity)
        line_total = product.price * item.quantity
        total += line_total
        quote.lines.append(
            QuoteLine(product.sku, product.name, item.quantity, str(product.price), str(line_total), available_stock)
        )
    quote.total = str(total)
    return quote


async def get_own_order(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, ref: str, *, for_update: bool = False
) -> Order | None:
    """The user's order by its full id or its order number (the id's first 8 characters).
    Other users' orders are never found, so the reply is the same as for an order that doesn't exist."""
    ref = ref.strip().lstrip("#").lower().replace("-", "")
    if not ref or any(c not in "0123456789abcdef" for c in ref) or not 6 <= len(ref) <= 32:
        return None
    stmt = select(Order).where(
        Order.org_id == org_id,
        Order.user_id == user_id,
        func.replace(cast(Order.id, Text), "-", "").like(f"{ref}%"),
    )
    if for_update:
        stmt = stmt.with_for_update()
    matches = (await db.scalars(stmt.limit(2))).all()
    return matches[0] if len(matches) == 1 else None


async def _lock_products(db: AsyncSession, org_id: uuid.UUID, skus: set[str]) -> dict[str, Product]:
    # Locked in SKU order, so two orders touching the same products can't deadlock.
    rows = await db.scalars(
        select(Product)
        .where(Product.org_id == org_id, Product.sku.in_(sorted(skus)))
        .order_by(Product.sku)
        .with_for_update()
    )
    return {p.sku: p for p in rows}


def _take_stock(products: dict[str, Product], items: list[ItemRequest]) -> Decimal:
    total = Decimal("0")
    for item in items:
        product = products.get(item.sku)
        if product is None:
            raise OrderError(f"There is no product with SKU {item.sku}.")
        if item.quantity > product.stock:
            raise OrderError(f"{product.name} now has only {product.stock} in stock.")
        product.stock -= item.quantity
        total += product.price * item.quantity
    return total


async def _record_status(db: AsyncSession, order: Order, from_status: str | None, to_status: str) -> None:
    db.add(
        OrderStatusHistory(
            id=uuid.uuid4(), org_id=order.org_id, order_id=order.id, from_status=from_status, to_status=to_status
        )
    )


async def place_order(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    items: list[ItemRequest],
    delivery_address: str,
) -> Order:
    """Places a pending order at current prices. Stock is checked again under a lock, so two
    customers can't both buy the last unit."""
    items = normalise_items(items)
    products = await _lock_products(db, org_id, {i.sku for i in items})
    total = _take_stock(products, items)
    order = Order(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
        status="pending",
        delivery_address=normalise_address(delivery_address),
        total=total,
    )
    db.add(order)
    await db.flush()
    db.add_all(
        OrderItem(
            id=uuid.uuid4(),
            org_id=org_id,
            order_id=order.id,
            product_id=products[i.sku].id,
            quantity=i.quantity,
            unit_price=products[i.sku].price,
        )
        for i in items
    )
    await _record_status(db, order, None, "pending")
    await db.flush()
    return order


def _require_editable(order: Order, action: str) -> None:
    if order.status != EDITABLE_STATUS:
        raise OrderError(
            f"Order {order_ref(order.id)} can't be {action} because it is already {order.status}. "
            "Only pending orders can be changed or cancelled."
        )


async def _current_items(db: AsyncSession, order: Order) -> list[tuple[OrderItem, Product]]:
    rows = await db.execute(
        select(OrderItem, Product)
        .join(Product, Product.id == OrderItem.product_id)
        .where(OrderItem.order_id == order.id)
        .order_by(Product.sku)
    )
    return [(item, product) for item, product in rows.tuples()]


async def update_order(
    db: AsyncSession,
    order: Order,
    *,
    items: list[ItemRequest] | None = None,
    delivery_address: str | None = None,
) -> Order:
    """Changes a pending order's items (replacing them all, at current prices) and/or address.
    The order must have been loaded with for_update=True."""
    _require_editable(order, "changed")
    if delivery_address is not None:
        order.delivery_address = normalise_address(delivery_address)
    if items is not None:
        items = normalise_items(items)
        old = await _current_items(db, order)
        products = await _lock_products(db, order.org_id, {p.sku for _, p in old} | {i.sku for i in items})
        for item, product in old:  # release the old reservation first
            products[product.sku].stock += item.quantity
        order.total = _take_stock(products, items)
        await db.execute(delete(OrderItem).where(OrderItem.order_id == order.id))
        db.add_all(
            OrderItem(
                id=uuid.uuid4(),
                org_id=order.org_id,
                order_id=order.id,
                product_id=products[i.sku].id,
                quantity=i.quantity,
                unit_price=products[i.sku].price,
            )
            for i in items
        )
    await db.flush()
    return order


async def cancel_order(db: AsyncSession, order: Order) -> Order:
    """Cancels a pending order and returns its items to stock. Loaded with for_update=True."""
    _require_editable(order, "cancelled")
    old = await _current_items(db, order)
    products = await _lock_products(db, order.org_id, {p.sku for _, p in old})
    for item, product in old:
        products[product.sku].stock += item.quantity
    order.status = "cancelled"
    await _record_status(db, order, "pending", "cancelled")
    await db.flush()
    return order


async def advance_status(db: AsyncSession, order: Order) -> Order:
    """Moves an order one step: pending -> confirmed -> shipped -> delivered. For admins."""
    if order.status not in STATUS_FLOW[:-1]:
        raise OrderError(f"Order {order_ref(order.id)} is {order.status} and can't move further.")
    new_status = STATUS_FLOW[STATUS_FLOW.index(order.status) + 1]
    await _record_status(db, order, order.status, new_status)
    order.status = new_status
    await db.flush()
    return order


async def order_details(db: AsyncSession, order: Order, tz: ZoneInfo) -> dict[str, Any]:
    items = await _current_items(db, order)
    history = await db.scalars(
        select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id == order.id)
        .order_by(OrderStatusHistory.changed_at)
    )
    return {
        "order_number": order_ref(order.id),
        "status": order.status,
        "placed_at": format_time(order.created_at, tz),
        "delivery_address": order.delivery_address,
        "items": [
            {
                "sku": product.sku,
                "name": product.name,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "line_total": str(item.unit_price * item.quantity),
            }
            for item, product in items
        ],
        "total": str(order.total),
        "status_history": [
            {"from": h.from_status, "to": h.to_status, "at": format_time(h.changed_at, tz)} for h in history
        ],
    }


async def list_orders(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, tz: ZoneInfo, limit: int = 10
) -> list[dict[str, Any]]:
    """The user's orders, newest first."""
    rows = await db.execute(
        select(Order, func.count(OrderItem.id))
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(Order.org_id == org_id, Order.user_id == user_id)
        .group_by(Order.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "order_number": order_ref(order.id),
            "status": order.status,
            "placed_at": format_time(order.created_at, tz),
            "items": item_count,
            "total": str(order.total),
        }
        for order, item_count in rows.tuples()
    ]


async def latest_order(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Order | None:
    return await db.scalar(
        select(Order)
        .where(Order.org_id == org_id, Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(1)
    )


async def items_of(db: AsyncSession, order: Order) -> list[ItemRequest]:
    return [ItemRequest(product.sku, item.quantity) for item, product in await _current_items(db, order)]
