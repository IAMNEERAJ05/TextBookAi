import logging
from fastapi import (
    FastAPI,
    Request,
    UploadFile,
    File as FastAPIFile,
    HTTPException,
    Form,
    status,
    Depends,
    BackgroundTasks,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import bcrypt

from pdf import NoteGenerator
from services import NoteService, UserService, FileService
from file_utils import save_uploaded_file
from db import DatabaseManager

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/images", StaticFiles(directory="uploads"), name="images")
templates = Jinja2Templates(directory="templates")

# Initialize services
note_service = NoteService()
user_service = UserService()
file_service = FileService()
db = DatabaseManager()
note_generator = NoteGenerator()

# Initialize scheduler
scheduler = AsyncIOScheduler()


async def get_current_user(request: Request) -> Optional[str]:
    """Get current authenticated user from session."""
    return request.session.get("username")


async def require_auth(request: Request, username: str = Depends(get_current_user)):
    """Dependency to require authentication."""
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return username


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render home page with user data."""
    username = request.session.get("username")
    pdfs = []

    if username:
        # Get user's PDFs if logged in
        response, _ = await file_service.get_user_pdfs(username)
        pdfs = response.get("pdfs", [])

    return templates.TemplateResponse(
        "index.html", {"request": request, "username": username, "pdfs": pdfs}
    )


@app.get("/topic/{chapter}/{topic}", response_class=HTMLResponse)
async def get_topic_page(
    request: Request, chapter: str, topic: str, username: str = Depends(require_auth)
):
    """Render topic page."""
    try:
        # Get existing notes if available
        result = db.get_topic_notes(chapter, topic)
        if not result:
            raise HTTPException(status_code=404, detail="Topic not found")

        # Always return empty notes if they don't exist or are empty
        # This will trigger the frontend to fetch them via API
        existing_notes = None

        return templates.TemplateResponse(
            "topic.html",
            {
                "request": request,
                "chapter": chapter,
                "topic": topic,
                "notes": existing_notes,  # Always None to trigger frontend fetch
                "username": username,
            },
        )
    except Exception as e:
        logger.error(f"Error rendering topic page: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/subtopic/{chapter}/{topic}/{subtopic}", response_class=HTMLResponse)
async def get_subtopic_page(
    request: Request,
    chapter: str,
    topic: str,
    subtopic: str,
    username: str = Depends(require_auth),
):
    """Render subtopic page."""
    try:
        # Always return empty notes to trigger frontend fetch
        return templates.TemplateResponse(
            "subtopic.html",
            {
                "request": request,
                "chapter": chapter,
                "topic": topic,
                "subtopic": subtopic,
                "notes": None,  # Always None to trigger frontend fetch
                "username": username,
            },
        )
    except Exception as e:
        logger.error(f"Error rendering subtopic page: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/topic_notes/{chapter}/{topic}")
async def get_topic_notes_api(
    request: Request, chapter: str, topic: str, username: str = Depends(require_auth)
):
    """API endpoint for topic notes."""
    response, status_code = await note_service.get_topic_notes(chapter, topic)
    return JSONResponse(content=response, status_code=status_code)


@app.get("/api/notes/{chapter}/{topic}/{subtopic}")
async def get_notes_api(
    request: Request,
    chapter: str,
    topic: str,
    subtopic: str,
    username: str = Depends(require_auth),
):
    """API endpoint for subtopic notes."""
    response, status_code = await note_service.get_subtopic_notes(
        chapter, topic, subtopic
    )
    return JSONResponse(content=response, status_code=status_code)


@app.post("/upload_pdf/")
async def upload_pdf(request: Request, file: UploadFile = FastAPIFile(...)):
    """Handle PDF upload."""
    try:
        # Get username from session
        username = request.session.get("username")
        if not username:
            return JSONResponse(
                content={"error": "User not authenticated"}, status_code=401
            )

        # Process the uploaded file
        response, status_code = await file_service.process_pdf_upload(file, username)
        return JSONResponse(content=response, status_code=status_code)
    except Exception as e:
        logger.error(f"Error uploading PDF: {str(e)}")
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render login page."""
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/signup", response_class=HTMLResponse)
async def register_page(request: Request):
    """Render registration page."""
    return templates.TemplateResponse("signup.html", {"request": request})


@app.post("/login")
async def login(
    request: Request,
    login: str = Form(...),  # Changed from username to login to match form
    password: str = Form(...),
    login_method: str = Form(...),  # Added to handle email/username login
):
    """Handle user login."""
    try:
        # Determine if login is email or username
        if login_method == "email":
            response, status_code = await user_service.login_user_by_email(
                login, password
            )
        else:
            response, status_code = await user_service.login_user(login, password)

        if status_code == 200:
            request.session["username"] = response.get(
                "username"
            )  # Store username from response

        return JSONResponse(content=response, status_code=status_code)
    except Exception as e:
        logger.error(f"Error during login: {str(e)}")
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)


