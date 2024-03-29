
"""
Implements auth methods
"""
from .err import OperationalError
from .util import byte2int, int2byte

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    _have_cryptography = True
except ImportError:
    _have_cryptography = False

from functools import partial
import hashlib
import io
import struct
import warnings


DEBUG = False
SCRAMBLE_LENGTH = 20
sha1_new = partial(hashlib.new, "sha1")


# mysql_native_password
# https://dev.mysql.com/doc/internals/en/secure-password-authentication.html#packet-Authentication::Native41


def scramble_native_password(password, message):
    """Scramble used for mysql_native_password"""
    if not password:
        return b""

    stage1 = sha1_new(password).digest()
    stage2 = sha1_new(stage1).digest()
    s = sha1_new()
    s.update(message[:SCRAMBLE_LENGTH])
    s.update(stage2)
    result = s.digest()
    return _my_crypt(result, stage1)


def _my_crypt(message1, message2):
    result = bytearray(message1)

    for i in range(len(result)):
        result[i] ^= message2[i]

    return bytes(result)


# old_passwords support ported from libmysql/password.c
# https://dev.mysql.com/doc/internals/en/old-password-authentication.html

SCRAMBLE_LENGTH_323 = 8


class RandStruct_323:
    def __init__(self, seed1, seed2):
        self.max_value = 0x3FFFFFFF
        self.seed1 = seed1 % self.max_value
        self.seed2 = seed2 % self.max_value

    def my_rnd(self):
        self.seed1 = (self.seed1 * 3 + self.seed2) % self.max_value
        self.seed2 = (self.seed1 + self.seed2 + 33) % self.max_value
        return float(self.seed1) / float(self.max_value)


def scramble_old_password(password, message):
    """Scramble for old_password"""
    warnings.warn(
        "old password (for MySQL <4.1) is used.  Upgrade your password with newer auth method.\n"
        "old password support will be removed in future PyMySQL version"
    )
    hash_pass = _hash_password_323(password)
    hash_message = _hash_password_323(message[:SCRAMBLE_LENGTH_323])
    hash_pass_n = struct.unpack(">LL", hash_pass)
    hash_message_n = struct.unpack(">LL", hash_message)

    rand_st = RandStruct_323(
        hash_pass_n[0] ^ hash_message_n[0], hash_pass_n[1] ^ hash_message_n[1]
    )
    outbuf = io.BytesIO()
    for _ in range(min(SCRAMBLE_LENGTH_323, len(message))):
        outbuf.write(int2byte(int(rand_st.my_rnd() * 31) + 64))
    extra = int2byte(int(rand_st.my_rnd() * 31))
    out = outbuf.getvalue()
    outbuf = io.BytesIO()
    for c in out:
        outbuf.write(int2byte(byte2int(c) ^ byte2int(extra)))
    return outbuf.getvalue()


def _hash_password_323(password):
    nr = 1345345333
    add = 7
    nr2 = 0x12345671

    # x in py3 is numbers, p27 is chars
    for c in [byte2int(x) for x in password if x not in (" ", "\t", 32, 9)]:
        nr ^= (((nr & 63) + add) * c) + (nr << 8) & 0xFFFFFFFF
        nr2 = (nr2 + ((nr2 << 8) ^ nr)) & 0xFFFFFFFF
        add = (add + c) & 0xFFFFFFFF

    r1 = nr & ((1 << 31) - 1)  # kill sign bits
    r2 = nr2 & ((1 << 31) - 1)
    return struct.pack(">LL", r1, r2)


# MariaDB's client_ed25519-plugin
# https://mariadb.com/kb/en/library/connection/#client_ed25519-plugin

_nacl_bindings = False


def _init_nacl():
    global _nacl_bindings
    try:
        from nacl import bindings

        _nacl_bindings = bindings
    except ImportError:
        raise RuntimeError(
            "'pynacl' package is required for ed25519_password auth method"
        )


def _scalar_clamp(s32):
    ba = bytearray(s32)
    ba0 = bytes(bytearray([ba[0] & 248]))
    ba31 = bytes(bytearray([(ba[31] & 127) | 64]))
    return ba0 + bytes(s32[1:31]) + ba31


def ed25519_password(password, scramble):
    """Sign a random scramble with elliptic curve Ed25519.

    Secret and public key are derived from password.
    """
    # variable names based on rfc8032 section-5.1.6
    #
    if not _nacl_bindings:
        _init_nacl()

    # h = SHA512(password)
    h = hashlib.sha512(password).digest()

    # s = prune(first_half(h))
    s = _scalar_clamp(h[:32])

    # r = SHA512(second_half(h) || M)
    r = hashlib.sha512(h[32:] + scramble).digest()

    # R = encoded point [r]B
    r = _nacl_bindings.crypto_core_ed25519_scalar_reduce(r)
    R = _nacl_bindings.crypto_scalarmult_ed25519_base_noclamp(r)

    # A = encoded point [s]B
    A = _nacl_bindings.crypto_scalarmult_ed25519_base_noclamp(s)

    # k = SHA512(R || A || M)
    k = hashlib.sha512(R + A + scramble).digest()

    # S = (k * s + r) mod L
    k = _nacl_bindings.crypto_core_ed25519_scalar_reduce(k)
    ks = _nacl_bindings.crypto_core_ed25519_scalar_mul(k, s)
    S = _nacl_bindings.crypto_core_ed25519_scalar_add(ks, r)

    # signature = R || S
    return R + S


