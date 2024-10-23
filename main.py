import traceback
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
from pathlib import Path

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


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page"""

    username = request.session.get("username")
    email = request.session.get("email")
    if not username:
        return JSONResponse(
            content={"error": "You need to be logged in to upload a file."},
            status_code=401,
        )
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Fetch only PDFs uploaded by the logged-in user
        cur.execute(
            """
            SELECT p.pdfid, p.pdf_path, p.username, 
                   c.chapterid, c.chaptername,
                   t.topicid, t.topicname,
                   s.subtopicid, s.subtopicname, s.content  
            FROM pdfs p
            LEFT JOIN chapters c ON p.pdfid = c.pdfid
            LEFT JOIN topics t ON c.chapterid = t.chapterid
            LEFT JOIN subtopics s ON t.topicid = s.topicid
            WHERE p.username = %s
            ORDER BY p.pdfid, c.chapterid, t.topicid, s.subtopicid
            """,
            (username,),  # Pass the current user's username to the query
        )
        pdfs = {}
        for row in cur.fetchall():
            pdfid = row["pdfid"]
            if pdfid not in pdfs:
                pdfs[pdfid] = {"pdf_path": row["pdf_path"], "chapters": {}}

            chapterid = row["chapterid"]
            if chapterid and chapterid not in pdfs[pdfid]["chapters"]:
                pdfs[pdfid]["chapters"][chapterid] = {
                    "chaptername": row["chaptername"],
                    "topics": {},
                }

            topicid = row["topicid"]
            if topicid and topicid not in pdfs[pdfid]["chapters"][chapterid]["topics"]:
                pdfs[pdfid]["chapters"][chapterid]["topics"][topicid] = {
                    "topicname": row["topicname"],
                    "subtopics": {},
                }

            subtopicid = row["subtopicid"]
            if subtopicid:
                pdfs[pdfid]["chapters"][chapterid]["topics"][topicid]["subtopics"][
                    subtopicid
                ] = {
                    "subtopicname": row["subtopicname"],
                    "content": row["content"],
                }

        # Convert to a list of PDFs
        pdf_list = [
            {
                "pdfid": pdfid,
                "pdf_path": pdf_info["pdf_path"],
                "chapters": [
                    {
                        "chapterid": chapterid,
                        "chaptername": chapter_info["chaptername"],
                        "topics": [
                            {
                                "topicid": topicid,
                                "topicname": topic_info["topicname"],
                                "subtopics": [
                                    {
                                        "subtopicid": subtopicid,
                                        "subtopicname": subtopic_info["subtopicname"],
                                        "content": subtopic_info["content"],
                                    }
                                    for subtopicid, subtopic_info in topic_info[
                                        "subtopics"
                                    ].items()
                                ],
                            }
                            for topicid, topic_info in chapter_info["topics"].items()
                        ],
                    }
                    for chapterid, chapter_info in pdf_info["chapters"].items()
                ],
            }
            for pdfid, pdf_info in pdfs.items()
        ]

    finally:
        cur.close()
        conn.close()

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "username": username, "email": email, "pdfs": pdf_list},
    )


@app.post("/upload_pdf/")
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    """Handle PDF upload and store file details in the database."""
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
    file_path = upload_folder / file.filename  # type: ignore
    with file_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # Store the PDF path and username in the 'pdfs' table
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO pdfs (pdf_path, username) 
            VALUES (%s, %s) RETURNING pdfid
            """,
            (str(file_path), username),
        )
        pdf_row = cur.fetchone()
        if pdf_row:
            pdf_id = pdf_row[0]
        else:
            return JSONResponse(
                content={"error": "Failed to retrieve pdfid after insertion."},
                status_code=500,
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        return JSONResponse(
            content={"error": "Database error: " + str(e)}, status_code=500
        )
    finally:
        cur.close()

    uploaded_file = upload_to_gemini(file_path)

    # Store only the file name in the session
    request.session["uploaded_file_name"] = file_path.name

    # Generate topics after file upload
    topics_data = generate_topics(uploaded_file)

    # Store the chapters, topics, and subtopics in the database
    try:
        for chapter_data in topics_data:  # Assuming topics_data is a list
            chapter_name = chapter_data["chapter"]
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO chapters (pdfid, chaptername) 
                VALUES (%s, %s) RETURNING chapterid
                """,
                (pdf_id, chapter_name),
            )
            chapter_row = cur.fetchone()
            if chapter_row:
                chapter_id = chapter_row[0]
            else:
                return JSONResponse(
                    content={"error": "Failed to retrieve chapterid after insertion."},
                    status_code=500,
                )
            conn.commit()
            cur.close()

            for topic_data in chapter_data["topics"]:
                topic_name = topic_data["topic"]
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO topics (chapterid, topicname) 
                    VALUES (%s, %s) RETURNING topicid
                    """,
                    (chapter_id, topic_name),
                )
                topics_row = cur.fetchone()
                if topics_row:
                    topic_id = topics_row[0]
                else:
                    return JSONResponse(
                        content={
                            "error": "Failed to retrieve topicid after insertion."
                        },
                        status_code=500,
                    )
                conn.commit()
                cur.close()

                for subtopic_data in topic_data.get("sub_topics", []):
                    if isinstance(subtopic_data, str):  # Handle subtopics as strings
                        subtopic_name = (
                            subtopic_data  # Use the string as the subtopic name
                        )
                        subtopic_content = None  # If no content is available, set it to None or leave it empty

                        cur = conn.cursor()
                        cur.execute(
                            """
                            INSERT INTO subtopics (topicid, subtopicname, content) 
                            VALUES (%s, %s, %s)
                            """,
                            (topic_id, subtopic_name, subtopic_content),
                        )
                        conn.commit()
                        cur.close()
                    else:
                        print("Warning: Subtopic data is not a string.", subtopic_data)

    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        return JSONResponse(
            content={"error": "Database error: " + str(e)}, status_code=500
        )
    finally:
        conn.close()

    return {
        "message": "File uploaded and processed successfully.",
        "file_name": file_path.name,
        "topics": topics_data,
    }


