import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import datetime
import json
import os
import time
from dotenv import load_dotenv
from database import (
    init_db, save_generated_mcqs, update_srs_item, DB_NAME,
    get_mock_test_questions, save_mock_test, get_mock_test_history,
    get_analytics_data, DIFFICULTY_POINTS, MOCK_TEST_MIN_QUESTIONS,
    get_question_bank_summary, delete_chapter_mcqs, delete_subject_mcqs,
    remove_duplicate_mcqs,
)
from parser import process_pdf_and_generate, extract_text_chunks, generate_from_chunk

load_dotenv()

st.set_page_config(page_title="CA Inter SRS Practice App", layout="wide")
init_db()

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

# ── Session state init for mock test ─────────────────────────────────────────
for key, default in [
    ("mock_screen",     "setup"),
    ("mock_questions",  []),
    ("mock_idx",        0),
    ("mock_answers",    {}),
    ("mock_start_time", None),
    ("mock_result",     None),
    ("mock_subject",    "All Subjects"),
    ("delete_confirm",  None),   # (subject, chapter) or ("subject", subject) pending confirmation
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── SRS auto-rating ──────────────────────────────────────────────────────────
_RATING_META = {
    5: ("🌟 Perfect recall",          "First attempt, within 10 seconds"),
    4: ("✅ Correct with hesitation",  "First attempt but took time, or correct on 2nd attempt within 30 s"),
    3: ("🔄 Correct with effort",      "Multiple attempts or took longer than 1 minute"),
    1: ("❌ Wrong answer",             "Did not recall correctly — scheduled for early review"),
}

def _compute_srs_rating(elapsed: float, attempts: int, is_correct: bool) -> int:
    if not is_correct:
        return 1
    if attempts == 1 and elapsed <= 10:
        return 5
    if attempts == 2 and elapsed <= 30:
        return 4
    if attempts >= 2 or elapsed > 60:
        return 3
    return 4  # correct first attempt, 11-60 s

# ── Tabs ──────────────────────────────────────────────────────────────────────
st.title("📚 CA Inter Local Practice Engine")
tab1, tab2, tab3, tab4 = st.tabs([
    "🎯 Daily Practice Deck",
    "📥 Ingest Study Material",
    "📊 Local Analytics",
    "📝 Mock Test"
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: PRACTICE DECK
# ─────────────────────────────────────────────────────────────────────────────
with tab1:
    st.header("Your Reviews for Today")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM subjects")
    all_subjects = [r[0] for r in cursor.fetchall()]
    conn.close()

    selected_subject = st.selectbox("Select Subject to Practice", ["All Subjects"] + all_subjects)
    test_mode = st.checkbox("🔄 Force Study Mode (Ignore SRS dates)", value=True)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    query = '''
        SELECT m.id, m.question, m.options, m.correct_option, m.explanation, c.name, sub.name
        FROM mcqs m
        JOIN srs_states s ON m.id = s.mcq_id
        JOIN chapters c ON m.chapter_id = c.id
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE m.is_retired = 0
    '''
    params = []
    if selected_subject != "All Subjects":
        query += " AND sub.name = ?"
        params.append(selected_subject)

    if test_mode:
        query += " ORDER BY s.repetitions ASC, s.next_review ASC"
    else:
        query += " AND s.next_review <= ? ORDER BY s.next_review ASC"
        params.append(datetime.date.today().isoformat())

    cursor.execute(query, params)
    due_items = cursor.fetchall()
    conn.close()

    if not due_items:
        st.success("🎉 No active cards found for this selection.")
    else:
        current = due_items[0]
        mcq_id, q_text, options_json, correct, explanation, ch_name, sub_name = current

        # ── Per-question state (resets whenever the question changes) ────────────
        if st.session_state.get("q1_mcq_id") != mcq_id:
            st.session_state.q1_mcq_id     = mcq_id
            st.session_state.q1_start_time = time.time()
            st.session_state.q1_attempts   = 0
            st.session_state.q1_answered   = False
            st.session_state.q1_correct    = False
            st.session_state.q1_rating     = None
            st.session_state.q1_elapsed    = 0.0
            st.session_state.q1_last       = None  # letter of last submitted choice

        st.markdown(f"**Subject:** {sub_name} | **Chapter:** {ch_name}")
        st.subheader(f"Question: {q_text}")

        try:
            opts = json.loads(options_json)
            option_labels = [f"A) {opts.get('A','')}", f"B) {opts.get('B','')}",
                             f"C) {opts.get('C','')}", f"D) {opts.get('D','')}"]
        except Exception:
            option_labels = ["A) [Error]", "B) [Error]", "C) [Error]", "D) [Error]"]

        # ── ANSWERING PHASE ───────────────────────────────────────────────────
        if not st.session_state.q1_answered:
            # Live timer (JavaScript, counts up from question-load epoch)
            epoch_ms = int(st.session_state.q1_start_time * 1000)
            components.html(
                f"""<script>
                var s={epoch_ms};
                function tick(){{
                    var el=document.getElementById('t');
                    if(el){{el.textContent='⏱ '+Math.floor((Date.now()-s)/1000)+'s elapsed';}}
                    setTimeout(tick,500);
                }}
                tick();
                </script>
                <div id="t" style="font-size:13px;color:#888;font-family:monospace;">⏱ 0s elapsed</div>""",
                height=28, scrolling=False
            )

            # Show "try again" warning after a wrong first attempt
            if st.session_state.q1_attempts == 1:
                remaining = 2 - st.session_state.q1_attempts
                st.warning(
                    f"❌ Attempt 1 was incorrect — **try again** "
                    f"({remaining} attempt remaining)"
                )

            user_choice_label = st.radio(
                "Choose the correct alternative:", option_labels,
                index=0, key=f"radio_{mcq_id}"
            )
            user_choice_letter = user_choice_label[0]

            if st.button("Submit Answer", use_container_width=True, type="primary"):
                elapsed = time.time() - st.session_state.q1_start_time
                st.session_state.q1_attempts += 1
                st.session_state.q1_last     = user_choice_letter
                st.session_state.q1_elapsed  = elapsed
                is_correct = (user_choice_letter == correct)

                if is_correct:
                    st.session_state.q1_correct  = True
                    st.session_state.q1_answered = True
                    st.session_state.q1_rating   = _compute_srs_rating(
                        elapsed, st.session_state.q1_attempts, True
                    )
                elif st.session_state.q1_attempts >= 2:
                    # Max attempts exhausted — mark answered, rating = 1
                    st.session_state.q1_correct  = False
                    st.session_state.q1_answered = True
                    st.session_state.q1_rating   = 1
                # else: first wrong attempt — just rerun to show "try again"
                st.rerun()

        # ── RESULT PHASE ──────────────────────────────────────────────────────
        else:
            elapsed  = st.session_state.q1_elapsed
            attempts = st.session_state.q1_attempts
            last     = st.session_state.q1_last
            rating   = st.session_state.q1_rating

            # Annotated options
            for lbl in option_labels:
                letter = lbl[0]
                if letter == correct and letter == last:
                    st.markdown(f"**✅ {lbl}** ← your answer (correct)")
                elif letter == correct:
                    st.markdown(f"**✅ {lbl}** ← correct answer")
                elif letter == last:
                    st.markdown(f"**❌ {lbl}** ← your answer")
                else:
                    st.markdown(f"{lbl}")

            if st.session_state.q1_correct:
                st.success(f"✅ Correct! — attempt {attempts}, {int(elapsed)}s")
            else:
                st.error(
                    f"❌ Incorrect after {attempts} attempt(s). "
                    f"Correct answer: **{correct}**"
                )

            st.markdown(f"**Explanation:** {explanation}")

            # Show auto-computed SRS rating (read-only)
            label, desc = _RATING_META.get(rating, ("📊 Recorded", ""))
            st.info(
                f"**Auto SRS rating:** {label}  \n"
                f"{desc} · {int(elapsed)}s · {attempts} attempt(s)"
            )

            if st.button("Load Next →", use_container_width=True, type="primary"):
                update_srs_item(mcq_id, rating,
                                time_spent_seconds=int(elapsed),
                                attempts=attempts)
                for k in ("q1_mcq_id", "q1_start_time", "q1_attempts", "q1_answered",
                          "q1_correct", "q1_rating", "q1_elapsed", "q1_last"):
                    st.session_state.pop(k, None)
                st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: INGESTION
# ─────────────────────────────────────────────────────────────────────────────
with tab2:
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

    # ── Question Bank Management ──────────────────────────────────────────────
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

            # ── Deduplication ─────────────────────────────────────────────────
            st.subheader("Remove Duplicates")
            if st.button("Scan & Remove Duplicate Questions", use_container_width=True):
                removed = remove_duplicate_mcqs()
                if removed:
                    st.success(f"Removed {removed} duplicate question(s).")
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
                st.warning(f"Are you sure? This will permanently delete {del_label}. This cannot be undone.")
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

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3: ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────
with tab3:
    st.header("Progress Analytics")

    data = get_analytics_data()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _confidence_score(avg_ease, total_rev, correct_rev):
        """0-100 blended: 60% ease-factor mastery + 40% answer accuracy."""
        if not total_rev:
            return 0.0
        acc  = (correct_rev / total_rev) * 100.0
        ease = max(0.0, min(100.0, ((avg_ease or 2.5) - 1.3) / 2.7 * 100.0))
        return round(ease * 0.6 + acc * 0.4, 1)

    def _conf_label(score, total_rev):
        if total_rev == 0:  return "— Not started"
        if score < 30:      return "🔴 Needs Work"
        if score < 60:      return "🟡 Learning"
        if score < 80:      return "🟢 Good"
        return "🌟 Strong"

    # ══ SECTION 1: STUDY TIME ═════════════════════════════════════════════════
    st.subheader("Study Time")
    st_row  = data.get('study_time') or (0, 0, 0)
    total_h = st_row[0] or 0.0
    week_h  = st_row[1] or 0.0
    avg_min = st_row[2] or 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Hours Studied", f"{total_h} h")
    c2.metric("This Week",           f"{week_h} h")
    c3.metric("Avg per Question",    f"{avg_min} min")
    if total_h == 0:
        st.caption("Study time accumulates as you practice in the Daily Practice tab.")

    # ══ SECTION 2: OVERALL READINESS SCORE ═══════════════════════════════════
    st.divider()
    st.subheader("Overall Readiness Score")
    st.caption(
        "Difficulty-weighted score: Hard correct = 3 pts, Medium = 2 pts, Easy = 1 pt. "
        "Reflects the last 7 days of practice."
    )

    cur_score   = data.get('trending_current', 0.0)
    prev_score  = data.get('trending_prev',    0.0)
    daily_trend = data.get('trending_daily',   [])

    if cur_score == 0 and not daily_trend:
        st.info("No practice data yet — answer questions in the Daily Practice tab to build your score.")
    else:
        delta_val = round(cur_score - prev_score, 1)
        delta_str = f"{delta_val:+.1f}% vs previous week" if prev_score > 0 else None

        col_score, col_bar = st.columns([1, 2])
        with col_score:
            st.metric("Current Score", f"{cur_score:.0f}%", delta=delta_str)
            if cur_score >= 75:
                st.success("🌟 Ready for mock test")
            elif cur_score >= 60:
                st.info("✅ On track — keep going")
            elif cur_score >= 40:
                st.warning("⚠️ Needs improvement")
            else:
                st.error("🔴 Focus required")

        with col_bar:
            st.markdown(f"**Readiness: {cur_score:.0f}%** &nbsp;|&nbsp; Target: 75%")
            st.progress(min(cur_score / 100.0, 1.0))
            if daily_trend:
                recent = daily_trend[-14:]
                bars = ""
                for date_str, score in recent:
                    h     = max(4, int((score or 0) / 100 * 52))
                    color = ("#4CAF50" if (score or 0) >= 75
                             else "#FF9800" if (score or 0) >= 50
                             else "#ef5350")
                    day   = date_str[5:]
                    bars += (
                        f'<div style="display:inline-flex;flex-direction:column;'
                        f'align-items:center;margin:0 3px;" title="{date_str}: {score}%">'
                        f'<div style="width:16px;height:{h}px;background:{color};'
                        f'border-radius:2px 2px 0 0;"></div>'
                        f'<div style="font-size:9px;color:#aaa;writing-mode:vertical-lr;'
                        f'transform:rotate(180deg);margin-top:2px;">{day}</div></div>'
                    )
                components.html(
                    f'<div style="display:flex;align-items:flex-end;padding:4px 0 0 0;">{bars}</div>',
                    height=78, scrolling=False
                )
                st.caption("Last 14 practice days  |  green >= 75%  |  orange >= 50%  |  red < 50%")

    # ══ SECTION 3: CHAPTER PERFORMANCE & CONFIDENCE ══════════════════════════
    st.divider()
    st.subheader("Chapter Performance & Confidence")
    st.caption(
        "Confidence = 60% ease-factor mastery + 40% answer accuracy. "
        "Aim for Good (>=60%) before sitting a mock test. Weakest chapters shown first."
    )

    chapter_perf = data.get('chapter_perf', [])
    if not chapter_perf:
        st.info("No chapters found. Ingest study materials first.")
    else:
        ch_rows = []
        for row in chapter_perf:
            subject, chapter, total_q, avg_ease, total_rev, correct_rev, due_cnt, time_sec = row
            conf    = _confidence_score(avg_ease or 2.5, total_rev, correct_rev)
            acc     = round((correct_rev / total_rev * 100), 1) if total_rev else 0.0
            hours_c = round((time_sec or 0) / 3600.0, 1)
            ch_rows.append({
                "Subject":    subject,
                "Chapter":    chapter,
                "MCQs":       total_q,
                "Reviews":    total_rev,
                "Accuracy":   f"{acc}%",
                "Confidence": f"{conf}%",
                "Status":     _conf_label(conf, total_rev),
                "Time":       f"{hours_c}h",
                "Due":        due_cnt or 0,
            })
        ch_rows.sort(key=lambda r: float(r["Confidence"].rstrip("%")))
        st.table(ch_rows)

    # ══ SECTION 4: MOCK TEST HISTORY (unchanged) ══════════════════════════════
    mock_history = data.get('mock_history', [])
    if mock_history:
        st.divider()
        st.subheader("Mock Test History")
        mock_rows = []
        for row in mock_history:
            date, subj, pct, proficient, total_qm, earned, max_s = row
            verdict = "✅ Proficient" if proficient else "❌ Not Proficient"
            mock_rows.append({
                "Date":       date,
                "Subject":    subj,
                "Questions":  total_qm,
                "Score":      f"{earned}/{max_s}",
                "Percentage": f"{pct}%",
                "Result":     verdict,
            })
        st.table(mock_rows)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4: MOCK TEST
# ─────────────────────────────────────────────────────────────────────────────
with tab4:
    st.header("Mock Test — CPA-Style Weighted Scoring")
    st.caption(
        f"Easy = 1pt | Medium = 2pts | Hard = 3pts | "
        f"Proficient at **75%+** with minimum **{MOCK_TEST_MIN_QUESTIONS} questions**"
    )

    screen = st.session_state.mock_screen

    # ── SETUP SCREEN ──────────────────────────────────────────────────────────
    if screen == "setup":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM subjects")
        subjects = [r[0] for r in cursor.fetchall()]
        conn.close()

        if not subjects:
            st.warning("No subjects found. Ingest some PDFs first.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                selected = st.selectbox("Select Subject", ["All Subjects"] + subjects)
            with col2:
                num_q = st.selectbox("Number of Questions", [50, 75, 100], index=0)

            # Count available questions
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            count_query = "SELECT COUNT(*) FROM mcqs m JOIN chapters c ON m.chapter_id=c.id JOIN subjects sub ON c.subject_id=sub.id WHERE m.is_retired=0"
            count_params = []
            if selected != "All Subjects":
                count_query += " AND sub.name = ?"
                count_params.append(selected)
            available = cursor.execute(count_query, count_params).fetchone()[0]
            conn.close()

            st.info(f"**{available}** questions available for *{selected}*")

            if available < MOCK_TEST_MIN_QUESTIONS:
                st.error(
                    f"Need at least **{MOCK_TEST_MIN_QUESTIONS} questions** for a valid mock test. "
                    f"Currently **{available}** available. "
                    f"Ingest more PDFs in the Ingest tab to build up your question bank."
                )
            else:
                if available < num_q:
                    st.warning(f"Only {available} questions available — test will use all of them.")

                if st.button("🚀 Start Mock Test", type="primary", use_container_width=True):
                    questions = get_mock_test_questions(selected, num_q)
                    if not questions:
                        st.error("Could not load questions.")
                    else:
                        st.session_state.mock_questions  = questions
                        st.session_state.mock_idx        = 0
                        st.session_state.mock_answers    = {}
                        st.session_state.mock_start_time = time.time()
                        st.session_state.mock_subject    = selected
                        st.session_state.mock_screen     = "testing"
                        st.rerun()

        # Recent history
        history = get_mock_test_history(limit=5)
        if history:
            st.divider()
            st.subheader("Recent Mock Tests")
            for row in history:
                _, subj, date, total_q, earned, max_s, pct, proficient, secs = row
                badge = "✅" if proficient else "❌"
                mins, secsR = divmod(int(secs or 0), 60)
                st.markdown(
                    f"{badge} **{date}** | {subj} | {total_q}Q | "
                    f"Score: {earned}/{max_s} ({pct}%) | {mins}m {secsR}s"
                )

    # ── TESTING SCREEN ────────────────────────────────────────────────────────
    elif screen == "testing":
        questions = st.session_state.mock_questions
        idx       = st.session_state.mock_idx
        total     = len(questions)
        q         = questions[idx]

        elapsed = int(time.time() - (st.session_state.mock_start_time or time.time()))
        elapsed_str = f"{elapsed // 60}m {elapsed % 60}s"

        st.progress((idx) / total)
        st.caption(f"Question {idx + 1} of {total} | Elapsed: {elapsed_str}")

        diff_badge = {"easy": "🟢 Easy", "medium": "🟡 Medium", "hard": "🔴 Hard"}.get(
            q['difficulty'], "🟡 Medium"
        )
        st.markdown(f"**{q['subject']} › {q['chapter']}** | {diff_badge}")
        st.subheader(f"Q{idx + 1}. {q['question']}")

        opts = q['options']
        option_labels = [f"{k}) {v}" for k, v in sorted(opts.items())]
        choice = st.radio("Select your answer:", option_labels, key=f"mock_radio_{idx}")
        selected_letter = choice[0]

        st.divider()
        col1, col2 = st.columns([3, 1])

        with col1:
            btn_label = "Next Question →" if idx < total - 1 else "Submit Test ✓"
            btn_type  = "secondary" if idx < total - 1 else "primary"

            if st.button(btn_label, use_container_width=True, type=btn_type):
                st.session_state.mock_answers[q['mcq_id']] = selected_letter

                if idx < total - 1:
                    st.session_state.mock_idx += 1
                    st.rerun()
                else:
                    # Last question — finalize
                    elapsed_final = int(time.time() - st.session_state.mock_start_time)
                    earned, max_score, pct, is_proficient = save_mock_test(
                        st.session_state.mock_subject,
                        questions,
                        st.session_state.mock_answers,
                        elapsed_final
                    )
                    per_question = []
                    for qs in questions:
                        ua = st.session_state.mock_answers.get(qs['mcq_id'])
                        per_question.append({**qs, 'user_answer': ua, 'is_correct': ua == qs['correct']})

                    st.session_state.mock_result = {
                        'earned': earned, 'max_score': max_score, 'pct': pct,
                        'is_proficient': is_proficient, 'elapsed': elapsed_final,
                        'per_question': per_question
                    }
                    st.session_state.mock_screen = "results"
                    st.rerun()

        with col2:
            if st.button("Submit Early", use_container_width=True):
                st.session_state.mock_answers[q['mcq_id']] = selected_letter
                elapsed_final = int(time.time() - st.session_state.mock_start_time)
                questions_done = questions[:idx + 1]
                earned, max_score, pct, is_proficient = save_mock_test(
                    st.session_state.mock_subject,
                    questions_done,
                    st.session_state.mock_answers,
                    elapsed_final
                )
                per_question = []
                for qs in questions_done:
                    ua = st.session_state.mock_answers.get(qs['mcq_id'])
                    per_question.append({**qs, 'user_answer': ua, 'is_correct': ua == qs['correct']})

                st.session_state.mock_result = {
                    'earned': earned, 'max_score': max_score, 'pct': pct,
                    'is_proficient': is_proficient, 'elapsed': elapsed_final,
                    'per_question': per_question
                }
                st.session_state.mock_screen = "results"
                st.rerun()

    # ── RESULTS SCREEN ────────────────────────────────────────────────────────
    elif screen == "results":
        result = st.session_state.mock_result
        earned      = result['earned']
        max_score   = result['max_score']
        pct         = result['pct']
        proficient  = result['is_proficient']
        elapsed     = result['elapsed']
        per_q       = result['per_question']

        mins, secs = divmod(elapsed, 60)

        too_small = len(per_q) < MOCK_TEST_MIN_QUESTIONS
        if proficient:
            st.success(f"## ✅ PROFICIENT — {pct}% (≥75% threshold met)")
        elif too_small:
            st.warning(
                f"## ⚠️ Score: {pct}% — Proficiency not assessed\n\n"
                f"This test had only **{len(per_q)} questions** (minimum {MOCK_TEST_MIN_QUESTIONS} required). "
                f"Complete a full {MOCK_TEST_MIN_QUESTIONS}-question mock test to receive a proficiency verdict."
            )
        else:
            st.error(f"## ❌ Not Yet Proficient — {pct}% (need 75%)")

        m1, m2, m3 = st.columns(3)
        m1.metric("Weighted Score", f"{earned} / {max_score}")
        m2.metric("Percentage", f"{pct}%")
        m3.metric("Time Taken", f"{mins}m {secs}s")

        # Difficulty breakdown
        st.divider()
        st.subheader("Breakdown by Difficulty")

        breakdown = {}
        for q in per_q:
            d = q['difficulty']
            if d not in breakdown:
                breakdown[d] = {'total': 0, 'correct': 0, 'pts_earned': 0, 'pts_max': 0}
            pts = DIFFICULTY_POINTS.get(d, 2)
            breakdown[d]['total'] += 1
            breakdown[d]['pts_max'] += pts
            if q['is_correct']:
                breakdown[d]['correct'] += 1
                breakdown[d]['pts_earned'] += pts

        table_rows = []
        for diff in ['easy', 'medium', 'hard']:
            if diff in breakdown:
                b = breakdown[diff]
                label = {"easy": "🟢 Easy (1pt)", "medium": "🟡 Medium (2pts)", "hard": "🔴 Hard (3pts)"}[diff]
                acc = round(b['correct'] / b['total'] * 100) if b['total'] > 0 else 0
                table_rows.append({
                    "Difficulty":  label,
                    "Questions":   b['total'],
                    "Correct":     b['correct'],
                    "Accuracy":    f"{acc}%",
                    "Points":      f"{b['pts_earned']} / {b['pts_max']}"
                })

        if table_rows:
            st.table(table_rows)

        # Per-question review
        st.divider()
        with st.expander("📋 Review All Answers"):
            for i, q in enumerate(per_q, 1):
                ua  = q['user_answer'] or "—"
                correct_ans = q['correct']
                icon = "✅" if q['is_correct'] else "❌"
                diff_label = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(q['difficulty'], "🟡")

                st.markdown(f"**{i}. {diff_label} {q['question']}**")
                opts = q['options']
                for letter in ['A', 'B', 'C', 'D']:
                    val = opts.get(letter, '')
                    if letter == correct_ans and letter == ua:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;**✅ {letter}) {val}** ← your answer (correct)")
                    elif letter == correct_ans:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;**✅ {letter}) {val}** ← correct answer")
                    elif letter == ua:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;**❌ {letter}) {val}** ← your answer")
                    else:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;{letter}) {val}")
                st.caption(f"**Explanation:** {q['explanation']}")
                st.divider()

        if st.button("🔁 Start New Test", type="primary"):
            st.session_state.mock_screen = "setup"
            st.session_state.mock_result = None
            st.rerun()
