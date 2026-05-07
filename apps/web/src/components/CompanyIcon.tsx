import Image from "next/image";
import { Building2 } from "lucide-react";

const SIZE_MAP = {
  16: { box: "size-4", fallback: 14 },
  20: { box: "size-5", fallback: 14 },
  24: { box: "size-6", fallback: 14 },
  28: { box: "size-7", fallback: 16 },
  32: { box: "size-8", fallback: 18 },
  36: { box: "size-9", fallback: 20 },
} as const;

export type CompanyIconSize = keyof typeof SIZE_MAP;

type CompanyIconProps = {
  icon: string | null | undefined;
  alt: string;
  size: CompanyIconSize;
  className?: string;
};

/**
 * Bypasses next/image optimization (`unoptimized`) — R2 icons are already
 * small WebP and Vercel docs say sub-10KB images shouldn't be transformed.
 * Width/height attrs preserved so CLS stays bounded; lazy/decoding=async
 * remain next/image defaults.
 */
export function CompanyIcon({ icon, alt, size, className = "" }: CompanyIconProps) {
  const { box, fallback } = SIZE_MAP[size];
  const base = `${box} shrink-0 rounded`;
  if (icon) {
    return (
      <Image
        src={icon}
        alt={alt}
        width={size}
        height={size}
        unoptimized
        className={`${base} ${className}`.trim()}
      />
    );
  }
  return (
    <div
      aria-hidden="true"
      className={`flex items-center justify-center bg-border-soft text-muted ${base} ${className}`.trim()}
    >
      <Building2 size={fallback} />
    </div>
  );
}