@app.get("/notes/")
async def get_notes(request: Request, chapter: str, topic: str, subtopic: str):
    """Generate notes for the subtopic if not already present and render the notes.html template."""
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
    conn = get_db_connection()
    cur = conn.cursor()

    if not topic.isdigit():
        cur.execute(
            """
        SELECT s.content, t.topicname, s.subtopicname, c.chaptername
        FROM subtopics s
        JOIN topics t ON s.topicid = t.topicid
        JOIN chapters c ON t.chapterid = c.chapterid
        WHERE c.chaptername = %s AND t.topicname = %s AND s.subtopicname = %s
        """,
            (chapter, topic, subtopic),
        )
        notes = ""
        result = cur.fetchone()
        if result and result[0]:  # If content exists and is not None/empty
            notes = result[0]
        else:
            try:

                notes = generate_notes(chapter, topic, subtopic, file_path)
                if not notes.strip():
                    notes = "No notes generated for this subtopic."

                cur.execute(
                    """
                    UPDATE subtopics
                    SET content = %s
                    WHERE subtopicname = %s
                    """,
                    (notes, subtopic),
                )
                conn.commit()
                cur.close()
                conn.close()
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

    # Fetch the existing subtopic content and the topic and subtopic names from the database
    cur.execute(
        """
        SELECT s.content, t.topicname, s.subtopicname
        FROM subtopics s
        JOIN topics t ON s.topicid = t.topicid
        WHERE s.subtopicid = %s
        """,
        (subtopic,),
    )
    topic_name = ""
    subtopic_name = ""
    result = cur.fetchone()
    # Check if content exists in the database
    if (
        result and result[0] and result[1] and result[2]
    ):  # If content exists and is not None/empty
        notes = result[0]
        topic_name = result[1]
        subtopic_name = result[2]
    else:
        # Fetch the topic name and subtopic name from the result
        topic_name = result[1]  # type: ignore
        subtopic_name = result[2]  # type: ignore

        # Generate notes if no content is found
        try:
            notes = generate_notes(chapter, topic_name, subtopic_name, file_path)
            if not notes.strip():
                notes = "No notes generated for this subtopic."

            # Update the subtopic content in the database with the generated notes
            cur.execute(
                """
                UPDATE subtopics
                SET content = %s
                WHERE subtopicid = %s
                """,
                (notes, subtopic),
            )
            conn.commit()

        except Exception as e:
            logging.error(f"Error generating notes: {str(e)}")
            notes = f"Error generating notes: {str(e)}"

    cur.close()
    conn.close()

    # Render the template with the existing or generated notes
    return templates.TemplateResponse(
        "notes.html",
        {
            "request": request,
            "chapter": chapter,
            "topic": topic,  # Pass the correct topic name
            "subtopic": subtopic_name,  # Pass the correct subtopic name  # type: ignore
            "notes": notes,  # type: ignore
        },
    )
