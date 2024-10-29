import logging
from pathlib import Path
from typing import List, Optional
import shutil
import os
from fastapi import UploadFile

logger = logging.getLogger(__name__)


async def save_uploaded_file(
    upload_file: UploadFile, username: str, filename: str
) -> Path:
    """Save uploaded file to disk.

    Args:
        upload_file: The uploaded file from FastAPI
        username: Username for creating user directory
        filename: Name to save the file as

    Returns:
        Path: Path where the file was saved

    Raises:
        ValueError: If file saving fails
    """
    try:
        # Create user directory if it doesn't exist
        upload_dir = Path("uploads") / username
        upload_dir.mkdir(parents=True, exist_ok=True)

        # Create file path
        file_path = upload_dir / filename

        # Read file content
        content = await upload_file.read()

        # Write to disk
        with open(file_path, "wb") as f:
            f.write(content)

        logger.info(f"Saved file: {file_path}")
        return file_path

    except Exception as e:
        logger.error(f"Error saving file: {str(e)}")
        raise ValueError(f"Failed to save file: {str(e)}")


def get_image_files(
    username: str, pdf_path: Path, base_folder: str
) -> Optional[List[str]]:
    """Get list of image files for a PDF."""
    user_images_path = Path("uploads") / username / "images"
    matching_folders = list(user_images_path.glob(f"{base_folder}_*"))

    if not matching_folders:
        logger.warning(f"No image folder found for PDF: {pdf_path}")
        return None

    image_folder = matching_folders[0]
    image_files = [
        str(img.name)
        for img in image_folder.glob("*")
        if img.suffix.lower() in [".png", ".jpg", ".jpeg"]
    ]

    logger.info(f"Found {len(image_files)} images for PDF: {pdf_path}")
    return image_files
