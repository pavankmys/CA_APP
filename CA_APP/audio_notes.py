"""
Audio Notes pipeline — converts a chapter PDF into ~20-30 min spoken-explainer episodes.

For each chunk of the chapter:
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
import google.generativeai as genai

import parser as pdf_parser

AUDIO_CHUNK_SIZE = 14000  # chars per episode — 23.9K chars produced a 47-min episode (5785
# words), 9.5K chars produced a 25-min episode (3798 words); content density varies a lot
# (worked examples expand more), so 14K aims for the 20-30 min target across both extremes
MIN_FINAL_CHUNK_CHARS = 5000  # merge a trailing chunk smaller than this into the previous one
MAX_EPISODE_WORDS = 4500  # ~30 min at 150wpm — scripts longer than this get split into
# multiple episodes at paragraph boundaries (content density varies too much per input
# chunk to control episode length via chunk size alone)
DEFAULT_VOICE = "en-IN-NeerjaNeural"

KIMCHI_BASE_URL = "https://llm.kimchi.dev/openai/v1"
KIMCHI_MODEL = "kimi-k2.5"

SCRIPT_SYSTEM_INSTRUCTION = """You are an experienced CA Inter faculty member recording audio study \
notes for students to listen to during their daily commute.

First, output a single line in the exact format:
TITLE: <a concise 4-8 word topic title for this segment>

Then, on the following lines, rewrite the given ICAI study material as a natural, \
spoken-language script of approximately 3,500-4,000 words. Follow these rules:

1. Conversational, teacher-explaining-to-a-student tone — like a lecture, not a textbook.
2. Expand every abbreviation, section number, and standard reference the first time it's \
mentioned (e.g. "Section 44AD of the Income Tax Act").
3. Describe tables, formulas, and journal entries entirely in words — never assume the \
listener can see anything.
4. Walk through worked examples step by step, narrating the numbers and reasoning aloud.
5. Use short sentences, verbal transitions ("Now,", "Next,", "Let's move to..."), and \
occasional rhetorical questions to keep attention.
6. Do not use bullet points, headings, markdown, or any visual formatting — output pure \
spoken prose only (after the TITLE line).
7. Begin with one sentence introducing what this segment covers, and end with a short \
one or two sentence recap of the key takeaway.
8. Stay strictly faithful to the source material's facts, figures, provisions, and \
numbers — do not invent or alter any of them. If a calculation needs a number that is \
not given in the source, describe the method generally without inventing a figure.
"""

VERIFY_SYSTEM_INSTRUCTION = """You are a meticulous fact-checker reviewing an audio script \
generated from ICAI CA Inter study material. The script is a SPOKEN-LANGUAGE REWRITE meant to \
teach the same material — it is EXPECTED to paraphrase, add verbal transitions, and include \
illustrative explanations of *why* a rule works. Your job is to catch FACTUAL ERRORS, not \
stylistic elaboration.

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

Do NOT flag (these are expected and desirable in a spoken rewrite):
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


def _parse_title(text):
    title = None
    if text.upper().startswith("TITLE:"):
        first_line, _, rest = text.partition("\n")
        title = first_line.split(":", 1)[1].strip()
        text = rest.strip()
    return title, text


def _generate_episode_script_gemini(source_text, api_key):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=SCRIPT_SYSTEM_INSTRUCTION,
    )
    prompt = f"Source material:\n\n{source_text}\n\nWrite the script now."
    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(temperature=0.1),
    )
    return _parse_title(response.text.strip())


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
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=VERIFY_SYSTEM_INSTRUCTION,
    )
    prompt = (
        f"SOURCE TEXT:\n\n{source_text}\n\n"
        f"GENERATED SCRIPT:\n\n{script_text}\n\n"
        f"List any unverifiable claims now."
    )
    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(temperature=0.0),
    )
    report = response.text.strip()
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
