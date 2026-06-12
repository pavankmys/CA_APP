import streamlit as st
import os
import re
import time
import asyncio
import tempfile
from dotenv import load_dotenv
from database import (
    save_generated_mcqs, get_question_bank_summary,
    delete_chapter_mcqs, delete_subject_mcqs, remove_duplicate_mcqs,
    save_generated_mock_mcqs, save_generated_simulations,
    get_mock_bank_summary, remove_duplicate_mock_mcqs,
    get_or_create_chapter, save_audio_episode, get_episodes_for_chapter,
    get_all_episodes_for_feed, get_audio_episode_summary, delete_audio_episode,
)
from parser import extract_text_chunks, generate_from_chunk, generate_mock_mcqs_from_chunk, generate_simulations_from_chunk
import audio_notes
import audio_publish

load_dotenv()

st.set_page_config(page_title="CA Inter — Ingest Study Material", layout="wide")

# ── Sidebar ──────────────────────────────────────────────────────────────────
provider = os.getenv("AI_PROVIDER", "gemini").lower()

st.sidebar.title("Configuration Panel")
_provider_models = {
    "gemini":  "gemini-2.5-flash",
    "claude":  "claude-haiku-4-5",
    "grok":    "grok-3-mini",
    "kimchi":  "kimi-k2.5",
}
st.sidebar.info(
    f"**AI Provider:** {provider.upper()}  \n"
    f"**Model:** {_provider_models.get(provider, provider)}  \n\n"
    f"Change via `AI_PROVIDER` in `.env`"
)

if provider == "claude":
    env_key = os.getenv("ANTHROPIC_API_KEY", "")
    key_label = "Anthropic API Key (override)"
elif provider == "grok":
    env_key = os.getenv("XAI_API_KEY", "")
    key_label = "xAI API Key (override)"
elif provider == "kimchi":
    env_key = os.getenv("KIMCHI_API_KEY", "")
    key_label = "Kimchi API Key (override)"
else:
    env_key = os.getenv("GEMINI_API_KEY", "")
    key_label = "Gemini API Key (override)"

key_override = st.sidebar.text_input(key_label, value="", type="password",
                                     help="Leave blank to use the key from .env")
api_key = key_override if key_override else env_key

# ── Session state init ────────────────────────────────────────────────────────
if "delete_confirm" not in st.session_state:
    st.session_state.delete_confirm = None  # (subject, chapter) or ("subject", subject) pending confirmation

# ─────────────────────────────────────────────────────────────────────────────
st.title("📥 CA Inter — Ingest Study Material")

st.header("Upload New ICAI Materials")
st.info(
    f"Provider: **{provider.upper()}** | "
    "Upload the full PDF — it will be automatically split into ~20-page sections and processed."
)

subject_input = st.text_input("Enter Subject Name (e.g., 'Advanced Accounting')")
chapter_input = st.text_input("Enter Chapter / Ref Name (e.g., 'Chapter 1-3: Partnership Accounts')")
uploaded_file = st.file_uploader("Choose a source PDF file", type=["pdf"])
mcqs_per_section = st.slider(
    "MCQs per section (~20 pages each)", min_value=5, max_value=30, value=15,
    help="Each ~20-page section generates this many MCQs. A 192-page PDF → ~9 sections → ~135 MCQs at default."
)

if uploaded_file:
    size_kb = uploaded_file.size / 1024
    est_sections = max(1, round((size_kb * 1750) / (8 * 40000)))
    st.caption(
        f"📄 **{uploaded_file.name}** — {size_kb:.0f} KB "
        f"(estimated ~{est_sections} sections → ~{est_sections * mcqs_per_section} MCQs)"
    )

