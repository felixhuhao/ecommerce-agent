# DeepAgents Skills Decision Record

## 1. Decision

Do not build a skills/workflow slice now.

Keep the current approach:

- mandatory behavior stays in specialist prompts;
- common workflows become shaped first-party tools;
- external capabilities stay behind MCP tools;
- reliability is enforced by tool metadata, middleware limits, routing evals, and
  live smoke tests.

Revisit DeepAgents skills later for optional, reusable workflows that are large
enough to benefit from progressive disclosure.

## 2. Why This Came Up

Recent prompt and tool cleanup raised the question of whether DeepAgents skills
could help with remaining procedure text:

- warehouse schema/query discipline;
- chart artifact guidance;
- avoiding low-level tool fanout.

The initial appeal was real: progressive disclosure keeps the default prompt
small and gives the product a "skills" story.

## 3. What Research Suggests

Mature agent systems tend to separate these concerns:

| Concern | Best Home |
|---|---|
| Mandatory safety and routing policy | system prompt / specialist prompt |
| External system capability | tool or MCP server |
| Deterministic app-owned workflow | first-party shaped tool |
| Optional reusable procedure | skill / playbook / progressive disclosure |
| Correctness regression guard | evals, smoke tests, middleware limits |

DeepAgents and Anthropic-style skills are progressive-disclosure artifacts:

1. skill metadata enters the prompt;
2. the model decides whether to open the skill;
3. full instructions are loaded only when relevant.

That is valuable for optional workflows, but not ideal for mandatory operating
rules.

## 4. Current Runtime Fit

DeepAgents `skills=` is a poor fit for our current mandatory warehouse/chart
procedure:

- `read_file` is excluded for `data-warehouse-analyst` and
  `customer-insights`;
- skills are loaded through the agent backend, not directly from the repo;
- `backend=None` specialists get an empty `StateBackend` unless files are
  supplied per invocation;
- the sales analyst backend points at the sandbox, not the repo;
- mandatory rules would require an extra model-initiated `read_file` call before
  the agent can act reliably.

An app-level "always-on skill" loader could avoid those issues, but it would
mostly be prompt modularization.

## 5. Cost/Benefit

The remaining target text is small:

- warehouse procedure is roughly a dozen lines and has one owner;
- generic chart guidance is only a few duplicated lines;
- specialist-specific chart guidance still belongs near each specialist.

Building a loader, frontmatter parser, directory convention, tests, extraction,
and re-verification would mostly relocate text rather than improve model
behavior.

The larger behavior gains have already come from:

- shaped analytics tools;
- `sales_forecast`;
- `create_chart_spec`;
- better MCP tool descriptions;
- specialist routing boundaries;
- live smoke coverage and tool budgets.

## 6. Final Position

Do not implement skills now.

Keep mandatory guidance inline and concise. If a clause starts growing again,
prefer improving the tool contract before adding a skill layer.

DeepAgents skills remain useful later, but for a different class of work:

- weekly executive report workflow;
- merchandising analysis playbook;
- cohort or retention analysis workflow;
- campaign planning workflow;
- demo/presentation style workflow;
- user- or team-authored optional procedures.

Those are optional enough to benefit from progressive disclosure and large
enough to justify skill machinery.

## 7. Revisit Criteria

Reopen this decision when at least one is true:

- a reusable optional workflow needs more than roughly 30-40 lines of procedure;
- multiple specialists need the same non-mandatory workflow;
- user/team-authored procedures become a product requirement;
- prompt size becomes a measurable latency or quality problem;
- DeepAgents provides a supported skill source that fits our runtime without
  broad `read_file` access or backend seeding.

## 8. No-Op Outcome

No code changes are planned from this record.

The current unimplemented skills design should not become a slice plan. The next
work should stay focused on concrete behavior gaps found by live smoke, manual
testing, and tool-contract audits.

