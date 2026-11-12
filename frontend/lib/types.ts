// Shapes of what the FastAPI backend sends. Tool results are loose (each tool returns its own
// fields), so cards read them defensively.

export type Branding = { primary: string; on_primary: string; tint: string; tagline: string };

export type OrgSummary = { slug: string; name: string; branding: Branding };

export type Org = OrgSummary & {
  greeting: string;
  toolset: "orders" | "bookings";
  timezone: string;
  currency: string;
};

export type User = { id: string; email: string; role: string; org_slug: string };

export type Conversation = { id: string; title: string; created_at: string; updated_at: string };

export type StoredMessage = { id: string; role: "user" | "assistant"; content: string; created_at: string };

export type ToolActivity = { tool: string; label: string };

export type ToolResult = { tool: string; data: Record<string, any> };

export type MessageStatus = "streaming" | "done" | "stopped" | "error";

/** A message as the thread shows it: saved text, plus (for live replies) the tools and their results. */
export type UiMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools: ToolActivity[];
  results: ToolResult[];
  status: MessageStatus;
  error?: string;
};

export type OrderItem = { sku: string; name: string; quantity: number; unit_price: string; line_total: string };

export type OrderDetails = {
  order_number: string;
  status: string;
  placed_at: string;
  delivery_address: string;
  items: OrderItem[];
  total: string;
  status_history: { from: string | null; to: string; at: string }[];
};

export type BookingDetails = {
  booking_number: string;
  service: string;
  practitioner: string;
  starts: string;
  ends: string;
  status: string;
  price: string;
  currency: string;
  cancellation_fee?: string;
};

export type Slot = { date: string; day: string; start: string; end: string; practitioner: string };
