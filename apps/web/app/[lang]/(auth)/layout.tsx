"use client";

import { useState, useEffect } from "react";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import Container from "@mui/material/Container";
import { useTheme } from "@mui/material/styles";
import { ThemeToggleButton } from "@/components/ThemeToggleButton";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  // Defer rendering to client to avoid hydration mismatch when the theme
  // changes between SSR (Router Cache may serve stale payload) and client.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) return null;

  return (
    <Container maxWidth="sm">
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          py: 4,
        }}
      >
        <Box
          component="img"
          src={isDark ? "/js_wide_logo_white.svg" : "/js_wide_logo_black.svg"}
          alt="Job Seek"
          sx={{ width: 144, height: 36, mb: 3 }}
        />
        <Card
          sx={{
            width: "100%",
            p: { xs: 3, sm: 4 },
          }}
        >
          {children}
        </Card>

        {/* Theme + locale switchers */}
        <Box sx={{ display: "flex", gap: 0.5, mt: 2 }}>
          <ThemeToggleButton />
          <LocaleSwitcher />
        </Box>
      </Box>
    </Container>
  );
}
