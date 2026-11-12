"use client";

import { useParams } from "next/navigation";
import { ChatApp } from "@/components/ChatApp";

export default function ChatPage() {
  const { org } = useParams<{ org: string }>();
  return <ChatApp key={org} slug={org} />;
}