if st.button("Parse & Generate MCQs", type="primary"):
    if not api_key:
        st.error(f"Please supply a valid {provider.upper()} API Key via `.env` or the sidebar.")
    elif not subject_input or not chapter_input or not uploaded_file:
        st.warning("Please complete all input fields.")
    else:
        file_bytes = uploaded_file.read()

        with st.spinner("Splitting PDF into sections..."):
            try:
                # Kimchi (kimi-k2.5) does extensive CoT reasoning — smaller chunks
                # keep each call under ~100s and avoid Cloudflare proxy timeouts.
                kimchi_chunk_size = 8000
                chunk_size = kimchi_chunk_size if provider == "kimchi" else None
                chunks = extract_text_chunks(file_bytes, **({} if chunk_size is None else {"chunk_size": chunk_size}))
            except Exception as e:
                st.error(f"PDF extraction failed: {str(e)}")
                st.stop()

        total_sections = len(chunks)
        st.info(f"📊 Found **{total_sections} sections** — generating up to {total_sections * mcqs_per_section} MCQs total")

        progress_bar = st.progress(0)
        status = st.empty()
        total_mcqs = 0
        failed_chunks = []   # [(original_index, chunk_text, error_str)]

        # ── First pass ────────────────────────────────────────────────────
        for i, chunk in enumerate(chunks):
            status.text(f"⚙️  Section {i + 1} of {total_sections} — calling {provider.upper()}...")
            try:
                mcqs = generate_from_chunk(chunk, api_key, mcqs_per_section, provider=provider,
                                           subject=subject_input.strip(), chapter=chapter_input.strip())
                save_generated_mcqs(subject_input.strip(), chapter_input.strip(), mcqs)
                total_mcqs += len(mcqs)
            except Exception as e:
                failed_chunks.append((i, chunk, str(e)[:200]))
            progress_bar.progress((i + 1) / total_sections)

        # ── Automatic retry for failed sections ───────────────────────────
        final_errors = []
        if failed_chunks:
            status.text(f"🔄 Retrying {len(failed_chunks)} failed section(s)...")
            retry_bar = st.progress(0)
            for j, (i, chunk, _) in enumerate(failed_chunks):
                status.text(f"🔄 Retrying section {i + 1} ({j + 1} of {len(failed_chunks)})...")
                try:
                    mcqs = generate_from_chunk(chunk, api_key, mcqs_per_section, provider=provider,
                                               subject=subject_input.strip(), chapter=chapter_input.strip())
                    save_generated_mcqs(subject_input.strip(), chapter_input.strip(), mcqs)
                    total_mcqs += len(mcqs)
                except Exception as e:
                    final_errors.append((i + 1, str(e)[:200]))
                retry_bar.progress((j + 1) / len(failed_chunks))
            retry_bar.empty()

        status.empty()
        progress_bar.empty()

        if final_errors:
            st.warning(f"⚠️ {len(final_errors)} section(s) still failed after retry:")
            for sec_num, err in final_errors:
                st.error(f"Section {sec_num}: {err}")

        successful = total_sections - len(final_errors)
        st.success(
            f"✅ Done! Saved **{total_mcqs} MCQs** from {successful}/{total_sections} sections "
            f"into the database for *{subject_input.strip()}*."
        )

# ── Generate Mock Exam Content (CPA-Style) ─────────────────────────────────────
st.divider()
st.header("🧪 Generate Mock Exam Content (CPA-Style)")
st.info(
    f"Provider: **{provider.upper()}** | "
    "Generates a separate high-difficulty MCQ pool plus CPA-style task-based simulations "
    "(case-study scenario + numeric entry, dropdown selection, and journal entry items) "
    "used by the CPA Simulation Exam. Stored separately from the practice question bank."
)

mock_subject_input = st.text_input("Subject Name", key="mock_subject_input")
mock_chapter_input = st.text_input("Chapter / Ref Name", key="mock_chapter_input")
mock_uploaded_file = st.file_uploader("Choose a source PDF file", type=["pdf"], key="mock_uploaded_file")

mock_col1, mock_col2 = st.columns(2)
with mock_col1:
    gen_mock_mcqs = st.checkbox("Generate Mock MCQs", value=True, key="gen_mock_mcqs")
    mock_mcqs_per_section = st.slider(
        "Mock MCQs per section (~20 pages each)", min_value=5, max_value=30, value=15,
        disabled=not gen_mock_mcqs, key="mock_mcqs_per_section"
    )
