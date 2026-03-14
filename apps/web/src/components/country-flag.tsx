/** Locale → ISO country code for the app locales. */
const LOCALE_TO_COUNTRY: Record<string, string> = {
  en: "gb",
  de: "de",
  fr: "fr",
  it: "it",
};

interface CountryFlagProps {
  /** ISO 3166-1 alpha-2 code (lowercase), e.g. "us", "gb" */
  iso: string;
  /** Width in pixels. Height is derived from 4:3 aspect ratio. */
  size?: number;
  className?: string;
}

/**
 * Renders a country flag from a static SVG in /flags/.
 * Returns null if the ISO code is empty.
 */
export function CountryFlag({ iso, size = 20, className }: CountryFlagProps) {
  if (!iso) return null;
  const height = Math.round(size * 0.75);
  return (
    <img
      src={`/flags/${iso.toLowerCase()}.svg`}
      alt=""
      aria-hidden
      width={size}
      height={height}
      className={className}
    />
  );
}

/**
 * Renders a flag for a locale code (en, de, fr, it).
 */
export function LocaleFlag({
  locale,
  size = 20,
  className,
}: {
  locale: string;
  size?: number;
  className?: string;
}) {
  const iso = LOCALE_TO_COUNTRY[locale];
  if (!iso) return null;
  return <CountryFlag iso={iso} size={size} className={className} />;
}