# sha256_password


def _roundtrip(conn, send_data):
    conn.write_packet(send_data)
    pkt = conn._read_packet()
    pkt.check_error()
    return pkt


def _xor_password(password, salt):
    # Trailing NUL character will be added in Auth Switch Request.
    # See https://github.com/mysql/mysql-server/blob/7d10c82196c8e45554f27c00681474a9fb86d137/sql/auth/sha2_password.cc#L939-L945
    salt = salt[:SCRAMBLE_LENGTH]
    password_bytes = bytearray(password)
    # salt = bytearray(salt)  # for PY2 compat.
    salt_len = len(salt)
    for i in range(len(password_bytes)):
        password_bytes[i] ^= salt[i % salt_len]
    return bytes(password_bytes)


def sha2_rsa_encrypt(password, salt, public_key):
    """Encrypt password with salt and public_key.

    Used for sha256_password and caching_sha2_password.
    """
    if not _have_cryptography:
        raise RuntimeError(
            "'cryptography' package is required for sha256_password or caching_sha2_password auth methods"
        )
    message = _xor_password(password + b"\0", salt)
    rsa_key = serialization.load_pem_public_key(public_key, default_backend())
    return rsa_key.encrypt(
        message,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None,
        ),
    )


def sha256_password_auth(conn, pkt):
    if conn._secure:
        if DEBUG:
            print("sha256: Sending plain password")
        data = conn.password + b"\0"
        return _roundtrip(conn, data)

    if pkt.is_auth_switch_request():
        conn.salt = pkt.read_all()
        if not conn.server_public_key and conn.password:
            # Request server public key
            if DEBUG:
                print("sha256: Requesting server public key")
            pkt = _roundtrip(conn, b"\1")

    if pkt.is_extra_auth_data():
        conn.server_public_key = pkt._data[1:]
        if DEBUG:
            print("Received public key:\n", conn.server_public_key.decode("ascii"))

    if conn.password:
        if not conn.server_public_key:
            raise OperationalError("Couldn't receive server's public key")

        data = sha2_rsa_encrypt(conn.password, conn.salt, conn.server_public_key)
    else:
        data = b""

    return _roundtrip(conn, data)


def scramble_caching_sha2(password, nonce):
    # (bytes, bytes) -> bytes
    """Scramble algorithm used in cached_sha2_password fast path.

    XOR(SHA256(password), SHA256(SHA256(SHA256(password)), nonce))
    """
    if not password:
        return b""

    p1 = hashlib.sha256(password).digest()
    p2 = hashlib.sha256(p1).digest()
    p3 = hashlib.sha256(p2 + nonce).digest()

    res = bytearray(p1)
    for i in range(len(p3)):
        res[i] ^= p3[i]

    return bytes(res)


def caching_sha2_password_auth(conn, pkt):
    # No password fast path
    if not conn.password:
        return _roundtrip(conn, b"")

    if pkt.is_auth_switch_request():
        # Try from fast auth
        if DEBUG:
            print("caching sha2: Trying fast path")
        conn.salt = pkt.read_all()
        scrambled = scramble_caching_sha2(conn.password, conn.salt)
        pkt = _roundtrip(conn, scrambled)
    # else: fast auth is tried in initial handshake

    if not pkt.is_extra_auth_data():
        raise OperationalError(
            "caching sha2: Unknown packet for fast auth: %s" % pkt._data[:1]
        )

    # magic numbers:
    # 2 - request public key
    # 3 - fast auth succeeded
    # 4 - need full auth

    pkt.advance(1)
    n = pkt.read_uint8()

    if n == 3:
        if DEBUG:
            print("caching sha2: succeeded by fast path.")
        pkt = conn._read_packet()
        pkt.check_error()  # pkt must be OK packet
        return pkt

    if n != 4:
        raise OperationalError("caching sha2: Unknwon result for fast auth: %s" % n)

    if DEBUG:
        print("caching sha2: Trying full auth...")

    if conn._secure:
        if DEBUG:
            print("caching sha2: Sending plain password via secure connection")
        return _roundtrip(conn, conn.password + b"\0")

    if not conn.server_public_key:
        pkt = _roundtrip(conn, b"\x02")  # Request public key
        if not pkt.is_extra_auth_data():
            raise OperationalError(
                "caching sha2: Unknown packet for public key: %s" % pkt._data[:1]
            )

        conn.server_public_key = pkt._data[1:]
        if DEBUG:
            print(conn.server_public_key.decode("ascii"))

    data = sha2_rsa_encrypt(conn.password, conn.salt, conn.server_public_key)
    pkt = _roundtrip(conn, data)