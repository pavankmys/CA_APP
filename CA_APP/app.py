import streamlit as st
import streamlit.components.v1 as components
import datetime
import json
import os
import time
from database import (
    update_srs_item, get_subject_list, get_due_mcqs,
    get_cpa_exam_availability, get_cpa_exam_mcqs, get_cpa_exam_simulations,
    save_cpa_exam, get_cpa_exam_history,
    get_analytics_data, DIFFICULTY_POINTS, CPA_EXAM_MIN_MCQS, CPA_EXAM_MIN_SIMS,
)

st.set_page_config(page_title="CA Inter SRS Practice App", layout="wide")

# ── Password gate ─────────────────────────────────────────────────────────────
def _check_password() -> bool:
    """Shows a password prompt and returns True once the correct password
    has been entered. If APP_PASSWORD is not configured, the gate is skipped."""
    if st.session_state.get("authenticated"):
        return True

    try:
        correct_password = st.secrets["APP_PASSWORD"]
    except Exception:
        correct_password = os.environ.get("APP_PASSWORD")

    if not correct_password:
        return True

    st.title("🔒 CA Inter Practice Engine")
    with st.form("login_form"):
        pwd = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary")
    if submitted:
        if pwd == correct_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password")
    return False


if not _check_password():
    st.stop()

