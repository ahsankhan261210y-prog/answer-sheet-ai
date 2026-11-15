import json, re
from google.genai import types
from ocr import client, model_name

SYSTEM = (
    "You are a fair, consistent examiner. Grade the student's answer sheet against the "
    "model answers. Give partial marks for partially correct answers. "
    "Reply with ONLY valid JSON, no markdown."
)


def grade(questions, student_text, recheck_reason=None):
    prompt = {
        "questions": questions,
        "student_answer_sheet": student_text,
        "output_format": {
            "results": [{"q_no": 1, "awarded": 0, "max": 0, "feedback": "short reason"}],
            "total": 0,
            "max_total": 0,
        },
    }
    if recheck_reason:
        prompt["recheck_request"] = (
            f"The student asked for a recheck: '{recheck_reason}'. Re-evaluate carefully "
            "and independently. Only change marks if the original grading was truly wrong."
        )
    resp = client().models.generate_content(
        model=model_name(),
        contents=json.dumps(prompt, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
        ),
    )
    text = re.sub(r"^```(?:json)?|```$", "", resp.text.strip(), flags=re.M).strip()
    data = json.loads(text)
    data["total"] = sum(r["awarded"] for r in data["results"])
    data["max_total"] = sum(r["max"] for r in data["results"])
    return data