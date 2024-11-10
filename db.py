from passlib.context import CryptContext
import logging
import json
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Dict, Optional, Tuple, Any, List
import os
from dotenv import load_dotenv
from psycopg2 import pool
from contextlib import contextmanager
from psycopg2.pool import SimpleConnectionPool
from tenacity import retry, stop_after_attempt, wait_exponential
import time

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self):
        self.pool = None
        self.create_pool()

    def create_pool(self):
        """Create a new connection pool."""
        try:
            if self.pool:
                self.pool.closeall()

            self.pool = SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                dbname=os.getenv("SUPABASE_DATABASE"),
                user=os.getenv("SUPABASE_USER"),
                password=os.getenv("SUPABASE_PASSWORD"),
                host=os.getenv("SUPABASE_HOST"),
                # Connection timeout set to 5 minutes (300 seconds)
                connect_timeout=300,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
            logger.info("Database connection pool established")
        except Exception as e:
            logger.error(f"Error creating connection pool: {str(e)}")
            raise

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    @contextmanager
    def get_connection(self):
        """Get a connection from the pool with retry logic."""
        if not self.pool:
            raise ValueError("Database connection pool not established")

        conn = None
        try:
            conn = self.pool.getconn()

            # Test if connection is alive
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

            yield conn

        except Exception as e:
            logger.error(f"Database connection error: {str(e)}")
            if conn:
                try:
                    self.pool.putconn(conn, close=True)
                except Exception:
                    pass  # Ignore errors when closing bad connection
            conn = None  # Ensure conn is None after handling error

            # Recreate pool if connection failed
            try:
                self.create_pool()
            except Exception as e:
                logger.error(f"Failed to recreate connection pool: {str(e)}")
            raise

        finally:
            if conn:
                try:
                    self.pool.putconn(conn)
                except Exception as e:
                    logger.error(f"Error returning connection to pool: {str(e)}")
                    # If we can't return the connection, close the pool and create a new one
                    try:
                        self.create_pool()
                    except Exception:
                        pass

    def __del__(self):
        """Clean up the connection pool when the instance is destroyed."""
        if hasattr(self, "pool") and self.pool:
            try:
                self.pool.closeall()
            except Exception as e:
                logger.error(f"Error closing connection pool: {str(e)}")

    def get_topic_notes(self, chapter: str, topic: str) -> Optional[Dict]:
        """Get topic notes from database if they exist."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT t.notes, t.images, p.pdf_path, p.username, p.pdfid, t.topicid
                    FROM topics t
                    JOIN chapters c ON t.chapterid = c.chapterid
                    JOIN pdfs p ON c.pdfid = p.pdfid
                    WHERE c.chaptername = %s AND t.topicname = %s
                    """,
                    (chapter, topic),
                )
                result = cur.fetchone()
                if result:
                    # Only log if actual notes content exists
                    if result["notes"] is not None and result["notes"].strip():
                        logger.info(
                            f"Found notes in database for topic: {chapter}/{topic}"
                        )
                    else:
                        logger.info(
                            f"Found topic record but no notes content for: {chapter}/{topic}"
                        )
                return result
            except Exception as e:
                logger.error(f"Database error in get_topic_notes: {str(e)}")
                raise
            finally:
                cur.close()

    def store_topic_notes(self, topic_id: int, notes: str, images: list) -> bool:
        """Store topic notes in database."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    UPDATE topics 
                    SET notes = %s, 
                        images = %s::jsonb,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE topicid = %s
                    RETURNING topicid
                    """,
                    (notes, json.dumps(images), topic_id),
                )
                updated = cur.fetchone()
                conn.commit()
                logger.info(f"Successfully stored notes for topic_id: {topic_id}")
                return bool(updated)
            except Exception as e:
                logger.error(f"Database error in store_topic_notes: {str(e)}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def get_subtopic_notes(
        self, chapter: str, topic: str, subtopic: str
    ) -> Optional[Dict]:
        """Get subtopic notes from database."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT 
                        s.notes, 
                        s.images::text, 
                        s.subtopicid,
                        p.pdf_path, 
                        p.username, 
                        p.pdfid
                    FROM subtopics s
                    JOIN topics t ON s.topicid = t.topicid
                    JOIN chapters c ON t.chapterid = c.chapterid
                    JOIN pdfs p ON c.pdfid = p.pdfid
                    WHERE 
                        c.chaptername = %s 
                        AND t.topicname = %s 
                        AND s.subtopicname = %s
                    """,
                    (chapter, topic, subtopic),
                )
                result = cur.fetchone()

                if result:
                    logger.info(
                        f"Found subtopic record for: {chapter}/{topic}/{subtopic}"
                    )
                    # Parse the JSON string if it exists, otherwise use empty list
                    if result["images"]:
                        result["images"] = json.loads(result["images"])
                    else:
                        result["images"] = []
                    return result
                else:
                    logger.warning(
                        f"No subtopic record found for: {chapter}/{topic}/{subtopic}"
                    )
                    return None

            except Exception as e:
                logger.error(f"Error getting subtopic notes: {str(e)}")
                raise
            finally:
                cur.close()

    def store_subtopic_notes(
        self, subtopic_id: int, notes: str, images: List[Dict]
    ) -> None:
        """Store generated notes for a subtopic."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    UPDATE subtopics 
                    SET notes = %s, 
                        images = %s::jsonb,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE subtopicid = %s
                    """,
                    (
                        notes,
                        json.dumps(images),
                        subtopic_id,
                    ),  # Convert images to JSON string
                )
                conn.commit()
                logger.info(f"Stored notes for subtopic ID: {subtopic_id}")

            except Exception as e:
                logger.error(f"Error storing subtopic notes: {str(e)}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def create_pdf_record(
        self, pdf_path: str, username: str, filename: str, status: str = "pending"
    ) -> int:
        """Create PDF record and return ID."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO pdfs (pdf_path, username, title, status, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    RETURNING pdfid
                    """,
                    (pdf_path, username, filename, status),
                )
                pdf_id = cur.fetchone()[0]
                conn.commit()
                logger.info(f"Created PDF record: {pdf_path} with ID: {pdf_id}")
                return pdf_id
            except Exception as e:
                conn.rollback()
                logger.error(f"Error creating PDF record: {str(e)}")
                raise
            finally:
                cur.close()

    def store_gemini_file(
        self, pdf_path: str, username: str, gemini_file_dict: Dict
    ) -> None:
        """Store Gemini file information in database.

        Args:
            pdf_path: Path to the PDF file
            username: Owner of the PDF
            gemini_file_dict: Dictionary containing Gemini file information

        Raises:
            ValueError: If PDF record is not found
            psycopg2.Error: If there's a database error
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    UPDATE pdfs 
                    SET gemini_file = %s::jsonb,
                        status = 'completed'
                    WHERE pdf_path = %s AND username = %s
                    RETURNING pdfid
                    """,
                    (json.dumps(gemini_file_dict), pdf_path, username),
                )
                result = cur.fetchone()
                if not result:
                    raise ValueError(
                        f"No PDF found with path {pdf_path} for user {username}"
                    )

                conn.commit()
                logger.info(f"Updated Gemini file for PDF: {pdf_path}")

            except Exception as e:
                logger.error(f"Error in store_gemini_file: {str(e)}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def get_gemini_file(self, pdf_id: int) -> Optional[Dict]:
        """Get Gemini file information from database."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    "SELECT gemini_file FROM pdfs WHERE pdfid = %s",
                    (pdf_id,),
                )
                result = cur.fetchone()
                if result:
                    logger.info(f"Found Gemini file for PDF ID: {pdf_id}")
                return result
            except Exception as e:
                logger.error(f"Database error in get_gemini_file: {str(e)}")
                raise
            finally:
                cur.close()

    def get_user(self, username: str) -> Optional[Dict]:
        """Get user information from database."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT userid, username, password_hash
                    FROM users
                    WHERE username = %s
                    """,
                    (username,),
                )
                result = cur.fetchone()
                if result:
                    logger.info(f"Found user: {username}")
                return result
            except Exception as e:
                logger.error(f"Database error in get_user: {str(e)}")
                raise
            finally:
                cur.close()

    def create_user(self, username: str, password_hash: str, email: str) -> int:
        """Create a new user in the database."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO users (username, password_hash, email)
                    VALUES (%s, %s, %s)
                    RETURNING userid
                    """,
                    (username, password_hash, email),
                )
                result = cur.fetchone()
                if not result:
                    raise ValueError(f"Failed to create user: {username}")

                user_id = result[0]
                conn.commit()
                logger.info(f"Created new user: {username} with ID: {user_id}")
                return user_id

            except Exception as e:
                logger.error(f"Error creating user: {str(e)}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def get_user_pdfs(self, username: str) -> List[Dict]:
        """Get all PDFs belonging to a user.

        Args:
            username: The username to get PDFs for

        Returns:
            List[Dict]: List of PDF information dictionaries

        Raises:
            psycopg2.Error: If there's a database error
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self.get_connection() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            """
                            SELECT 
                                pdfid,
                                pdf_path,
                                username,
                                gemini_file,
                                created_at as upload_date,
                                title,
                                description
                            FROM pdfs
                            WHERE username = %s
                            ORDER BY created_at DESC
                            """,
                            (username,),
                        )
                        results = cur.fetchall()

                        if results:
                            logger.info(
                                f"Found {len(results)} PDFs for user: {username}"
                            )
                        else:
                            logger.info(f"No PDFs found for user: {username}")

                        return list(results)

            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt == max_retries - 1:
                    logger.error("All retry attempts failed")
                    return []  # Return empty list after all retries fail
                # Wait before retrying
                time.sleep(1 * (attempt + 1))

        return []  # Ensure we always return a list

    def create_chapter(self, chapter_name: str, pdf_id: int) -> int:
        """Create a new chapter in the database."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO chapters (chaptername, pdfid)
                    VALUES (%s, %s)
                    RETURNING chapterid
                    """,
                    (chapter_name, pdf_id),
                )
                result = cur.fetchone()
                if not result:
                    raise ValueError(f"Failed to create chapter: {chapter_name}")

                chapter_id = result[0]
                conn.commit()
                logger.info(f"Created chapter: {chapter_name} with ID: {chapter_id}")
                return chapter_id

            except Exception as e:
                logger.error(f"Error creating chapter: {str(e)}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def create_topic(self, topic_name: str, chapter_id: int) -> int:
        """Create a new topic in the database."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO topics (topicname, chapterid)
                    VALUES (%s, %s)
                    RETURNING topicid
                    """,
                    (topic_name, chapter_id),
                )
                result = cur.fetchone()
                if not result:
                    raise ValueError(f"Failed to create topic: {topic_name}")

                topic_id = result[0]
                conn.commit()
                logger.info(f"Created topic: {topic_name} with ID: {topic_id}")
                return topic_id

            except Exception as e:
                logger.error(f"Error creating topic: {str(e)}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def create_subtopic(
        self,
        subtopic_name: str,
        topic_id: int,
        parent_subtopic_id: Optional[int] = None,
    ) -> int:
        """Create a new subtopic in the database."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO subtopics (subtopicname, topicid, parent_subtopicid)
                    VALUES (%s, %s, %s)
                    RETURNING subtopicid
                    """,
                    (subtopic_name, topic_id, parent_subtopic_id),
                )
                result = cur.fetchone()
                if not result:
                    raise ValueError(f"Failed to create subtopic: {subtopic_name}")

                subtopic_id = result[0]
                conn.commit()
                logger.info(f"Created subtopic: {subtopic_name} with ID: {subtopic_id}")
                return subtopic_id

            except Exception as e:
                logger.error(f"Error creating subtopic: {str(e)}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Get user information by email."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT userid, username, email, password_hash
                    FROM users
                    WHERE email = %s
                    """,
                    (email,),
                )
                result = cur.fetchone()
                return result
            except Exception as e:
                logger.error(f"Database error in get_user_by_email: {str(e)}")
                raise
            finally:
                cur.close()

    def create_pdf_structure(self, pdf_id: int, structure: Dict) -> None:
        """Create PDF structure in database with proper handling of nested subtopics."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                for chapter in structure["chapters"]:
                    # Create chapter
                    cur.execute(
                        """
                        INSERT INTO chapters (pdfid, chaptername)
                        VALUES (%s, %s)
                        RETURNING chapterid
                        """,
                        (pdf_id, chapter["name"]),
                    )
                    result = cur.fetchone()
                    if not result:
                        raise ValueError(f"Failed to create chapter: {chapter['name']}")
                    chapter_id = result[0]

                    # Process topics
                    if "topics" in chapter:
                        for topic in chapter["topics"]:
                            # Create topic
                            cur.execute(
                                """
                                INSERT INTO topics (chapterid, topicname)
                                VALUES (%s, %s)
                                RETURNING topicid
                                """,
                                (chapter_id, topic["name"]),
                            )
                            result = cur.fetchone()
                            if not result:
                                raise ValueError(
                                    f"Failed to create topic: {topic['name']}"
                                )
                            topic_id = result[0]

                            # Process subtopics recursively
                            if "subtopics" in topic:
                                self._create_subtopics(
                                    cur, topic_id, topic["subtopics"]
                                )

                conn.commit()
                logger.info(f"Successfully created structure for PDF {pdf_id}")

            except Exception as e:
                logger.error(f"Error creating PDF structure: {str(e)}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def _create_subtopics(
        self,
        cur: Any,
        topic_id: int,
        subtopics: List[Dict],
        parent_id: Optional[int] = None,
    ) -> None:
        """Recursively create subtopics with proper parent-child relationships."""
        for subtopic in subtopics:
            if isinstance(subtopic, dict):
                # Create the subtopic
                cur.execute(
                    """
                    INSERT INTO subtopics (topicid, subtopicname, parent_subtopicid)
                    VALUES (%s, %s, %s)
                    RETURNING subtopicid
                    """,
                    (topic_id, subtopic["name"], parent_id),
                )
                subtopic_id = cur.fetchone()[0]

                # Recursively process nested subtopics if they exist
                if "subtopics" in subtopic and subtopic["subtopics"]:
                    # Check if subtopics is a list of strings or objects
                    nested_subtopics = []
                    for nested in subtopic["subtopics"]:
                        if isinstance(nested, str):
                            # Convert string subtopic to dict format
                            nested_subtopics.append({"name": nested, "subtopics": []})
                        else:
                            nested_subtopics.append(nested)

                    self._create_subtopics(cur, topic_id, nested_subtopics, subtopic_id)
            elif isinstance(subtopic, str):
                # Handle string subtopics
                cur.execute(
                    """
                    INSERT INTO subtopics (topicid, subtopicname, parent_subtopicid)
                    VALUES (%s, %s, %s)
                    RETURNING subtopicid
                    """,
                    (topic_id, subtopic, parent_id),
                )

    def get_pdf_info(self, pdf_id: int) -> Optional[Dict]:
        """Get PDF information."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT pdf_path, username, title
                    FROM pdfs
                    WHERE pdfid = %s
                    """,
                    (pdf_id,),
                )
                return cur.fetchone()

            except Exception as e:
                logger.error(f"Error getting PDF info: {str(e)}")
                raise
            finally:
                cur.close()

    def delete_pdf(self, pdf_id: int) -> None:
        """Delete PDF record by ID."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute("DELETE FROM pdfs WHERE pdfid = %s", (pdf_id,))
                conn.commit()
                logger.info(f"Deleted PDF record with ID: {pdf_id}")
            except Exception as e:
                conn.rollback()
                logger.error(f"Error deleting PDF record: {str(e)}")
                raise
            finally:
                cur.close()

    def get_chapter_content(self, chapter_name: str) -> Optional[Dict]:
        """Get all content for a chapter including topics and subtopics."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT 
                        c.chaptername,
                        json_agg(
                            json_build_object(
                                'topic', t.topicname,
                                'notes', t.notes,
                                'subtopics', (
                                    SELECT json_agg(s.subtopicname)
                                    FROM subtopics s
                                    WHERE s.topicid = t.topicid
                                )
                            )
                        ) as topics
                    FROM chapters c
                    LEFT JOIN topics t ON c.chapterid = t.chapterid
                    WHERE c.chaptername = %s
                    GROUP BY c.chapterid, c.chaptername
                """,
                    (chapter_name,),
                )

                return cur.fetchone()

            except Exception as e:
                logger.error(f"Error getting chapter content: {str(e)}")
                raise
            finally:
                cur.close()

    def get_chapter_info(self, chapter_name: str) -> Optional[Dict]:
        """Get chapter information including PDF path."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT 
                        c.chapterid,
                        c.chaptername,
                        p.pdf_path,
                        p.pdfid
                    FROM chapters c
                    JOIN pdfs p ON c.pdfid = p.pdfid
                    WHERE c.chaptername = %s
                """,
                    (chapter_name,),
                )

                return cur.fetchone()

            except Exception as e:
                logger.error(f"Error getting chapter info: {str(e)}")
                raise
            finally:
                cur.close()

    def get_pdf_structure(self, pdf_id: int) -> Dict:
        """Get complete PDF structure with properly nested subtopics."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    WITH RECURSIVE subtopic_tree AS (
                        -- Base case: top-level subtopics
                        SELECT 
                            s.subtopicid,
                            s.subtopicname as name,
                            s.parent_subtopicid,
                            s.topicid,
                            1 as level,
                            ARRAY[s.subtopicid] as path
                        FROM subtopics s
                        WHERE s.parent_subtopicid IS NULL
                        
                        UNION ALL
                        
                        -- Recursive case: child subtopics
                        SELECT 
                            s.subtopicid,
                            s.subtopicname as name,
                            s.parent_subtopicid,
                            s.topicid,
                            st.level + 1,
                            st.path || s.subtopicid
                        FROM subtopics s
                        JOIN subtopic_tree st ON s.parent_subtopicid = st.subtopicid
                        WHERE NOT s.subtopicid = ANY(st.path)
                    )
                    SELECT 
                        c.chaptername as name,
                        (
                            SELECT json_agg(topic_data)
                            FROM (
                                SELECT jsonb_build_object(
                                    'name', t.topicname,
                                    'subtopics', (
                                        SELECT json_agg(
                                            jsonb_build_object(
                                                'name', st.name,
                                                'subtopics', (
                                                    SELECT COALESCE(json_agg(
                                                        jsonb_build_object(
                                                            'name', child.name,
                                                            'subtopics', (
                                                                SELECT COALESCE(json_agg(
                                                                    jsonb_build_object(
                                                                        'name', grandchild.name
                                                                    ) ORDER BY grandchild.subtopicid
                                                                ), '[]'::json)
                                                                FROM subtopic_tree grandchild
                                                                WHERE grandchild.parent_subtopicid = child.subtopicid
                                                            )
                                                        ) ORDER BY child.subtopicid
                                                    ), '[]'::json)
                                                    FROM subtopic_tree child
                                                    WHERE child.parent_subtopicid = st.subtopicid
                                                )
                                            ) ORDER BY st.subtopicid
                                        )
                                        FROM subtopic_tree st
                                        WHERE st.topicid = t.topicid AND st.parent_subtopicid IS NULL
                                    )
                                ) as topic_data
                                FROM topics t
                                WHERE t.chapterid = c.chapterid
                                ORDER BY t.topicid
                            ) sub
                        ) as topics
                    FROM chapters c
                    WHERE c.pdfid = %s
                    ORDER BY c.chapterid;
                    """,
                    (pdf_id,),
                )

                chapters = cur.fetchall()
                return {
                    "chapters": [
                        {
                            "name": chapter["name"],
                            "topics": chapter["topics"] if chapter["topics"] else [],
                        }
                        for chapter in chapters
                    ]
                }

            except Exception as e:
                logger.error(f"Error getting PDF structure: {str(e)}")
                raise
            finally:
                cur.close()

    def update_pdf_status(
        self, pdf_id: int, status: str, error_message: Optional[str] = None
    ) -> None:
        """Update PDF processing status."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                if error_message:
                    cur.execute(
                        """
                        UPDATE pdfs 
                        SET status = %s, error_message = %s
                        WHERE pdfid = %s
                        """,
                        (status, error_message, pdf_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE pdfs 
                        SET status = %s, error_message = NULL
                        WHERE pdfid = %s
                        """,
                        (status, pdf_id),
                    )
                conn.commit()
                logger.info(f"Updated PDF {pdf_id} status to {status}")
            finally:
                cur.close()

    def delete_pdf_by_path(self, pdf_path: str) -> None:
        """Delete PDF record by file path."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute("DELETE FROM pdfs WHERE pdf_path = %s", (pdf_path,))
                conn.commit()
                logger.info(f"Deleted PDF record for {pdf_path}")
            finally:
                cur.close()

    def store_quiz_questions(self, chapter: str, questions: List[Dict]) -> int:
        """Store quiz questions and return quiz ID."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                # First create a quiz record
                cur.execute(
                    """
                    INSERT INTO quizzes (chapter, created_at)
                    VALUES (%s, CURRENT_TIMESTAMP)
                    RETURNING quizid
                    """,
                    (chapter,),
                )
                quiz_id = cur.fetchone()[0]

                # Store each question
                for question in questions:
                    cur.execute(
                        """
                        INSERT INTO quiz_questions 
                        (quizid, question_text, options, correct_answer, explanation)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            quiz_id,
                            question["question"],
                            json.dumps(question["options"]),
                            question["correct_answer"],
                            question.get("explanation", ""),
                        ),
                    )

                conn.commit()
                return quiz_id

            except Exception as e:
                conn.rollback()
                logger.error(f"Error storing quiz: {str(e)}")
                raise

    def get_quiz_questions(self, quiz_id: int) -> List[Dict]:
        """Get quiz questions without answers."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT questionid, question_text as question, options::json as options
                    FROM quiz_questions
                    WHERE quizid = %s
                    ORDER BY questionid
                    """,
                    (quiz_id,),
                )
                return cur.fetchall()

            except Exception as e:
                logger.error(f"Error getting quiz questions: {str(e)}")
                raise

    def get_quiz_answers(self, quiz_id: int) -> List[Dict]:
        """Get quiz answers and explanations."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT questionid, correct_answer, explanation
                    FROM quiz_questions
                    WHERE quizid = %s
                    ORDER BY questionid
                    """,
                    (quiz_id,),
                )
                return cur.fetchall()

            except Exception as e:
                logger.error(f"Error getting quiz answers: {str(e)}")
                raise

    def get_latest_quiz(self, chapter: str) -> Optional[Dict]:
        """Get the most recent quiz for a chapter."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT q.quizid, q.created_at
                    FROM quizzes q
                    WHERE q.chapter = %s
                    ORDER BY q.created_at DESC
                    LIMIT 1
                    """,
                    (chapter,),
                )
                return cur.fetchone()
            except Exception as e:
                logger.error(f"Error getting latest quiz: {str(e)}")
                raise

    def check_connection_health(self):
        """Check if connection pool is healthy and reconnect if needed."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"Connection health check failed: {str(e)}")
            try:
                self.create_pool()
            except Exception as e:
                logger.error(f"Failed to recreate pool during health check: {str(e)}")
            return False

    def get_user_profile(self, username: str) -> Dict:
        """Get detailed user profile information."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    SELECT 
                        u.userid,
                        u.username,
                        u.email,
                        u.created_at,
                        u.last_login,
                        COUNT(DISTINCT p.pdfid) as total_pdfs,
                        COUNT(DISTINCT c.chapterid) as total_chapters,
                        COUNT(DISTINCT t.topicid) as total_topics,
                        COUNT(DISTINCT s.subtopicid) as total_subtopics
                    FROM users u
                    LEFT JOIN pdfs p ON u.username = p.username
                    LEFT JOIN chapters c ON p.pdfid = c.pdfid
                    LEFT JOIN topics t ON c.chapterid = t.chapterid
                    LEFT JOIN subtopics s ON t.topicid = s.topicid
                    WHERE u.username = %s
                    GROUP BY u.userid, u.username, u.email, u.created_at, u.last_login
                    """,
                    (username,),
                )
                return cur.fetchone()
            finally:
                cur.close()

    def get_user_detailed_statistics(self, username: str) -> Dict:
        """Get detailed user activity statistics."""
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute(
                    """
                    WITH pdf_stats AS (
                        SELECT 
                            COUNT(*) as total_pdfs,
                            MAX(created_at) as last_upload
                        FROM pdfs
                        WHERE username = %s
                    ),
                    quiz_stats AS (
                        SELECT 
                            COUNT(DISTINCT q.quizid) as total_quizzes
                        FROM quizzes q
                        WHERE q.chapter IN (
                            SELECT DISTINCT c.chaptername 
                            FROM chapters c 
                            JOIN pdfs p ON c.pdfid = p.pdfid 
                            WHERE p.username = %s
                        )
                    )
                    SELECT 
                        pdf_stats.*,
                        quiz_stats.*
                    FROM pdf_stats, quiz_stats
                    """,
                    (username, username),
                )
                return cur.fetchone()
            finally:
                cur.close()

    def update_user_profile(self, username: str, updates: Dict) -> None:
        """Update user profile information."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                # Build dynamic update query
                set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
                values = list(updates.values()) + [username]

                cur.execute(
                    f"""
                    UPDATE users 
                    SET {set_clause}
                    WHERE username = %s
                    """,
                    values,
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise
            finally:
                cur.close()


# Password hashing settings
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# Function to hash password
def hash_password(password: str):
    return pwd_context.hash(password)


# Function to verify hashed password
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)
