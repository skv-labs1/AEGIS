import type { ReactNode } from "react";

/** Small building blocks shared across the console. */

const RISK_TONE: Record<string, string> = {
  READ: "bg-ink-700 text-ink-200 ring-ink-600",
  WRITE_LOW: "bg-signal-500/15 text-signal-400 ring-signal-500/30",
  WRITE_MEDIUM: "bg-warn-500/15 text-warn-500 ring-warn-500/30",
  WRITE_HIGH: "bg-alert-500/15 text-alert-500 ring-alert-500/30",
  UNCLASSIFIED: "bg-violet-500/15 text-violet-500 ring-violet-500/30",
};

const STATE_TONE: Record<string, string> = {
  new: "bg-signal-500/15 text-signal-400 ring-signal-500/30",
  in_progress: "bg-warn-500/15 text-warn-500 ring-warn-500/30",
  investigating: "bg-warn-500/15 text-warn-500 ring-warn-500/30",
  diagnosed: "bg-violet-500/15 text-violet-500 ring-violet-500/30",
  proposed: "bg-violet-500/15 text-violet-500 ring-violet-500/30",
  awaiting_approval: "bg-warn-500/15 text-warn-500 ring-warn-500/30",
  approved: "bg-ok-500/15 text-ok-500 ring-ok-500/30",
  executed: "bg-ok-500/15 text-ok-500 ring-ok-500/30",
  verified: "bg-ok-500/15 text-ok-500 ring-ok-500/30",
  resolved: "bg-ok-500/15 text-ok-500 ring-ok-500/30",
  rejected: "bg-alert-500/15 text-alert-500 ring-alert-500/30",
  verification_failed: "bg-alert-500/15 text-alert-500 ring-alert-500/30",
  escalated: "bg-alert-500/15 text-alert-500 ring-alert-500/30",
  pending: "bg-warn-500/15 text-warn-500 ring-warn-500/30",
  failed: "bg-alert-500/15 text-alert-500 ring-alert-500/30",
};

export function Chip({
  children,
  tone,
  title,
}: {
  children: ReactNode;
  tone?: string;
  title?: string;
}) {
  const cls = tone ?? "bg-ink-700 text-ink-200 ring-ink-600";
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset whitespace-nowrap ${cls}`}
    >
      {children}
    </span>
  );
}

export function RiskChip({ risk }: { risk: string }) {
  return <Chip tone={RISK_TONE[risk] ?? RISK_TONE.UNCLASSIFIED}>{risk.replace("WRITE_", "")}</Chip>;
}

export function StateChip({ state }: { state: string }) {
  return <Chip tone={STATE_TONE[state]}>{state.replace(/_/g, " ")}</Chip>;
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-ink-800 bg-ink-900 ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-ink-800 px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-ink-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const styles = {
    default: "bg-ink-800 text-ink-100 hover:bg-ink-700 border-ink-700",
    primary: "bg-signal-500 text-white hover:bg-signal-400 border-signal-500",
    danger: "bg-alert-500/15 text-alert-500 hover:bg-alert-500/25 border-alert-500/40",
    ghost: "bg-transparent text-ink-300 hover:bg-ink-800 border-transparent",
  }[variant];
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${styles}`}
    >
      {children}
    </button>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[12px] text-ink-300">{children}</span>;
}

export function HealthDial({ score, band }: { score: number; band: string }) {
  const tone =
    band === "healthy" ? "text-ok-500" : band === "fair" ? "text-signal-400" : band === "degraded" ? "text-warn-500" : "text-alert-500";
  const stroke =
    band === "healthy" ? "#2fbf71" : band === "fair" ? "#6aa6ff" : band === "degraded" ? "#e0a63a" : "#e5484d";
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const filled = Math.max(0, Math.min(100, score)) / 100;
  return (
    <div className="flex items-center gap-3">
      <svg width="84" height="84" viewBox="0 0 84 84" className="-rotate-90">
        <circle cx="42" cy="42" r={radius} fill="none" stroke="#232c3d" strokeWidth="8" />
        <circle
          cx="42"
          cy="42"
          r={radius}
          fill="none"
          stroke={stroke}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - filled)}
          style={{ transition: "stroke-dashoffset 700ms ease-out" }}
        />
      </svg>
      <div>
        <div className={`text-2xl font-semibold tabular-nums ${tone}`}>{score.toFixed(1)}</div>
        <div className="text-xs uppercase tracking-wide text-ink-400">{band}</div>
      </div>
    </div>
  );
}

