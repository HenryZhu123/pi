from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path


_CONVERSION_LOCK = threading.Lock()


class LibreOfficeError(RuntimeError):
    pass


class LibreOfficeConverter:
    """Convert Word files to PDF with an isolated LibreOffice profile.

    This is adapted from exam_checker_agent's in-process converter. A global
    lock is retained because concurrent soffice processes can interfere when
    they share a user installation.
    """

    def __init__(
        self, timeout_seconds: int = 180, tmp_root: Path | None = None
    ) -> None:
        self.timeout_seconds = max(1, timeout_seconds)
        self.tmp_root = (
            tmp_root or Path(tempfile.gettempdir()) / "pi-exam-checker-libreoffice"
        )

    def convert_to_pdf(self, file_bytes: bytes, original_filename: str) -> bytes:
        soffice = shutil.which("soffice")
        if soffice is None:
            raise LibreOfficeError("LibreOffice executable 'soffice' is not available")
        suffix = Path(original_filename).suffix.lower()
        if suffix not in {".doc", ".docx"}:
            raise LibreOfficeError(
                f"Unsupported Word extension: {suffix or '(missing)'}"
            )

        task_dir = self.tmp_root / uuid.uuid4().hex
        input_dir = task_dir / "input"
        output_dir = task_dir / "output"
        profile_dir = task_dir / "profile"
        input_path = input_dir / f"input{suffix}"
        output_path = output_dir / "input.pdf"
        try:
            input_dir.mkdir(parents=True)
            output_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            input_path.write_bytes(file_bytes)
            command = [
                soffice,
                "--headless",
                "--nologo",
                "--nofirststartwizard",
                "--norestore",
                f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(input_path),
            ]
            acquired = _CONVERSION_LOCK.acquire(timeout=self.timeout_seconds)
            if not acquired:
                raise LibreOfficeError(
                    "Timed out waiting for the LibreOffice conversion lock"
                )
            try:
                process = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise LibreOfficeError("LibreOffice conversion timed out") from exc
            finally:
                _CONVERSION_LOCK.release()

            if process.returncode != 0:
                detail = (process.stderr or process.stdout or "").strip()[:1000]
                raise LibreOfficeError(
                    f"LibreOffice conversion failed with code {process.returncode}: {detail}"
                )
            if not output_path.is_file():
                raise LibreOfficeError("LibreOffice did not produce a PDF")
            output = output_path.read_bytes()
            if not output:
                raise LibreOfficeError("LibreOffice produced an empty PDF")
            return output
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)