# ── Session state init for mock test ─────────────────────────────────────────
for key, default in [
    ("mock_screen",      "setup"),
    ("mock_questions",   []),
    ("mock_idx",         0),
    ("mock_answers",     {}),
    ("mock_sims",        []),
    ("mock_sim_idx",     0),
    ("mock_sim_answers", {}),
    ("mock_start_time",  None),
    ("mock_result",      None),
    ("mock_subject",     "All Subjects"),
    ("deck_session_seconds", 0.0),
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
st.title("📚 CA Inter Practice Engine")
tab1, tab3, tab4 = st.tabs([
    "🎯 Daily Practice Deck",
    "📊 Analytics",
    "📝 CPA Simulation Exam"
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: PRACTICE DECK
# ─────────────────────────────────────────────────────────────────────────────
with tab1:
    st.header("Your Reviews for Today")

    all_subjects = get_subject_list()

    deck_mode = st.radio(
        "Mode", ["📘 Practice Mode", "📝 Exam Mode"],
        horizontal=True,
        help="Practice Mode: one attempt, explanation shown immediately, no live timer. "
             "Exam Mode: up to 2 attempts, live timer, explanation shown after attempts are used up."
    )
    is_practice_mode = deck_mode.startswith("📘")

    total_min = int(st.session_state.deck_session_seconds // 60)
    total_sec = int(st.session_state.deck_session_seconds % 60)
    st.caption(f"⏱ Total time spent this session: {total_min}m {total_sec}s")

    selected_subject = st.selectbox("Select Subject to Practice", ["All Subjects"] + all_subjects)
    test_mode = st.checkbox("🔄 Force Study Mode (Ignore SRS dates)", value=True)

    due_items = get_due_mcqs(selected_subject, test_mode)

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
            if not is_practice_mode:
                # Live timer (JavaScript, counts up from question-load epoch) — Exam Mode only
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

                if is_practice_mode:
                    # Single attempt — reveal the answer & explanation immediately
                    st.session_state.q1_correct  = is_correct
                    st.session_state.q1_answered = True
                    st.session_state.q1_rating   = _compute_srs_rating(
                        elapsed, st.session_state.q1_attempts, is_correct
                    )
                elif is_correct:
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

            if explanation:
                st.caption(f"**Explanation:** {explanation}")

            if st.button("Load Next →", use_container_width=True, type="primary"):
                update_srs_item(mcq_id, rating,
                                time_spent_seconds=int(elapsed),
                                attempts=attempts)
                st.session_state.deck_session_seconds += elapsed
                for k in ("q1_mcq_id", "q1_start_time", "q1_attempts", "q1_answered",
                          "q1_correct", "q1_rating", "q1_elapsed", "q1_last"):
                    st.session_state.pop(k, None)
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

    # ══ SECTION 4: CPA EXAM HISTORY ════════════════════════════════════════════
    exam_history = data.get('exam_history', [])
    if exam_history:
        st.divider()
        st.subheader("CPA Exam History")
        exam_rows = []
        for row in exam_history:
            date, subj, mcq_pct, sim_pct, overall_pct, proficient, mcq_total, sim_total = row
            verdict = "✅ Proficient" if proficient else "❌ Not Proficient"
            exam_rows.append({
                "Date":      date,
                "Subject":   subj,
                "MCQs":      mcq_total,
                "MCQ %":     f"{mcq_pct}%",
                "Sims":      sim_total,
                "Sim %":     f"{sim_pct}%",
                "Overall %": f"{overall_pct}%",
                "Result":    verdict,
            })
        st.table(exam_rows)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4: MOCK TEST
# ─────────────────────────────────────────────────────────────────────────────
with tab4:
    st.header("CPA Simulation Exam")
    st.caption(
        "MCQ section: Easy = 1pt | Medium = 2pts | Hard = 3pts (weighted %). "
        "Simulation section: simple correct/total %. "
        "Overall % = average of MCQ % and Simulation %. "
        f"Proficient at **75%+ overall** with minimum **{CPA_EXAM_MIN_MCQS} MCQs** "
        f"and **{CPA_EXAM_MIN_SIMS} simulations**."
    )

    screen = st.session_state.mock_screen

    # ── SETUP SCREEN ──────────────────────────────────────────────────────────
    if screen == "setup":
        subjects = get_subject_list()

        if not subjects:
            st.warning("No subjects found. Ingest some PDFs first.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                selected = st.selectbox("Select Subject", ["All Subjects"] + subjects)
            with col2:
                num_q = st.selectbox("Number of MCQs", [10, 25, 50, 75, 100], index=0)

            mock_mcq_count, n_chapters_with_sims, total_sims_avail = get_cpa_exam_availability(selected)

            st.info(
                f"**{mock_mcq_count}** mock MCQs and **{total_sims_avail}** simulations "
                f"across **{n_chapters_with_sims}** chapter(s) available for *{selected}*"
            )

            mcq_ok = mock_mcq_count >= CPA_EXAM_MIN_MCQS
            sim_ok = n_chapters_with_sims >= CPA_EXAM_MIN_SIMS

            if not mcq_ok or not sim_ok:
                if not mcq_ok:
                    st.error(
                        f"Need at least **{CPA_EXAM_MIN_MCQS} mock MCQs** for a valid exam. "
                        f"Currently **{mock_mcq_count}** available."
                    )
                if not sim_ok:
                    st.error(
                        f"Need simulations covering at least **{CPA_EXAM_MIN_SIMS} chapter(s)** for a valid exam. "
                        f"Currently **{n_chapters_with_sims}** chapter(s) covered."
                    )
                st.info("Generate more mock exam content in the Ingest app to build up the pool.")
            else:
                if mock_mcq_count < num_q:
                    st.warning(f"Only {mock_mcq_count} mock MCQs available — exam will use all of them.")

                if st.button("🚀 Start CPA Simulation Exam", type="primary", use_container_width=True):
                    questions = get_cpa_exam_mcqs(selected, num_q)
                    sims = get_cpa_exam_simulations(selected)
                    if not questions or not sims:
                        st.error("Could not load exam content.")
                    else:
                        st.session_state.mock_questions   = questions
                        st.session_state.mock_idx         = 0
                        st.session_state.mock_answers     = {}
                        st.session_state.mock_sims        = sims
                        st.session_state.mock_sim_idx     = 0
                        st.session_state.mock_sim_answers = {}
                        st.session_state.mock_start_time  = time.time()
                        st.session_state.mock_subject     = selected
                        st.session_state.mock_screen      = "testing"
                        st.rerun()

        # Recent history
        history = get_cpa_exam_history(limit=5)
        if history:
            st.divider()
            st.subheader("Recent CPA Exams")
            for row in history:
                _, subj, date, mcq_pct, sim_pct, overall_pct, proficient, mcq_total, sim_total, secs = row
                badge = "✅" if proficient else "❌"
                mins, secsR = divmod(int(secs or 0), 60)
                st.markdown(
                    f"{badge} **{date}** | {subj} | "
                    f"MCQ {mcq_pct}% ({mcq_total}Q) | Sim {sim_pct}% ({sim_total}Q) | "
                    f"Overall {overall_pct}% | {mins}m {secsR}s"
                )

    # ── TESTING SCREEN ────────────────────────────────────────────────────────
    elif screen == "testing":
        questions  = st.session_state.mock_questions
        idx        = st.session_state.mock_idx
        total_mcqs = len(questions)
        sims       = st.session_state.mock_sims
        sim_idx    = st.session_state.mock_sim_idx
        total_sims = len(sims)
        total_items = total_mcqs + total_sims

        elapsed = int(time.time() - (st.session_state.mock_start_time or time.time()))
        elapsed_str = f"{elapsed // 60}m {elapsed % 60}s"

        def _finalize_exam(mcq_done, sims_done):
            elapsed_final = int(time.time() - st.session_state.mock_start_time)
            result = save_cpa_exam(
                st.session_state.mock_subject,
                mcq_done,
                st.session_state.mock_answers,
                sims_done,
                st.session_state.mock_sim_answers,
                elapsed_final
            )

            mcq_per_question = []
            for qs in mcq_done:
                ua = st.session_state.mock_answers.get(qs['mcq_id'])
                mcq_per_question.append({**qs, 'user_answer': ua, 'is_correct': ua == qs['correct']})

            sim_review = []
            for sim in sims_done:
                sub_review = []
                for sq in sim['sub_questions']:
                    ua = st.session_state.mock_sim_answers.get(sq['id'])
                    sub_review.append({**sq, 'user_answer': ua, 'is_correct': ua == sq['correct']})
                sim_review.append({**sim, 'sub_questions': sub_review})

            st.session_state.mock_result = {
                **result,
                'elapsed': elapsed_final,
                'mcq_per_question': mcq_per_question,
                'sim_review': sim_review,
            }
            st.session_state.mock_screen = "results"
            st.rerun()

        # ── PHASE 1: MCQs ─────────────────────────────────────────────────────
        if idx < total_mcqs:
            q = questions[idx]

            st.progress(idx / total_items if total_items else 0)
            st.caption(f"MCQ {idx + 1} of {total_mcqs} | Elapsed: {elapsed_str}")

            diff_badge = {"easy": "🟢 Easy", "medium": "🟡 Medium", "hard": "🔴 Hard"}.get(
                q['difficulty'], "🔴 Hard"
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
                is_last_mcq = (idx == total_mcqs - 1)
                if not is_last_mcq:
                    btn_label, btn_type = "Next Question →", "secondary"
                elif total_sims > 0:
                    btn_label, btn_type = "Continue to Simulations →", "primary"
                else:
                    btn_label, btn_type = "Submit Test ✓", "primary"

                if st.button(btn_label, use_container_width=True, type=btn_type):
                    st.session_state.mock_answers[q['mcq_id']] = selected_letter

                    if not is_last_mcq:
                        st.session_state.mock_idx += 1
                        st.rerun()
                    elif total_sims > 0:
                        st.session_state.mock_idx += 1
                        st.rerun()
                    else:
                        _finalize_exam(questions, sims)

            with col2:
                if st.button("Submit Early", use_container_width=True):
                    st.session_state.mock_answers[q['mcq_id']] = selected_letter
                    _finalize_exam(questions[:idx + 1], [])

        # ── PHASE 2: SIMULATIONS ──────────────────────────────────────────────
        elif sim_idx < total_sims:
            sim = sims[sim_idx]

            st.progress((total_mcqs + sim_idx) / total_items if total_items else 0)
            st.caption(f"Simulation {sim_idx + 1} of {total_sims} | Elapsed: {elapsed_str}")

            st.markdown(f"**{sim['subject']} › {sim['chapter']}**")
            st.subheader(sim['title'])
            st.markdown(sim['scenario'])

            st.divider()
            sim_choices = {}
            for i, sq in enumerate(sim['sub_questions'], 1):
                st.markdown(f"**Sub-question {i}: {sq['question']}**")
                opts = sq['options']
                option_labels = [f"{k}) {v}" for k, v in sorted(opts.items())]
                choice = st.radio(
                    "Select your answer:", option_labels,
                    key=f"sim_radio_{sim_idx}_{sq['id']}"
                )
                sim_choices[sq['id']] = choice[0]
                st.markdown("")

            st.divider()
            col1, col2 = st.columns([3, 1])

            with col1:
                is_last_sim = (sim_idx == total_sims - 1)
                btn_label = "Submit Test ✓" if is_last_sim else "Submit & Next Simulation →"

                if st.button(btn_label, use_container_width=True, type="primary"):
                    st.session_state.mock_sim_answers.update(sim_choices)

                    if not is_last_sim:
                        st.session_state.mock_sim_idx += 1
                        st.rerun()
                    else:
                        _finalize_exam(questions, sims)

            with col2:
                if st.button("Submit Early", use_container_width=True):
                    st.session_state.mock_sim_answers.update(sim_choices)
                    _finalize_exam(questions, sims[:sim_idx + 1])

    # ── RESULTS SCREEN ────────────────────────────────────────────────────────
    elif screen == "results":
        result      = st.session_state.mock_result
        mcq_pct     = result['mcq_pct']
        sim_pct     = result['sim_pct']
        overall_pct = result['overall_pct']
        proficient  = result['is_proficient']
        elapsed     = result['elapsed']
        mcq_per_q   = result['mcq_per_question']
        sim_review  = result['sim_review']

        mins, secs = divmod(elapsed, 60)

        too_few_mcqs = result['mcq_total'] < CPA_EXAM_MIN_MCQS
        too_few_sims = len(sim_review) < CPA_EXAM_MIN_SIMS

        if proficient:
            st.success(f"## ✅ PROFICIENT — {overall_pct}% overall (≥75% threshold met)")
        elif too_few_mcqs or too_few_sims:
            reasons = []
            if too_few_mcqs:
                reasons.append(f"only {result['mcq_total']} MCQs (minimum {CPA_EXAM_MIN_MCQS})")
            if too_few_sims:
                reasons.append(f"only {len(sim_review)} simulations (minimum {CPA_EXAM_MIN_SIMS})")
            st.warning(
                f"## ⚠️ Score: {overall_pct}% — Proficiency not assessed\n\n"
                f"This exam had {' and '.join(reasons)}. "
                "Complete a full exam to receive a proficiency verdict."
            )
        else:
            st.error(f"## ❌ Not Yet Proficient — {overall_pct}% overall (need 75%)")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("MCQ Score", f"{mcq_pct}%")
        m2.metric("Simulation Score", f"{sim_pct}%")
        m3.metric("Overall Score", f"{overall_pct}%")
        m4.metric("Time Taken", f"{mins}m {secs}s")

        # MCQ difficulty breakdown
        st.divider()
        st.subheader("MCQ Section — Breakdown by Difficulty")

        breakdown = {}
        for q in mcq_per_q:
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

        # Simulation breakdown
        if sim_review:
            st.divider()
            st.subheader("Simulation Section — Breakdown by Case")
            sim_table_rows = []
            for sim in sim_review:
                subqs = sim['sub_questions']
                correct_n = sum(1 for sq in subqs if sq['is_correct'])
                acc = round(correct_n / len(subqs) * 100) if subqs else 0
                sim_table_rows.append({
                    "Simulation": sim['title'],
                    "Chapter":    f"{sim['subject']} › {sim['chapter']}",
                    "Sub-Qs":     len(subqs),
                    "Correct":    correct_n,
                    "Accuracy":   f"{acc}%",
                })
            st.table(sim_table_rows)

        # Review
        st.divider()
        with st.expander("📋 Review All Answers"):
            st.markdown("### MCQ Section")
            for i, q in enumerate(mcq_per_q, 1):
                ua  = q['user_answer'] or "—"
                correct_ans = q['correct']
                diff_label = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(q['difficulty'], "🔴")

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

            if sim_review:
                st.markdown("### Simulation Section")
                for s_i, sim in enumerate(sim_review, 1):
                    st.markdown(f"#### {s_i}. {sim['title']} ({sim['subject']} › {sim['chapter']})")
                    st.markdown(sim['scenario'])
                    for sq_i, sq in enumerate(sim['sub_questions'], 1):
                        ua = sq['user_answer'] or "—"
                        correct_ans = sq['correct']
                        st.markdown(f"**{s_i}.{sq_i}. {sq['question']}**")
                        opts = sq['options']
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
                        st.caption(f"**Explanation:** {sq['explanation']}")
                    st.divider()

        if st.button("🔁 Start New Test", type="primary"):
            st.session_state.mock_screen = "setup"
            st.session_state.mock_result = None
            st.rerun()