@app.get("/logout")
async def logout(request: Request):
    """Handle user logout."""
    try:
        request.session.clear()
        return await home(request)
    except Exception as e:
        logger.error(f"Error during logout: {str(e)}")
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)


@app.get("/api/user_pdfs")
async def get_user_pdfs(request: Request):
    """Get list of user's PDFs."""
    try:
        username = request.session.get("username")
        if not username:
            return JSONResponse(
                content={"error": "User not authenticated"}, status_code=401
            )

        response, status_code = await file_service.get_user_pdfs(username)
        return JSONResponse(content=response, status_code=status_code)
    except Exception as e:
        logger.error(f"Error getting user PDFs: {str(e)}")
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)


# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions."""
    return JSONResponse(content={"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions."""
    logger.error(f"Unhandled exception: {str(exc)}")
    return JSONResponse(content={"error": "Internal server error"}, status_code=500)


# Update the existing register endpoint to handle signup
@app.post("/signup")
async def signup(
    request: Request,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    """Handle user signup."""
    try:
        # First check if email exists
        if await user_service.email_exists(email):
            return JSONResponse(
                content={"error": "Email already exists"}, status_code=400
            )

        # Then try to register the user
        response, status_code = await user_service.register_user(
            username=username, password=password, email=email
        )
        return JSONResponse(content=response, status_code=status_code)
    except Exception as e:
        logger.error(f"Error during signup: {str(e)}")
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)


@app.delete("/delete_pdf/{pdf_id}")
async def delete_pdf(request: Request, pdf_id: int):
    """Delete a PDF and its associated data."""
    try:
        # Get username from session
        username = request.session.get("username")
        if not username:
            return JSONResponse(
                content={"error": "User not authenticated"}, status_code=401
            )

        # Delete the PDF using FileService
        response, status_code = await file_service.delete_pdf(pdf_id, username)
        return JSONResponse(content=response, status_code=status_code)

    except Exception as e:
        logger.error(f"Error deleting PDF: {str(e)}")
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)


@app.get("/quiz/{chapter}", response_class=HTMLResponse)
async def quiz_page(request: Request, chapter: str):
    """Render quiz page for a chapter."""
    return templates.TemplateResponse(
        "quiz.html", {"request": request, "chapter": chapter}
    )


@app.get("/api/quiz/{chapter}")
async def get_quiz(request: Request, chapter: str, new: bool = False):
    """Get quiz questions for a chapter."""
    try:
        username = request.session.get("username")
        if not username:
            return JSONResponse(
                content={"error": "User not authenticated"}, status_code=401
            )

        # Check if a quiz already exists and we're not forcing a new one
        if not new:
            existing_quiz = db.get_latest_quiz(chapter)
            if existing_quiz:
                questions = db.get_quiz_questions(existing_quiz["quizid"])
                if questions:
                    return JSONResponse(
                        content={
                            "quiz_id": existing_quiz["quizid"],
                            "questions": questions,
                        },
                        status_code=200,
                    )

        # If no quiz exists, generate a new one
        chapter_info = db.get_chapter_info(chapter)
        if not chapter_info:
            return JSONResponse(content={"error": "Chapter not found"}, status_code=404)

        # Get the PDF file and upload to Gemini
        pdf_path = Path(chapter_info["pdf_path"])
        gemini_file = note_generator.upload_to_gemini(pdf_path)

        # Generate quiz questions
        questions = note_generator.generate_quiz_questions(gemini_file, chapter)

        # Store quiz in database
        quiz_id = db.store_quiz_questions(chapter, questions)

        # Return only questions and options (no answers)
        questions_only = [
            {"questionid": i + 1, "question": q["question"], "options": q["options"]}
            for i, q in enumerate(questions)
        ]

        return JSONResponse(
            content={"quiz_id": quiz_id, "questions": questions_only}, status_code=200
        )

    except Exception as e:
        logger.error(f"Error generating quiz: {str(e)}")
        return JSONResponse(
            content={"error": "Failed to generate quiz"}, status_code=500
        )


@app.get("/api/quiz/{quiz_id}/answers")
async def get_quiz_answers(request: Request, quiz_id: int):
    """Get answers for a completed quiz."""
    try:
        username = request.session.get("username")
        if not username:
            return JSONResponse(
                content={"error": "User not authenticated"}, status_code=401
            )

        answers = db.get_quiz_answers(quiz_id)
        return JSONResponse(content={"answers": answers}, status_code=200)

    except Exception as e:
        logger.error(f"Error getting quiz answers: {str(e)}")
        return JSONResponse(
            content={"error": "Failed to get quiz answers"}, status_code=500
        )


@app.get("/book/{pdf_id}", response_class=HTMLResponse)
async def book_page(request: Request, pdf_id: int):
    """Render book structure page."""
    try:
        username = request.session.get("username")
        if not username:
            raise HTTPException(status_code=401, detail="User not authenticated")

        # Get PDF info and structure
        pdf_info = db.get_pdf_info(pdf_id)
        if not pdf_info:
            raise HTTPException(status_code=404, detail="PDF not found")

        if pdf_info["username"] != username:
            raise HTTPException(status_code=403, detail="Unauthorized")

        structure = db.get_pdf_structure(pdf_id)

        return templates.TemplateResponse(
            "book.html",
            {
                "request": request,
                "pdf_info": pdf_info,
                "structure": structure,
                "username": username,
            },
        )
    except Exception as e:
        logger.error(f"Error rendering book page: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/book/{pdf_id}")
async def get_book_structure(pdf_id: int):
    """Get book structure."""
    try:
        structure = db.get_pdf_structure(pdf_id)
        return JSONResponse(content={"structure": structure})
    except Exception as e:
        logger.error(f"Error getting book structure: {str(e)}")
        return JSONResponse(
            content={"error": "Failed to get book structure"}, status_code=500
        )


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """Render user profile page."""
    try:
        username = request.session.get("username")
        if not username:
            return RedirectResponse(url="/login", status_code=303)

        # Get user profile data and detailed statistics
        user_data = db.get_user_profile(username)
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")

        # Get detailed user statistics
        stats = db.get_user_detailed_statistics(username)

        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "username": username,
                "user_data": user_data,
                "stats": stats,
            },
        )
    except Exception as e:
        logger.error(f"Error rendering profile page: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


@app.post("/update-profile")
async def update_profile(
    request: Request,
    email: str = Form(...),
    current_password: str = Form(None),
    new_password: str = Form(None),
):
    """Update user profile information."""
    try:
        username = request.session.get("username")
        if not username:
            return JSONResponse(content={"error": "Not authenticated"}, status_code=401)

        # Verify current password if changing password
        if new_password:
            if not current_password:
                return JSONResponse(
                    content={"error": "Current password required"}, status_code=400
                )

            user = db.get_user(username)
            if not user or not verify_password(current_password, user["password_hash"]):
                return JSONResponse(
                    content={"error": "Invalid current password"}, status_code=400
                )

        # Update profile
        updates = {"email": email}
        if new_password:
            updates["password_hash"] = hash_password(new_password)

        db.update_user_profile(username, updates)

        return JSONResponse(content={"message": "Profile updated successfully"})

    except Exception as e:
        logger.error(f"Error updating profile: {str(e)}")
        return JSONResponse(
            content={"error": "Failed to update profile"}, status_code=500
        )


async def check_db_connection():
    """Background task to check database connection."""
    try:
        db.check_connection_health()
    except Exception as e:
        logger.error(f"Database health check failed: {str(e)}")


@app.on_event("startup")
async def startup_event():
    """Start the scheduler on app startup."""
    # More frequent checks during development
    scheduler.add_job(
        check_db_connection,
        "interval",
        minutes=2,  # Check every 2 minutes
        max_instances=1,  # Prevent overlapping executions
        coalesce=True,
    )  # Combine missed executions
    scheduler.start()


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on app shutdown."""
    scheduler.shutdown()
    if hasattr(db, "pool"):
        if not db.pool:
            raise ValueError("Database connection pool not established")
        db.pool.closeall()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)