---
description: When and how to use parallel subagents when working in k8s-vollminlab-cluster
---

# Subagent Parallelization

## When to spawn a Plan agent

Spawn a Plan agent before acting when **two or more** of these are true:

- The task touches 3+ files across different namespaces or directories
- You don't know which files need to change
- The task involves risk (ExternalSecrets/ESO, Kyverno policies, Flux bootstrap, RBAC)
- There are sequential dependencies that aren't obvious

**Skip the Plan agent for:** single-file edits, chart version bumps, label fixes, adding one resource to a known namespace — act directly.

## When to spawn an Explore agent

Use an Explore agent when:

- You need to search across multiple directories and aren't sure what you'll find
- A cross-namespace audit requires reading many files simultaneously
- The search is open-ended (e.g., "what version is X deployed at?")

Use `Grep`/`Glob`/`Read` directly when:

- You know the file path or can find it with a single targeted search
- You've already seen the file in this session

## When to parallelize

Parallelize when work is genuinely independent:

- Cross-namespace audits → one agent per namespace simultaneously
- Reading N files before editing → spawn all reads in parallel
- Independent concerns in one PR → parallel reads, then sequential edits

Never parallelize when step B depends on step A (e.g., save a 1Password item → then reference it from an ExternalSecret).

## Subagent types

| Task                                    | Agent type        |
| --------------------------------------- | ----------------- |
| Find files, search code (open-ended)    | `Explore`         |
| Plan a multi-step implementation        | `Plan`            |
| Research chart versions, external docs  | `general-purpose` |

## Example: auditing all HelmRelease chart versions

Spawn one Explore agent per namespace (`mediastack`, `shlink`, `dmz`, `cert-manager`, etc.) simultaneously, each reading `helmrelease.yaml` and returning the chart name + version. Collect results, then act.
