"""Runtime configuration and local secret encryption."""

from __future__ import annotations

import base64
import os
import ssl
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES


@dataclass(frozen=True)
class Settings:
    """Filesystem and server settings for the standalone workbench."""

    data_dir: Path
    database_path: Path
    upload_dir: Path
    asset_dir: Path
    skill_dir: Path
    key_path: Path
    frontend_dist: Path
    max_upload_bytes: int = 200 * 1024 * 1024
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_env(cls) -> "Settings":
        default_root = Path.home() / ".openjiuwen" / "knowledge-workbench"
        data_dir = Path(os.environ.get("WORKBENCH_DATA_DIR", default_root)).expanduser().resolve()
        frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "workbench.sqlite3",
            upload_dir=data_dir / "uploads",
            asset_dir=data_dir / "assets",
            skill_dir=data_dir / "skills",
            key_path=data_dir / "master.key",
            frontend_dist=frontend_dist,
            max_upload_bytes=int(os.environ.get("WORKBENCH_MAX_UPLOAD_BYTES", 200 * 1024 * 1024)),
            host=os.environ.get("WORKBENCH_HOST", "127.0.0.1"),
            port=int(os.environ.get("WORKBENCH_PORT", "8765")),
        )

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.asset_dir, self.skill_dir):
            path.mkdir(parents=True, exist_ok=True)


class SecretBox:
    """AES-GCM encryption backed by a local, mode-0600 master key."""

    def __init__(self, key_path: Path):
        self._key_path = key_path
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            key = self._key_path.read_bytes()
        else:
            key = os.urandom(32)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(key)
        os.chmod(self._key_path, 0o600)
        if len(key) != 32:
            raise RuntimeError("Workbench master key must contain exactly 32 bytes")
        return key

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        cipher = AES.new(self._key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
        packed = cipher.nonce + tag + ciphertext
        return base64.urlsafe_b64encode(packed).decode("ascii")

    def decrypt(self, encrypted: str) -> str:
        if not encrypted:
            return ""
        packed = base64.urlsafe_b64decode(encrypted.encode("ascii"))
        nonce, tag, ciphertext = packed[:16], packed[16:32], packed[32:]
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")


def trusted_ca_bundle() -> str:
    """Return a trusted CA bundle accepted by openJiuwen's strict TLS client."""

    configured = os.environ.get("WORKBENCH_SSL_CERT", "").strip()
    if configured:
        certificate = Path(configured).expanduser().resolve()
    else:
        default_certificate = ssl.get_default_verify_paths().cafile
        certificate = Path(default_certificate).resolve() if default_certificate else Path()
        if not certificate.is_file():
            # requests is a declared runtime dependency and ships a portable CA bundle.
            from requests.certs import where

            certificate = Path(where()).resolve()

    if not certificate.is_file():
        raise RuntimeError("Workbench TLS CA bundle does not exist")

    safe_directory_value = os.environ.get("SAFE_CERT_DIR", "").strip()
    if safe_directory_value:
        safe_directory = Path(safe_directory_value).expanduser().resolve()
        if safe_directory != certificate.parent and safe_directory not in certificate.parents:
            raise RuntimeError("WORKBENCH_SSL_CERT must be located inside SAFE_CERT_DIR")
    else:
        os.environ["SAFE_CERT_DIR"] = str(certificate.parent)

    return str(certificate)
