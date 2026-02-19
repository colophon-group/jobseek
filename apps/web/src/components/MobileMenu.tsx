"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useAuth } from "@/components/AuthContext";
import { siteConfig } from "@/content/config";
import { ThemeToggleButton } from "@/components/ThemeToggleButton";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { ThemedImage } from "@/components/ThemedImage";
import { useLocalePath } from "@/lib/useLocalePath";
import Drawer from "@mui/material/Drawer";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import Divider from "@mui/material/Divider";
import Button from "@mui/material/Button";

const CloseIcon = dynamic(() => import("@mui/icons-material/Close"), { ssr: false });

type MobileMenuProps = {
  open: boolean;
  onCloseAction: () => void;
};

export function MobileMenu({ open, onCloseAction }: MobileMenuProps) {
  const { isLoggedIn } = useAuth();
  const { t } = useLingui();
  const lp = useLocalePath();

  const authHref = isLoggedIn ? lp(siteConfig.nav.dashboard.href) : siteConfig.nav.login.href;
  const authLabel = isLoggedIn
    ? t({ id: "common.dashboard.action", comment: "Dashboard nav button label", message: "To dashboard" })
    : t({ id: "common.auth.login", comment: "Login button label", message: "Log in" });

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onCloseAction}
      slotProps={{ paper: { sx: { width: 320 } } }}
    >
      <Box sx={{ px: 2.5, py: 3 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
          <Box
            component={Link}
            href={lp("/")}
            onClick={onCloseAction}
            sx={{ display: "inline-flex", alignItems: "center", gap: 1, textDecoration: "none" }}
          >
            <ThemedImage
              darkSrc={siteConfig.logoWide.dark}
              lightSrc={siteConfig.logoWide.light}
              alt="Job Seek"
              width={siteConfig.logoWide.width}
              height={siteConfig.logoWide.height}
              style={{ height: 32, width: "auto" }}
            />
          </Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <LocaleSwitcher />
            <ThemeToggleButton />
            <IconButton
              onClick={onCloseAction}
              aria-label={t({ id: "common.mobileMenu.close", comment: "Aria label for close mobile menu button", message: "Close menu" })}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          </Stack>
        </Stack>

        <List sx={{ mt: 2 }}>
          <ListItemButton component={Link} href={lp(siteConfig.nav.product.href)} onClick={onCloseAction}>
            <ListItemText primary={<Trans id="common.nav.product" comment="Nav link: Product">Product</Trans>} />
          </ListItemButton>
          <ListItemButton component={Link} href={lp(siteConfig.nav.features.href)} onClick={onCloseAction}>
            <ListItemText primary={<Trans id="common.nav.features" comment="Nav link: Features">Features</Trans>} />
          </ListItemButton>
          <ListItemButton component={Link} href={lp(siteConfig.nav.pricing.href)} onClick={onCloseAction}>
            <ListItemText primary={<Trans id="common.nav.pricing" comment="Nav link: Pricing">Pricing</Trans>} />
          </ListItemButton>
          <ListItemButton component={Link} href={lp(siteConfig.nav.company.href)} onClick={onCloseAction}>
            <ListItemText primary={<Trans id="common.nav.company" comment="Nav link: How do we index jobs?">How do we index jobs?</Trans>} />
          </ListItemButton>
        </List>

        <Divider sx={{ my: 2 }} />

        <Button href={authHref} fullWidth variant="outlined" size="large" onClick={onCloseAction}>
          {authLabel}
        </Button>
      </Box>
    </Drawer>
  );
}
