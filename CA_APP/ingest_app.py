import streamlit as st
import os
import time
from dotenv import load_dotenv
from database import (
    save_generated_mcqs, get_question_bank_summary,
    delete_chapter_mcqs, delete_subject_mcqs, remove_duplicate_mcqs,
    save_generated_mock_mcqs, save_generated_simulations,
    get_mock_bank_summary, remove_duplicate_mock_mcqs,
)
from parser import extract_text_chunks, generate_from_chunk, generate_mock_mcqs_from_chunk, generate_simulations_from_chunk

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
    "Generates a separate high-difficulty MCQ pool plus CPA-style simulations "
    "(case-study scenario + 4-6 MCQ sub-questions) used by the CPA Simulation Exam. "
    "Stored separately from the practice question bank."
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
