"""Tools for an online store's agent: the brief's eight order tools.

Placing, changing, cancelling and repeating an order all work in two calls: the first returns a
summary (items, quantities, current prices, total) and records the request; the second, after the
customer confirms, carries it out. See confirmation_given.
"""

from typing import Annotated, Any

from llama_index.core.tools import FunctionTool
from pydantic import BaseModel, Field

from app.agent.base import AgentContext
from app.agent.tools.common import CONFIRM_NEXT_STEP, confirmation_given, knowledge_tool, make_tool, one_of, result
from app.db.session import session_factory
from app.services import orders
from app.services.orders import ItemRequest, OrderError

TOOL_LABELS = {
    "search_knowledge": "Searching our help articles…",
    "search_products": "Checking products and stock…",
    "create_order": "Preparing your order…",
    "update_order": "Updating your order…",
    "cancel_order": "Cancelling your order…",
    "get_order_status": "Looking up your order…",
    "list_my_orders": "Fetching your orders…",
    "reorder": "Checking your previous order…",
}


class OrderItemIn(BaseModel):
    sku: str = Field(description="The product's SKU, e.g. HT-BLK-001, from search_products.")
    quantity: int = Field(ge=1, description="How many.")


OrderNumber = Annotated[str, Field(description="The order number, e.g. 3F2A9C1B, from list_my_orders.")]


def _items(items: list[OrderItemIn]) -> list[ItemRequest]:
    # LlamaIndex passes the LLM's arguments through as plain JSON, so items arrive as dicts.
    parsed = [OrderItemIn.model_validate(i) if isinstance(i, dict) else i for i in items]
    return [ItemRequest(i.sku, i.quantity) for i in parsed]


def _summary(quote: orders.Quote, address: str, currency: str) -> dict[str, Any]:
    return {"items": [vars(line) for line in quote.lines], "total": quote.total, "currency": currency,
            "delivery_address": address}


