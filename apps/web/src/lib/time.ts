const DAY_MS = 24 * 60 * 60 * 1000;

type RelativeUnit = "year" | "month" | "week" | "day" | "hour" | "minute";

const unitFormatters = new Map<string, Intl.NumberFormat>();

function formatShortUnit(value: number, unit: RelativeUnit, locale: string): string {
  // CLDR's English narrow month symbol is "m", which is indistinguishable
  // from minutes. Preserve the established unambiguous English "mo" label.
  if (unit === "month" && locale.toLowerCase().startsWith("en")) {
    const key = `${locale}:month-token`;
    let formatter = unitFormatters.get(key);
    if (!formatter) {
      formatter = new Intl.NumberFormat(locale);
      unitFormatters.set(key, formatter);
    }
    return `${formatter.format(value)}mo`;
  }

  const key = `${locale}:${unit}`;
  let formatter = unitFormatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, {
      style: "unit",
      unit,
      unitDisplay: "narrow",
    });
    unitFormatters.set(key, formatter);
  }
  return formatter.format(value);
}

export function timeAgoShort(date: Date | string, locale = "en"): string {
  const d = typeof date === "string" ? new Date(date) : date;
  const diffMs = Date.now() - d.getTime();
  const days = Math.floor(diffMs / DAY_MS);

  if (days >= 365) return formatShortUnit(Math.floor(days / 365), "year", locale);
  if (days >= 30) return formatShortUnit(Math.floor(days / 30), "month", locale);
  if (days >= 7) return formatShortUnit(Math.floor(days / 7), "week", locale);
  if (days > 0) return formatShortUnit(days, "day", locale);

  const hours = Math.floor(diffMs / (60 * 60 * 1000));
  if (hours > 0) return formatShortUnit(hours, "hour", locale);

  const minutes = Math.max(1, Math.floor(diffMs / (60 * 1000)));
  return formatShortUnit(minutes, "minute", locale);
}
