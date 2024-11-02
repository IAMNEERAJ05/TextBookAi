from fastapi.responses import HTMLResponse
from fastapi import (
    FastAPI,
    Request,
    UploadFile,
    File,
)
from starlette.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles

load_dotenv()

# Initialize FastAPI app
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")

# Add session middleware (from starlette)
app.add_middleware(SessionMiddleware, secret_key="your_secret_key_here")

# Set up Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Initialize services
note_service = NoteService()
user_service = UserService()
file_service = FileService()
db = DatabaseManager()
note_generator = NoteGenerator()


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
async def get_topic_notes(
    request: Request, chapter: str, topic: str, username: str = Depends(require_auth)
):
    """API endpoint to get topic notes."""
    try:
        logger.info(f"Fetching notes for chapter: {chapter}, topic: {topic}")
        response, status_code = await note_service.get_topic_notes(chapter, topic)
        logger.info(f"Response status: {status_code}, content: {response}")
        return JSONResponse(content=response, status_code=status_code)
    except Exception as e:
        logger.error(f"Error getting topic notes: {str(e)}")
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)


@app.get("/api/notes/{chapter}/{topic}/{subtopic}")
async def get_notes(
    request: Request,
    chapter: str,
    topic: str,
    subtopic: str,
    username: str = Depends(require_auth),
):
    """API endpoint to get subtopic notes."""
    try:
        response, status_code = await note_service.get_subtopic_notes(
            chapter, topic, subtopic
        )
        return JSONResponse(content=response, status_code=status_code)
    except Exception as e:
        logger.error(f"Error getting subtopic notes: {str(e)}")
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)


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


# Add these routes after your existing routes


@app.get("/quiz/{chapter}", response_class=HTMLResponse)
async def quiz_page(request: Request, chapter: str):
    """Render quiz page for a chapter."""
    return templates.TemplateResponse(
        "quiz.html", {"request": request, "chapter": chapter}
    )


def check_existing_pdf(pdf_path: str) -> bool:
    """Check if a PDF already exists in the database by its path."""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        with conn.cursor() as cursor:
            query = "SELECT * FROM pdfs WHERE pdf_path = %s;"
            cursor.execute(query, (pdf_path,))
            return cursor.fetchone() is not None
    except psycopg2.Error as e:
        print(f"Error checking PDF existence: {e}")
    finally:
        conn.close()
    return False


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

        # If we get here, either:
        # 1. No existing quiz was found
        # 2. No questions were found for the existing quiz
        # 3. new=True was specified

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
            content={"error": "Invalid file type. Please upload a PDF file."},
            status_code=400,
        )

    # Ensure the upload folder exists
    upload_folder = Path("uploads")
    upload_folder.mkdir(exist_ok=True)

    # Save the file locally
    file_path = upload_folder / os.path.basename(str(file.filename))
    with file_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # Upload the file to Gemini and store its URI in the session
    uploaded_file = upload_to_gemini(file_path)

    # Store only the file name in the session
    request.session["uploaded_file_name"] = file_path.name

    # Generate topics after file upload
    topics = generate_topics(uploaded_file)

    return {"message": "File uploaded", "file_name": file_path.name, "topics": topics}


@app.get("/notes/")
async def get_notes(request: Request, chapter: str, topic: str, subtopic: str):
    """Generate notes for the subtopic and render the notes.html template."""
    file_name = request.session.get("uploaded_file_name")
    if not file_name:
        return HTMLResponse(
            "No file found in session. Please upload a PDF.", status_code=400
        )

    # Retrieve the file from storage
    file_path = Path("uploads") / file_name
    if not file_path.exists():
        return HTMLResponse(
            "File not found in storage. Please upload the PDF again.", status_code=400
        )

    # Generate notes
    try:
        notes = generate_notes(chapter, topic, subtopic, file_path)
        if not notes.strip():
            notes = "No notes generated for this subtopic."
    except Exception as e:
        logging.error(f"Error generating notes: {str(e)}")
        notes = f"Error generating notes: {str(e)}"

    # Render the template with the generated notes
    return templates.TemplateResponse(
        "notes.html",
        {
            "request": request,
            "chapter": chapter,
            "topic": topic,
            "subtopic": subtopic,
            "notes": notes,
        },
    )
