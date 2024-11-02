import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Any
from datetime import datetime, timezone
import hashlib
from fastapi import UploadFile
import bcrypt
from google.generativeai.types.file_types import File as GeminiFile
from db import DatabaseManager, hash_password, verify_password
from pdf import NoteGenerator
from file_utils import save_uploaded_file, get_image_files
import shutil
import json

logger = logging.getLogger(__name__)


class ServiceManager:
    """Singleton to manage shared resources."""

    _instance = None
    db: DatabaseManager  # Define the class attribute with type hint

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.db = DatabaseManager()
        return cls._instance

    def __init__(self):
        """Initialize instance attributes if not already set."""
        if not hasattr(self, "db"):
            self.db = DatabaseManager()


class NoteService:
    def __init__(self):
        self.service_manager = ServiceManager()
        self.db = self.service_manager.db
        self.note_generator = NoteGenerator()

    async def get_topic_notes(self, chapter: str, topic: str) -> Tuple[Dict, int]:
        """Get or generate topic notes."""
        try:
            # Check database first
            result = self.db.get_topic_notes(chapter, topic)
            if not result:
                return {"error": "Topic not found"}, 404

            # Check if notes field exists and has content
            if (
                result["notes"] is None
                or result["notes"] == ""
                or (isinstance(result["notes"], str) and not result["notes"].strip())
            ):
                logger.info(
                    f"No existing notes found for topic: {chapter}/{topic}. Generating new notes..."
                )

                try:
                    # Generate new notes
                    pdf_path = Path(result["pdf_path"])
                    gemini_file = await self.get_valid_gemini_file(
                        result["pdfid"], pdf_path
                    )

                    image_files = get_image_files(
                        result["username"], pdf_path, pdf_path.stem
                    )

                    generated_result = self.note_generator.generate_topic_notes(
                        gemini_file, chapter, topic, image_files or []
                    )

                    # Store the generated notes
                    self.db.store_topic_notes(
                        result["topicid"],
                        generated_result["notes"],
                        generated_result["images"],
                    )

                    logger.info(
                        f"Generated and stored new notes for topic: {chapter}/{topic}"
                    )
                    return {
                        "notes": generated_result["notes"],
                        "images": generated_result["images"],
                        "username": result["username"],
                        "pdf_folder": pdf_path.stem + "_18e1b007",
                    }, 200

                except Exception as e:
                    logger.error(f"Error generating notes: {str(e)}")
                    return {"error": "Failed to generate notes"}, 500

            # Return existing notes
            logger.info(f"Returning existing notes for topic: {chapter}/{topic}")
            return {
                "notes": result["notes"],
                "images": result["images"] or [],
                "username": result["username"],
                "pdf_folder": Path(result["pdf_path"]).stem + "_18e1b007",
            }, 200

        except Exception as e:
            logger.error(f"Error in get_topic_notes: {str(e)}")
            return {"error": "Internal server error"}, 500

    async def get_subtopic_notes(
        self, chapter: str, topic: str, subtopic: str
    ) -> Tuple[Dict, int]:
        """Get or generate subtopic notes."""
        try:
            # Check database first
            result = self.db.get_subtopic_notes(chapter, topic, subtopic)

            if result is None:  # No record found at all
                logger.error(
                    f"Subtopic not found in database: {chapter}/{topic}/{subtopic}"
                )
                return {"error": "Subtopic not found"}, 404

            # If we have a record but no notes, generate them
            if not result.get("notes"):
                logger.info(
                    f"Generating new notes for subtopic: {chapter}/{topic}/{subtopic}"
                )
                try:
                    pdf_path = Path(result["pdf_path"])
                    gemini_file = await self.get_valid_gemini_file(
                        result["pdfid"], pdf_path
                    )

                    # Get any associated images
                    image_files = get_image_files(
                        result["username"], pdf_path, pdf_path.stem
                    )

                    # Generate notes using NoteGenerator
                    generated_result = self.note_generator.generate_subtopic_notes(
                        gemini_file, chapter, topic, subtopic, image_files or []
                    )

                    # Ensure images is a list of dicts with filename and caption
                    images_to_store = generated_result.get("images", [])
                    if not isinstance(images_to_store, list):
                        images_to_store = []

                    # Store the generated notes
                    self.db.store_subtopic_notes(
                        result["subtopicid"], generated_result["notes"], images_to_store
                    )

                    logger.info(
                        f"Successfully generated notes for subtopic: {chapter}/{topic}/{subtopic}"
                    )
                    return {
                        "notes": generated_result["notes"],
                        "images": images_to_store,
                        "username": result["username"],
                        "pdf_folder": pdf_path.stem + "_18e1b007",
                    }, 200

                except Exception as e:
                    logger.error(f"Error generating subtopic notes: {str(e)}")
                    return {"error": "Failed to generate notes"}, 500

            # Return existing notes
            # Handle the images field properly whether it's a string or list
            images = result.get("images", [])
            if isinstance(images, str):
                try:
                    images = json.loads(images)
                except json.JSONDecodeError:
                    images = []
            elif not isinstance(images, list):
                images = []

            return {
                "notes": result["notes"],
                "images": images,
                "username": result["username"],
                "pdf_folder": Path(result["pdf_path"]).stem + "_18e1b007",
            }, 200

        except Exception as e:
            logger.error(f"Error in get_subtopic_notes: {str(e)}")
            return {"error": "Internal server error"}, 500

    async def get_valid_gemini_file(self, pdf_id: int, pdf_path: Path):
        """Get or create a valid Gemini file."""
        try:
            stored_file = self.db.get_gemini_file(pdf_id)

            if stored_file and stored_file["gemini_file"]:
                expiration_time = datetime.fromisoformat(
                    stored_file["gemini_file"]["expiration_time"]
                )
                if expiration_time > datetime.now(timezone.utc):
                    return self.note_generator.reconstruct_gemini_file(
                        stored_file["gemini_file"]
                    )

            # Get username for the PDF
            pdf_info = self.db.get_pdf_info(pdf_id)
            if not pdf_info:
                raise ValueError(f"PDF not found with ID: {pdf_id}")

            # Upload new file if not found or expired
            new_file = self.note_generator.upload_to_gemini(pdf_path)
            file_dict = self.note_generator.create_gemini_file_dict(new_file)
            self.db.store_gemini_file(
                str(pdf_path),
                pdf_info["username"],  # Use username from PDF info
                file_dict,
            )
            return new_file

        except Exception as e:
            logger.error(f"Error getting valid Gemini file: {str(e)}")
            raise


