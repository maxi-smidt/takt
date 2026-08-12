from __future__ import annotations

import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class JobSecretError(RuntimeError):
    pass


class JobSecretCipher:
    KEY_SIZE = 32
    NONCE_SIZE = 12

    def __init__(self, key_path: Path, *, create: bool) -> None:
        self.key_path = key_path
        if key_path.exists():
            key = key_path.read_bytes()
            key_path.chmod(0o600)
        elif create:
            key = AESGCM.generate_key(bit_length=256)
            self._write_key(key)
        else:
            raise JobSecretError(
                f"Registry job-secret key is missing: {key_path}. Restore it with registry.db."
            )
        if len(key) != self.KEY_SIZE:
            raise JobSecretError(f"Registry job-secret key is invalid: {key_path}.")
        self._cipher = AESGCM(key)

    def encrypt(self, value: str, *, associated_data: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(self.NONCE_SIZE)
        return nonce, self._cipher.encrypt(nonce, value.encode("utf-8"), associated_data)

    def decrypt(self, nonce: bytes, ciphertext: bytes, *, associated_data: bytes) -> str:
        try:
            return self._cipher.decrypt(nonce, ciphertext, associated_data).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError) as error:
            raise JobSecretError("Stored job secret cannot be decrypted.") from error

    def _write_key(self, key: bytes) -> None:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(self.key_path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            self.key_path.unlink(missing_ok=True)
            raise
        directory = os.open(self.key_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
