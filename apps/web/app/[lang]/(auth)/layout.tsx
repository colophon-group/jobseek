"use client";

import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import Container from "@mui/material/Container";
import { ThemeToggleButton } from "@/components/ThemeToggleButton";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { ThemedImage } from "@/components/ThemedImage";
import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
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
        <Box sx={{ width: 144, height: 36, mb: 3 }}>
          <ThemedImage
            lightSrc="/js_wide_logo_black.svg"
            darkSrc="/js_wide_logo_white.svg"
            alt="Job Seek"
            width={144}
            height={36}
          />
        </Box>
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
