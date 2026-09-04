# OpenLine Vision

**Status: unproven architectural direction.**

This document describes where the OpenLine architecture could lead. It is not
a list of claims earned by the experiments in this repository.

The demonstrated result in `PLATFORM-EXIT-LIVE-001` is much narrower: one owner,
one continuously running localhost Receiver Gate, a real Claude host before
revocation, and a real Codex host after the successor mandate. See the experiment
documents and frozen evidence for the exact boundaries.

## Direction

OpenLine could become a shared public agreement for how people and their machine
representatives express and verify authority.

The transport does not need to belong to OpenLine. Agents could communicate over
MCP, HTTP, queues, local IPC, or protocols that do not exist yet. The useful
interoperability layer is the answer to a different set of questions:

- Who is this machine acting for?
- What action did the principal authorize?
- What evidence and history support that authority?
- Is the authority still current?
- What receipt records what the receiver actually accepted or refused?

In that architecture, a Wallet is a user-owned coordination root rather than a
required standalone app. Its interface could be a dashboard widget, an IDE
panel, another wallet, a mobile application, or no dedicated interface at all.

The public part would be the verification rules and schemas. Keys, private
history, mandates, and evidence would not need to be globally public. Receivers
would keep the final authority to decide whether a presented proof earns a local
effect.

If independent implementations eventually converge on those rules, models and
providers could become replaceable machine representatives while the human or
organization remains the stable principal.

## What has not been demonstrated

This repository has not demonstrated:

- multi-party federation;
- public-network discovery or routing;
- trust between previously unrelated principals;
- institutional trust elimination;
- cross-machine revocation delivery;
- production key custody;
- economic or network effects from protocol adoption.

Those are future hypotheses. They should receive their own falsifiers before
appearing elsewhere as demonstrated OpenLine behavior.

The operating discipline is simple: **Vision may suggest the next experiment.
Only evidence moves a statement out of Vision.**
