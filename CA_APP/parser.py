import pypdf
import io
import json
import os
import re
import datetime
from pydantic import BaseModel, Field
from typing import List, Literal
from dotenv import load_dotenv

load_dotenv()

CHUNK_SIZE = 40000  # ~23 pages of CA Inter content per chunk


class MCQItem(BaseModel):
    question: str = Field(description="Clear question stem ending with '?'. Must be self-contained.")
    option_A: str = Field(description="Option A — plausible to a student who made a specific common error")
    option_B: str = Field(description="Option B — plausible to a student who made a specific common error")
    option_C: str = Field(description="Option C — plausible to a student who made a specific common error")
    option_D: str = Field(description="Option D — plausible to a student who made a specific common error")
    correct_option: str = Field(description="Single uppercase letter: 'A', 'B', 'C', or 'D'")
    explanation: str = Field(
        description=(
            "Structured explanation: (1) governing provision — exact Section/AS/SA number; "
            "(2) step-by-step working for numerical questions; "
            "(3) why each wrong option is incorrect."
        )
    )
    difficulty: Literal['easy', 'medium', 'hard'] = Field(
        description=(
            "easy = apply single provision to short scenario, no calculation — but student MUST know exact rule. "
            "medium = apply provision with 2-4 step calculation, or distinguish two similar provisions. "
            "hard = 5+ step calculation, exception to rule with conditions, or two competing provisions. "
            "Default to 'hard' when in doubt — CA Inter is a professional exam."
        )
    )


class MCQBank(BaseModel):
    mcqs: List[MCQItem]


