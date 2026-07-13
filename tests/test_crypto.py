import pytest
from cryptography.fernet import InvalidToken

from cluster.crypto import CredentialCipher


def test_encrypt_round_trip_and_uses_random_nonce():
    cipher = CredentialCipher(CredentialCipher.generate_key())

    first = cipher.encrypt("secret")
    second = cipher.encrypt("secret")

    assert first != "secret"
    assert first != second
    assert cipher.decrypt(first) == "secret"
    assert cipher.decrypt(second) == "secret"


def test_wrong_key_cannot_decrypt():
    first = CredentialCipher(CredentialCipher.generate_key())
    second = CredentialCipher(CredentialCipher.generate_key())

    with pytest.raises(InvalidToken):
        second.decrypt(first.encrypt("secret"))
