# Prompt Template

Use this template when you want Codex to implement a feature while preserving low-friction upstream sync into an internal fork.

## Copy-paste template

```text
This repository is developed externally and later imported into an internal Gitea environment.

Architectural constraint:
- Future upstream-to-internal sync cost must stay low.
- Internal follow-up work will be small customizations and bug fixes only.
- Design this change to minimize future merge conflicts.

Required rules:
1. Minimize edits to existing shared core files.
2. Prefer adding new modules over rewriting stable code.
3. Isolate environment-specific behavior behind config, adapters, interfaces, and hooks.
4. Create explicit internal customization points.
5. Do not hardcode organization-specific values in shared code.
6. Avoid broad renames, file moves, and formatting-only diffs.
7. Keep the change narrowly scoped by concern.
8. Document files intended for internal override.
9. Make the repository easy for smaller follow-up models to navigate.
10. Add or update `README.md` and/or repo-local `AGENTS.md` so the code structure, request flow, read order, and task-to-file mapping are explicit.
11. When an existing shared file must change, explain why that edit is unavoidable.

Deliverables:
- Conflict-resistant implementation
- Clear internal override points
- Minimal diff surface
- Brief note listing customization points, unavoidable core edits, and any README/AGENTS updates
```

## Review checklist

- Are new behaviors isolated in new modules where possible?
- Are internal-only values kept in config or dedicated adapters?
- Does the change introduce at least one clear override point when future customization is likely?
- Are shared-core edits small and justified?
- Did the change avoid rename, move, and formatting noise?
- Can a smaller follow-up model find the right file from README and/or AGENTS without scanning the whole repo?
