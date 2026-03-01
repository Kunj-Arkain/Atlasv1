import { NextResponse } from "next/server";
import type { Thread } from "@/lib/contracts";

const threads: Thread[] = [
  { id: "t1", title: "Springfield Strip Center Analysis", lastMessage: "The deal scores GO with IRR of 15.2%...", updatedAt: "2m ago", unread: true },
  { id: "t2", title: "BP Gas Station #12 Contract", lastMessage: "Monte Carlo simulation complete...", updatedAt: "1h ago", unread: false },
  { id: "t3", title: "Portfolio Concentration Review", lastMessage: "Illinois exposure is at 72%...", updatedAt: "3h ago", unread: false },
  { id: "t4", title: "Dollar General Lease Analysis", lastMessage: "Comparing flat lease vs hybrid...", updatedAt: "1d ago", unread: false },
];

export async function GET() {
  return NextResponse.json(threads);
}

export async function POST(req: Request) {
  const { title } = await req.json();
  const thread: Thread = {
    id: `t_${Date.now()}`,
    title: title || "New Thread",
    lastMessage: "",
    updatedAt: "now",
    unread: false,
  };
  threads.unshift(thread);
  return NextResponse.json(thread, { status: 201 });
}
