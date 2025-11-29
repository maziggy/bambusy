"""Camera capture service for Bambu Lab printers.

Captures images from the printer's RTSPS camera stream using ffmpeg.
"""

import asyncio
import logging
import subprocess
from pathlib import Path
from datetime import datetime
import uuid

from backend.app.core.config import settings

logger = logging.getLogger(__name__)


def get_camera_port(model: str | None) -> int:
    """Get the RTSPS port based on printer model.

    X1 and H2D series use port 322.
    P1 and A1 series use port 6000.
    """
    if model:
        model_upper = model.upper()
        if model_upper.startswith(("X1", "H2")):
            return 322
    # Default to 6000 for P1/A1 or unknown models
    return 6000


def build_camera_url(ip_address: str, access_code: str, model: str | None) -> str:
    """Build the RTSPS URL for the printer camera."""
    port = get_camera_port(model)
    return f"rtsps://bblp:{access_code}@{ip_address}:{port}/streaming/live/1"


async def capture_camera_frame(
    ip_address: str,
    access_code: str,
    model: str | None,
    output_path: Path,
    timeout: int = 30,
) -> bool:
    """Capture a single frame from the printer's camera stream.

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        model: Printer model (X1, H2D, P1, A1, etc.)
        output_path: Path where to save the captured image
        timeout: Timeout in seconds for the capture operation

    Returns:
        True if capture was successful, False otherwise
    """
    camera_url = build_camera_url(ip_address, access_code, model)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ffmpeg command to capture a single frame from RTSPS stream
    # -rtsp_transport tcp: Use TCP for RTSP (more reliable)
    # -y: Overwrite output file
    # -frames:v 1: Capture only 1 frame
    # -q:v 2: High quality JPEG (1-31, lower is better)
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output
        "-rtsp_transport", "tcp",
        "-i", camera_url,
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path),
    ]

    logger.info(f"Capturing camera frame from {ip_address} (model: {model})")

    try:
        # Run ffmpeg asynchronously with timeout
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logger.error(f"Camera capture timed out after {timeout}s")
            return False

        if process.returncode != 0:
            stderr_text = stderr.decode() if stderr else "Unknown error"
            logger.error(f"ffmpeg failed with code {process.returncode}: {stderr_text}")
            return False

        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info(f"Successfully captured camera frame: {output_path}")
            return True
        else:
            logger.error("Camera capture produced no output file")
            return False

    except FileNotFoundError:
        logger.error("ffmpeg not found. Please install ffmpeg to enable camera capture.")
        return False
    except Exception as e:
        logger.exception(f"Camera capture failed: {e}")
        return False


async def capture_finish_photo(
    printer_id: int,
    ip_address: str,
    access_code: str,
    model: str | None,
    archive_dir: Path,
) -> str | None:
    """Capture a finish photo and save it to the archive's photos folder.

    Args:
        printer_id: ID of the printer
        ip_address: Printer IP address
        access_code: Printer access code
        model: Printer model
        archive_dir: Directory of the archive (where the 3MF is stored)

    Returns:
        Filename of the captured photo, or None if capture failed
    """
    # Create photos subdirectory
    photos_dir = archive_dir / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"finish_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
    output_path = photos_dir / filename

    success = await capture_camera_frame(
        ip_address=ip_address,
        access_code=access_code,
        model=model,
        output_path=output_path,
        timeout=30,
    )

    if success:
        logger.info(f"Finish photo saved: {filename}")
        return filename
    else:
        logger.warning(f"Failed to capture finish photo for printer {printer_id}")
        return None


async def test_camera_connection(
    ip_address: str,
    access_code: str,
    model: str | None,
) -> dict:
    """Test if the camera stream is accessible.

    Returns dict with success status and any error message.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        test_path = Path(f.name)

    try:
        success = await capture_camera_frame(
            ip_address=ip_address,
            access_code=access_code,
            model=model,
            output_path=test_path,
            timeout=15,
        )

        if success:
            return {"success": True, "message": "Camera connection successful"}
        else:
            return {"success": False, "error": "Failed to capture frame from camera"}
    finally:
        # Clean up test file
        if test_path.exists():
            test_path.unlink()