class UserService:
    def __init__(self):
        self.service_manager = ServiceManager()
        self.db = self.service_manager.db

    async def login_user(self, username: str, password: str) -> Tuple[Dict, int]:
        """Handle user login by username."""
        try:
            user = self.db.get_user(username)
            if not user:
                return {"error": "User not found"}, 404

            if verify_password(password, user["password_hash"]):
                return {
                    "message": "Login successful",
                    "username": user["username"],
                }, 200
            else:
                return {"error": "Invalid password"}, 401

        except Exception as e:
            logger.error(f"Error in login_user: {str(e)}")
            return {"error": "Internal server error"}, 500

    async def login_user_by_email(self, email: str, password: str) -> Tuple[Dict, int]:
        """Handle user login by email."""
        try:
            user = self.db.get_user_by_email(email)
            if not user:
                return {"error": "User not found"}, 404

            if verify_password(password, user["password_hash"]):
                return {
                    "message": "Login successful",
                    "username": user["username"],
                }, 200
            else:
                return {"error": "Invalid password"}, 401

        except Exception as e:
            logger.error(f"Error in login_user_by_email: {str(e)}")
            return {"error": "Internal server error"}, 500

    async def email_exists(self, email: str) -> bool:
        """Check if email already exists."""
        try:
            user = self.db.get_user_by_email(email)
            return user is not None
        except Exception as e:
            logger.error(f"Error checking email existence: {str(e)}")
            raise

    async def register_user(
        self, username: str, password: str, email: str
    ) -> Tuple[Dict, int]:
        """Handle user registration."""
        try:
            if self.db.get_user(username):
                return {"error": "Username already exists"}, 400

            # Hash password using the utility function
            password_hash = hash_password(password)

            # Store user with email
            self.db.create_user(username, password_hash, email)
            return {"message": "Registration successful"}, 201

        except Exception as e:
            logger.error(f"Error in register_user: {str(e)}")
            return {"error": "Internal server error"}, 500


