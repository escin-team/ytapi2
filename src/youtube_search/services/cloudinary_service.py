"""Cloudinary service with multi-account failover support.

Perbaikan:
- Akun dibaca FRESH dari os.environ setiap kali, bukan dari __init__ yang cached.
  Ini memastikan akun yang ditambah via admin panel langsung terdeteksi tanpa restart.
- Tambah method check_exists() untuk cek file sebelum download.
"""

import asyncio
import json
import logging
import os
from typing import Dict, Any, List, Optional, Tuple

import cloudinary
import cloudinary.api
import cloudinary.uploader
from cloudinary.exceptions import Error as CloudinaryError

logger = logging.getLogger(__name__)


def _load_accounts_fresh() -> List[Dict[str, Any]]:
    """
    Baca akun Cloudinary FRESH dari os.environ setiap kali dipanggil.
    Tidak menggunakan cached settings agar akun yang baru ditambah via admin
    langsung terdeteksi tanpa restart server.
    """
    raw = os.environ.get("CLOUDINARY_ACCOUNTS_JSON", "[]")
    # Strip surrounding quotes yang kadang ditambah oleh env_manager
    raw = raw.strip().strip('"').strip("'")
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception as e:
        logger.error("Failed to parse CLOUDINARY_ACCOUNTS_JSON: %s", e)
    return []


class CloudinaryService:
    """
    Cloudinary upload/check service with automatic failover across multiple accounts.

    Accounts are re-read from os.environ on EVERY operation so that accounts
    added via the admin panel are immediately visible without a server restart.
    """

    def _get_accounts(self) -> List[Dict[str, Any]]:
        """Return current account list — always fresh from env, never cached."""
        accounts = _load_accounts_fresh()
        if not accounts:
            logger.warning(
                "CLOUDINARY_ACCOUNTS_JSON is empty or not set — "
                "audio uploads will fall back to local storage."
            )
        return accounts

    def _configure_account(self, account: Dict[str, Any]) -> bool:
        """Configure Cloudinary SDK with the given account credentials."""
        try:
            cloudinary.config(
                cloud_name=account.get("cloud_name"),
                api_key=account.get("api_key"),
                api_secret=account.get("api_secret"),
                secure=True,
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to configure Cloudinary account %s: %s",
                account.get("cloud_name", "unknown"),
                e,
            )
            return False

    # ------------------------------------------------------------------
    # check_exists — cek di semua akun sebelum download
    # ------------------------------------------------------------------

    async def check_exists(
        self,
        video_id: str,
        folder: str = "music",
    ) -> Optional[Tuple[str, str]]:
        """
        Cek apakah file sudah ada di Cloudinary (di semua akun yang terdaftar).

        Dipanggil SEBELUM proses download agar tidak re-download file yang
        sudah ada di Cloudinary.

        Returns:
            Tuple (secure_url, account_name) jika ditemukan, else None.
        """
        accounts = self._get_accounts()
        if not accounts:
            return None

        full_public_id = f"{folder}/{video_id}"

        for account in accounts:
            account_name = account.get("cloud_name", "unknown")
            if not self._configure_account(account):
                continue
            try:
                result = await asyncio.to_thread(
                    cloudinary.api.resource,
                    full_public_id,
                    resource_type="video",
                )
                secure_url = result.get("secure_url")
                if secure_url:
                    logger.info(
                        "Cloudinary HIT — '%s' sudah ada di akun %s",
                        full_public_id,
                        account_name,
                    )
                    return secure_url, account_name
            except CloudinaryError as e:
                # NotFound → lanjut ke akun berikutnya
                err_str = str(e).lower()
                if "not found" in err_str or "404" in err_str:
                    logger.debug(
                        "Cloudinary MISS — '%s' tidak ada di akun %s",
                        full_public_id,
                        account_name,
                    )
                else:
                    logger.debug(
                        "Cloudinary check error di %s: %s",
                        account_name,
                        e,
                    )
            except Exception as e:
                logger.debug(
                    "Cloudinary check error di %s: %s",
                    account_name,
                    e,
                )
        return None

    # ------------------------------------------------------------------
    # upload_file
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        file_path: str,
        public_id: str,
        folder: str = "music",
        resource_type: str = "video",  # Cloudinary treats audio as "video"
    ) -> Tuple[str, str]:
        """
        Upload file ke Cloudinary dengan automatic failover antar akun.

        Returns:
            Tuple (secure_url, account_name)

        Raises:
            RuntimeError: If all accounts fail.
        """
        accounts = self._get_accounts()

        if not accounts:
            import shutil, pathlib
            from youtube_search.config import get_settings
            port = get_settings().api_port
            # Simpan file ke direktori persisten agar bisa diserve via /local/
            local_dir = pathlib.Path(__file__).parents[3] / "local_files"
            local_dir.mkdir(exist_ok=True)
            dest = local_dir / f"{public_id}.mp3"
            if file_path and os.path.exists(file_path) and not dest.exists():
                shutil.copy2(file_path, dest)
            local_url = f"http://localhost:{port}/local/{public_id}.mp3"
            logger.warning("Tidak ada akun Cloudinary, file disimpan lokal: %s", dest)
            return local_url, "local"

        errors: List[str] = []

        for account in accounts:
            account_name = account.get("cloud_name", "unknown")
            try:
                logger.info("Uploading ke Cloudinary akun: %s", account_name)

                if not self._configure_account(account):
                    errors.append(f"{account_name}: configuration failed")
                    continue

                result = await asyncio.to_thread(
                    cloudinary.uploader.upload,
                    file_path,
                    resource_type=resource_type,
                    folder=folder,
                    public_id=public_id,
                    format="mp3",
                    overwrite=True,
                    invalidate=True,
                )

                secure_url = result.get("secure_url")
                if not secure_url:
                    raise ValueError("No secure_url in Cloudinary response")

                logger.info("Upload berhasil ke %s: %s", account_name, secure_url)
                return secure_url, account_name

            except CloudinaryError as e:
                errors.append(f"{account_name}: {e}")
                logger.warning("Cloudinary error pada %s: %s", account_name, e)

            except Exception as e:
                errors.append(f"{account_name}: {e}")
                logger.error("Unexpected error pada %s: %s", account_name, e)

        raise RuntimeError(
            f"Semua akun Cloudinary gagal. Errors: {' | '.join(errors)}"
        )

    # ------------------------------------------------------------------
    # delete_file
    # ------------------------------------------------------------------

    async def delete_file(self, public_id: str, folder: str = "music") -> bool:
        """Delete file dari Cloudinary (coba semua akun)."""
        accounts = self._get_accounts()
        if not accounts:
            return False

        success = False
        for account in accounts:
            try:
                self._configure_account(account)
                result = await asyncio.to_thread(
                    cloudinary.uploader.destroy,
                    f"{folder}/{public_id}",
                    resource_type="video",
                )
                if result.get("result") == "ok":
                    success = True
            except Exception as e:
                logger.warning(
                    "Gagal hapus dari %s: %s", account.get("cloud_name"), e
                )
        return success

    def get_account_count(self) -> int:
        """Get jumlah akun yang dikonfigurasi (fresh count)."""
        return len(self._get_accounts())
