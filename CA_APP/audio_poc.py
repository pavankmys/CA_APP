"""
CLI test driver for the Audio Notes pipeline (audio_notes.py).

Processes a whole chapter PDF into multiple ~20-30 min episodes: each chunk gets a
spoken-language script, a hallucination-verification pass against the source, and (if
clean) an MP3 via edge-tts.

Usage:
    python audio_poc.py <path_to_pdf> [--voice en-IN-NeerjaNeural]

Output (per episode):
    CA_APP/audio_poc_output/episode_NN/script.txt
    CA_APP/audio_poc_output/episode_NN/verification.txt
    CA_APP/audio_poc_output/episode_NN/episode.mp3   (only if verification is clean)

A single source chunk may produce multiple episodes if its generated script exceeds
audio_notes.MAX_EPISODE_WORDS (split at paragraph boundaries).
"""
import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

import audio_notes

load_dotenv()

# Windows console (cp1252) can't print some characters extracted from PDFs (e.g. )
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("pdf_path")
    arg_parser.add_argument("--voice", default=audio_notes.DEFAULT_VOICE)
    args = arg_parser.parse_args()

    provider = os.environ.get("AI_PROVIDER", "gemini").lower()
    if provider == "kimchi":
        api_key = os.environ["KIMCHI_API_KEY"]
    else:
        api_key = os.environ["GEMINI_API_KEY"]
    print(f"Using provider: {provider}")

    print(f"Extracting and chunking {args.pdf_path}...")
    file_bytes = audio_notes.extract_pdf_bytes(args.pdf_path)
    chunks = audio_notes.chunk_chapter_text(file_bytes)
    print(f"{len(chunks)} episode(s) from {sum(len(c) for c in chunks)} total chars")

    out_root = os.path.join(os.path.dirname(__file__), "audio_poc_output")
    os.makedirs(out_root, exist_ok=True)

    summary = []
    episode_num = 0
    for i, chunk in enumerate(chunks, start=1):
        print(f"\n--- Chunk {i}/{len(chunks)} ({len(chunk)} chars) ---")
        print("Generating script...")
        title, script = audio_notes.generate_episode_script(chunk, api_key, provider=provider)
        title = title or f"Chunk {i}"
        word_count = len(script.split())
        print(f"Title: {title} ({word_count} words)")

        print("Verifying against source...")
        is_clean, report = audio_notes.verify_script(chunk, script, api_key, provider=provider)

        if is_clean:
            episodes = audio_notes.split_script_into_episodes(title, script)
        else:
            episodes = [(title, script)]

        for ep_title, ep_script in episodes:
            episode_num += 1
            ep_dir = os.path.join(out_root, f"episode_{episode_num:02d}")
            os.makedirs(ep_dir, exist_ok=True)
            ep_words = len(ep_script.split())

            with open(os.path.join(ep_dir, "script.txt"), "w", encoding="utf-8") as f:
                f.write(ep_script)
            with open(os.path.join(ep_dir, "verification.txt"), "w", encoding="utf-8") as f:
                f.write(report)

            if is_clean:
                print(f"Synthesizing audio for episode {episode_num}: {ep_title!r} ({ep_words} words)...")
                audio_path = os.path.join(ep_dir, "episode.mp3")
                asyncio.run(audio_notes.synthesize(ep_script, args.voice, audio_path))
                duration = audio_notes.get_audio_duration_seconds(audio_path)
                duration_str = f"{duration // 60}m {duration % 60}s"
                print(f"Audio saved: {audio_path} (~{duration_str})")
                summary.append((episode_num, ep_title, ep_words, "CLEAN", duration_str))
            else:
                print("Verification: ISSUES FOUND - audio skipped.")
                print(report)
                summary.append((episode_num, ep_title, ep_words, "ISSUES - skipped", "-"))

    print("\n=== Summary ===")
    for i, title, words, status, duration in summary:
        print(f"Episode {i:>2}: {title!r:50} {words:5} words  {status:18} {duration}")


if __name__ == "__main__":
    main()