export function Sparkline({ points }: { points: number[] }) {
  if (points.length < 2) return null;
  const width = 220;
  const height = 44;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const path = points
    .map((value, index) => {
      const x = (index / (points.length - 1)) * width;
      const y = height - ((value - min) / span) * (height - 6) - 3;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const declining = points[points.length - 1] < points[0];
  return (
    <svg width={width} height={height} className="overflow-visible">
      <path d={path} fill="none" stroke={declining ? "#e5484d" : "#2fbf71"} strokeWidth="1.5" />
    </svg>
  );
}

export function StatTile({
  label,
  value,
  detail,
  tone,
  onClick,
  title,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: string;
  onClick?: () => void;
  title?: string;
}) {
  const body = (
    <>
      <div className={`text-[26px] leading-none font-semibold tabular-nums ${tone ?? "text-ink-100"}`}>
        {value}
      </div>
      <div className="mt-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-400">{label}</div>
      {detail && <div className="mt-1 text-[11px] text-ink-500">{detail}</div>}
    </>
  );
  const shell = "rounded-lg border border-ink-800 bg-ink-900 px-3.5 py-3 text-left";
  return onClick ? (
    <button onClick={onClick} title={title} className={`${shell} w-full transition-colors hover:border-ink-700 hover:bg-ink-850`}>
      {body}
    </button>
  ) : (
    <div title={title} className={shell}>
      {body}
    </div>
  );
}

/** A labelled set of counts drawn as proportional bars. */
export function BarList({
  items,
  total,
  emptyLabel = "Nothing to show.",
}: {
  items: { label: string; value: number; tone?: string; hint?: string }[];
  total?: number;
  emptyLabel?: string;
}) {
  const sum = total ?? items.reduce((acc, item) => acc + item.value, 0);
  if (!items.length || sum === 0) return <Empty>{emptyLabel}</Empty>;
  return (
    <ul className="space-y-2">
      {items.map((item) => {
        const pct = Math.round((item.value / sum) * 100);
        return (
          <li key={item.label} title={item.hint}>
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <span className="truncate text-ink-300">{item.label}</span>
              <span className="shrink-0 tabular-nums text-ink-400">
                <span className="text-ink-100">{item.value}</span>
                <span className="ml-1.5 text-[11px] text-ink-500">{pct}%</span>
              </span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-800">
              <div
                className={`h-full rounded-full ${item.tone ?? "bg-signal-500"}`}
                style={{ width: `${Math.max(pct, 2)}%`, transition: "width 500ms ease-out" }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-xs text-ink-400">{children}</p>;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-ink-400">
      <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-ink-600 border-t-signal-500" />
      {label ?? "Loading"}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-md border border-alert-500/30 bg-alert-500/10 px-3 py-2 text-xs text-alert-500">
      {children}
    </div>
  );
}

export function Untrusted({ text, source }: { text: string; source: string }) {
  return (
    <div className="rounded-md border border-ink-700 bg-ink-850">
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-1.5">
        <Chip tone="bg-violet-500/15 text-violet-500 ring-violet-500/30">untrusted input</Chip>
        <span className="text-[11px] text-ink-400">
          {source} · treated as data, never as instructions
        </span>
      </div>
      <p className="px-3 py-2 text-sm whitespace-pre-wrap text-ink-200">{text}</p>
    </div>
  );
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function clockTime(iso: string | null): string {
  if (!iso) return "--:--:--";
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}
