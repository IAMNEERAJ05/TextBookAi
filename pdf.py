import json
import os
from pathlib import Path
import re
from typing import Dict, Optional, List
import google.generativeai as genai
from google.generativeai.types.file_types import File
from google.generativeai.types import GenerationConfig
from dotenv import load_dotenv
import fitz  # PyMuPDF
import hashlib
import shutil

load_dotenv()

# Configure the API key for Google Gemini
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

# Initialize the model once to avoid recreating it
# we will put this in class init function later on
model = genai.GenerativeModel(model_name="gemini-1.5-flash")


def upload_to_gemini(path: Path, mime_type: Optional[str] = None) -> File:
    """Uploads file to Gemini.

    Args:
        path (Path): file path
        mime_type (Optional[str], optional): mime type of the file. Defaults to None.

    Returns:
        File: File object
    """
    file = genai.upload_file(path, mime_type=mime_type)
    print(f"Uploaded file '{file.display_name}' as: {file}")
    return file


def extract_images_from_pdf(pdf_path: Path, output_folder: Path) -> List[str]:
    """Extracts images from PDF and saves them to a file-specific folder.

    Args:
        pdf_path (Path): PDF file path
        output_folder (Path): Output folder path

    Returns:
        List[str]: List of image file paths
    """
    pdf_name = pdf_path.stem
    with open(pdf_path, "rb") as f:
        pdf_hash = hashlib.md5(f.read()).hexdigest()[:8]

    file_specific_folder = output_folder / f"{pdf_name}_{pdf_hash}"

    # Remove the existing folder if it exists
    if file_specific_folder.exists():
        shutil.rmtree(file_specific_folder)

    file_specific_folder.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    image_files = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        image_list = page.get_images(full=True)

        # Extract images from the page
        for img_index, img in enumerate(image_list):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]

            # Use a consistent naming scheme
            image_filename = (
                file_specific_folder / f"page_{page_num+1}_img_{img_index+1}.png"
            )

            with open(image_filename, "wb") as image_file:
                image_file.write(image_bytes)

            image_files.append(str(image_filename))

        # Check if there are other XObjects (like images) on the page
        try:
            xobjects = page.get_xobjects()
            if isinstance(xobjects, list):
                for obj in xobjects:
                    if "/Image" in obj:
                        base_image = doc.extract_image(obj["xref"])
                        image_bytes = base_image["image"]
                        image_filename = (
                            file_specific_folder
                            / f"page_{page_num+1}_xobj_{obj['xref']}.png"
                        )
                        with open(image_filename, "wb") as image_file:
                            image_file.write(image_bytes)

                        image_files.append(str(image_filename))
        except Exception as e:
            print(f"Error extracting xobjects from page {page_num}: {e}")

    doc.close()
    return image_files