async def build_tools(ctx: AgentContext) -> list[FunctionTool]:
    tz = ctx.config.tz
    currency = ctx.config.currency
    # Read per request, so a product added or renamed in the database shows up in the next chat.
    async with session_factory()() as db:
        categories, product_names = await orders.catalogue_options(db, ctx.org_id)

    async def search_products(
        query: Annotated[
            str | None,
            Field(description="Words to match in the name, category, SKU or tags (other names customers use, "
                              "e.g. 'chiya', 'caffeine-free', 'gift'). Every word must match."),
        ] = None,
        category: Annotated[str | None, Field(description="A category." + one_of(categories))] = None,
        min_price: Annotated[float | None, Field(description=f"Lowest price in {currency}.")] = None,
        max_price: Annotated[float | None, Field(description=f"Highest price in {currency}.")] = None,
        in_stock_only: Annotated[bool, Field(description="Only products in stock.")] = False,
    ) -> str:
        async with session_factory()() as db:
            found = await orders.search_products(
                db, ctx.org_id, query=query, category=category, min_price=min_price,
                max_price=max_price, in_stock_only=in_stock_only,
            )
        return result(currency=currency, products=found)

    async def create_order(
        items: Annotated[list[OrderItemIn], Field(description="The products and quantities to order.")],
        delivery_address: Annotated[str, Field(description="The full delivery address in Nepal.")],
    ) -> str:
        try:
            async with session_factory()() as db:
                quote = await orders.quote_items(db, ctx.org_id, _items(items))
                if not quote.ok:
                    return result(status="cannot_place", problems=quote.problems,
                                  alternatives=quote.alternatives, currency=currency)
                address = orders.normalise_address(delivery_address)
                request = {"items": [(l.sku, l.quantity) for l in quote.lines], "address": address.lower(),
                           "total": quote.total}
                if not await confirmation_given(db, ctx, "create_order", request):
                    await db.commit()
                    return result(status="needs_confirmation", summary=_summary(quote, address, currency),
                                  next_step=CONFIRM_NEXT_STEP)
                order = await orders.place_order(
                    db, org_id=ctx.org_id, user_id=ctx.user_id, conversation_id=ctx.conversation_id,
                    items=_items(items), delivery_address=address,
                )
                details = await orders.order_details(db, order, tz)
                await db.commit()
                return result(status="placed", order=details, payment="cash on delivery")
        except OrderError as error:
            return result(status="refused", reason=str(error))

    async def update_order(
        order_number: OrderNumber,
        items: Annotated[
            list[OrderItemIn] | None,
            Field(description="The complete new list of items (replaces all current items). Omit to keep them."),
        ] = None,
        delivery_address: Annotated[str | None, Field(description="A new delivery address. Omit to keep it.")] = None,
    ) -> str:
        if items is None and delivery_address is None:
            return result(status="refused", reason="Nothing to change: give new items or a new address.")
        try:
            async with session_factory()() as db:
                order = await orders.get_own_order(db, ctx.org_id, ctx.user_id, order_number, for_update=True)
                if order is None:
                    return result(status="not_found", reason=f"You have no order {order_number}.")
                if order.status != orders.EDITABLE_STATUS:
                    return result(status="refused", current_status=order.status,
                                  reason=f"Order {orders.order_ref(order.id)} is already {order.status}; "
                                         "only pending orders can be changed.")
                current = await orders.items_of(db, order)
                new_items = _items(items) if items is not None else current
                quote = await orders.quote_items(db, ctx.org_id, new_items,
                                                 reserved={i.sku: i.quantity for i in current})
                if not quote.ok:
                    return result(status="cannot_change", problems=quote.problems,
                                  alternatives=quote.alternatives, currency=currency)
                address = orders.normalise_address(delivery_address or order.delivery_address)
                request = {"order": str(order.id), "items": [(l.sku, l.quantity) for l in quote.lines],
                           "address": address.lower(), "total": quote.total}
                if not await confirmation_given(db, ctx, "update_order", request):
                    await db.commit()
                    return result(status="needs_confirmation", order_number=orders.order_ref(order.id),
                                  current_order=await orders.order_details(db, order, tz),
                                  new_order=_summary(quote, address, currency), next_step=CONFIRM_NEXT_STEP)
                await orders.update_order(db, order, items=_items(items) if items is not None else None,
                                          delivery_address=delivery_address)
                details = await orders.order_details(db, order, tz)
                await db.commit()
                return result(status="updated", order=details)
        except OrderError as error:
            return result(status="refused", reason=str(error))

    async def cancel_order(order_number: OrderNumber) -> str:
        try:
            async with session_factory()() as db:
                order = await orders.get_own_order(db, ctx.org_id, ctx.user_id, order_number, for_update=True)
                if order is None:
                    return result(status="not_found", reason=f"You have no order {order_number}.")
                if order.status != orders.EDITABLE_STATUS:
                    return result(status="refused", current_status=order.status,
                                  reason=f"Order {orders.order_ref(order.id)} is already {order.status}; "
                                         "only pending orders can be cancelled.")
                if not await confirmation_given(db, ctx, "cancel_order", {"order": str(order.id)}):
                    details = await orders.order_details(db, order, tz)
                    await db.commit()
                    return result(status="needs_confirmation", order_to_cancel=details, next_step=CONFIRM_NEXT_STEP)
                await orders.cancel_order(db, order)
                details = await orders.order_details(db, order, tz)
                await db.commit()
                return result(status="cancelled", order=details)
        except OrderError as error:
            return result(status="refused", reason=str(error))

    async def get_order_status(order_number: OrderNumber) -> str:
        async with session_factory()() as db:
            order = await orders.get_own_order(db, ctx.org_id, ctx.user_id, order_number)
            if order is None:
                return result(status="not_found", reason=f"You have no order {order_number}.")
            return result(order=await orders.order_details(db, order, tz), currency=currency)

    async def list_my_orders() -> str:
        async with session_factory()() as db:
            return result(orders=await orders.list_orders(db, ctx.org_id, ctx.user_id, tz), currency=currency)

    async def reorder(
        order_number: Annotated[
            str | None, Field(description="The order to repeat. Omit for the customer's most recent order.")
        ] = None,
        delivery_address: Annotated[
            str | None, Field(description="Deliver somewhere else. Omit to use the previous order's address.")
        ] = None,
    ) -> str:
        try:
            async with session_factory()() as db:
                if order_number:
                    previous = await orders.get_own_order(db, ctx.org_id, ctx.user_id, order_number)
                else:
                    previous = await orders.latest_order(db, ctx.org_id, ctx.user_id)
                if previous is None:
                    return result(status="not_found", reason="There is no previous order to repeat.")
                before = await orders.order_details(db, previous, tz)
                items = await orders.items_of(db, previous)
                quote = await orders.quote_items(db, ctx.org_id, items)
                old_prices = {i["sku"]: i["unit_price"] for i in before["items"]}
                price_changes = [
                    {"sku": l.sku, "name": l.name, "was": old_prices[l.sku], "now": l.unit_price}
                    for l in quote.lines if old_prices.get(l.sku) != l.unit_price
                ]
                if not quote.ok:
                    return result(status="cannot_repeat_as_is", previous_order=before, problems=quote.problems,
                                  alternatives=quote.alternatives, price_changes=price_changes,
                                  next_step="Tell the customer what changed and offer the alternatives; "
                                            "use create_order for an adjusted order.")
                address = orders.normalise_address(delivery_address or previous.delivery_address)
                request = {"repeat": str(previous.id), "items": [(l.sku, l.quantity) for l in quote.lines],
                           "address": address.lower(), "total": quote.total}
                if not await confirmation_given(db, ctx, "reorder", request):
                    await db.commit()
                    return result(status="needs_confirmation", repeating=before["order_number"],
                                  summary=_summary(quote, address, currency), price_changes=price_changes,
                                  next_step=CONFIRM_NEXT_STEP)
                order = await orders.place_order(
                    db, org_id=ctx.org_id, user_id=ctx.user_id, conversation_id=ctx.conversation_id,
                    items=items, delivery_address=address,
                )
                details = await orders.order_details(db, order, tz)
                await db.commit()
                return result(status="placed", order=details, payment="cash on delivery")
        except OrderError as error:
            return result(status="refused", reason=str(error))

    return [
        knowledge_tool(ctx),
        make_tool(search_products, "search_products",
                  "Finds products by name, category, tag or price range, with live prices and stock. "
                  "Use it for any question about what is available, how much it costs or how many are left."
                  + (f" Products sold: {', '.join(product_names)}." if product_names else "")),
        make_tool(create_order, "create_order",
                  "Places an order for the signed-in customer (cash on delivery). The first call returns a "
                  "summary to confirm; the call after the customer confirms places it."),
        make_tool(update_order, "update_order",
                  "Changes a pending order's items, quantities or delivery address. Refused for orders that "
                  "are no longer pending. First call returns a summary to confirm."),
        make_tool(cancel_order, "cancel_order",
                  "Cancels a pending order. Refused for orders that are no longer pending. "
                  "First call returns the order to confirm."),
        make_tool(get_order_status, "get_order_status",
                  "Shows one of the customer's orders: status, items, total and status history."),
        make_tool(list_my_orders, "list_my_orders", "Lists the customer's orders, newest first."),
        make_tool(reorder, "reorder",
                  "Repeats a previous order ('same as last time') as a new pending order, re-checking stock "
                  "and current prices and reporting any changes. First call returns a summary to confirm."),
    ]
