"""Signing keys, JWK thumbprints and the JWKS document (T-0008)."""

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from app.core.auth.keys import (
    AuthConfigError,
    KeySet,
    generate_signing_key,
    jwk_thumbprint,
    keyset_from_settings,
    load_signing_key,
    load_verification_key,
    private_key_pem,
    public_key_pem,
    signing_key_from_private,
)
from tests.auth_support import make_settings

PRIVATE_MEMBERS = {"d", "p", "q", "dp", "dq", "qi"}


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_rfc7638_thumbprint_example() -> None:
    # The worked example of RFC 7638 §3.1.
    jwk = {
        "kty": "RSA",
        "n": (
            "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6"
            "tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5"
            "v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbIS"
            "D08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-k"
            "EgU8awapJzKnqDKgw"
        ),
        "e": "AQAB",
        "alg": "RS256",
        "kid": "2011-04-29",
    }
    assert jwk_thumbprint(jwk) == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"


@pytest.mark.parametrize(("algorithm", "kty"), [("EdDSA", "OKP"), ("RS256", "RSA")])
def test_generated_key_publishes_public_jwk_only(algorithm: str, kty: str) -> None:
    key = generate_signing_key(algorithm)  # type: ignore[arg-type]
    assert key.algorithm == algorithm
    jwk = key.verification.jwk()
    assert jwk["kty"] == kty
    assert jwk["kid"] == key.kid == jwk_thumbprint(jwk)
    assert jwk["use"] == "sig"
    assert jwk["alg"] == algorithm
    assert not PRIVATE_MEMBERS & set(jwk)


def test_ed25519_jwk_round_trips_to_the_public_key() -> None:
    key = generate_signing_key("EdDSA")
    jwk = key.verification.jwk()
    assert jwk["crv"] == "Ed25519"
    raw = base64.urlsafe_b64decode(jwk["x"] + "==")
    assert raw == key.private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def test_jwk_returns_a_copy() -> None:
    key = generate_signing_key("EdDSA")
    key.verification.jwk()["kid"] = "tampered"
    assert key.verification.jwk()["kid"] == key.kid


def test_keyset_jwks_lists_active_and_retired_keys_once() -> None:
    active = generate_signing_key("EdDSA")
    retired = generate_signing_key("EdDSA").verification
    keys = KeySet(active, (retired, retired, active.verification))
    kids = [k["kid"] for k in keys.jwks()["keys"]]
    assert kids == [active.kid, retired.kid]
    assert keys.get(retired.kid) is retired
    assert keys.get("unknown") is None
    json.dumps(keys.jwks())  # serialisable


def test_pem_round_trip(rsa_key: rsa.RSAPrivateKey) -> None:
    key = signing_key_from_private(rsa_key)
    loaded = load_signing_key(private_key_pem(key))
    assert loaded.kid == key.kid
    assert loaded.algorithm == "RS256"
    assert load_verification_key(public_key_pem(key.verification)).kid == key.kid
    assert load_signing_key(private_key_pem(key).encode()).kid == key.kid
    assert load_verification_key(public_key_pem(key.verification).encode()).kid == key.kid


def test_small_rsa_keys_are_refused() -> None:
    small = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    with pytest.raises(AuthConfigError, match="2048"):
        signing_key_from_private(small)


def test_unsupported_key_types_are_refused() -> None:
    ec_key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(AuthConfigError, match="unsupported"):
        signing_key_from_private(ec_key)
    pem = ec_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(AuthConfigError, match="unsupported"):
        load_verification_key(pem)


def test_unreadable_pems_are_config_errors() -> None:
    with pytest.raises(AuthConfigError, match="private key"):
        load_signing_key("-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n")
    with pytest.raises(AuthConfigError, match="public key"):
        load_verification_key("not a pem")


# --- from settings --------------------------------------------------------------------------


def _pem(key: ed25519.Ed25519PrivateKey | rsa.RSAPrivateKey) -> str:
    return private_key_pem(signing_key_from_private(key))


def test_no_key_outside_production_generates_an_ephemeral_one() -> None:
    keys, ephemeral = keyset_from_settings(make_settings())
    assert ephemeral
    assert keys.signing.algorithm == "EdDSA"
    keys_rs, _ = keyset_from_settings(make_settings(jwt_algorithm="RS256"))
    assert keys_rs.signing.algorithm == "RS256"


def test_no_key_in_production_is_an_error() -> None:
    with pytest.raises(AuthConfigError, match="production"):
        keyset_from_settings(make_settings(environment="production"))


def test_key_from_setting_and_retired_keys() -> None:
    active = ed25519.Ed25519PrivateKey.generate()
    retired = generate_signing_key("EdDSA")
    settings = make_settings(
        environment="production",
        jwt_private_key=_pem(active),
        jwt_retired_public_keys=(public_key_pem(retired.verification),),
    )
    keys, ephemeral = keyset_from_settings(settings)
    assert not ephemeral
    assert keys.signing.kid == signing_key_from_private(active).kid
    assert keys.get(retired.kid) is not None


def test_key_from_file(tmp_path: Path, rsa_key: rsa.RSAPrivateKey) -> None:
    path = tmp_path / "jwt.pem"
    path.write_text(_pem(rsa_key), encoding="utf-8")
    keys, _ = keyset_from_settings(make_settings(jwt_algorithm="RS256", jwt_private_key_file=path))
    assert keys.signing.algorithm == "RS256"


def test_missing_key_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(AuthConfigError, match="FILE"):
        keyset_from_settings(make_settings(jwt_private_key_file=tmp_path / "missing.pem"))


def test_algorithm_must_match_the_key(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(AuthConfigError, match="KERP_JWT_ALGORITHM"):
        keyset_from_settings(make_settings(jwt_algorithm="EdDSA", jwt_private_key=_pem(rsa_key)))


def test_private_key_is_kept_out_of_settings_repr() -> None:
    pem = _pem(ed25519.Ed25519PrivateKey.generate())
    settings = make_settings(jwt_private_key=pem)
    assert "PRIVATE" not in repr(settings)
    assert "PRIVATE" not in str(settings.model_dump())


def test_retired_keys_parse_from_json_env(monkeypatch: pytest.MonkeyPatch) -> None:
    retired = public_key_pem(generate_signing_key("EdDSA").verification)
    monkeypatch.setenv("KERP_JWT_RETIRED_PUBLIC_KEYS", json.dumps([retired]))
    assert make_settings().jwt_retired_public_keys == (retired,)
