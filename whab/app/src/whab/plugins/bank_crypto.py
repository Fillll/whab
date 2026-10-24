"""Host token cryptography, retaining existing Item-token wire compatibility."""

import base64
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .sdk import BankError


class BankCipher:
    def __init__(self, key_hex):
        if type(key_hex) is not str or re.fullmatch(r'[a-fA-F0-9]{64}', key_hex) is None:
            raise BankError('Bank encryption key missing. Check local configuration.')
        self._aes = AESGCM(bytes.fromhex(key_hex))

    def encrypt_token(self, token, item_identity):
        if type(token) is not str or not 1 <= len(token) <= 4096:
            raise BankError('Bank provider returned an invalid connection token.')
        try:
            secret, aad = token.encode('utf-8'), item_identity.encode('utf-8')
        except (AttributeError, UnicodeError):
            raise BankError('Bank provider returned an invalid connection token.') from None
        nonce = os.urandom(12)
        raw = self._aes.encrypt(nonce, secret, aad)
        return '.'.join(base64.b64encode(part).decode('ascii') for part in (nonce, raw[-16:], raw[:-16]))

    def decrypt_token(self, encoded, item_identity):
        try:
            nonce, tag, raw = [base64.b64decode(part, validate=True) for part in encoded.split('.')]
            if len(nonce) != 12 or len(tag) != 16:
                raise ValueError()
            token = self._aes.decrypt(nonce, raw + tag, item_identity.encode('utf-8')).decode('utf-8')
            if not 1 <= len(token) <= 4096:
                raise ValueError()
            return token
        except Exception:
            raise BankError('Stored bank connection could not be unlocked. Check the local encryption key.') from None

    def seal(self, raw, aad):
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, raw, b'whab-bank-continuation-v2\0' + aad)

    def open(self, raw, aad):
        return self._aes.decrypt(raw[:12], raw[12:], b'whab-bank-continuation-v2\0' + aad)