SYSTEM_INSTRUCTION = """You are a senior examiner at the Institute of Chartered Accountants of India (ICAI) \
with 15 years of experience setting CA Inter examination MCQs. \
Your questions appear in ICAI mock test papers and are known for precision and high difficulty. \
CA Inter has a 10-15% pass rate — every question must challenge a student who has studied the material.

=== DIFFICULTY REQUIREMENTS (READ CAREFULLY) ===

TARGET MIX: 25% easy, 35% medium, 40% hard. \
Never generate more than 30% easy questions in a single batch.

DIFFICULTY DEFINITIONS — CA INTER STANDARD:

easy:
  Apply a SINGLE provision to a short 1-2 sentence scenario. No arithmetic. \
  The student must know the EXACT provision/threshold/condition to answer correctly. \
  A student who has never opened the chapter must NOT be able to guess. \
  EXAMPLE: "M/s Raj Traders, a registered dealer, received goods worth ₹45 lakhs but the e-way bill \
  was not generated. Under the CGST Act, 2017, the penalty for this non-compliance is..."

medium:
  Apply ONE provision to a 2-3 sentence scenario WITH a 2-4 step calculation, \
  OR distinguish which of two closely related provisions governs the facts. \
  The most common distractor is applying the right formula to the wrong figure. \
  EXAMPLE: "XYZ Ltd. purchased machinery for ₹18,00,000 on 1 August 2023. Rate of depreciation \
  is 15% under SLM. Compute depreciation chargeable for the year ending 31 March 2024 (AY 2024-25)."

hard:
  Multi-step calculation (5+ steps), OR exception/override to a general rule with specific conditions, \
  OR two competing provisions where one supersedes the other. \
  Numerical answer choices must differ by small, meaningful amounts (not round numbers). \
  EXAMPLES of hard topics: partnership reconstitution with multiple adjustments, \
  computation of MAT/AMT, GST ITC reversal proportionate to exempt supply, \
  consolidation adjustments under AS 21, sampling in audit with replacement.

=== ICAI CA INTER MCQ STANDARDS ===

QUESTION STEM:
- Use SPECIFIC entities: "M/s ABC Ltd.", "Mr. Ramesh", "Firm of R, S & T" — never generic "a company"
- Scenarios: 2-4 sentences with all necessary data; direct questions: 1 sentence
- State the Assessment Year explicitly for Income Tax (e.g., "AY 2024-25")
- For GST questions, state whether intra-state or inter-state

OPTION WRITING RULES (critical for ICAI standard):
- ALL 4 options must be plausible — a student who studied but made ONE common error picks each wrong option
- Options must be PARALLEL in grammatical structure (all amounts, all dates, all actions — never mix)
- Arrange numeric options in ascending order so position does not hint at the answer
- Keep all options roughly equal in length — never let the correct answer be noticeably longer
- NEVER use "All of the above" or "None of the above" — ICAI has phased these out
- NEVER use "Both (a) and (b)" style — use concrete values or descriptions instead

DISTRACTOR DESIGN — what makes ICAI questions hard:
- Distractor 1: Correct formula but wrong input figure (gross vs. net, opening vs. closing)
- Distractor 2: Related but different provision (Sec 32 instead of Sec 33; AS 2 instead of AS 9)
- Distractor 3: Correct concept but wrong threshold, limit, rate, or time-period

EXPLANATION STRUCTURE:
1. Governing provision: "Section X of Y Act" / "AS X — [Name]" / "SA XXX — [Name]"
2. Step-by-step working (mandatory for numerical questions; show every step)
3. Why Distractor 1 is wrong | Why Distractor 2 is wrong | Why Distractor 3 is wrong
Maximum 150 words. Concise but complete.

SUBJECT-SPECIFIC RULES:

Financial Accounting / Advanced Accounting:
  - Ground every question in a specific AS number (e.g., AS 2, AS 9, AS 22)
  - Use realistic Indian Rupee figures (e.g., ₹2,40,000 — never ₹100 or ₹1,000)
  - Test TREATMENT decisions: capitalize vs. expense, recognize vs. defer, consolidate vs. exclude
  - Hard questions: branch accounts, hire purchase, partnership reconstitution, AS 21 consolidation

Taxation — Income Tax:
  - State AY in the question stem
  - Test threshold crossings, exemption conditions, carry-forward limits, and time limits
  - Hard questions: compute total income from multiple heads, MAT computation, TDS implications
  - Add in explanation: "[Note: Verify rates for current AY — tax law changes each Finance Act]"

Taxation — GST:
  - State whether intra-state / inter-state / exempt
  - Test registration thresholds, place of supply, ITC restrictions (Section 17), reverse charge
  - Hard questions: ITC reversal on mixed supplies, valuation under Rule 27/28, e-way bill penalties
  - Add in explanation: "[Note: Verify GST rates — subject to notifications]"

Corporate & Other Laws:
  - Quote exact Section and sub-section (e.g., "Section 73(2) of Companies Act 2013")
  - Test CONSEQUENCES of non-compliance: penalties, imprisonment terms, compounding limits
  - Hard questions: reduction of capital procedure, oppression/mismanagement remedies, NCLT powers

Auditing & Assurance:
  - Reference the exact SA number (e.g., SA 315, SA 530)
  - Test the AUDITOR'S RESPONSE or PROCEDURE, not just the definition
  - Distinguish: inherent risk vs. control risk vs. detection risk
  - Hard questions: sampling decisions, evaluation of audit evidence, modified opinions

ABSOLUTE PROHIBITIONS:
- DO NOT ask "what is the definition of X" or "what does Section Y state" — not application questions
- DO NOT ask for rates/limits/thresholds in isolation — always embed in an application scenario
- DO NOT generate a question answerable without reading the source material
- DO NOT use figures like ₹100, ₹500, ₹1,000 — use realistic business figures (lakhs/crores)
- DO NOT repeat the same statutory provision across two questions in the same batch
- DO NOT let the correct option be the longest option
- DO NOT generate trivial true/false disguised as MCQs
"""