class FileService:
    def __init__(self):
        self.service_manager = ServiceManager()
        self.db = self.service_manager.db
        self.note_generator = NoteGenerator()

    async def process_pdf_upload(
        self, file: UploadFile, username: str
    ) -> Tuple[Dict, int]:
        """Process uploaded PDF file."""
        # Initialize images_dir outside try block
        images_dir = None

        try:
            # Save the uploaded file
            filename = file.filename

            if filename is None:
                return {"error": "No filename provided"}, 400
            pdf_path = await save_uploaded_file(file, username, filename)

            # Create images directory for this PDF
            pdf_base_name = Path(str(filename)).stem
            images_dir = (
                Path("uploads") / username / "images" / f"{pdf_base_name}_18e1b007"
            )
            images_dir.mkdir(parents=True, exist_ok=True)

            # Extract images from PDF
            logger.info(f"Extracting images from PDF: {pdf_path}")
            try:
                image_files = self.note_generator.extract_images_from_pdf(
                    pdf_path, images_dir
                )
                logger.info(f"Extracted {len(image_files)} images to {images_dir}")
            except Exception as e:
                logger.error(f"Error extracting images: {str(e)}")
                image_files = []

            # Create PDF record in database
            pdf_id = self.db.create_pdf_record(str(pdf_path), username, filename)

            # Upload to Gemini and extract structure
            gemini_file = self.note_generator.upload_to_gemini(pdf_path)
            structure = self.note_generator.extract_pdf_structure(gemini_file)

            # Store PDF structure in database
            self.db.create_pdf_structure(pdf_id, structure)

            return {
                "message": "PDF processed successfully",
                "pdf_id": pdf_id,
                "structure": structure,
                "images": image_files,
            }, 200

        except Exception as e:
            logger.error(f"Error processing PDF upload: {str(e)}")
            # Clean up any partially created directories if they exist
            if images_dir and images_dir.exists():
                shutil.rmtree(images_dir)
            return {"error": str(e)}, 500

    async def get_user_pdfs(self, username: str) -> Tuple[Dict, int]:
        """Get list of user's PDFs."""
        try:
            pdfs = self.db.get_user_pdfs(username)
            return {
                "pdfs": [
                    {
                        "id": pdf["pdfid"],
                        "name": Path(pdf["pdf_path"]).name,
                        "upload_date": pdf["upload_date"].isoformat(),
                    }
                    for pdf in pdfs
                ]
            }, 200

        except Exception as e:
            logger.error(f"Error getting user PDFs: {str(e)}")
            return {"error": "Internal server error"}, 500

    async def process_pdf_content(
        self, pdf_id: int, pdf_path: Path, gemini_file: GeminiFile
    ) -> List[Dict]:
        """Process PDF content to extract chapters and topics."""
        try:
            # Extract structure using existing method
            structure = self.note_generator.extract_pdf_structure(gemini_file)

            # Store the entire structure in a single database transaction
            self.db.create_pdf_structure(pdf_id, structure)

            logger.info(
                f"Successfully processed and stored structure for PDF: {pdf_path}"
            )
            return structure["chapters"]

        except Exception as e:
            logger.error(f"Error processing PDF content: {str(e)}")
            raise

    async def delete_pdf(self, pdf_id: int, username: str) -> Tuple[Dict, int]:
        """Delete PDF and associated data."""
        try:
            # Verify ownership
            pdf_info = self.db.get_pdf_info(pdf_id)
            if not pdf_info:
                return {"error": "PDF not found"}, 404

            if pdf_info["username"] != username:
                return {"error": "Unauthorized"}, 403

            # Delete the PDF file and its data
            pdf_path = Path(pdf_info["pdf_path"])

            # Delete from database first
            self.db.delete_pdf(pdf_id)

            # Delete the physical file if it exists
            if pdf_path.exists():
                pdf_path.unlink()

                # Delete associated image folder if it exists
                image_folder = pdf_path.parent / f"{pdf_path.stem}_18e1b007"
                if image_folder.exists() and image_folder.is_dir():
                    import shutil

                    shutil.rmtree(image_folder)

            logger.info(f"Successfully deleted PDF: {pdf_path}")
            return {"message": "PDF deleted successfully"}, 200

        except Exception as e:
            logger.error(f"Error deleting PDF: {str(e)}")
            return {"error": "Internal server error"}, 500

    async def upload_pdf(self, file: UploadFile, username: str) -> Tuple[Dict, int]:
        """Upload and process PDF file."""
        pdf_path = None
        pdf_id = None
        try:
            # Save the file first
            filename = file.filename
            if filename is None:
                return {"error": "No filename provided"}, 400

            # Check if PDF already exists for this user
            existing_path = Path("uploads") / username / filename
            if existing_path.exists():
                # Generate unique filename by appending timestamp
                base = Path(filename).stem
                ext = Path(filename).suffix
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{base}_{timestamp}{ext}"

            pdf_path = await save_uploaded_file(file, username, filename)

            # Extract images using note_generator
            image_folder = await self.extract_images_from_pdf(username, pdf_path)

            try:
                # Create initial PDF record with 'pending' status
                pdf_id = self.db.create_pdf_record(
                    str(pdf_path), username, filename, "pending"
                )

                # Try to process with Gemini
                gemini_file = self.note_generator.upload_to_gemini(pdf_path)
                structure = self.note_generator.extract_pdf_structure(gemini_file)

                # Store PDF structure in database
                self.db.create_pdf_structure(pdf_id, structure)

                # Update status to 'completed' if successful
                self.db.update_pdf_status(pdf_id, "completed")

                return {
                    "message": "PDF processed successfully",
                    "pdf_id": pdf_id,
                    "structure": structure,
                }, 200

            except Exception as e:
                # If Gemini processing fails, mark as 'failed' but keep the record
                if pdf_id:
                    self.db.update_pdf_status(pdf_id, "failed", str(e))
                logger.error(f"Error processing PDF content: {str(e)}")
                return {
                    "error": "Failed to process PDF content",
                    "pdf_id": pdf_id,
                    "status": "failed",
                    "can_retry": True,
                }, 500

        except Exception as e:
            # If initial upload fails, clean up everything
            if pdf_path and Path(pdf_path).exists():
                self.cleanup_failed_upload(pdf_path)
            if pdf_id:
                self.db.delete_pdf(pdf_id)
            logger.error(f"Error processing PDF upload: {str(e)}")
            return {"error": str(e)}, 500

    async def extract_images_from_pdf(self, username: str, pdf_path: Path) -> str:
        """Extract images from PDF and save them to user's directory."""
        try:
            # Create unique folder name for images
            base_folder = pdf_path.stem
            folder_hash = hashlib.sha256(str(datetime.now()).encode()).hexdigest()[:8]
            image_folder = f"{base_folder}_{folder_hash}"

            # Create images directory
            output_folder = Path("uploads") / username / "images" / image_folder
            output_folder.mkdir(parents=True, exist_ok=True)

            # Extract images using note_generator
            image_files = self.note_generator.extract_images_from_pdf(
                pdf_path, output_folder
            )

            logger.info(f"Extracted {len(image_files)} images to {output_folder}")
            return image_folder

        except Exception as e:
            logger.error(f"Error extracting images: {str(e)}")
            raise

    def cleanup_failed_upload(self, pdf_path: Path) -> None:
        """Clean up files and DB records for failed uploads."""
        try:
            # Delete the PDF file
            if pdf_path.exists():
                pdf_path.unlink()

            # Delete the images folder if it exists
            image_folder = pdf_path.parent / f"{pdf_path.stem}_18e1b007"
            if image_folder.exists():
                shutil.rmtree(image_folder)

            # Delete database record if it exists
            self.db.delete_pdf_by_path(str(pdf_path))

            logger.info(f"Cleaned up failed upload: {pdf_path}")

        except Exception as e:
            logger.error(f"Error cleaning up failed upload: {str(e)}")

    async def retry_pdf_processing(self, pdf_id: int) -> Tuple[Dict, int]:
        """Retry processing a failed PDF."""
        try:
            pdf_info = self.db.get_pdf_info(pdf_id)
            if not pdf_info:
                return {"error": "PDF not found"}, 404

            if pdf_info["status"] != "failed":
                return {"error": "PDF is not in failed state"}, 400

            pdf_path = Path(pdf_info["pdf_path"])
            if not pdf_path.exists():
                return {"error": "PDF file not found"}, 404

            # Try processing again
            gemini_file = self.note_generator.upload_to_gemini(pdf_path)
            chapters = await self.process_pdf_content(pdf_id, pdf_path, gemini_file)

            # Update status to 'completed' if successful
            self.db.update_pdf_status(pdf_id, "completed")

            return {
                "message": "PDF reprocessed successfully",
                "chapters": chapters,
            }, 200

        except Exception as e:
            logger.error(f"Error retrying PDF processing: {str(e)}")
            self.db.update_pdf_status(pdf_id, "failed", str(e))
            return {"error": str(e)}, 500
