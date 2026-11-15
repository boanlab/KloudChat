"""Web Push encryption matches RFC 8291 Appendix A; VAPID JWT per RFC 8292."""

import jwt
from cryptography.hazmat.primitives import serialization

from app.services import webpush_crypto as wc

# RFC 8291 Appendix A
PLAINTEXT = wc.b64u_decode("V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24")
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
AS_PUBLIC = (
    "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
)
UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
UA_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
MESSAGE = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmY"
    "WAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgS"
    "xsj_Qulcy4a-fN"
)


def test_the_rfc_vector_encrypts_to_the_published_message():
    body = wc.encrypt(
        PLAINTEXT,
        UA_PUBLIC,
        AUTH,
        _as_private=wc.private_key_from_raw(wc.b64u_decode(AS_PRIVATE)),
        _salt=wc.b64u_decode(SALT),
    )
    assert wc.b64u_encode(body) == MESSAGE


def test_the_vector_keys_agree():
    assert wc.public_from_private(AS_PRIVATE) == AS_PUBLIC
    assert wc.public_from_private(UA_PRIVATE) == UA_PUBLIC


def test_the_published_message_decrypts_with_the_user_agent_key():
    ua = wc.private_key_from_raw(wc.b64u_decode(UA_PRIVATE))
    assert wc.decrypt(wc.b64u_decode(MESSAGE), ua, AUTH) == PLAINTEXT


def test_a_fresh_message_round_trips_and_differs_each_time():
    priv, pub = wc.generate_keypair()
    ua = wc.private_key_from_raw(wc.b64u_decode(priv))
    a = wc.encrypt(b'{"title":"t"}', pub, AUTH)
    b = wc.encrypt(b'{"title":"t"}', pub, AUTH)
    assert a != b
    assert wc.decrypt(a, ua, AUTH) == b'{"title":"t"}'


def test_an_oversized_payload_is_refused():
    try:
        wc.encrypt(b"x" * (wc.MAX_PLAINTEXT + 1), UA_PUBLIC, AUTH)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_the_vapid_jwt_verifies_as_es256_for_the_push_origin():
    priv, pub = wc.generate_keypair()
    endpoint = "https://fcm.googleapis.com/fcm/send/abc123"
    token = wc.vapid_jwt(endpoint, priv, "mailto:admin@example.com", now=1_000_000)
    key = wc.public_key_from_raw(wc.b64u_decode(pub)).public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = jwt.decode(
        token,
        key,
        algorithms=["ES256"],
        audience="https://fcm.googleapis.com",
        options={"verify_exp": False},
    )
    assert claims["sub"] == "mailto:admin@example.com"
    assert claims["exp"] - 1_000_000 <= 24 * 3600
    assert jwt.get_unverified_header(token)["typ"] == "JWT"
    # raw r||s, not DER
    assert len(wc.b64u_decode(token.split(".")[2])) == 64


def test_the_vapid_header_carries_token_and_key():
    priv, pub = wc.generate_keypair()
    header = wc.vapid_header("https://push.example/x", priv, pub, "mailto:a@b.c")
    assert header.startswith("vapid t=") and header.endswith(f", k={pub}")