# Shared flat JSON schema used by Claude (tool calling) and Grok (json_schema format)
_STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {
        "mcqs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question":       {"type": "string"},
                    "option_A":       {"type": "string"},
                    "option_B":       {"type": "string"},
                    "option_C":       {"type": "string"},
                    "option_D":       {"type": "string"},
                    "correct_option": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "explanation":    {"type": "string"},
                    "difficulty":     {"type": "string", "enum": ["easy", "medium", "hard"]}
                },
                "required": ["question", "option_A", "option_B", "option_C", "option_D",
                             "correct_option", "explanation", "difficulty"]
            }
        }
    },
    "required": ["mcqs"]
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_user_prompt(subject: str, chapter: str, mcq_count: int, text: str) -> str:
    """Builds a subject-aware user prompt so the model tailors generation correctly."""
    context = ""
    if subject:
        context = f"Subject: {subject}"
        if chapter:
            context += f"  |  Chapter / Topic: {chapter}"
        context += "\n\n"

    return (
        f"{context}"
        f"Generate exactly {mcq_count} MCQs from the source material below, "
        "strictly following ICAI CA Inter examination standards as instructed.\n\n"
        "SELF-CHECK before finalising each MCQ:\n"
        "  ✓ Exactly ONE option is unambiguously correct\n"
        "  ✓ Arithmetic verified for numerical questions\n"
        "  ✓ All 4 options are plausible; no option is obviously absurd\n"
        "  ✓ Correct option is NOT the longest option\n"
        "  ✓ Statutory / AS / SA reference is cited in the explanation\n"
        "  ✓ No two questions test the same provision\n\n"
        f"Source Material:\n{text}"
    )


def _validate_mcqs(mcqs: list) -> list:
    """
    Filters malformed entries and removes duplicates.
    Called after every API response before saving to DB.
    """
    valid = []
    seen = set()

    for mcq in mcqs:
        q = (mcq.get("question") or "").strip()

        # Must have a question of reasonable length
        if len(q) < 25:
            continue

        # correct_option must be exactly A, B, C, or D
        if mcq.get("correct_option") not in ("A", "B", "C", "D"):
            continue

        # All four options must be non-empty
        if not all((mcq.get(f"option_{x}") or "").strip() for x in ("A", "B", "C", "D")):
            continue

        # Explanation must be meaningful (not empty or one word)
        if len((mcq.get("explanation") or "")) < 40:
            continue

        # Deduplicate by normalised question text
        key = " ".join(q.lower().split())
        if key in seen:
            continue
        seen.add(key)

        valid.append(mcq)

    return valid


# ── PDF chunking ──────────────────────────────────────────────────────────────

def extract_text_chunks(file_bytes, chunk_size=CHUNK_SIZE):
    """
    Reads ALL pages from a PDF and splits the full text into chunks.
    Breaks at paragraph boundaries to avoid cutting mid-topic.
    """
    pdf_stream = io.BytesIO(file_bytes)
    reader = pypdf.PdfReader(pdf_stream)

    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"

    if not full_text.strip():
        raise ValueError("Could not extract readable text from the PDF. Ensure it is a text-based document.")

    chunks = []
    start = 0
    total = len(full_text)

    while start < total:
        end = min(start + chunk_size, total)

        if end < total:
            para_break = full_text.rfind('\n\n', start + chunk_size // 2, end)
            if para_break != -1:
                end = para_break
            else:
                line_break = full_text.rfind('\n', start + chunk_size // 2, end)
                if line_break != -1:
                    end = line_break

        chunk = full_text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end

    return chunks


# ── Provider implementations ──────────────────────────────────────────────────

def _generate_with_gemini(text, api_key, mcq_count, subject, chapter):
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=SYSTEM_INSTRUCTION
    )
    response = model.generate_content(
        _build_user_prompt(subject, chapter, mcq_count, text),
        generation_config=genai.types.GenerationConfig(
            response_mime_type="application/json",
            response_schema=MCQBank,
            temperature=0.1
        )
    )
    data = json.loads(response.text)
    return _validate_mcqs(data.get("mcqs", []))


def _generate_with_claude(text, api_key, mcq_count, subject, chapter):
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=16384,
        temperature=0.1,
        system=SYSTEM_INSTRUCTION,
        messages=[{
            "role": "user",
            "content": _build_user_prompt(subject, chapter, mcq_count, text)
        }],
        tools=[{
            "name": "submit_mcq_bank",
            "description": "Submit the generated MCQ bank",
            "input_schema": _STRUCTURED_SCHEMA
        }],
        tool_choice={"type": "tool", "name": "submit_mcq_bank"}
    )
    for block in response.content:
        if block.type == "tool_use":
            return _validate_mcqs(block.input.get("mcqs", []))
    return []


