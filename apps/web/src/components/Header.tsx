"use client";

import Link from "next/link";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useAuth } from "@/components/AuthContext";
import { siteConfig } from "@/content/config";
import { ThemeToggleButton } from "@/components/ThemeToggleButton";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { ThemedImage } from "@/components/ThemedImage";
import { useLocalePath } from "@/lib/useLocalePath";
import AppBar from "@mui/material/AppBar";
import Toolbar from "@mui/material/Toolbar";
import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Box from "@mui/material/Box";
import MenuIcon from "@mui/icons-material/Menu";
import LoginIcon from "@mui/icons-material/Login";

type HeaderProps = {
  onOpenMobileAction: () => void;
};

const navButtonSx = {
  fontSize: "0.875rem",
  fontWeight: 500,
  textTransform: "none",
  px: 1.5,
  py: 0.5,
  borderColor: "transparent",
  color: "text.primary",
  letterSpacing: 0.2,
  minWidth: "auto",
  "&:hover": { borderColor: "transparent", backgroundColor: "transparent", color: "text.secondary" },
  "&:focus-visible": { outline: "none", borderColor: "transparent" },
} as const;

export function Header({ onOpenMobileAction }: HeaderProps) {
  const { isLoggedIn } = useAuth();
  const { t } = useLingui();
  const lp = useLocalePath();

  const authHref = isLoggedIn ? lp(siteConfig.nav.dashboard.href) : lp(siteConfig.nav.login.href);
  const authLabel = isLoggedIn
    ? t({ id: "common.dashboard.action", comment: "Dashboard nav button label", message: "To dashboard" })
    : t({ id: "common.auth.login", comment: "Login button label", message: "Log in" });

  return (
    <AppBar
      position="sticky"
      color="transparent"
      elevation={0}
      sx={{ backdropFilter: "blur(12px)", borderBottom: 1, borderColor: "divider" }}
    >
      <Container maxWidth="lg">
        <Toolbar disableGutters sx={{ minHeight: 72, gap: 2 }}>
          <Box component={Link} href={lp("/")} sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
            <ThemedImage
              lightSrc={siteConfig.logoWide.light}
              darkSrc={siteConfig.logoWide.dark}
              alt="Job Seek"
              width={siteConfig.logoWide.width}
              height={siteConfig.logoWide.height}
              style={{ height: 36, width: "auto" }}
            />
          </Box>

          <Box flexGrow={1} />

          <Stack component="nav" direction="row" spacing={2.5} sx={{ display: { xs: "none", md: "flex" } }}>
            <Button component={Link} href={lp(siteConfig.nav.product.href)} variant="outlined" color="inherit" size="small" disableElevation sx={navButtonSx}>
              <Trans id="common.nav.product" comment="Nav link: Product">Product</Trans>
            </Button>
            <Button component={Link} href={lp(siteConfig.nav.features.href)} variant="outlined" color="inherit" size="small" disableElevation sx={navButtonSx}>
              <Trans id="common.nav.features" comment="Nav link: Features">Features</Trans>
            </Button>
            <Button component={Link} href={lp(siteConfig.nav.pricing.href)} variant="outlined" color="inherit" size="small" disableElevation sx={navButtonSx}>
              <Trans id="common.nav.pricing" comment="Nav link: Pricing">Pricing</Trans>
            </Button>
            <Button component={Link} href={lp(siteConfig.nav.company.href)} variant="outlined" color="inherit" size="small" disableElevation sx={navButtonSx}>
              <Trans id="common.nav.company" comment="Nav link: How do we index jobs?">How do we index jobs?</Trans>
            </Button>
          </Stack>

          <Stack direction="row" spacing={1.5} alignItems="center" sx={{ display: { xs: "none", md: "flex" } }}>
            <LocaleSwitcher />
            <ThemeToggleButton />
            <Button
              component={Link}
              href={authHref}
              variant="contained"
              color="primary"
              size="small"
              startIcon={<LoginIcon fontSize="small" />}
              sx={{ "&:focus-visible": { outline: "none" } }}
            >
              {authLabel}
            </Button>
          </Stack>

          <IconButton
            edge="end"
            onClick={onOpenMobileAction}
            sx={{ display: { xs: "inline-flex", md: "none" } }}
            aria-label={t({ id: "common.header.openMenu", comment: "Aria label for mobile menu button", message: "Open main menu" })}
            disableRipple
            disableFocusRipple
          >
            <MenuIcon fontSize="small" />
          </IconButton>
        </Toolbar>
      </Container>
    </AppBar>
  );
}
