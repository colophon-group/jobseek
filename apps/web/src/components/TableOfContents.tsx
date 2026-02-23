"use client";

export type TocItem = {
  label: string;
  href: string;
  children?: TocItem[];
};

type TableOfContentsProps = {
  title: string;
  ariaLabel: string;
  items: TocItem[];
  className?: string;
};

export function TableOfContents({
  title,
  ariaLabel,
  items,
  className,
}: TableOfContentsProps) {
  if (!items?.length) {
    return null;
  }

  const renderItems = (entries: TocItem[], depth = 0) =>
    entries.map((item) => (
      <li key={item.href} style={depth ? { paddingLeft: `${depth * 1.5}rem` } : undefined}>
        <a
          href={item.href}
          className={`block rounded px-3 py-1.5 text-[0.9rem] hover:bg-border-soft ${depth ? "font-medium" : "font-semibold"}`}
          style={{ minHeight: 36 }}
        >
          {item.label}
        </a>
        {item.children?.length ? (
          <ul className="list-none p-0">{renderItems(item.children, depth + 1)}</ul>
        ) : null}
      </li>
    ));

  return (
    <nav
      aria-label={ariaLabel}
      className={`min-w-[200px] w-full self-start md:sticky md:top-[120px] ${className ?? ""}`}
    >
      <div className="flex flex-col gap-4">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted">
          {title}
        </span>
        <ul className="list-none p-0">
          {renderItems(items)}
        </ul>
      </div>
    </nav>
  );
}
