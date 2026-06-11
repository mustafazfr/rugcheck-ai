"""ADR-047 PRO pass payments: pure on-chain verification, HMAC pass tokens, restore-by-signMessage,
intent/pass DB roundtrips, and the default-OFF config posture. No network anywhere — the verifier is
pure and fed fixture getTransaction payloads."""

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from solscout.core.config import Config
from solscout.core.db import Db
from solscout.web import payments as pay

PAYOUT = "Payout11111111111111111111111111111111111111"
REF = "Ref1111111111111111111111111111111111111111"
BUYER = "Buyer111111111111111111111111111111111111111"
PRICE = pay.lamports(0.1)


def _tx(*, keys=None, pre=None, post=None, err=None, parsed=True):
    """A minimal getTransaction(jsonParsed) result. parsed=False exercises the plain-string
    accountKeys shape (encoding='json') the normalizer must also accept."""
    keys = keys if keys is not None else [BUYER, PAYOUT, REF, "11111111111111111111111111111111"]
    ak = [{"pubkey": k, "signer": i == 0, "writable": i < 2} for i, k in enumerate(keys)] if parsed else keys
    n = len(keys)
    return {
        "meta": {
            "err": err,
            "preBalances": pre if pre is not None else [10 * PRICE, 0] + [0] * (n - 2),
            "postBalances": post if post is not None else [9 * PRICE - 5000, PRICE] + [0] * (n - 2),
        },
        "transaction": {"message": {"accountKeys": ak}},
    }


# — pure verification —

