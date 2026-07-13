from cryptography.fernet import Fernet


class CredentialCipher:
    def __init__(self, key: str | bytes):
        value = key.encode("ascii") if isinstance(key, str) else key
        self._fernet = Fernet(value)

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()

    def encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
