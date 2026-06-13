"""
Audio Notes pipeline — converts a chapter PDF into ONE ~20-22 min high-level spoken
OVERVIEW episode (conceptual summary; worked examples are intentionally skipped).

For the chapter:
  1. generate_episode_script() — Gemini rewrites the chunk as a spoken-language script
     with a leading "TITLE: ..." line
  2. verify_script() — separate Gemini pass checks the script against the source for
     hallucinated numbers/references/claims
  3. synthesize() — edge-tts converts the script to an MP3

This module is pipeline logic only (no DB/Storage I/O) so it can be driven by both the
CLI test script (audio_poc.py) and, later, the ingest app.
"""
import asyncio
import math

import edge_tts
from google.genai import types

import gemini_cost
import parser as pdf_parser

AUDIO_CHUNK_SIZE = 400000  # effectively "whole chapter in one call" — the note is now a
# single high-level OVERVIEW episode per chapter, not a full rewrite. Gemini 2.5 Flash
# (1M-token context) comfortably takes an entire ICAI chapter; the prompt caps output
# at ~3,000-3,300 words (~20-22 min at 150 wpm) regardless of input length.
MIN_FINAL_CHUNK_CHARS = 5000  # merge a trailing chunk smaller than this into the previous one
MAX_EPISODE_WORDS = 4000  # safety net (~26 min at 150wpm) — if the model overshoots the
# target length, the script gets split at paragraph boundaries rather than producing
# one over-long episode
DEFAULT_VOICE = "en-IN-NeerjaNeural"

