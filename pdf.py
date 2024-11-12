import json
import os
import re
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any, TypedDict, Union
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from google.generativeai.types.file_types import File as GeminiFile
from datetime import datetime, timezone
from google.generativeai import protos
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


class SubtopicDict(TypedDict):
    name: str
    subtopics: Optional[List["SubtopicDict"]]


class TopicDict(TypedDict):
    name: str
    subtopics: List[SubtopicDict]


class ChapterDict(TypedDict):
    name: str
    topics: List[TopicDict]


class PDFStructure(TypedDict):
    chapters: List[ChapterDict]


class NoteGenerator:
    def __init__(self):
        # Configure the API key for Google Gemini
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=GenerationConfig(
                temperature=0.3,
                top_p=0.8,
                top_k=40,
                max_output_tokens=8192,
                response_mime_type="text/plain",
            ),
            safety_settings=[
                {
                    "category": "HARM_CATEGORY_DANGEROUS",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE",
                },
            ],
        )

    def upload_to_gemini(
        self, path: Path, mime_type: Optional[str] = None
    ) -> GeminiFile:
        """Uploads file to Gemini."""
        try:
            file = genai.upload_file(path, mime_type=mime_type)
            logger.info(f"Uploaded file '{file.display_name}' to Gemini")
            return file
        except Exception as e:
            logger.error(f"Error uploading file to Gemini: {str(e)}")
            raise

    def generate_topic_notes(
        self, gemini_file: GeminiFile, chapter: str, topic: str, image_files: List[str]
    ) -> Dict:
        """Generate comprehensive notes for a topic."""
        try:
            print(f"gemini_file: {gemini_file}")
            print(f"chapter: {chapter}")
            print(f"topic: {topic}")
            print(f"image_files: {image_files}")
            logger.info(f"Generating notes for topic: {chapter}/{topic}")

            prompt = f"""Analyze the PDF content and generate comprehensive notes for the topic '{topic}' 
            from chapter '{chapter}'.
            
            Everything should be in the context of the book don't include anything outside the book.
            
            Explain the content in a way that is easy to understand and easy to remember.

            You can use the examples of this topic from the book to explain the content.

            Also, select only the relevant images for the content you mentioned in the notes.
            If no image is relevant, don't include any image.
            
            For each selected image, extract the caption from the file.

            Available images: {', '.join(image_files)}

            Format the response as a JSON object with 'notes' in markdown format and relevant 'images':
            ```json
            {{
                "notes": "[A proper markdown formatted notes here Start directly with the content for '{topic}' without repeating the chapter or topic names. ]",
                "images": [
                    {{"filename": "image_1_1.jpeg", "caption": "extract the caption from the file"}}
                ]
            }}
            ```
            
            Make the notes clear, well-structured, and easy to understand. properly format the output in JSON.
            """

            response = self.model.generate_content([gemini_file, prompt]).text
            return self._parse_json_response(response)
        except Exception as e:
            logger.error(f"Error generating topic notes: {str(e)}")
            raise

    def generate_subtopic_notes(
        self,
        gemini_file: GeminiFile,
        chapter: str,
        topic: str,
        subtopic: str,
        image_files: List[str],
    ) -> Dict:
        """Generate detailed notes for a subtopic."""
        try:
            logger.info(f"Generating notes for subtopic: {chapter}/{topic}/{subtopic}")

            prompt = f"""Analyze the PDF content and generate detailed notes for the subtopic '{subtopic}' 
            under topic '{topic}' from chapter '{chapter}'. 
            Everything should be in the context of the book don't include anything outside the book.
            Explain the content in a way that is easy to understand and easy to remember.

            Also, select only the relevant images for the content you mentioned in the notes.
            If no image is relevant, don't include any image.
            
            For each selected image, extract the caption from the file.

            The available images are: {', '.join(image_files)}

            Format the response as a JSON object with 'notes' in markdown format and relevant 'images':
            {{
                "notes": "[A proper markdown formatted notes here Start directly with the content for '{subtopic}' without repeating the chapter or topic names. ]",
                "images": [
                    {{"filename": "image_1_1.jpeg", "caption": "extract the caption from the file"}}
                ]
            }}

            Some important points:
            1. Start the notes content directly without repeating chapter/topic names
            2. Use proper markdown formatting
            3. Ensure the JSON is properly formatted and valid
            4. Do not include any explanation text outside the JSON structure
            """

            response = self.model.generate_content(
                [gemini_file, prompt],
                generation_config=GenerationConfig(
                    temperature=0.3,
                    top_p=0.8,
                    top_k=40,
                    max_output_tokens=8192,
                ),
            ).text

            # Clean the response to ensure valid JSON
            cleaned_response = self._clean_json_response(response)
            return json.loads(cleaned_response)

        except Exception as e:
            logger.error(f"Error generating subtopic notes: {str(e)}")
            return {"notes": f"Error generating notes: {str(e)}", "images": []}

    def _clean_json_response(self, response: str) -> str:
        """Clean the response to ensure valid JSON."""
        try:
            # Find the first { and last } to extract the JSON object
            start = response.find("{")
            end = response.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON object found in response")

            json_str = response[start:end]

            # Remove any markdown code block markers
            json_str = json_str.replace("```json", "").replace("```", "")

            # Validate the JSON
            json.loads(json_str)  # This will raise an exception if invalid
            return json_str

        except Exception as e:
            logger.error(f"Error cleaning JSON response: {str(e)}")
            raise

    def extract_pdf_structure(self, gemini_file: GeminiFile) -> Dict[str, Any]:
        """Extract the structure of chapters and topics from the PDF."""
        try:
            model = genai.GenerativeModel(
                model_name="gemini-1.5-pro",
                generation_config=GenerationConfig(
                    temperature=0.3,
                    top_p=0.8,
                    top_k=40,
                ),
                system_instruction="""
                You are designated as an expert text analyst specializing in content organization and summarization. Your primary task is to dissect and organize book content into a structured format comprising chapters, main topics, and subtopics. Adhere to the following directives:

                1. Reading Comprehension: Thoroughly read and understand the content of the provided book or document.
                2. Content Breakdown: Identify and delineate the chapters first, followed by the main topics within each chapter, and further break these down into their respective subtopics.
                3. Distinctiveness and Comprehensiveness: Ensure that each chapter and topic is distinct, comprehensive, and reflective of the book's overarching content.
                4. Subtopic Clarity: Formulate clear and concise subtopics that logically fit under each main topic.
                5. Consistency: Maintain a uniform level of detail across all chapters, topics, and subtopics.
                6. Accuracy Verification: Perform cross-checks to validate the accuracy and completeness of your analysis.
                7. Data Structuring: Organize the structured data in the JSON format as illustrated below:
                    ```json
                    {
                        "chapters": [
                            {
                                "name": "Chapter 1: Introduction",
                                "topics": [
                                    {
                                        "name": "Topic 1.1",
                                        "subtopics": [
                                            {
                                                "name": "Subtopic 1.1.1",
                                                "subtopics": ["Inner Subtopic 1", "Inner Subtopic 2"]
                                            },
                                            {
                                                "name": "Subtopic 1.1.2"
                                            }
                                        ]
                                    },
                                    {
                                        "name": "Topic 1.2"
                                    }
                                ]
                            },
                            {
                                "name": "Chapter 2: Chapter Title"
                            }
                        ]
                    }
                    ```
                8. Meaningful Labeling: Ensure all names for chapters, topics, and subtopics are short, meaningful and informative.
                9. Handling Complexity: If a topic presents a complex structure, incorporate nested subtopics as necessary.
                10. Balance Detail and Brevity: Strike a balance between providing sufficient detail and maintaining brevity in your outline.
                11. Content Relevance: Create subtopics or topics only if there is sufficient context provided in the book. Do not derive new subtopics or topics with no content foundation in the text.
                12. Problem-Solving: In case of ambiguities or categorization difficulties, briefly explain your reasoning after the JSON output.
                13. Review and Validation: Thoroughly review your output to ensure accuracy, consistency, and correct JSON formatting before submission.
                14. Do not create any subtopics or topics if there is no context about them in the book. Or do not create nested subtopics without any context.
                15. You can use book's table of contents to create chapters, topics, and subtopics.
                16. Do not include references, citations and Appendix as the chapters, anything outside the chapters are not required.
                17. If you think there is no context for a topic, then do not derive any subtopics for that topic.
                18. If you think there is no context for a subtopic, you can skip it.
                19. Everything should be in the context of the book.
                20. Give me the proper reasoning for the subtopics and topics you created outside the JSON structure.
                """,
            )
            response = model.generate_content(
                [
                    gemini_file,
                    "Give me chapters, topics, and subtopics from this book.Make sure topics and subtopics are not created if there is no context in the book. And Make sure to follow the instructions strictly.",
                ]
            )

            print(f"response: {response.text}")

            # Parse and validate the structure
            structure = self._parse_json_response(response.text)
            self._validate_structure(structure)

            return structure

        except Exception as e:
            logger.error(f"Error extracting PDF structure: {str(e)}")
            raise

    def _validate_structure(self, structure: Dict) -> PDFStructure:
        """Validate the PDF structure format and clean any nested objects."""
        try:
            if not isinstance(structure, dict) or "chapters" not in structure:
                raise ValueError("Invalid structure: missing 'chapters' key")

            def clean_name(item: Any) -> str:
                """Convert any name object to string."""
                if isinstance(item, dict):
                    return str(item.get("name", ""))
                return str(item)

            def process_subtopics(subtopics_list: List[Any]) -> List[SubtopicDict]:
                """Process subtopics recursively maintaining structure."""
                cleaned_subtopics: List[SubtopicDict] = []
                for subtopic in subtopics_list:
                    if isinstance(subtopic, dict):
                        cleaned_subtopic: SubtopicDict = {
                            "name": clean_name(subtopic["name"]),
                            "subtopics": [],  # Initialize with empty list
                        }
                        # Handle nested subtopics recursively
                        if "subtopics" in subtopic and subtopic["subtopics"]:
                            cleaned_subtopic["subtopics"] = process_subtopics(
                                subtopic["subtopics"]
                            )
                        cleaned_subtopics.append(cleaned_subtopic)
                    else:
                        cleaned_subtopics.append(
                            {"name": clean_name(subtopic), "subtopics": []}
                        )
                return cleaned_subtopics

            # Clean and validate chapters
            cleaned_structure: PDFStructure = {"chapters": []}

            for chapter in structure["chapters"]:
                if not isinstance(chapter, dict):
                    return self.fix_structure(
                        structure
                    )  # Return fixed structure instead of raising error

                cleaned_chapter: ChapterDict = {
                    "name": clean_name(chapter["name"]),
                    "topics": [],
                }

                # Clean and validate topics
                if "topics" in chapter:
                    for topic in chapter["topics"]:
                        if not isinstance(topic, dict):
                            return self.fix_structure(
                                structure
                            )  # Return fixed structure instead of raising error

                        cleaned_topic: TopicDict = {
                            "name": clean_name(topic["name"]),
                            "subtopics": [],
                        }

                        # Clean and validate subtopics
                        if "subtopics" in topic:
                            cleaned_topic["subtopics"] = process_subtopics(
                                topic["subtopics"]
                            )

                        cleaned_chapter["topics"].append(cleaned_topic)

                cleaned_structure["chapters"].append(cleaned_chapter)

            logger.info("Structure validation and cleaning completed successfully")
            return cleaned_structure
        except Exception as e:
            logger.error(f"Error validating structure: {str(e)}")
            return self.fix_structure(structure)

    def fix_structure(self, structure: Dict) -> PDFStructure:
        """Fix the structure of the PDF."""
        try:
            prompt = f"""Fix the structure of the following JSON structure:
            {json.dumps(structure, indent=2)}
            and return in the below format:
            ```json
            {{
                "chapters": [
                    {{
                        "name": "Chapter name",
                        "topics": [
                            {{
                                "name": "Topic name",
                                "subtopics": [
                                    {{
                                        "name": "Subtopic name",
                                        "subtopics": []
                                    }}
                                ]
                            }}
                        ]
                    }}
                ]
            }}
            ```
            """
            response = self.model.generate_content([prompt]).text
            json_structure = self._parse_json_response(response)

            # Create a basic valid structure if the response is invalid
            if not isinstance(json_structure, dict) or "chapters" not in json_structure:
                return PDFStructure(chapters=[])

            # Cast the structure to PDFStructure type
            fixed_structure: PDFStructure = {
                "chapters": [
                    ChapterDict(
                        name=chapter.get("name", ""),
                        topics=[
                            TopicDict(
                                name=topic.get("name", ""),
                                subtopics=[
                                    SubtopicDict(
                                        name=subtopic.get("name", ""),
                                        subtopics=subtopic.get("subtopics", []),
                                    )
                                    for subtopic in topic.get("subtopics", [])
                                ],
                            )
                            for topic in chapter.get("topics", [])
                        ],
                    )
                    for chapter in json_structure.get("chapters", [])
                ]
            }

            return fixed_structure

        except Exception as e:
            logger.error(f"Error fixing structure: {str(e)}")
            # Return a valid empty structure as fallback
            return PDFStructure(chapters=[])

    def generate_quiz_questions(
        self, gemini_file: GeminiFile, chapter: str
    ) -> List[Dict]:
        """Generate quiz questions for a chapter."""
        try:
            prompt = f"""Generate a comprehensive multiple-choice quiz for the chapter '{chapter}' following these specifications:

            Structure:
            - Total questions: fifteen questions only.
            - Difficulty levels: 3 (Easy, Medium, Hard)
            - Questions per level: 5
            - Options per question: 4 (A, B, C, D)

            Question Guidelines:
            1. Progress difficulty gradually:
            - Level 1 (Q1-5): Basic recall and understanding
            - Level 2 (Q6-10): Application and analysis
            - Level 3 (Q11-fifteen): Analysis, evaluation, and synthesis

            2. Question Characteristics:
            - Must be unique (no repetition)
            - Clear and unambiguous
            - Grammatically correct
            - All distractors (wrong options) should be plausible
            - Include a mix of question types (factual, conceptual, scenario-based)

            3. Option Requirements:
            - Must have exactly one correct answer
            - All options should be similar in length
            - Avoid options with long length.
            - Maintain consistent formatting across all options


            Format the response as a JSON array of questions:
            [
                {{
                    "question": "Question text here?",
                    "options": [
                        "A. First option",
                        "B. Second option",
                        "C. Third option",
                        "D. Fourth option"
                    ],
                    "correct_answer": "A",
                    "explanation": "Explanation of the correct answer"
                }}
            ]

            Ensure each question:
            1. Tests understanding, not just memorization
            2. Has exactly 4 options labeled A through D
            3. Has one clear correct answer
            4. Includes an explanation for the correct answer
            5. A total of fifteen questions only.
            6. Give me the proper explanation on why you chose those fifteen questions outside the JSON structure.
            """

            response = self.model.generate_content([gemini_file, prompt]).text
            questions = self._parse_json_response(response)

            # Validate that we got a list of questions
            if not isinstance(questions, list):
                raise ValueError("Invalid quiz format: expected list of questions")

            return questions

        except Exception as e:
            logger.error(f"Error generating quiz questions: {str(e)}")
            raise

    def _parse_json_response(self, response: str) -> Dict:
        """Parse JSON response from Gemini."""
        try:
            # First try to find JSON within code blocks
            match = re.search(r"```(?:json)?\n(.*?)\n```", response, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                # If no code blocks found, try to parse the entire response
                json_str = response

            # Clean up the string
            json_str = json_str.strip()

            # Parse JSON
            result = json.loads(json_str)

            logger.info(f"Successfully parsed JSON structure: {result}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response: {str(e)}")
            logger.error(f"Raw response: {response}")
            raise ValueError(f"Failed to parse JSON response: {str(e)}")

    def create_gemini_file_dict(self, gemini_file: GeminiFile) -> Dict:
        """Create a dictionary of Gemini file information for storage."""
        return {
            "name": gemini_file.name,
            "display_name": gemini_file.display_name,
            "mime_type": gemini_file.mime_type,
            "sha256_hash": (
                gemini_file.sha256_hash.decode("utf-8")
                if isinstance(gemini_file.sha256_hash, bytes)
                else gemini_file.sha256_hash
            ),
            "size_bytes": str(gemini_file.size_bytes),
            "state": gemini_file.state,
            "uri": gemini_file.uri,
            "create_time": (
                gemini_file.create_time.isoformat() if gemini_file.create_time else None
            ),
            "expiration_time": (
                gemini_file.expiration_time.isoformat()
                if gemini_file.expiration_time
                else None
            ),
            "update_time": (
                gemini_file.update_time.isoformat() if gemini_file.update_time else None
            ),
        }

    def reconstruct_gemini_file(self, stored_file: Dict) -> GeminiFile:
        """Reconstruct a Gemini file object from stored data."""
        try:
            file_proto = protos.File(
                name=stored_file["name"],
                display_name=stored_file["display_name"],
                mime_type=stored_file["mime_type"],
                sha256_hash=stored_file["sha256_hash"].encode("utf-8"),
                size_bytes=int(stored_file["size_bytes"]),
                state=stored_file["state"],
                uri=stored_file["uri"],
                create_time=stored_file["create_time"],
                expiration_time=stored_file["expiration_time"],
                update_time=stored_file["update_time"],
            )
            return GeminiFile(proto=file_proto)
        except Exception as e:
            logger.error(f"Error reconstructing Gemini file: {str(e)}")
            raise

    def extract_images_from_pdf(self, pdf_path: Path, output_folder: Path) -> List[str]:
        """Extract images from PDF and save them."""
        doc = None  # Initialize doc outside try block
        try:
            doc = fitz.open(pdf_path)
            image_files = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images()

                for img_index, img in enumerate(image_list):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)

                        if base_image:
                            image_bytes = base_image["image"]
                            image_ext = base_image["ext"]

                            # Generate a unique name for each image
                            image_name = (
                                f"image_{page_num + 1}_{img_index + 1}.{image_ext}"
                            )
                            image_path = output_folder / image_name

                            # Save the image
                            with open(image_path, "wb") as f:
                                f.write(image_bytes)

                            logger.info(f"Saved image: {image_name}")
                            image_files.append(image_name)

                    except Exception as e:
                        logger.warning(
                            f"Failed to extract image {img_index} from page {page_num}: {str(e)}"
                        )
                        continue

            logger.info(
                f"Successfully extracted {len(image_files)} images from {pdf_path}"
            )
            return image_files

        except Exception as e:
            logger.error(f"Error extracting images from PDF: {str(e)}")
            raise

        finally:
            if doc is not None:  # Check if doc was initialized
                doc.close()
