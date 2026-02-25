import Link from "next/link";
import type { ComponentProps } from "react";

const base =
  "inline-flex items-center justify-center whitespace-nowrap rounded-full font-semibold cursor-pointer disabled:opacity-50";

const variants = {
  primary: "border border-primary bg-primary text-primary-contrast transition-opacity hover:opacity-90",
  outline: "border border-current transition-colors hover:bg-border-soft",
  danger: "border border-error-border bg-error-border text-error transition-opacity hover:opacity-80",
  "danger-outline": "border border-error-border text-error transition-colors hover:bg-error-bg",
} as const;

const sizes = {
  sm: "px-4 py-1.5 text-sm",
  md: "px-5 py-2",
} as const;

type Variant = keyof typeof variants;
type Size = keyof typeof sizes;

type ButtonOwnProps = {
  variant?: Variant;
  size?: Size;
};

type AsLink = ButtonOwnProps & { href: string } & Omit<ComponentProps<typeof Link>, "href">;
type AsButton = ButtonOwnProps & { href?: undefined } & ComponentProps<"button">;

export type ButtonProps = AsLink | AsButton;

export function Button({ variant = "primary", size = "md", className, ...rest }: ButtonProps) {
  const cls = `${base} ${variants[variant]} ${sizes[size]} ${className ?? ""}`;

  if (rest.href != null) {
    const { href, ...linkProps } = rest as AsLink;
    return <Link href={href} className={cls} {...linkProps} />;
  }

  const buttonProps = rest as AsButton;
  return <button className={cls} {...buttonProps} />;
}
