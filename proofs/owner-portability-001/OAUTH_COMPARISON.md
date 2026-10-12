# OAuth/token exchange comparison and earned claim

This comparison describes published protocol capabilities; no OAuth provider,
Poppy implementation, external website or paid API was contacted or tested.
Standards links below are references for reviewers, not evidence of tested
interoperability. The experiment's two local token dictionaries are a toy
site-specific baseline, not a representative OAuth implementation.

| Capability | Existing OAuth-related mechanisms | OWNER-PORTABILITY-001 |
|---|---|---|
| User delegates bounded access to an application | [OAuth 2.0, RFC 6749](https://www.rfc-editor.org/rfc/rfc6749) issues scoped access tokens under the authorization server's policy. A resource server validates the token and enforces its policy. | An owner issues a scoped, signed mandate; each receiver must recognize the owner's root and enforce its own policy. |
| Delegation across services or changing actors | [OAuth Token Exchange, RFC 8693](https://www.rfc-editor.org/rfc/rfc8693) supports exchanging a subject token, optionally an actor token, for another token; resource/audience and scope parameters and actor claims support controlled delegation/impersonation. Support and permitted exchanges depend on the authorization server and trust arrangement. | The owner revokes A and issues a new mandate for B's different public key; public history is carried to locally configured receivers. This is not an implementation or test of token exchange. |
| A replacement agent need not receive its predecessor's credential | A supported authorization flow or token-exchange service can issue a distinct credential for the replacement client/actor. This does not inherently require sharing the predecessor's private key. | B gets its own key and a new owner mandate. The local fixture directly tests that A's key cannot satisfy B's subject binding. |
| Signed tokens verified without a per-request issuer call | [JWT access token profile, RFC 9068](https://www.rfc-editor.org/rfc/rfc9068) enables local validation using a trusted issuer's public keys, with issuer/audience/time/scope checks. Trust and key distribution remain required. | Both receivers validate the same signed owner bundle using a pinned root, then choose whether to accept the proposed arrangement. Offline verification is not unique to this experiment. |
| Proof of possession and replay resistance | [DPoP, RFC 9449](https://www.rfc-editor.org/rfc/rfc9449) binds supported tokens to a proof-of-possession key and defines request proof/replay handling. | The existing ReferenceGate binds a subject signature to an exact action, bundle head and one-use receiver challenge. No claim of DPoP compliance or equivalence of complete security properties. |
| Revocation and current token state | [Token Revocation, RFC 7009](https://www.rfc-editor.org/rfc/rfc7009) and [Token Introspection, RFC 7662](https://www.rfc-editor.org/rfc/rfc7662) provide supported revocation/status mechanisms; propagation and offline-validation policies matter. | Signed revocation history is carried by the owner. A receiver rejects rollback after observing it. An offline receiver cannot discover an undisclosed newer revocation; bundle expiry bounds exposure. |
| Detailed consent/action constraints | [Rich Authorization Requests, RFC 9396](https://www.rfc-editor.org/rfc/rfc9396) represents structured authorization details interpreted according to supported types and server policy. Deployments can add exact consent/terms bindings and audit records. | This fixture defines root-signed preferences, exact site/terms-version/action approval, and explicit withdrawals. These are experiment-specific policy documents, not universal contractual semantics. |
| Portable refusal/audit history | OAuth deployments can maintain/export logs and signed records. The cited token-exchange protocol does not by itself specify this owner's append-only wallet history and refusal schema. | An owner-held event chain, separately signed consent/refusal log, receiver receipts and effect records reconstruct this specific synthetic story. No proof of all events or undisclosed effects. |

OAuth can support cooperating services, shared/federated issuers, multiple
clients, audience-specific tokens and controlled token exchange. The toy result
"A's local token does not work at B" establishes only the deliberately separate
registries used here. It does not establish that OAuth, token exchange, or Poppy
is inherently nonportable, and it establishes no performance or security
advantage over a comparably configured OAuth deployment. Poppy was not examined;
no product-specific comparison is claimed.

## Precise earned claim

In one local offline fixture, one synthetic owner carried the same signed Wallet
authority bundle to two separately keyed and configured receivers. Both verified
it under locally pinned trust. The fixture enforced explicit approval of exact
signed site terms and an exact action; refused advertising-sharing terms,
non-recognition and a declined arrangement without a protected simulated effect;
stopped a revoked agent; continued with a different agent's own key under a new
scoped mandate; and retained sufficient signed evidence for a separate offline
verifier to reconstruct the eight recorded outcomes.

The supplemental review controls exercise each distinct Gate's holder, replay,
revocation and subject-key binding checks. They are authorization controls with
no protected effects, not extra accepted Website B transactions.

## What this claim does not earn

The receivers are separate objects with different signing keys, terms and state,
but the researcher configured and operated both in the same process/environment.
Their cooperation is **simulated cooperation**, not acceptance by an independently
operated website. The offline verifier is independently executable; it is not an
outside organization's endorsement or independent validation of website adoption.

The fixture demonstrates signed evidence and key input separation. It does not
prove operating-system isolation between agents, key secrecy against the shared
controller, legal consent, an existing account's transfer, authentication to a real
website, distributed revocation delivery, durable restart recovery, complete logs,
or superiority/novelty relative to existing OAuth capabilities.

A real website would need to adopt the protocol or an adapter, independently
establish trust in the owner identity, understand the exact approval semantics,
accept compatible terms, enforce its own policy and implement safe effects and
storage. It remains free to decline. Preserved history cannot force recognition,
make unacceptable terms acceptable, or carry a site's permissions to another site
without that site's cooperation.