with mock_col2:
    gen_sims = st.checkbox("Generate Simulations", value=True, key="gen_sims")
    sims_per_section = st.slider(
        "Simulations per section (~20 pages each)", min_value=1, max_value=3, value=1,
        disabled=not gen_sims, key="sims_per_section"
    )

if mock_uploaded_file:
    size_kb = mock_uploaded_file.size / 1024
    est_sections = max(1, round((size_kb * 1750) / (8 * 40000)))
    st.caption(f"📄 **{mock_uploaded_file.name}** — {size_kb:.0f} KB (estimated ~{est_sections} sections)")

if st.button("Generate Mock Exam Content", type="primary"):
    if not api_key:
        st.error(f"Please supply a valid {provider.upper()} API Key via `.env` or the sidebar.")
    elif not mock_subject_input or not mock_chapter_input or not mock_uploaded_file:
        st.warning("Please complete all input fields.")
    elif not gen_mock_mcqs and not gen_sims:
        st.warning("Select at least one of: Generate Mock MCQs, Generate Simulations.")
    else:
        file_bytes = mock_uploaded_file.read()

        with st.spinner("Splitting PDF into sections..."):
            try:
                kimchi_chunk_size = 8000
                chunk_size = kimchi_chunk_size if provider == "kimchi" else None
                chunks = extract_text_chunks(file_bytes, **({} if chunk_size is None else {"chunk_size": chunk_size}))
            except Exception as e:
                st.error(f"PDF extraction failed: {str(e)}")
                st.stop()

        total_sections = len(chunks)
        st.info(f"📊 Found **{total_sections} sections**")

        progress_bar = st.progress(0)
        status = st.empty()
        total_mock_mcqs = 0
        total_sims = 0
        failed_chunks = []   # [(original_index, chunk_text, error_str)]

        subj = mock_subject_input.strip()
        chap = mock_chapter_input.strip()

        def _process_mock_chunk(chunk):
            mcqs_added, sims_added = 0, 0
            if gen_mock_mcqs:
                mcqs = generate_mock_mcqs_from_chunk(chunk, api_key, mock_mcqs_per_section, provider=provider,
                                                      subject=subj, chapter=chap)
                mcqs_added = save_generated_mock_mcqs(subj, chap, mcqs)
            if gen_sims:
                sims = generate_simulations_from_chunk(chunk, api_key, sims_per_section, provider=provider,
                                                         subject=subj, chapter=chap)
                sims_added = save_generated_simulations(subj, chap, sims)
            return mcqs_added, sims_added

        # ── First pass ────────────────────────────────────────────────────
        for i, chunk in enumerate(chunks):
            status.text(f"⚙️  Section {i + 1} of {total_sections} — calling {provider.upper()}...")
            try:
                mcqs_added, sims_added = _process_mock_chunk(chunk)
                total_mock_mcqs += mcqs_added
                total_sims += sims_added
            except Exception as e:
                failed_chunks.append((i, chunk, str(e)[:200]))
            progress_bar.progress((i + 1) / total_sections)

        # ── Automatic retry for failed sections ───────────────────────────
        final_errors = []
        if failed_chunks:
            status.text(f"🔄 Retrying {len(failed_chunks)} failed section(s)...")
            retry_bar = st.progress(0)
            for j, (i, chunk, _) in enumerate(failed_chunks):
                status.text(f"🔄 Retrying section {i + 1} ({j + 1} of {len(failed_chunks)})...")
                try:
                    mcqs_added, sims_added = _process_mock_chunk(chunk)
                    total_mock_mcqs += mcqs_added
                    total_sims += sims_added
                except Exception as e:
                    final_errors.append((i + 1, str(e)[:200]))
                retry_bar.progress((j + 1) / len(failed_chunks))
            retry_bar.empty()

        status.empty()
        progress_bar.empty()

        if final_errors:
            st.warning(f"⚠️ {len(final_errors)} section(s) still failed after retry:")
            for sec_num, err in final_errors:
                st.error(f"Section {sec_num}: {err}")

        successful = total_sections - len(final_errors)
        result_parts = []
        if gen_mock_mcqs:
            result_parts.append(f"**{total_mock_mcqs} mock MCQs**")
        if gen_sims:
            result_parts.append(f"**{total_sims} simulations**")
        st.success(
            f"✅ Done! Saved {' and '.join(result_parts)} from {successful}/{total_sections} sections "
            f"into the database for *{subj}*."
        )

