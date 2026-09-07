# Pilot operator guide

This is the first market-learning loop for Airlock v0.4.0. Keep the product and its proof boundaries stable. The aim is an external user completing real work, not another internal benchmark or a new policy engine.

## Intake and qualification

Review new pilot applications. Prefer a maintainer who owns a real repository, can run a supported agent, and has a small task with a clear acceptance condition. Do not recruit users who need production deployment, secret access, or an unbounded autonomous agent as their first experience.

Before a pilot starts, agree on the repository, starting commit, task, existing checks, any additional maintainer constraints, a maximum number of attempts, an approximate provider-spend ceiling, and a stop condition. Explain that a CLI budget hint does not guarantee a billing cap. The participant controls their credentials and environment.

Use the existing CLI. Do not bypass Starter Rules, rewrite tests to make a candidate pass, or promote a patch because an LLM says it looks good. A missing acceptance constraint is a finding, not a reason to weaken the gate.

## Record the outcome

Keep one internal record per pilot, with an anonymous identifier if the participant has not consented to attribution. The minimum fields are:

- Repository and starting commit, or a private identifier.
- Task, frozen acceptance conditions, and actual commands.
- Airlock version, agent/provider, attempts, elapsed time, and reported cost (unknown when unavailable).
- Baseline status, survivor count, final disposition, and human review time.
- The participant's own answer to whether they would use it again and what they would pay for, if anything.
- A concrete product blocker or requested capability, linked to evidence.

Never publish a participant's name, repository, quote, or commercial details without permission. Do not collect tokens or raw private source in the public intake. Keep private pilot notes out of the repository.

## Decision rules

The first milestone is three qualified external pilots, with at least one complete run on a real task. Count kept improvements separately from attempts and survivor candidates. Report failures and unknown costs without filling in guesses.

A product change is earned when a participant encounters a reproducible blocker that prevents the intended workflow and the existing controls cannot solve it. Reproduce the smallest case, preserve the failing receipt, and fix only that boundary. One complaint is evidence to investigate, not evidence of a market.

Do not claim economic parity from SEARCH-004. Its frozen result showed unattended work at 83.91% of the guided economic yield against the preregistered 85% threshold. The supported claim is that unattended search removed task-picking while finding four improvements; guided Hermes was cheaper. New customer data must be reported separately.

After the pilots, choose one commercial hypothesis to test: hosted execution and evidence retention, managed receiver infrastructure for consequential actions, or another service demanded by participants. Ask about willingness to pay and an actual buying process before publishing pricing or building billing.

## Wallet boundary

PROVIDER-EFFECT-001 is a controlled GitHub transport proof. A live disposable GitHub merge remains unproved. Use the existing `github_effect_live` runner for that separate experiment, with an explicitly selected disposable repository, a short-lived repository-scoped credential, and private state outside public evidence. Do not run it against Airlock main or a participant's production repository.

The live experiment must preserve actual GitHub observations and uncertain results. Its held-acknowledgement test cannot establish cancellation of GitHub's internal queue or downstream Actions. No universal closure, exact-base CAS, or production-recovery claim is earned by a successful local run.

The historical Wallet source-provenance limitation also remains recorded. Do not rewrite old receipts or silently strengthen their source identity.

## Distribution

Share the public pilot link with a small number of developers who already have the problem. Use direct, specific invitations rather than mass tagging or automated outreach. A public issue is an application, not a customer. Record actual responses, completed runs, and retained usage separately from views, likes, and signups.
