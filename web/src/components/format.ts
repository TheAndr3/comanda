// Formatting for the panel, which speaks Portuguese to the people using it.

const AGENT_NAMES: Record<string, string> = {
  reception: "Recepção",
  orders: "Pedidos",
  refunds: "Reembolsos",
  customer: "Cliente",
  system: "Sistema",
};

export function agentName(agent: string): string {
  return AGENT_NAMES[agent] ?? agent;
}

const ISSUE_NAMES: Record<string, string> = {
  missing_item: "Item faltando",
  wrong_item: "Item errado",
  late_delivery: "Atraso",
  not_delivered: "Pedido não entregue",
  quality: "Qualidade",
};

export function issueName(issue: string): string {
  return ISSUE_NAMES[issue] ?? issue;
}

const integer = new Intl.NumberFormat("pt-BR");
const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const dateTime = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });

/** Money is integer cents everywhere; only the display turns it into reais. */
export function formatBrl(cents: number): string {
  return brl.format(cents / 100);
}

export function formatDateTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : dateTime.format(date);
}
const dollars = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 5, maximumFractionDigits: 5 });
const percent = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 0 });
const clock = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function formatInt(value: number): string {
  return integer.format(value);
}

/** API-equivalent cost: an estimate in dollars with fractions of a cent (ADR-0014). */
export function formatUsd(value: number): string {
  return `US$ ${dollars.format(value)}`;
}

export function formatPercent(fraction: number): string {
  return percent.format(fraction);
}

export function formatTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : clock.format(date);
}

/** A payload for reading: text as it came, anything else as indented JSON. */
export function formatJson(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2) ?? String(value);
}