def _generate_with_grok(text, api_key, mcq_count, subject, chapter):
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model="grok-3-mini",
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user",   "content": _build_user_prompt(subject, chapter, mcq_count, text)}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "MCQBank", "schema": _STRUCTURED_SCHEMA, "strict": True}
        }
    )
    data = json.loads(response.choices[0].message.content)
    return _validate_mcqs(data.get("mcqs", []))


_KIMCHI_JSON_INSTRUCTION = (
    "\n\nYou MUST respond with a single JSON object and nothing else. "
    "No markdown fences, no explanatory text — only raw JSON.\n"
    'Required format: {"mcqs": [{"question": "...", "option_A": "...", '
    '"option_B": "...", "option_C": "...", "option_D": "...", '
    '"correct_option": "A", "explanation": "...", '
    '"difficulty": "easy"}, ...]}'
)


def _generate_with_kimchi(text, api_key, mcq_count, subject, chapter):
    from openai import OpenAI

    ch_num = re.search(r'\d+', chapter or "")
    ch_tag = ch_num.group() if ch_num else (chapter or "unknown").lower().replace(" ", "_")[:20]
    date_tag = datetime.date.today().strftime("%d%m%Y")
    tags = f"chapter:{ch_tag},date:{date_tag}"

    client = OpenAI(api_key=api_key, base_url="https://llm.kimchi.dev/openai/v1")
    # stream=True bypasses Cloudflare's 120-second proxy timeout: the first tokens
    # arrive within seconds, keeping the connection alive for the full response.
    stream = client.chat.completions.create(
        model="kimi-k2.5",
        temperature=0.1,
        stream=True,
        extra_headers={"X-Tags": tags},
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user",
             "content": _build_user_prompt(subject, chapter, mcq_count, text)
                        + _KIMCHI_JSON_INSTRUCTION}
        ]
    )
    raw = ""
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            raw += delta.content

    raw = raw.strip()
    if not raw:
        raise ValueError("Kimchi returned an empty response. Check your API key and account credits.")
    # Strip markdown fences if model wraps output in ```json ... ```
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    data = json.loads(raw)
    return _validate_mcqs(data.get("mcqs", []))


# ── Public API ────────────────────────────────────────────────────────────────

def generate_from_chunk(chunk_text, api_key, mcq_count, provider=None, subject="", chapter=""):
    """Generates and validates MCQs from a single pre-split text chunk."""
    if provider is None:
        provider = os.getenv("AI_PROVIDER", "gemini").lower()

    if provider == "claude":
        return _generate_with_claude(chunk_text, api_key, mcq_count, subject, chapter)
    elif provider == "grok":
        return _generate_with_grok(chunk_text, api_key, mcq_count, subject, chapter)
    elif provider == "kimchi":
        return _generate_with_kimchi(chunk_text, api_key, mcq_count, subject, chapter)
    else:
        return _generate_with_gemini(chunk_text, api_key, mcq_count, subject, chapter)


def process_pdf_and_generate(file_bytes, api_key, mcq_count=5, provider=None, subject="", chapter=""):
    """Single-chunk wrapper kept for compatibility. For full PDFs use extract_text_chunks + generate_from_chunk."""
    if provider is None:
        provider = os.getenv("AI_PROVIDER", "gemini").lower()
    chunks = extract_text_chunks(file_bytes)
    if not chunks:
        raise ValueError("Could not extract readable text from the PDF.")
    return generate_from_chunk(chunks[0], api_key, mcq_count, provider, subject, chapter)
