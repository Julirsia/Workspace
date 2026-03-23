## Skills

A skill is a set of local instructions to follow that is stored in a `SKILL.md` file. Below is the list of skills that can be used. Each entry includes a name, description, and file path so you can open the source for full instructions when using a specific skill.

### Available skills

- skill-creator: Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Codex's capabilities with specialized knowledge, workflows, or tool integrations. (file: /Users/julirsia/.codex/skills/.system/skill-creator/SKILL.md)
- skill-installer: Install Codex skills into `$CODEX_HOME/skills` from a curated list or a GitHub repo path. Use when a user asks to list installable skills, install a curated skill, or install a skill from another repo. (file: /Users/julirsia/.codex/skills/.system/skill-installer/SKILL.md)
- upstream-sync-friendly: Design and implement changes so future upstream-to-internal syncs stay low-conflict. Use when a repository is developed externally and later imported into an internal fork or Gitea, or when the user asks to minimize merge conflicts, isolate internal customization, add adapters/hooks/config, avoid broad rewrites, or document internal override points. (file: /Users/julirsia/workspace/skills/upstream-sync-friendly/SKILL.md)

### How to use skills

- Discovery: The list above is the skills available in this workspace. Skill bodies live on disk at the listed paths.
- Trigger rules: If the user names a skill (with `$SkillName` or plain text) or the task clearly matches a skill's description, use that skill for the turn. Multiple mentions mean use the minimal set that covers the task.
- Missing or blocked: If a named skill is unavailable or the path cannot be read, say so briefly and continue with the best fallback.
- Progressive disclosure:
  1. Open the skill's `SKILL.md` first.
  2. Resolve relative paths from the skill directory.
  3. Load only the specific referenced files needed for the task.
  4. Prefer bundled scripts or assets when they exist.
- Context hygiene:
  - Keep skill usage concise.
  - Do not bulk-load references unless needed.
  - Prefer one level of references from `SKILL.md`.
