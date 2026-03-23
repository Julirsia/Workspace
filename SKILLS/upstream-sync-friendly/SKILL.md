---
name: upstream-sync-friendly
description: Design and implement changes so future upstream-to-internal syncs stay low-conflict. Use when a repository is developed externally and later imported into an internal fork or Gitea, or when the user asks to minimize merge conflicts, isolate internal customization, add adapters/hooks/config, avoid broad rewrites, document internal override points, or make the repository easier for smaller follow-up models to navigate through README or AGENTS guidance.
---

# Upstream Sync Friendly

Use this skill to keep feature work easy to import into an internal fork later. Optimize for small diff surface, explicit customization points, and predictable sync boundaries.

## Workflow

1. Map the current code into three zones before editing: shared core, integration wiring, and environment-specific behavior.
2. Decide whether each shared-core edit is unavoidable. Prefer adding a new module or a thin extension point instead of rewriting existing code.
3. Isolate variability behind config, adapters, interfaces, and explicit hooks.
4. Keep internal-only behavior out of shared code. Create or reuse clear override locations instead of hardcoding organization values.
5. Make the change discoverable for smaller follow-up models. Add or update README and/or repo-local AGENTS guidance so a reader can find the right entrypoint and edit zone quickly.
6. Keep diffs narrow. Avoid broad renames, file moves, formatting-only sweeps, and unrelated cleanup.
7. At handoff, report the internal customization points, the unavoidable shared-core edits, any remaining sync risk, and any missing discoverability docs.

## Design Rules

- Minimize edits to existing shared core files.
- Prefer adding modules over rewriting stable files.
- Keep entrypoints thin; push behavior into services, adapters, or helper modules.
- Move environment-specific values into config or dependency injection.
- Put organization-specific behavior behind hooks or provider-specific adapters.
- Separate core logic, wiring, and internal customization points.
- Split work by concern when possible: design surface, implementation, wiring, docs.
- Document which files are intended for internal override.
- Document where smaller follow-up models should read first and which file owns each common task.

## Implementation Patterns

### Config-first

Place model IDs, base URLs, proxy toggles, auth header names, template selectors, feature flags, and workspace-specific values in config. Do not hardcode them in shared logic.

### Adapters and interfaces

Define an interface or protocol first when provider behavior may differ across environments. Keep provider-specific code in dedicated adapters.

### Hooks for internal customization

Add explicit pre/post-processing points when internal logic is expected later.

```python
draft = generate_reply(context)
draft = postprocess_reply_draft(draft, env_config)
```

If the repository already has a pattern for extension points, follow that pattern instead of introducing a new framework.

### Thin wiring

Modify controllers, routes, CLI entrypoints, or app bootstrap code only enough to connect the new module. Avoid embedding business logic there.

### Discoverability docs

When future work may be done by a smaller or weaker follow-up model, make the structure legible in repository-local docs.

- Put the human-readable code map in `README.md`.
- Put model-facing read order, edit boundaries, and task-to-file routing in repo-local `AGENTS.md` when the environment supports it.
- Prefer one short structure map over long prose.
- If the repo already has these docs, update them instead of creating duplicates.

Minimum useful documentation:

- `README.md`: code structure, request flow, and "If you need to change X" mapping
- `AGENTS.md`: read order, safe customization zones, shared-core boundaries, and task-to-file routing

The goal is that a smaller model can answer "where do requests enter?", "where does session behavior live?", and "which file should I edit?" without scanning the whole repo.

## Avoid

- Broad rename or file-move batches
- Formatter-only or lint-only sweeps unrelated to the task
- Mixing refactors, feature work, and cleanup in one change when they can be separated
- Hardcoding internal URLs, auth rules, or workspace identifiers in shared code
- Repeated edits across many shared files when a single extension point would contain the change
- Leaving the code map implicit so follow-up models must reverse-engineer the structure from source
- Scattering customization guidance across comments instead of centralizing it in README and/or AGENTS

## Output Contract

When finishing the task, include:

- Internal customization points
- Unavoidable shared-core edits and why they were necessary
- Remaining upstream-sync risk
- A note about any missing documentation for internal overrides
- Whether README and/or repo-local AGENTS were added or updated to expose entrypoints and edit boundaries

For a reusable user prompt template, read [references/prompt-template.md](references/prompt-template.md).
