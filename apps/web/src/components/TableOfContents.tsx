"use client";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import type { SxProps, Theme } from "@mui/material/styles";

export type TocItem = {
  label: string;
  href: string;
  children?: TocItem[];
};

type TableOfContentsProps = {
  title: string;
  ariaLabel: string;
  items: TocItem[];
  sx?: SxProps<Theme>;
};

export function TableOfContents({
  title,
  ariaLabel,
  items,
  sx,
}: TableOfContentsProps) {
  if (!items?.length) {
    return null;
  }

  const renderItems = (entries: TocItem[], depth = 0) =>
    entries.map((item) => (
      <Box key={item.href} sx={{ pl: depth ? depth * 1.5 : 0 }}>
        <ListItemButton
          component="a"
          href={item.href}
          dense
          sx={{
            borderRadius: 1,
            minHeight: 36,
            px: 1.5,
            "&:hover": { backgroundColor: "action.hover" },
          }}
        >
          <ListItemText
            primary={item.label}
            slotProps={{
              primary: {
                sx: {
                  fontSize: "0.9rem",
                  fontWeight: depth ? 500 : 600,
                },
              },
            }}
          />
        </ListItemButton>
        {item.children?.length ? renderItems(item.children, depth + 1) : null}
      </Box>
    ));

  return (
    <Box
      component="nav"
      aria-label={ariaLabel}
      sx={{
        minWidth: 200,
        width: "100%",
        position: { xs: "static", md: "sticky" },
        top: { md: 120 },
        alignSelf: "flex-start",
        ...sx,
      }}
    >
      <Stack spacing={2}>
        <Typography variant="overline" color="text.secondary" letterSpacing={1.5}>
          {title}
        </Typography>
        <List disablePadding dense>
          {renderItems(items)}
        </List>
      </Stack>
    </Box>
  );
}
