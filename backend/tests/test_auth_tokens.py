"""Asymmetric access/refresh JWTs: issue and verify (T-0008, diff §4)."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from app.core.auth.keys import Algorithm, KeySet, generate_signing_key
from app.core.auth.tokens import InvalidTokenError, TokenClaims, TokenService

USER = uuid.uuid4()
TENANT = uuid.uuid4()
SESSION = uuid.uuid4()
IDS = {"user_id": USER, "tenant_id": TENANT, "session_id": SESSION}


def _service(keys: KeySet, **overrides: object) -> TokenService:
    options: dict[str, object] = {
        "issuer": "kerp",
        "audience": "kerp-api",
        "access_ttl": timedelta(minutes=15),
        "refresh_ttl": timedelta(days=14),
    }
    options.update(overrides)
    return TokenService(keys, **options)  # type: ignore[arg-type]


@pytest.fixture(params=["EdDSA", "RS256"])
def keys(request: pytest.FixtureRequest) -> KeySet:
    algorithm: Algorithm = request.param
    return KeySet(generate_signing_key(algorithm))


@pytest.fixture
def service(keys: KeySet) -> TokenService:
    return _service(keys)


def _b64(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_access_token_round_trip(service: TokenService, keys: KeySet) -> None:
    issued = service.issue("access", **IDS)
    claims = service.verify(issued.token, "access")
    assert claims == issued.claims
    assert claims.user_id == USER
    assert claims.tenant_id == TENANT
    assert claims.session_id == SESSION
    assert claims.expires_at - claims.issued_at == timedelta(minutes=15)
    header = jwt.get_unverified_header(issued.token)
    assert header["alg"] == keys.signing.algorithm
    assert header["kid"] == keys.signing.kid
    assert header["typ"] == "at+jwt"


def test_refresh_token_lifetime_and_type(service: TokenService) -> None:
    issued = service.issue("refresh", **IDS)
    claims = service.verify(issued.token, "refresh")
    assert claims.type == "refresh"
    assert claims.expires_at - claims.issued_at == timedelta(days=14)


def test_every_token_has_a_unique_jti(service: TokenService) -> None:
    jtis = {service.issue("access", **IDS).claims.jti for _ in range(20)}
    assert len(jtis) == 20


def test_payload_carries_the_documented_claims(service: TokenService) -> None:
    token = service.issue("access", **IDS).token
    payload = jwt.decode(token, options={"verify_signature": False})
    assert set(payload) == {"iss", "aud", "sub", "tid", "sid", "jti", "type", "iat", "nbf", "exp"}


@pytest.mark.parametrize(("issued", "expected"), [("access", "refresh"), ("refresh", "access")])
def test_wrong_token_type_is_rejected(service: TokenService, issued: str, expected: str) -> None:
    token = service.issue(issued, **IDS).token  # type: ignore[arg-type]
    with pytest.raises(InvalidTokenError, match="type"):
        service.verify(token, expected)  # type: ignore[arg-type]


def test_expired_token_is_rejected(keys: KeySet) -> None:
    past = datetime.now(UTC) - timedelta(minutes=16)
    old = _service(keys, clock=lambda: past).issue("access", **IDS).token
    with pytest.raises(InvalidTokenError):
        _service(keys).verify(old, "access")


def test_token_within_lifetime_from_injected_clock_verifies(keys: KeySet) -> None:
    recent = datetime.now(UTC) - timedelta(minutes=14)
    token = _service(keys, clock=lambda: recent).issue("access", **IDS).token
    assert _service(keys).verify(token, "access").user_id == USER


@pytest.mark.parametrize(("field", "value"), [("issuer", "other"), ("audience", "other-api")])
def test_issuer_and_audience_are_enforced(keys: KeySet, field: str, value: str) -> None:
    token = _service(keys, **{field: value}).issue("access", **IDS).token
    with pytest.raises(InvalidTokenError):
        _service(keys).verify(token, "access")


def test_token_signed_by_an_unknown_key_is_rejected(service: TokenService) -> None:
    stranger = _service(KeySet(generate_signing_key("EdDSA")))
    with pytest.raises(InvalidTokenError, match="unknown signing key"):
        service.verify(stranger.issue("access", **IDS).token, "access")


def test_forged_kid_with_another_key_fails_signature(service: TokenService, keys: KeySet) -> None:
    # An attacker reuses our kid but signs with their own key.
    attacker = generate_signing_key(keys.signing.algorithm)
    payload = jwt.decode(service.issue("access", **IDS).token, options={"verify_signature": False})
    forged = jwt.encode(
        payload,
        attacker.private_key,
        algorithm=attacker.algorithm,
        headers={"kid": keys.signing.kid},
    )
    with pytest.raises(InvalidTokenError):
        service.verify(forged, "access")


def test_alg_none_is_rejected(service: TokenService, keys: KeySet) -> None:
    payload = jwt.decode(service.issue("access", **IDS).token, options={"verify_signature": False})
    unsigned = f"{_b64({'alg': 'none', 'kid': keys.signing.kid})}.{_b64(payload)}."
    with pytest.raises(InvalidTokenError):
        service.verify(unsigned, "access")


def test_hs256_with_the_public_key_as_secret_is_rejected(service: TokenService) -> None:
    # Classic algorithm confusion: HMAC "signed" with the published public key.
    keys = service.keys
    public_pem = keys.signing.private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    payload = jwt.decode(service.issue("access", **IDS).token, options={"verify_signature": False})
    header = _b64({"alg": "HS256", "typ": "JWT", "kid": keys.signing.kid})
    body = _b64(payload)
    import hashlib
    import hmac

    signature = hmac.new(public_pem, f"{header}.{body}".encode(), hashlib.sha256).digest()
    token = f"{header}.{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
    with pytest.raises(InvalidTokenError):
        service.verify(token, "access")


def test_tampered_payload_is_rejected(service: TokenService) -> None:
    header, _, signature = service.issue("access", **IDS).token.split(".")
    other = _service(service.keys).issue("access", **{**IDS, "tenant_id": uuid.uuid4()}).token
    other_body = other.split(".")[1]
    with pytest.raises(InvalidTokenError):
        service.verify(f"{header}.{other_body}.{signature}", "access")


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "eyJhbGciOiJFZERTQSJ9.e30.AA"])
def test_garbage_is_rejected(service: TokenService, token: str) -> None:
    with pytest.raises(InvalidTokenError):
        service.verify(token, "access")


def _sign_raw(keys: KeySet, payload: dict[str, object]) -> str:
    signing = keys.signing
    return jwt.encode(
        payload, signing.private_key, algorithm=signing.algorithm, headers={"kid": signing.kid}
    )


def _base_payload() -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())
    return {
        "iss": "kerp",
        "aud": "kerp-api",
        "sub": str(USER),
        "tid": str(TENANT),
        "sid": str(SESSION),
        "jti": "abc",
        "type": "access",
        "iat": now,
        "nbf": now,
        "exp": now + 60,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"jti": None},
        {"jti": ""},
        {"jti": 5},
        {"tid": None},
        {"tid": "not-a-uuid"},
        {"sid": None},
        {"sub": 12},
        {"type": None},
    ],
)
def test_malformed_custom_claims_are_rejected(
    service: TokenService, keys: KeySet, change: dict[str, object]
) -> None:
    payload = _base_payload()
    for name, value in change.items():
        if value is None:
            payload.pop(name)
        else:
            payload[name] = value
    with pytest.raises(InvalidTokenError):
        service.verify(_sign_raw(keys, payload), "access")


def test_well_formed_raw_payload_verifies(service: TokenService, keys: KeySet) -> None:
    assert service.verify(_sign_raw(keys, _base_payload()), "access").jti == "abc"


@pytest.mark.parametrize("missing", ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"])
def test_registered_claims_are_required(service: TokenService, keys: KeySet, missing: str) -> None:
    payload = _base_payload()
    payload.pop(missing)
    with pytest.raises(InvalidTokenError):
        service.verify(_sign_raw(keys, payload), "access")


def test_token_from_a_retired_key_still_verifies() -> None:
    old = generate_signing_key("EdDSA")
    token = _service(KeySet(old)).issue("access", **IDS).token
    rotated = KeySet(generate_signing_key("EdDSA"), (old.verification,))
    assert _service(rotated).verify(token, "access").user_id == USER


def test_remaining_is_never_negative() -> None:
    now = datetime.now(UTC)
    claims = TokenClaims("access", USER, TENANT, SESSION, "j", now, now + timedelta(seconds=30))
    assert claims.remaining(now) == timedelta(seconds=30)
    assert claims.remaining(now + timedelta(minutes=5)) == timedelta(0)
    assert claims.remaining() <= timedelta(seconds=30)
