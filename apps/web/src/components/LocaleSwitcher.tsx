"use client";

import { useParams, usePathname, useRouter } from "next/navigation";
import { useLingui } from "@lingui/react/macro";
import { locales, type Locale } from "@/lib/i18n";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Globe } from "lucide-react";

const localeLabels: Record<Locale, { label: string; flag: string }> = {
  en: { label: "English", flag: "/flags/gb.svg" },
  de: { label: "Deutsch", flag: "/flags/de.svg" },
  fr: { label: "Français", flag: "/flags/fr.svg" },
  it: { label: "Italiano", flag: "/flags/it.svg" },
};

type LocaleSwitcherProps = {
  className?: string;
};

export function LocaleSwitcher({ className }: LocaleSwitcherProps) {
  const { t } = useLingui();
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams();
  const currentLocale = (params.lang as string) ?? "en";

  const label = t({
    id: "common.locale.switch",
    comment: "Aria label for language switcher button",
    message: "Change language",
  });

  function handleSelect(locale: Locale) {
    if (locale === currentLocale) return;
    const newPath = pathname.replace(`/${currentLocale}`, `/${locale}`);
    router.push(newPath);
  }

  return (
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      <Tooltip.Root>
        <DropdownMenu.Root>
          <Tooltip.Trigger asChild>
            <DropdownMenu.Trigger asChild>
              <button
                className={`inline-flex items-center justify-center rounded-md p-1.5 text-foreground hover:bg-border-soft transition-colors ${className ?? ""}`}
                aria-label={label}
              >
                <Globe size={18} />
              </button>
            </DropdownMenu.Trigger>
          </Tooltip.Trigger>
          <Tooltip.Portal>
            <Tooltip.Content
              className="z-50 rounded-md bg-tooltip-bg px-2.5 py-1 text-xs text-white data-[state=delayed-open]:animate-[tooltip-in_150ms_ease] data-[state=instant-open]:animate-[tooltip-in_150ms_ease] data-[state=closed]:animate-[tooltip-out_100ms_ease_forwards]"
              sideOffset={6}
            >
              {label}
            </Tooltip.Content>
          </Tooltip.Portal>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              className="z-50 min-w-[140px] rounded-md border border-border-soft bg-surface p-1 shadow-lg"
              sideOffset={5}
              align="end"
            >
              {locales.map((locale) => (
                <DropdownMenu.Item
                  key={locale}
                  className={`flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-border-soft ${locale === currentLocale ? "font-semibold" : ""}`}
                  onSelect={() => handleSelect(locale)}
                >
                  <img src={localeLabels[locale].flag} alt="" width={20} height={15} className="block" />
                  <span>{localeLabels[locale].label}</span>
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
