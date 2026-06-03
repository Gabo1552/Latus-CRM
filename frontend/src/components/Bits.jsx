import { useState } from "react";
import { LEAD_STATUSES, PRIORITIES, statusMeta } from "@/lib/constants";

export function Badge({ label, color, bg, testid }) {
  return (
    <span
      data-testid={testid}
      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold border"
      style={{ color, backgroundColor: bg, borderColor: color + "33" }}
    >
      {label}
    </span>
  );
}

export function StatusBadge({ list, value, testid }) {
  const m = statusMeta(list, value);
  return <Badge label={m.label} color={m.color} bg={m.bg} testid={testid} />;
}

export function PriorityDot({ value }) {
  const m = statusMeta(PRIORITIES, value);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-[#888888]">
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: m.color }} />
      {m.label}
    </span>
  );
}

export function Avatar({ src, name, size = 36 }) {
  const init = (name || "?").split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  if (src) {
    return <img src={src} alt={name} style={{ width: size, height: size }} className="rounded-sm object-cover shrink-0" />;
  }
  return (
    <div
      style={{ width: size, height: size }}
      className="rounded-sm bg-[#0E8DDB] text-white flex items-center justify-center text-xs font-bold shrink-0"
    >
      {init}
    </div>
  );
}

export function EmptyState({ icon: Icon, title, subtitle }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="h-14 w-14 rounded-sm bg-latus-warm-gray flex items-center justify-center mb-4">
        {Icon && <Icon className="h-6 w-6 text-latus-muted" />}
      </div>
      <p className="font-semibold text-[#0B1B26]">{title}</p>
      {subtitle && <p className="text-sm text-[#888888] mt-1 max-w-xs">{subtitle}</p>}
    </div>
  );
}

export { LEAD_STATUSES, PRIORITIES };
