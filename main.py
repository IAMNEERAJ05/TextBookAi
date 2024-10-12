from fastapi.responses import HTMLResponse
from fastapi import (
    FastAPI,
    Request,
    Form,
    UploadFile,
    File,
)
from starlette.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from pathlib import Path
import os
from starlette.middleware.sessions import SessionMiddleware
import shutil
from pdf import upload_to_gemini, generate_topics
from passlib.context import CryptContext
import logging
from db import hash_password, verify_password
from fastapi.templating import Jinja2Templates
from pdf import generate_notes
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles

load_dotenv()


# Set up FastAPI
app = FastAPI()
# Mount the static directory
app.mount("/static", StaticFiles(directory="static"), name="static")

# Add session middleware (from starlette)
app.add_middleware(SessionMiddleware, secret_key="your_secret_key_here")

# Set up Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# Mock database connection function
def get_db_connection():
    """Establish and return a connection to the database."""
    return psycopg2.connect(
        database=os.getenv("SUPABASE_DATABASE"),
        user=os.getenv("SUPABASE_USER"),
        password=os.getenv("SUPABASE_PASSWORD"),
        host=os.getenv("SUPABASE_HOST"),
    )


# Sign-up route
@app.get("/signup", response_class=HTMLResponse)
async def get_signup(request: Request):
    """Render the signup page."""
    return templates.TemplateResponse("signup.html", {"request": request})


@app.post("/signup")
async def signup(
    request: Request,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    """Handle user signup process."""
    conn = get_db_connection()
    cur = conn.cursor()
    hashed_password = hash_password(password)
    try:
        # Insert new user into the database
        cur.execute(
            "INSERT INTO authentication (email, username, password) VALUES (%s, %s, %s)",
            (email, username, hashed_password),
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        # Handle case where email or username already exists
        conn.rollback()
        return HTMLResponse(status_code=400, content="Email or Username already exists")
    finally:
        cur.close()
        conn.close()

    return RedirectResponse(url="/login", status_code=302)


# Login route
@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request):
    """Render the login page."""
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
):
    """Handle user login process."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Fetch user from database
        cur.execute(
            "SELECT * FROM authentication WHERE email = %s or username = %s",
            (login, login),
        )
        user = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    # Verify user credentials
    if not user or not verify_password(password, user["password"]):
        return HTMLResponse(status_code=400, content="Invalid email or password")

    # Store user information in session
    request.session["email"] = user["email"]
    request.session["username"] = user["username"]

    return RedirectResponse(url="/", status_code=302)


# Configure logging
logging.basicConfig(level=logging.INFO)


@app.get("/logout")
def logout(request: Request):
    """Handle user logout process."""
    username = request.session.get("username")
    logging.info(f"Logging out user: {username}")

    # Clear the session
    request.session.clear()

    # Redirect to home page
    response = RedirectResponse(url="/")
    logging.info("Session cleared. Redirecting to home page.")
    return response


# Home route
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page."""
    username = request.session.get("username")
    email = request.session.get("email")
    return templates.TemplateResponse(
        "index.html", {"request": request, "username": username, "email": email}
    )


@app.post("/upload_pdf/")
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    """Handle PDF upload and store file reference in session."""
    username = request.session.get("username")
    if not username:
        return JSONResponse(
            content={"error": "You need to be logged in to upload a file."},
            status_code=401,
        )

    # Validate file type
    if file.content_type != "application/pdf":
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
