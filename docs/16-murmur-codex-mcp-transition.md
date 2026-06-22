# Murmur Codex MCP Transition

This is a documentation-only transition plan. Murmur remains optional for the
Jobseek migration pilots; company onboarding must keep working through `ws`
without a Murmur dependency.

## Goal

Use Murmur as a Codex-accessible MCP surface for workflow context and publisher
actions, while keeping the existing Claude-compatible MCP path available during
the transition.

## Codex Setup

Set `MURMUR_TOKEN` in the shell that launches Codex, then add the streamable
HTTP MCP server:

```bash
codex mcp add murmur --url https://murmur.colophon-group.org/mcp --bearer-token-env-var MURMUR_TOKEN
```

Verify from Codex CLI with `codex mcp list` or from an interactive session with
`/mcp`. Treat initialization failure as a soft failure for migration pilots:
continue with `ws` and record the missing Murmur server in the pilot notes.

## Intended Use

- Keep pipeline definitions in `apps/crawler/murmur/pipelines/*.yaml`.
- Keep local validation and registration through
  `apps/crawler/murmur/README.md` commands until Murmur MCP tooling proves
  parity.
- Use Murmur MCP for live pipeline discovery, publisher checks, and future
  workflow handoff actions once the tool contracts are stable.
- Do not move `ws` state, crawler CSVs, prompts, schema validation, or
  persistence rules into provider-specific MCP code.

## Claude-Compatible Alternate Path

Claude clients may keep using the same Murmur endpoint and `MURMUR_TOKEN`
through their MCP configuration. This is an alternate access path, not a
separate workflow definition. Pipeline YAML, validation rules, and registrar
semantics stay shared.

## Migration Checks

1. Run `pnpm --filter @jobseek/murmur-pipelines validate-pipeline`.
2. Confirm Codex can initialize the `murmur` MCP server.
3. Compare any MCP-reported pipeline metadata with the committed YAML.
4. Register only through the existing registrar or an MCP tool with the same
   idempotent upsert semantics.
5. If Murmur MCP is unavailable or returns mismatched schema information,
   disable the MCP server and continue through the existing local scripts.

## Non-Goals

- Replacing `ws` in this migration.
- Changing crawler runtime behavior.
- Removing the Claude MCP path.
- Making Murmur availability a prerequisite for Codex migration pilots.
