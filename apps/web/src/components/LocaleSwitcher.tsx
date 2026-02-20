"use client";

import { useState } from "react";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useLingui } from "@lingui/react/macro";
import { locales, type Locale } from "@/lib/i18n";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import ListItemText from "@mui/material/ListItemText";
import ListItemIcon from "@mui/material/ListItemIcon";
import LanguageIcon from "@mui/icons-material/Language";
import type { IconButtonProps } from "@mui/material/IconButton";

const localeLabels: Record<Locale, { label: string; flag: string }> = {
  en: { label: "English", flag: "/flags/gb.svg" },
  de: { label: "Deutsch", flag: "/flags/de.svg" },
  fr: { label: "Français", flag: "/flags/fr.svg" },
  it: { label: "Italiano", flag: "/flags/it.svg" },
};

type LocaleSwitcherProps = Omit<IconButtonProps, "onClick" | "color">;

export function LocaleSwitcher({ sx, ...iconButtonProps }: LocaleSwitcherProps) {
  const { t } = useLingui();
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams();
  const currentLocale = (params.lang as string) ?? "en";

  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const open = Boolean(anchorEl);

  const label = t({
    id: "common.locale.switch",
    comment: "Aria label for language switcher button",
    message: "Change language",
  });

  function handleSelect(locale: Locale) {
    setAnchorEl(null);
    if (locale === currentLocale) return;
    // Replace the current locale segment in the path
    const newPath = pathname.replace(`/${currentLocale}`, `/${locale}`);
    router.push(newPath);
  }

  return (
    <>
      <Tooltip title={label}>
        <IconButton
          onClick={(e) => setAnchorEl(e.currentTarget)}
          size="small"
          color="inherit"
          aria-label={label}
          aria-haspopup="true"
          aria-expanded={open || undefined}
          sx={sx}
          {...iconButtonProps}
        >
          <LanguageIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={() => setAnchorEl(null)}
        slotProps={{ paper: { sx: { minWidth: 140 } } }}
      >
        {locales.map((locale) => (
          <MenuItem
            key={locale}
            selected={locale === currentLocale}
            onClick={() => handleSelect(locale)}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <img src={localeLabels[locale].flag} alt="" width={20} height={15} style={{ display: "block" }} />
            </ListItemIcon>
            <ListItemText>{localeLabels[locale].label}</ListItemText>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
