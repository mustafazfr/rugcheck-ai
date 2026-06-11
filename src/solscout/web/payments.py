"""PRO pass payments (ADR-047) — pure verification + token helpers. NON-CUSTODIAL by design.

The flow this module verifies:
  1. server mints an intent: a fresh random *reference* pubkey (the keypair's private half is
     DISCARDED immediately — it never signs anything, it's just an unforgeable tracking tag)
  2. the buyer's wallet signs ONE SystemProgram transfer  buyer → payout_wallet  with that
     reference attached as a read-only account key
  3. we fetch the broadcast transaction from the chain and check, from balances (not from
     instruction shapes, which wallets love to decorate): tx succeeded, the reference is in it,
     the payout wallet gained >= price. The fee payer (first signer) is who earns the pass.

Why the reference makes this safe: an attacker can only redeem a transaction containing *their
own intent's* reference — other people's on-chain payments carry other references, and the
`sig UNIQUE` column in pay_intents makes every transaction single-redeem anyway.

Everything here is pure (dict in → verdict out) so it's fully unit-testable from fixture RPC
payloads. No private key ever exists server-side; the only secret is the HMAC pass-token key.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature

LAMPORTS_PER_SOL = 1_000_000_000
# restore-flow messages must be fresh — an old signed message found in a log can't mint a token forever
RESTORE_MAX_SKEW_S = 600


def lamports(price_sol: float) -> int:
    return int(round(price_sol * LAMPORTS_PER_SOL))


def new_intent() -> tuple[str, str]:
    """(intent_id, reference_pubkey). The reference keypair's secret half is dropped on the floor
    here, deliberately — nothing ever needs to sign with it."""
    return pysecrets.token_urlsafe(16), str(Keypair().pubkey())


def _account_keys(tx: dict) -> list[str]:
    """Normalized account-key pubkeys. jsonParsed gives [{'pubkey': ..}], plain json gives [str]."""
    msg = ((tx.get("transaction") or {}).get("message")) or {}
    out = []
    for k in msg.get("accountKeys") or []:
        out.append(k.get("pubkey") if isinstance(k, dict) else k)
    return [k for k in out if isinstance(k, str)]


def validate_payment_tx(
    tx: dict | None, *, reference: str, payout: str, min_lamports: int
) -> tuple[bool, str, dict]:
    """(ok, reason, info) for a getTransaction result. info = {'wallet','lamports'} when ok.

    Balance-delta verification: `postBalances[payout] - preBalances[payout] >= min_lamports`.
    This is robust against however the wallet wrapped the transfer (compute-budget ixs, memo,
    multisig fee payer...) — the chain's own accounting is the truth, not the instruction list.
    """
    if not tx:
        return False, "not_found", {}
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return False, "tx_failed", {}
    keys = _account_keys(tx)
    if not keys:
        return False, "malformed", {}
    if reference not in keys:
        return False, "reference_missing", {}
    if payout not in keys:
        return False, "wrong_destination", {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    i = keys.index(payout)
    if i >= len(pre) or i >= len(post):
        return False, "malformed", {}
    delta = int(post[i]) - int(pre[i])
    if delta < min_lamports:
        return False, "underpaid", {"lamports": delta}
    payer = keys[0]  # index 0 is always the fee payer / first signer on Solana
    if payer in (payout, reference):
        return False, "malformed", {}
    return True, "ok", {"wallet": payer, "lamports": delta}


# --- pass tokens: HMAC(secret, wallet) — self-validating, nothing to store per request ---
def mint_pass_token(secret: str, wallet: str) -> str:
    return hmac.new(secret.encode(), f"pass:{wallet}".encode(), hashlib.sha256).hexdigest()


def check_pass_token(secret: str, wallet: str, token: str) -> bool:
    if not (secret and wallet and token):
        return False
    return hmac.compare_digest(mint_pass_token(secret, wallet), token)


def parse_pass_header(value: str | None) -> tuple[str, str] | None:
    """`X-Pass: <wallet>.<token>` → (wallet, token); None for anything malformed."""
    if not value or "." not in value:
        return None
    wallet, _, token = value.partition(".")
    if not (30 <= len(wallet) <= 50 and len(token) == 64):
        return None
    return wallet, token


# --- restore flow: prove wallet ownership with a signMessage (ed25519), re-issue the token ---
def restore_message(wallet: str, ts: int) -> bytes:
    return f"rugcheck-pass:{wallet}:{ts}".encode()


def verify_wallet_signature(wallet: str, message: bytes, signature_hex: str) -> bool:
    """ed25519 check via solders. Hex signature (64 bytes) — the frontend hex-encodes the
    Uint8Array from `provider.signMessage` so neither side needs a base58 codec."""
    try:
        pub = Pubkey.from_string(wallet)
        sig = Signature.from_bytes(bytes.fromhex(signature_hex))
    except (ValueError, TypeError):
        return False
    return sig.verify(pub, message)
