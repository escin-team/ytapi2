"""
Audio downloader — yt-dlp dengan OAuth 2.0 Bearer token injection.

Perbaikan dari versi sebelumnya:
1. Urutan client diubah ke ios → mweb → android → tv_embedded.
   Client 'ios' jauh lebih stabil untuk request tanpa autentikasi.
   'tv_embedded' sekarang dipakai sebagai last-resort fallback saja.
2. Tambah impersonate=chrome via curl_cffi untuk bypass bot-detection YouTube.
3. Cek Cloudinary SEBELUM download — jika sudah ada di salah satu akun,
   langsung kembalikan URL yang ada beserta metadata (title, durasi, artis).
4. Metadata diambil dengan extract_info(download=False) terpisah jika file
   sudah ada di Cloudinary, sehingga tetap ada judul/durasi di response.

Authentication priority:
  1. OAuth 2.0 Bearer token (web client, best quality)
  2. Cookie file (legacy fallback)
  3. Unauthenticated ios/mweb client (default, paling robust)
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from typing import Optional

from youtube_search.models.download import AudioFile
from youtube_search.services.cloudinary_service import CloudinaryService
from youtube_search.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Browser-like header baseline
# ---------------------------------------------------------------------------

_YTDL_BASE_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Urutan client default saat TIDAK ada OAuth token.
# None = pakai default yt-dlp client (web_creator-like, paling banyak format audio-only).
# "android" sebagai fallback jika default gagal.
# ios/mweb TIDAK dipakai — di Replit environment keduanya mengembalikan 0 format audio.
_DEFAULT_CLIENT_CHAIN: list = [None, "android", "tv_embedded"]
_OAUTH_CLIENT_CHAIN:   list = ["web", None, "android"]


class AudioDownloader:
    """
    Download audio dari YouTube dan upload ke Cloudinary.

    Token management dihandle internal via token_provider.
    Caller tidak perlu mengurus OAuth sama sekali.
    """

    def __init__(self) -> None:
        self.cloudinary = CloudinaryService()

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def _get_oauth_token(self) -> Optional[str]:
        """Ambil OAuth access token secara sync. Return None jika tidak ada."""
        try:
            from youtube_search.auth.token_provider import get_valid_access_token_sync
            return get_valid_access_token_sync()
        except Exception as exc:
            logger.debug("OAuth token tidak tersedia: %s", exc)
            return None

    # ------------------------------------------------------------------
    # yt-dlp options builder
    # ------------------------------------------------------------------

    def _get_ydl_opts(
        self,
        output_file: str,
        timeout: int,
        bitrate: int,
        client: Optional[str],
        oauth_token: Optional[str] = None,
    ) -> dict:
        """
        Build yt-dlp options untuk satu client/attempt.

        Perubahan penting:
        - impersonate='chrome' menggunakan curl_cffi untuk bypass TLS fingerprint.
        - nocheckcertificate=True menghindari SSL error di beberapa hosting.
        - extractor_args[youtube][skip] tidak mengandung 'login' agar OAuth bisa
          digunakan saat token tersedia.
        """
        no_cookies = os.getenv("YTDL_NO_COOKIES", "true").lower() == "true"

        http_headers = dict(_YTDL_BASE_HEADERS)
        if oauth_token:
            http_headers["Authorization"] = f"Bearer {oauth_token}"
            logger.debug("OAuth Bearer token disuntikkan ke yt-dlp headers.")

        # Gunakan impersonation Chrome via curl_cffi (bypass TLS fingerprint)
        # Hanya aktif jika curl_cffi terinstall DAN yt-dlp mendukung ImpersonateTarget
        impersonate_cfg = None
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            import curl_cffi  # noqa: F401 — pastikan backend tersedia
            impersonate_cfg = ImpersonateTarget("chrome", None, None, None)
        except Exception:
            pass

        opts: dict = {
            # bestaudio/best — works with default client (webm/opus audio-only)
            "format": "bestaudio/best",
            "outtmpl": output_file,
            "noplaylist": True,
            "quiet": False,
            "no_warnings": False,
            "extract_flat": False,
            "socket_timeout": min(timeout, 60),
            "retries": 5,
            "fragment_retries": 5,
            "extractor_retries": 3,
            "retry_sleep_functions": {
                "http": lambda n: min(2 ** n, 30),
                "fragment": lambda n: min(2 ** n, 30),
            },
            "skip_unavailable_fragments": True,
            "keepvideo": False,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "geo_bypass_country": "US",
            "no_color": True,
            "http_headers": http_headers,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(bitrate),
                }
            ],
        }

        # Hanya set extractor_args jika ada client spesifik (None = pakai default yt-dlp)
        if client is not None:
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": [client],
                    "skip": [] if oauth_token else ["authcheck"],
                }
            }

        # Impersonation via curl_cffi — bypass TLS fingerprint YouTube
        if impersonate_cfg:
            opts["impersonate"] = impersonate_cfg

        # Cookie file (legacy fallback — hanya jika OAuth tidak ada)
        if not oauth_token and not no_cookies:
            cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")
            if cookies_file and os.path.exists(cookies_file):
                opts["cookiefile"] = cookies_file
                logger.info("Cookie file dipakai (OAuth tidak tersedia): %s", cookies_file)

        return opts

    # ------------------------------------------------------------------
    # Ambil metadata video tanpa download
    # ------------------------------------------------------------------

    async def _fetch_video_info(self, video_id: str, timeout: int) -> Optional[dict]:
        """
        Ambil info (title, duration, channel) dari YouTube tanpa download.
        Dipakai saat file sudah ada di Cloudinary.
        """
        import yt_dlp

        url = f"https://www.youtube.com/watch?v={video_id}"

        # None = default client (paling banyak format), android sebagai fallback
        for client in [None, "android"]:
            try:
                opts: dict = {
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": min(timeout, 30),
                    "nocheckcertificate": True,
                }
                if client is not None:
                    opts["extractor_args"] = {"youtube": {"player_client": [client]}}
                try:
                    from yt_dlp.networking.impersonate import ImpersonateTarget
                    import curl_cffi  # noqa: F401
                    opts["impersonate"] = ImpersonateTarget("chrome", None, None, None)
                except Exception:
                    pass

                def _extract():
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        return ydl.extract_info(url, download=False)

                info = await asyncio.to_thread(_extract)
                if info:
                    return info
            except Exception as exc:
                logger.debug("fetch_info client=%s gagal: %s", client, exc)
        return None

    # ------------------------------------------------------------------
    # Entry point utama
    # ------------------------------------------------------------------

    async def download_and_upload(
        self,
        video_id: str,
        timeout: int = 300,
        max_duration: int = 600,
        bitrate: int = 128,
    ) -> AudioFile:
        """
        Download audio dari YouTube dan upload ke Cloudinary.

        Alur:
          1. Cek Cloudinary — jika sudah ada, ambil metadata dan kembalikan URL.
          2. Ambil OAuth token (opsional).
          3. Download dengan fallback chain client.
          4. Upload ke Cloudinary.

        Fallback chain (tanpa OAuth): ios → mweb → android → tv_embedded
        Fallback chain (dengan OAuth): web → ios → mweb → android
        """
        # ── 1. Cek Cloudinary sebelum download ──────────────────────────
        existing = await self.cloudinary.check_exists(video_id, folder="music")
        if existing:
            cloudinary_url, account_name = existing
            logger.info(
                "File sudah ada di Cloudinary (%s) — skip download: %s",
                account_name,
                video_id,
            )
            # Ambil metadata video (title, durasi, channel)
            info = await self._fetch_video_info(video_id, timeout)
            title    = (info or {}).get("title", f"YouTube: {video_id}")
            duration = int((info or {}).get("duration", 0) or 0)

            return AudioFile(
                video_id=video_id,
                title=title,
                file_name=f"{video_id}.mp3",
                file_path=cloudinary_url,
                file_size=0,          # ukuran tidak diketahui dari cache
                duration=duration,
                cached=True,
                storage_source="cloudinary",
                storage_account=account_name,
            )

        # ── 2. Ambil OAuth token ─────────────────────────────────────────
        oauth_token: Optional[str] = await asyncio.to_thread(self._get_oauth_token)
        if oauth_token:
            logger.info("OAuth token tersedia — pakai authenticated yt-dlp path.")
            clients = _OAUTH_CLIENT_CHAIN
        else:
            logger.info("Tidak ada OAuth token — pakai unauthenticated client chain.")
            clients = _DEFAULT_CLIENT_CHAIN

        # Override dari env jika ingin paksa client tertentu
        # Gunakan None untuk default yt-dlp client; string kosong = diabaikan
        forced_env = os.getenv("YTDL_CLIENT", "").strip()
        if forced_env and forced_env.lower() not in ("", "none", "default"):
            clients = [forced_env] + [c for c in clients if c != forced_env]
        elif forced_env.lower() in ("none", "default"):
            clients = [None] + [c for c in clients if c is not None]

        # ── 3. Download ──────────────────────────────────────────────────
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                output_file = os.path.join(temp_dir, f"{video_id}.%(ext)s")
                final_mp3   = os.path.join(temp_dir, f"{video_id}.mp3")

                logger.info("Mulai download: %s", video_id)

                import yt_dlp

                info: Optional[dict] = None
                last_error: Optional[Exception] = None

                for attempt_idx, client in enumerate(clients):
                    try:
                        logger.info(
                            "Attempt %d/%d — client: %s, oauth: %s",
                            attempt_idx + 1,
                            len(clients),
                            client,
                            bool(oauth_token),
                        )

                        ydl_opts = self._get_ydl_opts(
                            output_file, timeout, bitrate, client, oauth_token
                        )

                        def _run_download():
                            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                                _info = ydl.extract_info(
                                    f"https://www.youtube.com/watch?v={video_id}",
                                    download=False,
                                )
                                dur = (_info or {}).get("duration", 0) or 0
                                if dur > max_duration:
                                    raise ValueError(
                                        f"Video terlalu panjang ({dur}s > {max_duration}s)"
                                    )
                                ydl.download(
                                    [f"https://www.youtube.com/watch?v={video_id}"]
                                )
                                return _info

                        info = await asyncio.to_thread(_run_download)
                        logger.info("Download berhasil dengan client: %s", client)
                        break

                    except Exception as exc:
                        last_error = exc
                        logger.warning(
                            "Attempt %d dengan %s gagal: %s",
                            attempt_idx + 1,
                            client,
                            str(exc)[:300],
                        )
                        if attempt_idx < len(clients) - 1:
                            await asyncio.sleep(min(2 ** attempt_idx, 15))
                        else:
                            raise RuntimeError(
                                f"Semua {len(clients)} client gagal. "
                                f"Error terakhir: {last_error}"
                            ) from last_error

                # ── Cari file hasil download ─────────────────────────────
                downloaded_file: Optional[str] = None
                for ext in ["mp3", "m4a", "webm", "opus", "ogg"]:
                    candidate = os.path.join(temp_dir, f"{video_id}.{ext}")
                    if os.path.exists(candidate):
                        downloaded_file = candidate
                        break

                if not downloaded_file:
                    # Scan semua file di temp_dir
                    all_files = os.listdir(temp_dir)
                    audio_files = [
                        f for f in all_files
                        if any(f.endswith(f".{e}") for e in ["mp3","m4a","webm","opus","ogg"])
                    ]
                    if audio_files:
                        downloaded_file = os.path.join(temp_dir, audio_files[0])
                    else:
                        raise FileNotFoundError(
                            f"File audio tidak ditemukan. Isi temp dir: {all_files}"
                        )

                # ── Konversi ke MP3 jika perlu ──────────────────────────
                if not downloaded_file.endswith(".mp3"):
                    logger.info("Konversi ke MP3: %s", video_id)
                    cmd = [
                        "ffmpeg", "-y",
                        "-i", downloaded_file,
                        "-vn",
                        "-acodec", "libmp3lame",
                        "-ab", f"{bitrate}k",
                        "-ar", "44100",
                        "-ac", "2",
                        final_mp3,
                    ]
                    result = subprocess.run(
                        cmd, capture_output=True, timeout=timeout
                    )
                    if result.returncode != 0:
                        logger.error("FFmpeg error: %s", result.stderr.decode())
                        result.check_returncode()
                    downloaded_file = final_mp3

                # ── Upload ke Cloudinary ─────────────────────────────────
                logger.info("Upload ke Cloudinary: %s", video_id)
                cloudinary_url, account_name = await self.cloudinary.upload_file(
                    file_path=downloaded_file,
                    public_id=video_id,
                    folder="music",
                )

                info = info or {}
                audio_file = AudioFile(
                    video_id=video_id,
                    title=info.get("title", "Unknown"),
                    file_name=f"{video_id}.mp3",
                    file_path=cloudinary_url,
                    file_size=os.path.getsize(downloaded_file),
                    duration=int(info.get("duration", 0) or 0),
                    storage_source="cloudinary",
                    storage_account=account_name,
                )

                logger.info("Berhasil diproses: %s", video_id)
                return audio_file

        except subprocess.TimeoutExpired:
            raise ValueError(f"FFmpeg timeout setelah {timeout} detik")
        except Exception as exc:
            logger.error("Gagal proses %s: %s", video_id, exc)
            raise