# ── Question Bank Management ──────────────────────────────────────────────────
st.divider()
with st.expander("Manage Question Bank", expanded=False):
    summary = get_question_bank_summary()

    if not summary:
        st.info("No questions in the bank yet. Upload a PDF above to get started.")
    else:
        # ── Summary table ─────────────────────────────────────────────────
        st.subheader("Current Bank")
        total_in_bank = sum(r[2] for r in summary)
        st.caption(f"Total: **{total_in_bank} questions** across {len(summary)} chapter(s)")

        rows_display = [{"Subject": s, "Chapter": c, "MCQs": n} for s, c, n in summary]
        st.table(rows_display)

        # ── Mock exam content summary ───────────────────────────────────────
        mock_summary = get_mock_bank_summary()
        if mock_summary:
            st.subheader("Mock Exam Content")
            total_mock_mcqs = sum(r[2] for r in mock_summary)
            total_sims = sum(r[3] for r in mock_summary)
            st.caption(
                f"Total: **{total_mock_mcqs} mock MCQs** and **{total_sims} simulations** "
                f"across {len(mock_summary)} chapter(s)"
            )
            mock_rows_display = [
                {"Subject": s, "Chapter": c, "Mock MCQs": mm, "Simulations": sc}
                for s, c, mm, sc in mock_summary
            ]
            st.table(mock_rows_display)

        # ── Deduplication ─────────────────────────────────────────────────
        st.subheader("Remove Duplicates")
        if st.button("Scan & Remove Duplicate Questions", use_container_width=True):
            removed = remove_duplicate_mcqs()
            removed_mock = remove_duplicate_mock_mcqs()
            if removed or removed_mock:
                st.success(f"Removed {removed} duplicate practice question(s) and {removed_mock} duplicate mock MCQ(s).")
            else:
                st.info("No duplicates found — bank is clean.")

        st.divider()

        # ── Delete controls ───────────────────────────────────────────────
        st.subheader("Delete Questions")
        subjects_in_bank = sorted({r[0] for r in summary})
        del_subject = st.selectbox("Select subject", subjects_in_bank, key="del_subject")

        chapters_for_subj = [r[1] for r in summary if r[0] == del_subject]
        del_scope = st.radio(
            "Delete scope",
            ["Entire subject", "Specific chapter"],
            horizontal=True,
            key="del_scope"
        )

        del_chapter = None
        if del_scope == "Specific chapter":
            del_chapter = st.selectbox("Select chapter", chapters_for_subj, key="del_chapter")

        # Count questions that would be deleted
        if del_scope == "Entire subject":
            del_count = sum(r[2] for r in summary if r[0] == del_subject)
            del_label = f"all {del_count} questions in **{del_subject}**"
            del_key   = ("subject", del_subject)
        else:
            del_count = next((r[2] for r in summary if r[0] == del_subject and r[1] == del_chapter), 0)
            del_label = f"{del_count} questions in **{del_subject} › {del_chapter}**"
            del_key   = ("chapter", del_subject, del_chapter)

        if st.button(f"Delete {del_label}", type="secondary", use_container_width=True):
            st.session_state.delete_confirm = del_key

        # Confirmation step
        if st.session_state.delete_confirm == del_key:
            st.warning(
                f"Are you sure? This will permanently delete {del_label}, "
                "along with any associated mock exam MCQs and simulations for this scope. "
                "This cannot be undone."
            )
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Yes, delete permanently", type="primary", use_container_width=True):
                    if del_key[0] == "subject":
                        n = delete_subject_mcqs(del_key[1])
                    else:
                        n = delete_chapter_mcqs(del_key[1], del_key[2])
                    st.session_state.delete_confirm = None
                    st.success(f"Deleted {n} question(s).")
                    st.rerun()
            with col_no:
                if st.button("Cancel", use_container_width=True):
                    st.session_state.delete_confirm = None
                    st.rerun()

