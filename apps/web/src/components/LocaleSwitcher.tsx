"use client";

import type { SVGProps } from "react";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useLingui } from "@lingui/react/macro";
import { locales, type Locale } from "@/lib/i18n";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Globe } from "lucide-react";

function FlagGB(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <path fill="#012169" d="M0 0h640v480H0z"/>
      <path fill="#FFF" d="m75 0 244 181L562 0h78v62L400 241l240 178v61h-80L320 301 81 480H0v-60l239-178L0 64V0z"/>
      <path fill="#C8102E" d="m424 281 216 159v40L369 281zm-184 20 6 35L54 480H0zM640 0v3L391 191l2-44L590 0zM0 0l239 176h-60L0 42z"/>
      <path fill="#FFF" d="M241 0v480h160V0zM0 160v160h640V160z"/>
      <path fill="#C8102E" d="M0 193v96h640v-96zM273 0v480h96V0z"/>
    </svg>
  );
}

function FlagDE(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <path fill="#fc0" d="M0 320h640v160H0z"/>
      <path fill="#000001" d="M0 0h640v160H0z"/>
      <path fill="red" d="M0 160h640v160H0z"/>
    </svg>
  );
}

function FlagFR(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <path fill="#fff" d="M0 0h640v480H0z"/>
      <path fill="#000091" d="M0 0h213.3v480H0z"/>
      <path fill="#e1000f" d="M426.7 0H640v480H426.7z"/>
    </svg>
  );
}

function FlagIT(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <g fillRule="evenodd" strokeWidth="1pt">
        <path fill="#fff" d="M0 0h640v480H0z"/>
        <path fill="#009246" d="M0 0h213.3v480H0z"/>
        <path fill="#ce2b37" d="M426.7 0H640v480H426.7z"/>
      </g>
    </svg>
  );
}

const flags: Record<Locale, typeof FlagGB> = { en: FlagGB, de: FlagDE, fr: FlagFR, it: FlagIT };

const localeLabels: Record<Locale, { label: string }> = {
  en: { label: "English" },
  de: { label: "Deutsch" },
  fr: { label: "Français" },
  it: { label: "Italiano" },
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
                  {(() => { const Flag = flags[locale]; return <Flag width={20} height={15} className="block" aria-hidden />; })()}
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
