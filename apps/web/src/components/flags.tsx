import type { SVGProps } from "react";
import type { Locale } from "@/lib/i18n";

export function FlagGB(props: SVGProps<SVGSVGElement>) {
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

export function FlagDE(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <path fill="#fc0" d="M0 320h640v160H0z"/>
      <path fill="#000001" d="M0 0h640v160H0z"/>
      <path fill="red" d="M0 160h640v160H0z"/>
    </svg>
  );
}

export function FlagFR(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <path fill="#fff" d="M0 0h640v480H0z"/>
      <path fill="#000091" d="M0 0h213.3v480H0z"/>
      <path fill="#e1000f" d="M426.7 0H640v480H426.7z"/>
    </svg>
  );
}

export function FlagIT(props: SVGProps<SVGSVGElement>) {
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

export const flags: Record<Locale, typeof FlagGB> = { en: FlagGB, de: FlagDE, fr: FlagFR, it: FlagIT };

export const localeLabels: Record<Locale, string> = {
  en: "English",
  de: "Deutsch",
  fr: "Français",
  it: "Italiano",
};