# ── Audio Notes ─────────────────────────────────────────────────────────────
def _slugify(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-') or "x"


st.divider()
st.header("🎧 Generate Audio Notes")

gemini_api_key = os.getenv("GEMINI_API_KEY", "")
r2_account_id = os.getenv("R2_ACCOUNT_ID", "")
r2_access_key_id = os.getenv("R2_ACCESS_KEY_ID", "")
r2_secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY", "")
r2_bucket = os.getenv("R2_BUCKET", "")
r2_public_url = os.getenv("R2_PUBLIC_URL", "")
r2_configured = all([r2_account_id, r2_access_key_id, r2_secret_access_key, r2_bucket, r2_public_url])

st.info(
    "Converts a chapter PDF into ~20-30 min spoken-explainer audio episodes. Each script is "
    "verified against the source for hallucinated numbers/references before being synthesized "
    "and uploaded. Always uses **Gemini** (long-form generation isn't reliable on other "
    "providers), regardless of the `AI_PROVIDER` setting above."
)

if not gemini_api_key:
    st.warning("`GEMINI_API_KEY` is not set in `.env` — required for script generation.")
if not r2_configured:
    st.warning("`R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` / `R2_PUBLIC_URL` "
               "are not all set in `.env` — required to upload audio and publish the feed.")

audio_subject_input = st.text_input("Subject Name", key="audio_subject_input")
audio_chapter_input = st.text_input("Chapter / Ref Name", key="audio_chapter_input")
audio_uploaded_file = st.file_uploader("Choose a source PDF file", type=["pdf"], key="audio_uploaded_file")
audio_voice = st.text_input("edge-tts voice", value=audio_notes.DEFAULT_VOICE, key="audio_voice")

if audio_uploaded_file:
    size_kb = audio_uploaded_file.size / 1024
    st.caption(f"📄 **{audio_uploaded_file.name}** — {size_kb:.0f} KB")

if st.button("Generate Audio Notes", type="primary"):
    if not gemini_api_key or not r2_configured:
        st.error("Missing required configuration — see warnings above.")
    elif not audio_subject_input or not audio_chapter_input or not audio_uploaded_file:
        st.warning("Please complete all input fields.")
    else:
        subj = audio_subject_input.strip()
        chap = audio_chapter_input.strip()
        file_bytes = audio_uploaded_file.read()
        r2_client = audio_publish.make_client(r2_account_id, r2_access_key_id, r2_secret_access_key)

        with st.spinner("Extracting and chunking PDF..."):
            chunks = audio_notes.chunk_chapter_text(file_bytes)

        st.info(f"📊 {len(chunks)} source chunk(s) — each may produce one or more episodes")

        chapter_id = get_or_create_chapter(subj, chap)
        existing = get_episodes_for_chapter(chapter_id)
        next_episode_num = (max(ep[1] for ep in existing) + 1) if existing else 1

        progress_bar = st.progress(0)
        status = st.empty()
        saved = []     # [(episode_num, title, duration_seconds)]
        skipped = []   # [(chunk_index, title, report)]

        for i, chunk in enumerate(chunks):
            try:
                status.text(f"⚙️  Chunk {i + 1} of {len(chunks)} — generating script...")
                title, script = audio_notes.generate_episode_script(chunk, gemini_api_key, provider="gemini")
                title = title or f"{chap} — Part {i + 1}"

                status.text(f"⚙️  Chunk {i + 1} of {len(chunks)} — verifying against source...")
                is_clean, report = audio_notes.verify_script(chunk, script, gemini_api_key, provider="gemini")
            except Exception as e:
                skipped.append((i + 1, f"{chap} — Part {i + 1}", f"Generation failed: {e}"))
                progress_bar.progress((i + 1) / len(chunks))
                continue

            if not is_clean:
                skipped.append((i + 1, title, report))
                progress_bar.progress((i + 1) / len(chunks))
                continue

            for ep_title, ep_script in audio_notes.split_script_into_episodes(title, script):
                status.text(f"⚙️  Chunk {i + 1} of {len(chunks)} — synthesizing audio for '{ep_title}'...")
                with tempfile.TemporaryDirectory() as tmpdir:
                    mp3_path = os.path.join(tmpdir, "episode.mp3")
                    asyncio.run(audio_notes.synthesize(ep_script, audio_voice, mp3_path))
                    duration = audio_notes.get_audio_duration_seconds(mp3_path)
                    file_size = os.path.getsize(mp3_path)

                    storage_path = f"{_slugify(subj)}/{_slugify(chap)}/episode_{next_episode_num:03d}.mp3"
                    status.text(f"⚙️  Chunk {i + 1} of {len(chunks)} — uploading '{ep_title}'...")
                    audio_url = audio_publish.upload_audio_file(
                        r2_client, r2_bucket, r2_public_url, mp3_path, storage_path
                    )

                save_audio_episode(
                    chapter_id, next_episode_num, ep_title, audio_url,
                    duration, len(ep_script.split()), file_size
                )
                saved.append((next_episode_num, ep_title, duration))
                next_episode_num += 1

            progress_bar.progress((i + 1) / len(chunks))

        status.empty()
        progress_bar.empty()

        if saved:
            st.success(f"✅ Saved and uploaded {len(saved)} episode(s) for *{subj} › {chap}*:")
            for ep_num, ep_title, duration in saved:
                st.write(f"- Episode {ep_num}: **{ep_title}** (~{duration // 60}m {duration % 60}s)")
            st.info("Go to **Manage Audio Notes** below and click **Publish Feed** to update the podcast feed.")

        if skipped:
            st.warning(f"⚠️ {len(skipped)} chunk(s) failed verification and were skipped (no audio generated):")
            for chunk_num, title, report in skipped:
                with st.expander(f"Chunk {chunk_num}: {title} — verification issues"):
                    st.text(report)

# ── Manage Audio Notes ──────────────────────────────────────────────────────
with st.expander("Manage Audio Notes", expanded=False):
    audio_summary = get_audio_episode_summary()

    if not audio_summary:
        st.info("No audio episodes yet. Generate some above.")
    else:
        for subj, chap, count, total_seconds in audio_summary:
            total_minutes = total_seconds // 60
            st.write(f"**{subj} › {chap}** — {count} episode(s), ~{total_minutes} min total")

        st.divider()

        if st.button("Publish Feed", type="primary"):
            if not r2_configured:
                st.error("R2 configuration is missing in `.env` — see warning above.")
            else:
                rows = get_all_episodes_for_feed()
                episodes = [
                    {
                        "title": title, "audio_url": audio_url, "duration_seconds": duration,
                        "file_size_bytes": file_size, "episode_num": episode_num,
                        "subject": subj, "chapter": chap,
                    }
                    for (_id, title, audio_url, duration, file_size, episode_num, subj, chap) in rows
                ]
                with st.spinner("Building and uploading feed.xml..."):
                    r2_client = audio_publish.make_client(r2_account_id, r2_access_key_id, r2_secret_access_key)
                    feed_url = audio_publish.publish_feed(r2_client, r2_bucket, r2_public_url, episodes)
                st.success("Feed published! Add this URL in your podcast app (e.g. Pocket Casts → Discover → search icon → paste URL):")
                st.code(feed_url)

        st.divider()
        st.subheader("Delete an Episode")
        all_rows = get_all_episodes_for_feed()
        if all_rows:
            options = {
                f"{subj} › {chap} — Episode {episode_num}: {title}": ep_id
                for (ep_id, title, _url, _dur, _size, episode_num, subj, chap) in all_rows
            }
            choice = st.selectbox("Select episode", list(options.keys()), key="audio_delete_choice")
            if st.button("Delete Episode", type="secondary"):
                delete_audio_episode(options[choice])
                st.success("Deleted. Click **Publish Feed** above to update the podcast feed.")
                st.rerun()
        else:
            st.caption("No episodes to delete.")