_BLOCK_NONE_SAFETY_SETTINGS = [
    types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for category in (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
]

KIMCHI_BASE_URL = "https://llm.kimchi.dev/openai/v1"
KIMCHI_MODEL = "kimi-k2.5"

SCRIPT_SYSTEM_INSTRUCTION = """You are an experienced CA Inter faculty member recording a single \
HIGH-LEVEL AUDIO OVERVIEW of a chapter, for students to listen to during their daily commute. \
This is NOT a full rewrite of the material — it is a conceptual summary that gives the student \
a mental map of the chapter before (or after) they read it in detail.

First, output a single line in the exact format:
TITLE: <a concise 4-8 word topic title for this chapter>

Then, on the following lines, write a natural, spoken-language overview script of \
approximately 3,000-3,300 words (this yields a 20-22 minute episode — do NOT exceed this, \
no matter how long the source material is). Follow these rules:

1. Conversational, teacher-explaining-to-a-student tone — like a lecture, not a textbook.
2. Cover the chapter at a CONCEPTUAL level only: what each topic is, why it exists, the key \
rules/provisions/principles, how the pieces relate to each other, and what is exam-relevant. \
Allocate words to topics in proportion to their importance, not their page count.
3. SKIP the worked examples, illustrations, and practice problems in the source material \
entirely. Do not narrate any step-by-step calculations. At most, mention in one sentence \
what kind of problems the chapter's examples cover (e.g. "the chapter then works through \
illustrations on computing depreciation under both methods — practice those on paper").
4. State the most important numbers, thresholds, rates, and section/standard references — \
the ones a student must remember — but do not enumerate every figure in the source.
5. Expand every abbreviation, section number, and standard reference the first time it's \
mentioned (e.g. "Section 44AD of the Income Tax Act").
6. Describe any essential formula or rule entirely in words — never assume the listener \
can see anything.
7. Use short sentences, verbal transitions ("Now,", "Next,", "Let's move to..."), and \
occasional rhetorical questions to keep attention.
8. Do not use bullet points, headings, markdown, or any visual formatting — output pure \
spoken prose only (after the TITLE line).
9. Begin with one or two sentences giving a roadmap of what this chapter covers, and end \
with a short recap of the three to five key takeaways.
10. Stay strictly faithful to the source material's facts, figures, provisions, and \
numbers — do not invent or alter any of them.
"""

VERIFY_SYSTEM_INSTRUCTION = """You are a meticulous fact-checker reviewing an audio script \
generated from ICAI CA Inter study material. The script is a CONDENSED, HIGH-LEVEL SPOKEN \
OVERVIEW of the material — it is EXPECTED to omit most of the source (especially worked \
examples and illustrations), paraphrase heavily, add verbal transitions, and explain *why* \
rules work. Your job is to catch FACTUAL ERRORS, not omissions or stylistic elaboration.

You will be given SOURCE TEXT (the original study material) and a GENERATED SCRIPT (a spoken \
rewrite of it).

Flag a finding as a CRITICAL ISSUE only if it is one of:
- A number, percentage, or monetary figure that contradicts the source, or is not a correct \
arithmetic derivation from numbers given in the source
- A section number, Accounting Standard / Standard on Auditing / other regulatory reference \
that contradicts the source (wrong number, or a reference inconsistent with the source's scope)
- A specific example or scenario with invented entity names, dates, or figures NOT present in \
the source, presented as if it were part of the source material
- A statement about accounting, tax, audit, or legal treatment that CONTRADICTS the source

Do NOT flag (these are expected and desirable in a condensed spoken overview):
- Omission of any source content — examples, illustrations, tables, sub-topics, or figures \
left out of the script are intentional, never an issue
- Paraphrasing or restating a rule in different words
- Illustrative examples that name a general category of item (e.g. "custom-made jewellery" as \
an example of a unique high-value item) without inventing source-like data
- Pedagogical explanations of *why* a rule matters, logically derived from the source's own \
numbers/rules
- Equivalent mathematical reformulations (e.g. "subtract 10%" vs "multiply by 90%")
- General true statements about the standard's purpose or importance

For each CRITICAL ISSUE, output a numbered item with the exact phrase/sentence from the script \
and why it's critical (state the source's value if there's a number mismatch).

If there are no critical issues, respond with exactly: NO CRITICAL ISSUES
"""


def extract_pdf_bytes(pdf_path):
    with open(pdf_path, "rb") as f:
        return f.read()


def chunk_chapter_text(file_bytes, chunk_size=AUDIO_CHUNK_SIZE):
    """Splits a chapter PDF into episode-sized text chunks at paragraph boundaries."""
    chunks = pdf_parser.extract_text_chunks(file_bytes, chunk_size=chunk_size)
    if len(chunks) >= 2 and len(chunks[-1]) < MIN_FINAL_CHUNK_CHARS:
        chunks[-2] = chunks[-2] + "\n\n" + chunks[-1]
        chunks.pop()
    return chunks


def _extract_gemini_text(response):
    """Returns response.text, or raises a RuntimeError with diagnostic info if Gemini
    returned no usable content (e.g. content filtering, or an empty STOP response)."""
    try:
        return response.text
    except ValueError as e:
        candidate = response.candidates[0] if response.candidates else None
        finish_reason = candidate.finish_reason.name if candidate else "UNKNOWN"
        feedback = getattr(response, "prompt_feedback", None)
        raise RuntimeError(
            f"Gemini returned no content (finish_reason={finish_reason}, "
            f"prompt_feedback={feedback}). This is usually a transient issue or content "
            f"filtering on this chunk — try regenerating."
        ) from e


def _parse_title(text):
    title = None
    if text.upper().startswith("TITLE:"):
        first_line, _, rest = text.partition("\n")
        title = first_line.split(":", 1)[1].strip()
        text = rest.strip()
    return title, text


def script_to_markdown(title, script_text):
    """Returns an episode script as an editable markdown document with a leading
    '# Title' heading."""
    return f"# {title}\n\n{script_text}"


def markdown_to_script(markdown_text):
    """Returns (title, script_text) from a markdown document produced by
    `script_to_markdown`. title is None if the text has no leading '# ' heading."""
    first_line, _, rest = markdown_text.partition("\n")
    if first_line.startswith("# "):
        return first_line[2:].strip(), rest.strip()
    return None, markdown_text.strip()


def _generate_episode_script_gemini(source_text, api_key):
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = f"Source material:\n\n{source_text}\n\nWrite the script now."
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SCRIPT_SYSTEM_INSTRUCTION,
            temperature=0.1,
            safety_settings=_BLOCK_NONE_SAFETY_SETTINGS,
            http_options=types.HttpOptions(timeout=180_000),
        ),
    )
    gemini_cost.record("audio_script_gen", response.usage_metadata)
    return _parse_title(_extract_gemini_text(response).strip())


