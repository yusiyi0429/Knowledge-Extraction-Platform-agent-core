import { useEffect, useId, type ButtonHTMLAttributes, type ReactNode } from "react";

export type IconName =
  | "archive"
  | "arrow"
  | "book"
  | "brain"
  | "check"
  | "chevron"
  | "close"
  | "database"
  | "download"
  | "edit"
  | "file"
  | "grid"
  | "layers"
  | "model"
  | "plus"
  | "refresh"
  | "search"
  | "send"
  | "settings"
  | "spark"
  | "upload"
  | "users"
  | "warning";

const paths: Record<IconName, ReactNode> = {
  archive: <><path d="M4 7h16v13H4z"/><path d="M3 4h18v3H3zM9 11h6"/></>,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
  book: <><path d="M4 5.5A3.5 3.5 0 0 1 7.5 2H11v18H7.5A3.5 3.5 0 0 0 4 23.5z"/><path d="M20 5.5A3.5 3.5 0 0 0 16.5 2H13v18h3.5a3.5 3.5 0 0 1 3.5 3.5z"/></>,
  brain: <><path d="M9.5 5.5A3.5 3.5 0 0 0 3 7.3 3.4 3.4 0 0 0 4.2 13 4 4 0 0 0 9.5 19"/><path d="M14.5 5.5A3.5 3.5 0 0 1 21 7.3a3.4 3.4 0 0 1-1.2 5.7 4 4 0 0 1-5.3 6M9.5 4v16M14.5 4v16M7 9h2.5M14.5 9H17M6.5 15h3M14.5 15h3"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  chevron: <path d="m9 18 6-6-6-6"/>,
  close: <path d="M6 6l12 12M18 6 6 18"/>,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></>,
  download: <><path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 20h16"/></>,
  edit: <><path d="m4 20 4.4-1 10.8-10.8a2 2 0 0 0-2.8-2.8L5.6 16.2z"/><path d="m14.8 7 2.8 2.8"/></>,
  file: <><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
  layers: <><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></>,
  model: <><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9zM4 10H2M4 14H2M22 10h-2M22 14h-2M10 4V2M14 4V2M10 22v-2M14 22v-2"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
  refresh: <><path d="M20 6v5h-5"/><path d="M18.2 16A8 8 0 1 1 20 11"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m16.5 16.5 4 4"/></>,
  send: <><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4z"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.8 1.8 0 0 0 .4 2l.1.1-2.8 2.8-.1-.1a1.8 1.8 0 0 0-2-.4 1.8 1.8 0 0 0-1.1 1.6v.2h-4V21A1.8 1.8 0 0 0 8.8 19.4a1.8 1.8 0 0 0-2 .4l-.1.1-2.8-2.8L4 17a1.8 1.8 0 0 0 .4-2 1.8 1.8 0 0 0-1.6-1.1h-.2v-4h.2A1.8 1.8 0 0 0 4.4 8.8a1.8 1.8 0 0 0-.4-2l-.1-.1 2.8-2.8.1.1a1.8 1.8 0 0 0 2 .4A1.8 1.8 0 0 0 9.9 2.8v-.2h4v.2A1.8 1.8 0 0 0 15 4.4a1.8 1.8 0 0 0 2-.4l.1-.1 2.8 2.8-.1.1a1.8 1.8 0 0 0-.4 2 1.8 1.8 0 0 0 1.6 1.1h.2v4H21a1.8 1.8 0 0 0-1.6 1.1z"/></>,
  spark: <><path d="m12 2 1.5 5.5L19 9l-5.5 1.5L12 16l-1.5-5.5L5 9l5.5-1.5z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></>,
  upload: <><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 20h16"/></>,
  users: <><circle cx="9" cy="8" r="4"/><path d="M2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 6 6v1M16 5.5a3.5 3.5 0 0 1 0 6.8M18 15a5 5 0 0 1 4 5"/></>,
  warning: <><path d="m12 3 10 18H2z"/><path d="M12 9v5M12 18h.01"/></>,
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

export function Button({
  kind = "ghost",
  icon,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  kind?: "primary" | "ghost" | "danger" | "text";
  icon?: IconName;
}) {
  return (
    <button className={`button button-${kind} ${className}`} {...props}>
      {icon && <Icon name={icon} size={16} />}
      {children}
    </button>
  );
}

export function Modal({
  open,
  title,
  subtitle,
  onClose,
  children,
  footer,
  wide = false,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const titleId = useId();
  useEffect(() => {
    if (!open) return;
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="modal-head">
          <div>
            <h2 id={titleId}>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="icon-button" aria-label="关闭" onClick={onClose}><Icon name="close" /></button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </section>
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const labels: Record<string, string> = {
    DRAFT: "草稿",
    EXTRACTING: "萃取中",
    REVIEW: "待对齐",
    READY: "已就绪",
    PUBLISHED: "已发布",
    FAILED: "失败",
    QUEUED: "排队中",
    RUNNING: "运行中",
    COMPLETED: "已完成",
    ENABLED: "已启用",
  };
  return <span className={`status status-${status.toLowerCase()}`}>{labels[status] || status}</span>;
}

export function EmptyState({ icon = "file", title, detail, action }: { icon?: IconName; title: string; detail: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon name={icon} size={26} /></span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warning" | "success" | "danger"; children: ReactNode }) {
  return <div className={`notice notice-${tone}`} role={tone === "danger" ? "alert" : "status"} aria-live="polite">{tone === "warning" && <Icon name="warning" size={16} />}{children}</div>;
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