def generate_topics(file: File) -> List[Dict]:
    """Generates chapters, topics, and subtopics from the book.

    Args:
        file (File): File object

    Returns:
        List[Dict]: List of dictionaries containing chapters, topics, and subtopics
    """

    generation_config = GenerationConfig(
        temperature=1,
        top_p=0.95,
        top_k=64,
        max_output_tokens=8192,
    )

    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
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
            [
                {
                    "chapter": "Chapter 1: Chapter Title",
                    "topics": [
                        {
                            "topic": "Main Topic 1",
                            "sub_topics": ["Subtopic 1A", "Subtopic 1B", "Subtopic 1C"]
                        },
                        {
                            "topic": "Main Topic 2",
                            "sub_topics": ["Subtopic 2A", "Subtopic 2B"]
                        }
                    ]
                },
                {
                    "chapter": "Chapter 2: Chapter Title",
                    "topics": [
                        // ... similar structure as Chapter 1
                    ]
                }
            ]
            ```
        8. Meaningful Labeling: Ensure all names for chapters, topics, and subtopics are short, meaningful and informative.
        9. Handling Complexity: If a topic presents a complex structure, incorporate nested subtopics as necessary.
        10. Balance Detail and Brevity: Strike a balance between providing sufficient detail and maintaining brevity in your outline.
        11. Content Relevance: Create subtopics or topics only if there is sufficient context provided in the book. Do not derive new subtopics or topics with no content foundation in the text.
        12. Problem-Solving: In case of ambiguities or categorization difficulties, briefly explain your reasoning after the JSON output.
        13. Review and Validation: Thoroughly review your output to ensure accuracy, consistency, and correct JSON formatting before submission.
        14. Do not create any subtopics or topics if there is no context about them in the book.
        15. You can use book's table of contents to create chapters, topics, and subtopics.
        16. Do not include references and citations in the output.
        17. If you think there is no context for a topic, then do not derive any subtopics for that topic.
        18. If you think there is no context for a subtopic, you can skip it.
        
        """,
        generation_config=generation_config,
    )

    response = model.generate_content(
        [
            file,
            "Give me chapters, topics, and subtopics from this book.Make sure topics and subtopics are not created if there is no context in the book. And Make sure to follow the instructions strictly.",
        ]
    ).text

    print(f"Response: {response}")

    # Extract the JSON data from the response
    match = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
    if match:
        json_data = match.group(1)
        try:
            parsed_data = json.loads(json_data)
            # Return chapters as a list of dictionaries
            return parsed_data
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
            return []
    else:
        return []


def generate_subtopic_notes(
    chapter: str, topic: str, sub_topic: str, file_path: Path, image_files: List[str]
) -> Dict:
    """Generate notes for each chapter, topic and subtopic, and select relevant images with captions.

    Args:
        chapter (str): Chapter name
        topic (str): Topic name
        sub_topic (str): Subtopic name
        file_path (Path): File path
        image_files (List[str]): List of image file paths

    Returns:
        Dict: Dictionary containing notes and images
    """
    file = upload_to_gemini(file_path)

    # Create a prompt that includes information about available images
    image_prompt = "\n".join(image_files)

    response = model.generate_content(
        [
            file,
            f"""Generate comprehensive notes on the subtopic '{sub_topic}' under the topic '{topic}' in the chapter '{chapter}'. 
            Start directly with the content for '{sub_topic}' without repeating the chapter or topic names. 
            Also, select only the relevant images for the content you mentioned in the notes.
            If no image is relevant, don't include any image.
            
            For each selected image, extract the caption from the file.
            
            Format your response as a JSON object with the following structure:
            
            ```json
            {{
                "notes": "The comprehensive notes for the subtopic",
                "images": [
                    {{
                        "filename": "image1.png",
                        "caption": "Extract the caption of image1 from the file."
                    }},
                    {{
                        "filename": "image2.png",
                        "caption": "Extract the caption of image2 from the file."
                    }}
                ]
            }}
            ```
            
            Available images:
            {image_prompt}
            """,
        ]
    ).text

    print(f"Response: {response}")
    match = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
    if match:
        json_data = match.group(1)
        try:
            result = json.loads(json_data)
            return result
        except json.JSONDecodeError:
            print(f"Error parsing JSON response: {response}")
            return {"notes": "Error generating notes", "images": []}
    else:
        print("No JSON data found in the response")
        return {"notes": "Error generating notes", "images": []}


def generate_topic_notes(
    file: File, chapter: str, topic: str, image_files: List[str]
) -> Dict:
    """Generate notes and select images with captions for the topic.

    Args:
        file (File): File object
        chapter (str): Chapter name
        topic (str): Topic name
        image_files (List[str]): List of image file paths

    Returns:
        Dict: Dictionary containing notes and images
    """
    image_prompt = "\n".join(image_files)

    response = model.generate_content(
        [
            file,
            f"""Generate a comprehensive overview of the topic '{topic}' in the chapter '{chapter}'. 
            Include key concepts and main ideas. Also, select only the relevant images for the content you mentioned in the notes.
            If no image is relevant, don't include any image.
            
            For each selected image, extract the caption from the file.
            
            Format your response as a JSON object with the following structure:
            ```json
            {{
                "notes": "The comprehensive notes for the topic",
                "images": [
                    {{
                        "filename": "image1.png",
                        "caption": "Extract the caption of image1 from the file."
                    }},
                    {{
                        "filename": "image2.png",
                        "caption": "Extract the caption of image2 from the file."
                    }}
                ]
            }}
            
            Available images:
            {image_prompt}
            """,
        ]
    ).text

    print(f"Response: {response}")

    match = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
    if match:
        json_data = match.group(1)
        try:
            result = json.loads(json_data)
            return result
        except json.JSONDecodeError:
            print(f"Error parsing JSON response: {response}")
            return {"notes": "Error generating notes", "images": []}
    else:
        print("No JSON data found in the response")
        return {"notes": "Error generating notes", "images": []}


def generate_quiz(file: File, chapter: str) -> List[Dict]:
    """Generate quiz questions for a specific chapter.

    Args:
        file (File): File object
        chapter (str): Chapter name

    Returns:
        List[Dict]: List of dictionaries containing quiz questions
    """
    print(f"Generating quiz for chapter: {chapter}")
    response = model.generate_content(
        [
            file,
            f"""Generate a comprehensive multiple-choice quiz for the chapter '{chapter}' following these specifications:

            Structure:
            - Total questions: 15 questions only.
            - Difficulty levels: 3 (Easy, Medium, Hard)
            - Questions per level: 5
            - Options per question: 4 (A, B, C, D)

            Question Guidelines:
            1. Progress difficulty gradually:
            - Level 1 (Q1-5): Basic recall and understanding
            - Level 2 (Q6-10): Application and analysis
            - Level 3 (Q11-15): Analysis, evaluation, and synthesis

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

                        
            ```json
            [
                {{"question": "...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."], "correct_answer": "A"}},
                {{"question": "...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."], "correct_answer": "A"}},
                {{"question": "...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."], "correct_answer": "A"}},
                {{"question": "...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."], "correct_answer": "A"}},
                {{"question": "...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."], "correct_answer": "A"}}
            ]
            ```
            """,
        ]
    ).text

    # Extract the JSON data from the response
    match = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
    if match:
        json_data = match.group(1)
        try:
            parsed_data = json.loads(json_data)
            print(f"Parsed quiz data: {parsed_data}")
            if not parsed_data:  # If parsed_data is empty, use fallback
                return fallback_quiz_questions(chapter)
            return parsed_data
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
            return fallback_quiz_questions(chapter)
    else:
        print("No JSON data found in the response")
        return fallback_quiz_questions(chapter)


def fallback_quiz_questions(chapter: str) -> List[Dict]:
    """Generate fallback quiz questions if the AI model fails to generate quiz questions.

    Args:
        chapter (str): Chapter name

    Returns:
        List[Dict]: List of dictionaries containing quiz questions
    """
    return [
        {
            "question": f"This is a sample question about {chapter}. What is the correct answer?",
            "options": [
                "A. Sample answer 1",
                "B. Sample answer 2",
                "C. Sample answer 3",
                "D. Sample answer 4",
            ],
            "correct_answer": "A",
        },
        # Add more fallback questions here...
    ]