def _generate_episode_script_kimchi(source_text, api_key):
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=KIMCHI_BASE_URL)
    prompt = f"Source material:\n\n{source_text}\n\nWrite the script now."
    # stream=True bypasses Cloudflare's 120-second proxy timeout: the first tokens
    # arrive within seconds, keeping the connection alive for the full response.
    stream = client.chat.completions.create(
        model=KIMCHI_MODEL,
        temperature=0.1,
        stream=True,
        messages=[
            {"role": "system", "content": SCRIPT_SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
    )
    raw = ""
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            raw += delta.content

    raw = raw.strip()
    if not raw:
        raise ValueError("Kimchi returned an empty response. Check your API key and account credits.")
    return _parse_title(raw)


def split_script_into_episodes(title, script_text, max_words=MAX_EPISODE_WORDS):
    """Splits an over-long script into multiple (title, script_text) episodes at paragraph
    boundaries, roughly balancing word count across parts. Returns a list with a single
    (title, script_text) entry if the script is already within max_words."""
    words = script_text.split()
    if len(words) <= max_words:
        return [(title, script_text)]

    paragraphs = [p for p in script_text.split("\n\n") if p.strip()]
    num_parts = math.ceil(len(words) / max_words)
    target_words = len(words) / num_parts

    parts = []
    current_paragraphs = []
    current_words = 0
    for para in paragraphs:
        para_words = len(para.split())
        if (current_paragraphs and current_words + para_words > target_words
                and len(parts) < num_parts - 1):
            parts.append("\n\n".join(current_paragraphs))
            current_paragraphs = []
            current_words = 0
        current_paragraphs.append(para)
        current_words += para_words
    if current_paragraphs:
        parts.append("\n\n".join(current_paragraphs))

    return [(f"{title} (Part {i} of {len(parts)})", part) for i, part in enumerate(parts, start=1)]


def generate_episode_script(source_text, api_key, provider="gemini"):
    """Returns (title, script_text). title is None if the model didn't include a TITLE line."""
    if provider == "kimchi":
        return _generate_episode_script_kimchi(source_text, api_key)
    return _generate_episode_script_gemini(source_text, api_key)


def _verify_script_gemini(source_text, script_text, api_key):
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = (
        f"SOURCE TEXT:\n\n{source_text}\n\n"
        f"GENERATED SCRIPT:\n\n{script_text}\n\n"
        f"List any unverifiable claims now."
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=VERIFY_SYSTEM_INSTRUCTION,
            temperature=0.0,
            safety_settings=_BLOCK_NONE_SAFETY_SETTINGS,
            http_options=types.HttpOptions(timeout=180_000),
        ),
    )
    gemini_cost.record("audio_verify", response.usage_metadata)
    report = _extract_gemini_text(response).strip()
    return report == "NO CRITICAL ISSUES", report


def _verify_script_kimchi(source_text, script_text, api_key):
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=KIMCHI_BASE_URL)
    prompt = (
        f"SOURCE TEXT:\n\n{source_text}\n\n"
        f"GENERATED SCRIPT:\n\n{script_text}\n\n"
        f"List any unverifiable claims now."
    )
    stream = client.chat.completions.create(
        model=KIMCHI_MODEL,
        temperature=0.0,
        stream=True,
        messages=[
            {"role": "system", "content": VERIFY_SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
    )
    raw = ""
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            raw += delta.content

    report = raw.strip()
    if not report:
        raise ValueError("Kimchi returned an empty response. Check your API key and account credits.")
    return report == "NO CRITICAL ISSUES", report


def verify_script(source_text, script_text, api_key, provider="gemini"):
    """Returns (is_clean, report). is_clean is True iff report == 'NO CRITICAL ISSUES'."""
    if provider == "kimchi":
        return _verify_script_kimchi(source_text, script_text, api_key)
    return _verify_script_gemini(source_text, script_text, api_key)


async def synthesize(text, voice, out_path):
    communicate = edge_tts.Communicate(text, voice=voice)
    await communicate.save(out_path)


def get_audio_duration_seconds(mp3_path):
    from mutagen.mp3 import MP3
    return int(round(MP3(mp3_path).info.length))