def test_valid_payment_grants_to_fee_payer():
    ok, reason, info = pay.validate_payment_tx(_tx(), reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert ok and reason == "ok"
    assert info["wallet"] == BUYER and info["lamports"] == PRICE


def test_plain_string_account_keys_also_accepted():
    ok, _, info = pay.validate_payment_tx(_tx(parsed=False), reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert ok and info["wallet"] == BUYER


def test_overpayment_is_fine():
    t = _tx(post=[0, 2 * PRICE, 0, 0])
    ok, _, info = pay.validate_payment_tx(t, reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert ok and info["lamports"] == 2 * PRICE


def test_not_on_chain_yet_is_pending_not_failure():
    ok, reason, _ = pay.validate_payment_tx(None, reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert not ok and reason == "not_found"


def test_failed_tx_rejected():
    ok, reason, _ = pay.validate_payment_tx(_tx(err={"InstructionError": [0, "Custom"]}),
                                            reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert not ok and reason == "tx_failed"


def test_missing_reference_rejected():
    # someone else's real payment to us — without OUR intent's reference it redeems nothing
    t = _tx(keys=[BUYER, PAYOUT, "0therRef1111111111111111111111111111111111", "11111111111111111111111111111111"])
    ok, reason, _ = pay.validate_payment_tx(t, reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert not ok and reason == "reference_missing"


def test_wrong_destination_rejected():
    t = _tx(keys=[BUYER, "Att4cker111111111111111111111111111111111111", REF])
    ok, reason, _ = pay.validate_payment_tx(t, reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert not ok and reason == "wrong_destination"


def test_underpaid_rejected():
    t = _tx(post=[0, PRICE // 2, 0, 0])
    ok, reason, info = pay.validate_payment_tx(t, reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert not ok and reason == "underpaid" and info["lamports"] == PRICE // 2


def test_payout_as_fee_payer_is_malformed():
    # delta>=price on the payout account can't be claimed by the payout wallet itself
    t = _tx(keys=[PAYOUT, BUYER, REF], pre=[0, 0, 0], post=[PRICE, 0, 0])
    ok, reason, _ = pay.validate_payment_tx(t, reference=REF, payout=PAYOUT, min_lamports=PRICE)
    assert not ok and reason == "malformed"


def test_lamports_rounding():
    assert pay.lamports(0.1) == 100_000_000
    assert pay.lamports(1) == pay.LAMPORTS_PER_SOL


# — intent + pass tokens —

def test_new_intent_reference_is_a_real_pubkey():
    intent_id, reference = pay.new_intent()
    assert len(intent_id) >= 16
    Pubkey.from_string(reference)  # raises if not valid


def test_pass_token_roundtrip_and_tamper():
    tok = pay.mint_pass_token("s3cret", BUYER)
    assert len(tok) == 64
    assert pay.check_pass_token("s3cret", BUYER, tok)
    assert not pay.check_pass_token("s3cret", BUYER, tok[:-1] + ("0" if tok[-1] != "0" else "1"))
    assert not pay.check_pass_token("s3cret", "OtherWallet", tok)
    assert not pay.check_pass_token("other-secret", BUYER, tok)
    assert not pay.check_pass_token("", BUYER, tok)  # secret missing → never valid


def test_parse_pass_header():
    tok = pay.mint_pass_token("s", BUYER)
    assert pay.parse_pass_header(f"{BUYER}.{tok}") == (BUYER, tok)
    assert pay.parse_pass_header(None) is None
    assert pay.parse_pass_header("garbage") is None
    assert pay.parse_pass_header("w.t") is None  # token must be 64 hex chars
    assert pay.parse_pass_header(f"{BUYER}.{tok}x") is None


# — restore flow (ed25519 via solders) —

def test_restore_signature_verifies():
    kp = Keypair()
    wallet = str(kp.pubkey())
    msg = pay.restore_message(wallet, 1_700_000_000)
    sig_hex = bytes(kp.sign_message(msg)).hex()
    assert pay.verify_wallet_signature(wallet, msg, sig_hex)
    assert not pay.verify_wallet_signature(wallet, pay.restore_message(wallet, 1_700_000_001), sig_hex)
    assert not pay.verify_wallet_signature(str(Keypair().pubkey()), msg, sig_hex)
    assert not pay.verify_wallet_signature(wallet, msg, "zz-not-hex")
    assert not pay.verify_wallet_signature("not-a-pubkey", msg, sig_hex)


# — DB: intents + passes —

@pytest.fixture
async def db():
    async with Db(":memory:") as d:
        yield d


async def test_intent_lifecycle(db):
    await db.create_pay_intent("i1", REF)
    got = await db.get_pay_intent("i1", ttl_s=900)
    assert got["reference"] == REF and got["status"] == "pending"
    assert await db.get_pay_intent("i1", ttl_s=-1) is None  # pending intents expire
    await db.mark_intent_paid("i1", BUYER, "sig1", PRICE)
    paid = await db.get_pay_intent("i1", ttl_s=-1)  # ...but PAID intents never do (they're the receipt)
    assert paid["status"] == "paid" and paid["wallet"] == BUYER and paid["sig"] == "sig1"


async def test_sig_replay_lock(db):
    await db.create_pay_intent("i1", REF)
    await db.mark_intent_paid("i1", BUYER, "sig1", PRICE)
    assert await db.pay_sig_used("sig1")
    assert not await db.pay_sig_used("sig2")


async def test_pass_grant_idempotent(db):
    assert not await db.has_pass(BUYER)
    await db.grant_pass(BUYER, "sig1")
    await db.grant_pass(BUYER, "sig-later")  # second payment can't replace the original receipt
    assert await db.has_pass(BUYER)
    cur = await db._conn.execute("SELECT sig FROM passes WHERE wallet=?", (BUYER,))
    assert (await cur.fetchone())[0] == "sig1"


async def test_prune_keeps_paid_intents(db):
    await db.create_pay_intent("stale", REF)
    await db.create_pay_intent("paid", "Ref2222222222222222222222222222222222222222")
    await db.mark_intent_paid("paid", BUYER, "sig1", PRICE)
    await db.prune_pay_intents(ttl_s=-1)  # everything pending is "stale" at this ttl
    assert await db.get_pay_intent("stale", ttl_s=10**9) is None
    assert (await db.get_pay_intent("paid", ttl_s=-1))["status"] == "paid"


# — config posture (Golden Rule: money ships default-OFF) —

def test_payments_default_off_and_tunable():
    cfg = Config()
    assert cfg.payments.enabled is False
    assert cfg.payments.price_sol == 0.1
    assert cfg.payments.payout_wallet == ""  # comes from env PAYOUT_WALLET per deploy
