import os
import datetime
import itertools
import json
import random
import re

import psycopg2
import psycopg2.extensions
from dotenv import load_dotenv

import icai_syllabus

load_dotenv()

# Postgres NUMERIC columns (ease_factor, *_pct, time_spent_seconds, etc.) are
# returned as decimal.Decimal by psycopg2, which cannot be mixed with floats
# in arithmetic (e.g. `decimal.Decimal - float`). Cast them to float globally
# so callers can treat all numeric results as plain floats.
_DEC2FLOAT = psycopg2.extensions.new_type(
    psycopg2.extensions.DECIMAL.values,
    'DEC2FLOAT',
    lambda value, curs: float(value) if value is not None else None,
)
psycopg2.extensions.register_type(_DEC2FLOAT)

DIFFICULTY_POINTS = {'easy': 1, 'medium': 2, 'hard': 3}
CPA_EXAM_MIN_MCQS = 10  # Minimum MCQs for a valid proficiency assessment
CPA_EXAM_MIN_SIMS = 1   # Minimum simulations for a valid proficiency assessment


def _norm_q(text: str) -> str:
    """Normalised question key used for duplicate detection."""
    return re.sub(r'\s+', ' ', (text or "").lower().strip())


def _get_db_url():
    try:
        import streamlit as st
        if "SUPABASE_DB_URL" in st.secrets:
            return st.secrets["SUPABASE_DB_URL"]
    except Exception:
        pass
    return os.environ["SUPABASE_DB_URL"]


_conn = None


def _get_conn():
    global _conn
    if _conn is not None:
        try:
            with _conn.cursor() as cur:
                cur.execute("SELECT 1")
            return _conn
        except psycopg2.Error:
            _conn = None
    _conn = psycopg2.connect(_get_db_url())
    _conn.autocommit = True
    return _conn


def save_generated_mcqs(subject_name, chapter_name, mcq_list):
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute("INSERT INTO subjects (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (subject_name,))
    cursor.execute("SELECT id FROM subjects WHERE name = %s", (subject_name,))
    subject_id = cursor.fetchone()[0]

    cursor.execute(
        "INSERT INTO chapters (subject_id, name) VALUES (%s, %s) ON CONFLICT (subject_id, name) DO NOTHING",
        (subject_id, chapter_name)
    )
    cursor.execute("SELECT id FROM chapters WHERE subject_id = %s AND name = %s", (subject_id, chapter_name))
    chapter_id = cursor.fetchone()[0]

    # Load existing normalised question texts for this chapter to skip duplicates
    cursor.execute("SELECT question FROM mcqs WHERE chapter_id = %s", (chapter_id,))
    existing_keys = {_norm_q(r[0]) for r in cursor.fetchall()}

    added = 0
    for item in mcq_list:
        nk = _norm_q(item['question'])
        if nk in existing_keys:
            continue  # duplicate — skip
        existing_keys.add(nk)

        # Shuffle option letters so the correct answer is not always in the same position
        letters = ['A', 'B', 'C', 'D']
        texts = [item['option_A'], item['option_B'], item['option_C'], item['option_D']]
        original_correct_text = item[f"option_{item['correct_option']}"]
        random.shuffle(texts)
        options_dict = dict(zip(letters, texts))
        correct_option = next(k for k, v in options_dict.items() if v == original_correct_text)
        difficulty = item.get('difficulty', 'medium')

        cursor.execute('''
            INSERT INTO mcqs (chapter_id, question, options, correct_option, explanation, difficulty)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (chapter_id, item['question'], json.dumps(options_dict),
              correct_option, item['explanation'], difficulty))

        mcq_id = cursor.fetchone()[0]
        cursor.execute('INSERT INTO srs_states (mcq_id, next_review) VALUES (%s, %s)',
                        (mcq_id, datetime.date.today()))
        added += 1

    conn.commit()
    cursor.close()
    return added


def save_generated_mock_mcqs(subject_name, chapter_name, mcq_list):
    """Mirrors save_generated_mcqs but inserts into the separate mock_mcqs pool (no SRS state)."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute("INSERT INTO subjects (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (subject_name,))
    cursor.execute("SELECT id FROM subjects WHERE name = %s", (subject_name,))
    subject_id = cursor.fetchone()[0]

    cursor.execute(
        "INSERT INTO chapters (subject_id, name) VALUES (%s, %s) ON CONFLICT (subject_id, name) DO NOTHING",
        (subject_id, chapter_name)
    )
    cursor.execute("SELECT id FROM chapters WHERE subject_id = %s AND name = %s", (subject_id, chapter_name))
    chapter_id = cursor.fetchone()[0]

    # Dedup against both the practice bank and the existing mock pool for this chapter
    cursor.execute("SELECT question FROM mcqs WHERE chapter_id = %s", (chapter_id,))
    existing_keys = {_norm_q(r[0]) for r in cursor.fetchall()}
    cursor.execute("SELECT question FROM mock_mcqs WHERE chapter_id = %s", (chapter_id,))
    existing_keys |= {_norm_q(r[0]) for r in cursor.fetchall()}

    added = 0
    for item in mcq_list:
        nk = _norm_q(item['question'])
        if nk in existing_keys:
            continue  # duplicate — skip
        existing_keys.add(nk)

        # Shuffle option letters so the correct answer is not always in the same position
        letters = ['A', 'B', 'C', 'D']
        texts = [item['option_A'], item['option_B'], item['option_C'], item['option_D']]
        original_correct_text = item[f"option_{item['correct_option']}"]
        random.shuffle(texts)
        options_dict = dict(zip(letters, texts))
        correct_option = next(k for k, v in options_dict.items() if v == original_correct_text)
        difficulty = item.get('difficulty', 'hard')

        cursor.execute('''
            INSERT INTO mock_mcqs (chapter_id, question, options, correct_option, explanation, difficulty)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (chapter_id, item['question'], json.dumps(options_dict),
              correct_option, item['explanation'], difficulty))
        added += 1

    conn.commit()
    cursor.close()
    return added


def save_generated_simulations(subject_name, chapter_name, sim_list):
    """Inserts simulations and their numeric/dropdown/journal-entry items into simulations / simulation_questions."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute("INSERT INTO subjects (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (subject_name,))
    cursor.execute("SELECT id FROM subjects WHERE name = %s", (subject_name,))
    subject_id = cursor.fetchone()[0]

    cursor.execute(
        "INSERT INTO chapters (subject_id, name) VALUES (%s, %s) ON CONFLICT (subject_id, name) DO NOTHING",
        (subject_id, chapter_name)
    )
    cursor.execute("SELECT id FROM chapters WHERE subject_id = %s AND name = %s", (subject_id, chapter_name))
    chapter_id = cursor.fetchone()[0]

    cursor.execute("SELECT title FROM simulations WHERE chapter_id = %s", (chapter_id,))
    existing_titles = {_norm_q(r[0]) for r in cursor.fetchall()}

    added = 0
    for sim in sim_list:
        nk = _norm_q(sim['title'])
        if nk in existing_titles:
            continue  # duplicate — skip
        existing_titles.add(nk)

        cursor.execute('''
            INSERT INTO simulations (chapter_id, title, scenario, difficulty)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        ''', (chapter_id, sim['title'], sim['scenario'], 'hard'))
        sim_id = cursor.fetchone()[0]

        seq_no = 1

        for item in sim['numeric_items']:
            payload = {'unit': item['unit'], 'correct_value': item['correct_value']}
            cursor.execute('''
                INSERT INTO simulation_questions
                    (simulation_id, seq_no, item_type, question, payload, explanation)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (sim_id, seq_no, 'numeric', item['question'], json.dumps(payload), item['explanation']))
            seq_no += 1

        for item in sim['dropdown_items']:
            choices = item['choices'][:]
            random.shuffle(choices)
            payload = {'choices': choices, 'correct_choice': item['correct_choice']}
            cursor.execute('''
                INSERT INTO simulation_questions
                    (simulation_id, seq_no, item_type, question, payload, explanation)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (sim_id, seq_no, 'dropdown', item['question'], json.dumps(payload), item['explanation']))
            seq_no += 1

        for item in sim['journal_items']:
            rows = []
            for row in item['rows']:
                account_choices = row['account_choices'][:]
                random.shuffle(account_choices)
                rows.append({
                    'side': row['side'],
                    'account_choices': account_choices,
                    'correct_account': row['correct_account'],
                    'correct_amount': row['correct_amount'],
                })
            payload = {'narration': item['narration'], 'rows': rows}
            cursor.execute('''
                INSERT INTO simulation_questions
                    (simulation_id, seq_no, item_type, question, payload, explanation)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (sim_id, seq_no, 'journal_entry', item['question'], json.dumps(payload), item['explanation']))
            seq_no += 1

        added += 1

    conn.commit()
    cursor.close()
    return added


def update_srs_item(mcq_id, q, time_spent_seconds=0, attempts=1):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT repetitions, interval, ease_factor FROM srs_states WHERE mcq_id = %s", (mcq_id,))
    row = cursor.fetchone()
    repetitions, interval, ease_factor = row if row else (0, 0, 2.5)
    repetitions = int(repetitions)
    interval = int(interval)
    ease_factor = float(ease_factor)

    if q < 3:
        new_repetitions, new_interval = 0, 1
    else:
        new_repetitions = repetitions + 1
        if new_repetitions == 1:   new_interval = 1
        elif new_repetitions == 2: new_interval = 6
        else:                      new_interval = int(round(interval * ease_factor))

    new_ease_factor = max(ease_factor + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)), 1.3)
    next_date = datetime.date.today() + datetime.timedelta(days=new_interval)

    cursor.execute('''
        UPDATE srs_states SET repetitions = %s, interval = %s, ease_factor = %s, next_review = %s
        WHERE mcq_id = %s
    ''', (new_repetitions, new_interval, round(new_ease_factor, 2), next_date, mcq_id))

    cursor.execute('''
        INSERT INTO practice_log (mcq_id, reviewed_at, time_spent_seconds, srs_rating, attempts)
        VALUES (%s, %s, %s, %s, %s)
    ''', (mcq_id, datetime.date.today(), time_spent_seconds, q, attempts))

    conn.commit()
    cursor.close()


def get_subject_list():
    """Returns a sorted list of all subject names."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM subjects ORDER BY name")
    names = [r[0] for r in cursor.fetchall()]
    cursor.close()
    return names


def get_due_mcqs(subject_name, test_mode):
    """
    Returns active (non-retired) MCQs for the practice deck.
    If test_mode is True, ignores SRS due dates and orders by repetitions/next_review
    so weakest cards surface first. Otherwise only returns cards due today or earlier.
    Row shape: (id, question, options, correct_option, explanation, chapter_name, subject_name)
    """
    conn = _get_conn()
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
    if subject_name != "All Subjects":
        query += " AND sub.name = %s"
        params.append(subject_name)

    if test_mode:
        query += " ORDER BY s.repetitions ASC, s.next_review ASC"
    else:
        query += " AND s.next_review <= %s ORDER BY s.next_review ASC"
        params.append(datetime.date.today())

    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def get_cpa_exam_availability(subject_name):
    """Returns (mock_mcq_count, distinct_chapters_with_sims, total_sims) for the exam setup screen."""
    conn = _get_conn()
    cursor = conn.cursor()

    mcq_query = '''
        SELECT COUNT(*) FROM mock_mcqs m
        JOIN chapters c ON m.chapter_id = c.id
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE m.is_retired = 0
    '''
    sim_query = '''
        SELECT COUNT(DISTINCT s.chapter_id), COUNT(*) FROM simulations s
        JOIN chapters c ON s.chapter_id = c.id
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE s.is_retired = 0
    '''
    params = []
    if subject_name != "All Subjects":
        mcq_query += " AND sub.name = %s"
        sim_query += " AND sub.name = %s"
        params.append(subject_name)

    cursor.execute(mcq_query, params)
    mock_mcq_count = cursor.fetchone()[0]

    cursor.execute(sim_query, params)
    distinct_chapters_with_sims, total_sims = cursor.fetchone()

    cursor.close()
    return mock_mcq_count, distinct_chapters_with_sims, total_sims


def get_cpa_exam_mcqs(subject_name, count):
    """
    Returns up to `count` MCQs from the mock-exam pool, balanced by difficulty
    (30% easy, 40% medium, 30% hard). Falls back to any available questions if
    a difficulty bucket is too small.
    """
    conn = _get_conn()
    cursor = conn.cursor()

    base_query = '''
        SELECT m.id, m.question, m.options, m.correct_option, m.explanation,
               COALESCE(m.difficulty, 'hard') as difficulty, c.name, sub.name
        FROM mock_mcqs m
        JOIN chapters c ON m.chapter_id = c.id
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE m.is_retired = 0
    '''
    params = []
    if subject_name != "All Subjects":
        base_query += " AND sub.name = %s"
        params.append(subject_name)

    cursor.execute(base_query + " AND COALESCE(m.difficulty,'hard') = 'easy'", params)
    easy = cursor.fetchall()
    cursor.execute(base_query + " AND COALESCE(m.difficulty,'hard') = 'medium'", params)
    medium = cursor.fetchall()
    cursor.execute(base_query + " AND COALESCE(m.difficulty,'hard') = 'hard'", params)
    hard = cursor.fetchall()
    cursor.close()

    n_easy   = max(1, round(count * 0.30))
    n_medium = max(1, round(count * 0.40))
    n_hard   = max(1, round(count * 0.30))

    selected = []
    selected += random.sample(easy,   min(n_easy,   len(easy)))
    selected += random.sample(medium, min(n_medium, len(medium)))
    selected += random.sample(hard,   min(n_hard,   len(hard)))

    already = {r[0] for r in selected}
    remaining = [r for r in (easy + medium + hard) if r[0] not in already]
    needed = count - len(selected)
    if needed > 0 and remaining:
        selected += random.sample(remaining, min(needed, len(remaining)))

    random.shuffle(selected)

    result = []
    for row in selected[:count]:
        mcq_id, question, options_json, correct, explanation, difficulty, chapter, subject = row
        result.append({
            'mcq_id':      mcq_id,
            'question':    question,
            'options':     json.loads(options_json),
            'correct':     correct,
            'explanation': explanation,
            'difficulty':  difficulty,
            'chapter':     chapter,
            'subject':     subject
        })
    return result


def get_cpa_exam_simulations(subject_name):
    """
    Selects target_n = min(7, max(5, n_available_chapters)) chapters with
    simulations (random, capped by availability), one random non-retired
    simulation per chosen chapter, each with its ordered sub_items.
    """
    conn = _get_conn()
    cursor = conn.cursor()

    query = '''
        SELECT DISTINCT s.chapter_id
        FROM simulations s
        JOIN chapters c ON s.chapter_id = c.id
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE s.is_retired = 0
    '''
    params = []
    if subject_name != "All Subjects":
        query += " AND sub.name = %s"
        params.append(subject_name)

    cursor.execute(query, params)
    chapter_ids = [r[0] for r in cursor.fetchall()]

    if not chapter_ids:
        cursor.close()
        return []

    target_n = min(7, max(5, len(chapter_ids)))
    chosen_chapters = random.sample(chapter_ids, min(target_n, len(chapter_ids)))

    simulations = []
    for chapter_id in chosen_chapters:
        cursor.execute('''
            SELECT s.id, s.title, s.scenario, c.name, sub.name
            FROM simulations s
            JOIN chapters c ON s.chapter_id = c.id
            JOIN subjects sub ON c.subject_id = sub.id
            WHERE s.chapter_id = %s AND s.is_retired = 0
        ''', (chapter_id,))
        candidates = cursor.fetchall()
        if not candidates:
            continue
        sim_id, title, scenario, chapter_name, subject_name_row = random.choice(candidates)

        cursor.execute('''
            SELECT id, item_type, question, payload, explanation
            FROM simulation_questions
            WHERE simulation_id = %s
            ORDER BY seq_no
        ''', (sim_id,))
        sub_items = []
        for sq_id, item_type, question, payload_json, explanation in cursor.fetchall():
            sub_items.append({
                'id':          sq_id,
                'item_type':   item_type,
                'question':    question,
                'payload':     json.loads(payload_json),
                'explanation': explanation,
            })

        simulations.append({
            'simulation_id': sim_id,
            'title':         title,
            'scenario':      scenario,
            'chapter':       chapter_name,
            'subject':       subject_name_row,
            'sub_items':     sub_items,
        })

    cursor.close()
    return simulations


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _grade_sim_item(item_type, payload, user_answer):
    """
    Grades one simulation_questions item. Returns 1 if correct, 0 otherwise.

    user_answer shapes: numeric -> {'value': ...}, dropdown -> {'choice': ...},
    journal_entry -> {'rows': [{'account':, 'amount':}, ...]}
    """
    if not user_answer:
        return 0

    if item_type == 'numeric':
        value = user_answer.get('value')
        if not _is_number(value):
            return 0
        correct = payload['correct_value']
        tolerance = max(abs(correct) * 0.01, 1)
        return 1 if abs(value - correct) <= tolerance else 0

    if item_type == 'dropdown':
        return 1 if user_answer.get('choice') == payload['correct_choice'] else 0

    if item_type == 'journal_entry':
        user_rows = user_answer.get('rows') or []
        correct_rows = payload['rows']
        if len(user_rows) != len(correct_rows):
            return 0
        for ur, cr in zip(user_rows, correct_rows):
            if ur.get('account') != cr['correct_account']:
                return 0
            amount = ur.get('amount')
            if not _is_number(amount):
                return 0
            tolerance = max(abs(cr['correct_amount']) * 0.01, 1)
            if abs(amount - cr['correct_amount']) > tolerance:
                return 0
        return 1

    return 0


def save_cpa_exam(subject_name, mcq_questions, mcq_answers, simulations, sim_answers, time_taken_seconds):
    """
    Persists a CPA simulation exam attempt and returns a result dict with
    mcq_pct, sim_pct, overall_pct, and is_proficient.

    mcq_questions : list from get_cpa_exam_mcqs
    mcq_answers   : dict {mcq_id: selected_option}
    simulations   : list from get_cpa_exam_simulations
    sim_answers   : dict {simulation_question_id: user_answer} where user_answer is
                    {'value': ...} / {'choice': ...} / {'rows': [{'account':, 'amount':}, ...]}

    MCQ scoring is difficulty-weighted (easy=1pt, medium=2pts, hard=3pts);
    simulation scoring is a simple correct/total ratio across all sub_items, graded by
    _grade_sim_item (numeric: ±1% tolerance; dropdown: exact choice; journal_entry: every row correct).
    overall_pct = (mcq_pct + sim_pct) / 2. Proficient if overall_pct >= 75 AND
    minimum item-count gates (CPA_EXAM_MIN_MCQS, CPA_EXAM_MIN_SIMS) are met.
    """
    conn = _get_conn()
    cursor = conn.cursor()

    subject_id = None
    if subject_name != "All Subjects":
        cursor.execute("SELECT id FROM subjects WHERE name = %s", (subject_name,))
        row = cursor.fetchone()
        subject_id = row[0] if row else None

    mcq_earned = 0
    mcq_max = 0
    mcq_correct_count = 0
    answer_rows = []

    for q in mcq_questions:
        pts = DIFFICULTY_POINTS.get(q['difficulty'], 2)
        mcq_max += pts
        user_ans = mcq_answers.get(q['mcq_id'])
        is_correct = 1 if user_ans == q['correct'] else 0
        if is_correct:
            mcq_earned += pts
            mcq_correct_count += 1
        answer_rows.append(('mcq', q['mcq_id'], user_ans, is_correct, q['difficulty']))

    mcq_total = len(mcq_questions)
    mcq_pct = round((mcq_earned / mcq_max * 100), 1) if mcq_max > 0 else 0.0

    sim_total = 0
    sim_correct = 0
    sim_results = {}

    for sim in simulations:
        for item in sim['sub_items']:
            sim_total += 1
            user_ans = sim_answers.get(item['id'])
            is_correct = _grade_sim_item(item['item_type'], item['payload'], user_ans)
            sim_results[item['id']] = is_correct
            if is_correct:
                sim_correct += 1
            selected_json = json.dumps(user_ans) if user_ans else None
            answer_rows.append(('sim', item['id'], selected_json, is_correct, 'hard'))

    sim_pct = round((sim_correct / sim_total * 100), 1) if sim_total > 0 else 0.0

    overall_pct = round((mcq_pct + sim_pct) / 2, 1)
    is_proficient = 1 if (
        overall_pct >= 75
        and mcq_total >= CPA_EXAM_MIN_MCQS
        and len(simulations) >= CPA_EXAM_MIN_SIMS
    ) else 0

    cursor.execute('''
        INSERT INTO cpa_exams
            (subject_id, taken_at, mcq_total, mcq_correct, mcq_pct,
             sim_total, sim_correct, sim_pct, overall_pct, is_proficient, time_taken_seconds)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (subject_id, datetime.date.today(), mcq_total, mcq_correct_count, mcq_pct,
          sim_total, sim_correct, sim_pct, overall_pct, is_proficient, time_taken_seconds))

    exam_id = cursor.fetchone()[0]
    for item_type, item_id, selected_option, is_correct, difficulty in answer_rows:
        cursor.execute('''
            INSERT INTO cpa_exam_answers (exam_id, item_type, item_id, selected_option, is_correct, difficulty)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (exam_id, item_type, item_id, selected_option, is_correct, difficulty))

    conn.commit()
    cursor.close()

    return {
        'mcq_total':     mcq_total,
        'mcq_correct':   mcq_correct_count,
        'mcq_pct':       mcq_pct,
        'sim_total':     sim_total,
        'sim_correct':   sim_correct,
        'sim_pct':       sim_pct,
        'overall_pct':   overall_pct,
        'is_proficient': bool(is_proficient),
        'sim_results':   sim_results,
    }


def get_cpa_exam_history(limit=10):
    """Returns recent CPA exam rows: (id, subject, date, mcq_pct, sim_pct, overall_pct, is_proficient, mcq_total, sim_total, seconds)."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ce.id, COALESCE(sub.name, 'All Subjects'), ce.taken_at,
               ce.mcq_pct, ce.sim_pct, ce.overall_pct, ce.is_proficient,
               ce.mcq_total, ce.sim_total, ce.time_taken_seconds
        FROM cpa_exams ce
        LEFT JOIN subjects sub ON ce.subject_id = sub.id
        ORDER BY ce.taken_at DESC
        LIMIT %s
    ''', (limit,))
    rows = cursor.fetchall()
    cursor.close()
    return rows


def get_analytics_data():
    """
    Returns a dict with comprehensive analytics:
      overall   : (total, easy, medium, hard, total_reviews, due_today)
      stages    : (new_cards, learning, mature)
      subjects  : list of (name, total, easy, medium, hard, due, avg_ease, total_reps)
      weak_chapters : list of (label, avg_ease, count, due)
      exam_history  : list of (date, subject, mcq_pct, sim_pct, overall_pct, is_proficient, mcq_total, sim_total)
    """
    conn = _get_conn()
    cursor = conn.cursor()
    today = datetime.date.today()

    cursor.execute('''
        SELECT
            COUNT(m.id),
            SUM(CASE WHEN COALESCE(m.difficulty,'medium')='easy'   THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(m.difficulty,'medium')='medium' THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(m.difficulty,'medium')='hard'   THEN 1 ELSE 0 END),
            COALESCE(SUM(s.repetitions), 0),
            SUM(CASE WHEN s.next_review <= %s THEN 1 ELSE 0 END)
        FROM mcqs m
        JOIN srs_states s ON s.mcq_id = m.id
        WHERE m.is_retired = 0
    ''', (today,))
    overall = cursor.fetchone()

    cursor.execute('''
        SELECT
            SUM(CASE WHEN s.repetitions = 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN s.repetitions > 0 AND s.interval <= 21 THEN 1 ELSE 0 END),
            SUM(CASE WHEN s.interval > 21 THEN 1 ELSE 0 END)
        FROM srs_states s
        JOIN mcqs m ON s.mcq_id = m.id
        WHERE m.is_retired = 0
    ''')
    stages = cursor.fetchone()

    cursor.execute('''
        SELECT sub.name,
               COUNT(m.id),
               SUM(CASE WHEN COALESCE(m.difficulty,'medium')='easy'   THEN 1 ELSE 0 END),
               SUM(CASE WHEN COALESCE(m.difficulty,'medium')='medium' THEN 1 ELSE 0 END),
               SUM(CASE WHEN COALESCE(m.difficulty,'medium')='hard'   THEN 1 ELSE 0 END),
               SUM(CASE WHEN s.next_review <= %s THEN 1 ELSE 0 END),
               ROUND(AVG(s.ease_factor), 2),
               COALESCE(SUM(s.repetitions), 0)
        FROM subjects sub
        JOIN chapters c ON c.subject_id = sub.id
        JOIN mcqs m ON m.chapter_id = c.id
        JOIN srs_states s ON s.mcq_id = m.id
        WHERE m.is_retired = 0
        GROUP BY sub.name
        ORDER BY sub.name
    ''', (today,))
    subjects = cursor.fetchall()

    cursor.execute('''
        SELECT sub.name || ' › ' || c.name,
               ROUND(AVG(s.ease_factor), 2),
               COUNT(m.id),
               SUM(CASE WHEN s.next_review <= %s THEN 1 ELSE 0 END)
        FROM chapters c
        JOIN mcqs m ON m.chapter_id = c.id
        JOIN srs_states s ON s.mcq_id = m.id
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE m.is_retired = 0 AND s.repetitions >= 2
        GROUP BY c.id, sub.name, c.name
        HAVING COUNT(m.id) >= 3
        ORDER BY AVG(s.ease_factor) ASC
        LIMIT 5
    ''', (today,))
    weak_chapters = cursor.fetchall()

    cursor.execute('''
        SELECT ce.taken_at, COALESCE(sub.name, 'All Subjects'),
               ce.mcq_pct, ce.sim_pct, ce.overall_pct, ce.is_proficient,
               ce.mcq_total, ce.sim_total
        FROM cpa_exams ce
        LEFT JOIN subjects sub ON ce.subject_id = sub.id
        ORDER BY ce.taken_at DESC
        LIMIT 10
    ''')
    exam_history = cursor.fetchall()

    # ── Study time ─────────────────────────────────────────────────────────────
    cursor.execute('''
        SELECT
            ROUND(SUM(time_spent_seconds) / 3600.0, 1),
            ROUND(SUM(CASE WHEN reviewed_at >= CURRENT_DATE - INTERVAL '7 days'
                          THEN time_spent_seconds ELSE 0 END) / 3600.0, 1),
            ROUND(AVG(CASE WHEN time_spent_seconds > 0 THEN time_spent_seconds END) / 60.0, 1)
        FROM practice_log
    ''')
    study_time = cursor.fetchone()   # (total_hours, week_hours, avg_min_per_q)

    # ── Trending score — difficulty-weighted: hard correct = 3pts, medium = 2, easy = 1 ──
    def _score_sql(date_filter):
        return f'''
            SELECT ROUND(
                SUM(CASE WHEN pl.srs_rating >= 3
                    THEN CASE COALESCE(m.difficulty,'medium')
                         WHEN 'easy' THEN 1.0 WHEN 'hard' THEN 3.0 ELSE 2.0 END
                    ELSE 0 END) * 100.0
                / NULLIF(SUM(
                    CASE COALESCE(m.difficulty,'medium')
                    WHEN 'easy' THEN 1.0 WHEN 'hard' THEN 3.0 ELSE 2.0 END), 0)
            , 1)
            FROM practice_log pl JOIN mcqs m ON pl.mcq_id = m.id
            WHERE {date_filter}
        '''

    cursor.execute(_score_sql("pl.reviewed_at >= CURRENT_DATE - INTERVAL '7 days'"))
    current_score = (cursor.fetchone() or (None,))[0] or 0.0

    cursor.execute(_score_sql(
        "pl.reviewed_at >= CURRENT_DATE - INTERVAL '14 days' AND pl.reviewed_at < CURRENT_DATE - INTERVAL '7 days'"
    ))
    prev_score = (cursor.fetchone() or (None,))[0] or 0.0

    cursor.execute('''
        SELECT pl.reviewed_at,
            ROUND(
                SUM(CASE WHEN pl.srs_rating >= 3
                    THEN CASE COALESCE(m.difficulty,'medium')
                         WHEN 'easy' THEN 1.0 WHEN 'hard' THEN 3.0 ELSE 2.0 END
                    ELSE 0 END) * 100.0
                / NULLIF(SUM(
                    CASE COALESCE(m.difficulty,'medium')
                    WHEN 'easy' THEN 1.0 WHEN 'hard' THEN 3.0 ELSE 2.0 END), 0)
            , 1)
        FROM practice_log pl JOIN mcqs m ON pl.mcq_id = m.id
        WHERE pl.reviewed_at >= CURRENT_DATE - INTERVAL '30 days'
        GROUP BY pl.reviewed_at
        ORDER BY pl.reviewed_at
    ''')
    daily_trend = [(d.isoformat(), score) for d, score in cursor.fetchall()]

    # ── Daily activity (question count) for streak/consistency heatmap ────────
    cursor.execute('''
        SELECT reviewed_at, COUNT(*)
        FROM practice_log
        WHERE reviewed_at >= CURRENT_DATE - INTERVAL '34 days'
        GROUP BY reviewed_at
        ORDER BY reviewed_at
    ''')
    daily_activity = [(d.isoformat(), n) for d, n in cursor.fetchall()]

    # ── Speed vs accuracy by difficulty ────────────────────────────────────────
    cursor.execute('''
        SELECT
            COALESCE(m.difficulty, 'medium') AS difficulty,
            COUNT(*) AS total,
            SUM(CASE WHEN pl.srs_rating >= 3 THEN 1 ELSE 0 END) AS correct,
            ROUND(AVG(CASE WHEN pl.srs_rating >= 3 THEN pl.time_spent_seconds END), 1) AS avg_time_correct,
            ROUND(AVG(CASE WHEN pl.srs_rating < 3 THEN pl.time_spent_seconds END), 1) AS avg_time_incorrect
        FROM practice_log pl
        JOIN mcqs m ON m.id = pl.mcq_id
        WHERE m.is_retired = 0
        GROUP BY COALESCE(m.difficulty, 'medium')
        ORDER BY CASE COALESCE(m.difficulty, 'medium')
            WHEN 'easy' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
    ''')
    speed_accuracy = cursor.fetchall()
    # (difficulty, total, correct, avg_time_correct, avg_time_incorrect)

    # ── Chapter performance with confidence score ──────────────────────────────
    cursor.execute('''
        SELECT
            sub.name,
            c.name,
            COUNT(DISTINCT m.id)                      AS total_mcqs,
            ROUND(AVG(s.ease_factor), 2)              AS avg_ease,
            COALESCE(SUM(pl_agg.total_reviews), 0)    AS total_reviews,
            COALESCE(SUM(pl_agg.correct_reviews), 0)  AS correct_reviews,
            SUM(CASE WHEN s.next_review <= %s THEN 1 ELSE 0 END) AS due_count,
            COALESCE(SUM(pl_agg.total_time_sec), 0)   AS total_time_sec,
            c.syllabus_section                        AS syllabus_section
        FROM chapters c
        JOIN subjects sub ON c.subject_id = sub.id
        JOIN mcqs m ON m.chapter_id = c.id
        JOIN srs_states s ON s.mcq_id = m.id
        LEFT JOIN (
            SELECT mcq_id,
                   COUNT(*)                                           AS total_reviews,
                   SUM(CASE WHEN srs_rating >= 3 THEN 1 ELSE 0 END)  AS correct_reviews,
                   SUM(time_spent_seconds)                            AS total_time_sec
            FROM practice_log
            GROUP BY mcq_id
        ) pl_agg ON pl_agg.mcq_id = m.id
        WHERE m.is_retired = 0
        GROUP BY c.id, sub.name, c.name, c.syllabus_section
        ORDER BY sub.name, c.name
    ''', (today,))
    chapter_perf = cursor.fetchall()
    # (subject, chapter, total_mcqs, avg_ease, total_reviews, correct_reviews, due_count, total_time_sec, syllabus_section)

    # ── Lapse detection: questions that were "learned" (2+ consecutive good
    # reviews) and were then answered incorrectly again ──────────────────────
    cursor.execute('''
        SELECT sub.name, c.name, pl.mcq_id, pl.reviewed_at, pl.srs_rating
        FROM practice_log pl
        JOIN mcqs m ON m.id = pl.mcq_id
        JOIN chapters c ON c.id = m.chapter_id
        JOIN subjects sub ON sub.id = c.subject_id
        WHERE m.is_retired = 0
        ORDER BY pl.mcq_id, pl.id
    ''')
    review_rows = cursor.fetchall()

    lapse_cutoff = today - datetime.timedelta(days=30)
    lapses_by_chapter = {}  # (subject, chapter) -> {'count': int, 'latest': date}
    for mcq_id, group in itertools.groupby(review_rows, key=lambda r: r[2]):
        streak = 0
        for subject, chapter, _mcq_id, reviewed_at, rating in group:
            if rating is not None and rating < 3:
                if streak >= 2 and reviewed_at >= lapse_cutoff:
                    entry = lapses_by_chapter.setdefault((subject, chapter), {'count': 0, 'latest': reviewed_at})
                    entry['count'] += 1
                    entry['latest'] = max(entry['latest'], reviewed_at)
                streak = 0
            else:
                streak += 1

    recent_lapses = [
        (subject, chapter, info['count'], info['latest'])
        for (subject, chapter), info in lapses_by_chapter.items()
    ]
    recent_lapses.sort(key=lambda r: r[3], reverse=True)
    recent_lapses = recent_lapses[:8]
    # (subject, chapter, lapse_count, most_recent_lapse_date)

    cursor.close()
    return {
        'overall':          overall,
        'stages':           stages,
        'subjects':         subjects,
        'weak_chapters':    weak_chapters,
        'exam_history':     exam_history,
        'study_time':       study_time,
        'trending_current': current_score,
        'trending_prev':    prev_score,
        'trending_daily':   daily_trend,
        'daily_activity':   daily_activity,
        'speed_accuracy':   speed_accuracy,
        'chapter_perf':     chapter_perf,
        'recent_lapses':    recent_lapses,
    }


# ── Question-bank management ──────────────────────────────────────────────────

def get_question_bank_summary():
    """Returns list of (subject, chapter, mcq_count) sorted by subject, chapter."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT sub.name, c.name, COUNT(m.id)
        FROM subjects sub
        JOIN chapters c ON c.subject_id = sub.id
        JOIN mcqs m ON m.chapter_id = c.id
        WHERE m.is_retired = 0
        GROUP BY sub.name, c.name
        ORDER BY sub.name, c.name
    ''')
    rows = cursor.fetchall()
    cursor.close()
    return rows  # [(subject, chapter, count), ...]


def get_mock_bank_summary():
    """Returns list of (subject, chapter, mock_mcq_count, sim_count) sorted by subject, chapter."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT sub.name, c.name,
               COUNT(DISTINCT mm.id),
               COUNT(DISTINCT s.id)
        FROM subjects sub
        JOIN chapters c ON c.subject_id = sub.id
        LEFT JOIN mock_mcqs mm ON mm.chapter_id = c.id AND mm.is_retired = 0
        LEFT JOIN simulations s ON s.chapter_id = c.id AND s.is_retired = 0
        GROUP BY sub.name, c.name
        HAVING COUNT(DISTINCT mm.id) > 0 OR COUNT(DISTINCT s.id) > 0
        ORDER BY sub.name, c.name
    ''')
    rows = cursor.fetchall()
    cursor.close()
    return rows  # [(subject, chapter, mock_mcq_count, sim_count), ...]


def delete_chapter_mcqs(subject_name, chapter_name):
    """Hard-deletes all MCQs, mock MCQs, and simulations (+ dependents) for one chapter."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.id FROM chapters c
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE sub.name = %s AND c.name = %s
    ''', (subject_name, chapter_name))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        return 0
    chapter_id = row[0]

    cursor.execute("DELETE FROM mcqs WHERE chapter_id = %s", (chapter_id,))
    deleted = cursor.rowcount
    cursor.execute("DELETE FROM mock_mcqs WHERE chapter_id = %s", (chapter_id,))
    cursor.execute("DELETE FROM simulations WHERE chapter_id = %s", (chapter_id,))
    conn.commit()
    cursor.close()
    return deleted


def delete_subject_mcqs(subject_name):
    """Hard-deletes all MCQs, mock MCQs, and simulations (+ dependents) for an entire subject."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.id FROM chapters c
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE sub.name = %s
    ''', (subject_name,))
    chapter_ids = [r[0] for r in cursor.fetchall()]
    if not chapter_ids:
        cursor.close()
        return 0

    cursor.execute("DELETE FROM mcqs WHERE chapter_id = ANY(%s)", (chapter_ids,))
    deleted = cursor.rowcount
    cursor.execute("DELETE FROM mock_mcqs WHERE chapter_id = ANY(%s)", (chapter_ids,))
    cursor.execute("DELETE FROM simulations WHERE chapter_id = ANY(%s)", (chapter_ids,))
    conn.commit()
    cursor.close()
    return deleted


def remove_duplicate_mcqs():
    """
    Removes duplicate MCQs across the whole bank (keeps the oldest per chapter).
    Returns the number of duplicates removed.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, chapter_id, question FROM mcqs WHERE is_retired = 0 ORDER BY id")
    rows = cursor.fetchall()

    seen = {}   # (chapter_id, norm_q) -> first_id
    to_delete = []
    for mcq_id, chapter_id, question in rows:
        key = (chapter_id, _norm_q(question))
        if key in seen:
            to_delete.append(mcq_id)
        else:
            seen[key] = mcq_id

    if to_delete:
        cursor.execute("DELETE FROM mcqs WHERE id = ANY(%s)", (to_delete,))
        conn.commit()

    cursor.close()
    return len(to_delete)


# ── Audio Notes ──────────────────────────────────────────────────────────────

def get_chapters_for_subject(subject_name):
    """Returns [(chapter_id, chapter_name), ...] for a subject, ordered by name."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.id, c.name FROM chapters c
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE sub.name = %s
        ORDER BY c.name
    ''', (subject_name,))
    rows = cursor.fetchall()
    cursor.close()
    return rows


def get_or_create_chapter(subject_name, chapter_name):
    """Returns the chapter_id for (subject_name, chapter_name), creating both if needed."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO subjects (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (subject_name,))
    cursor.execute("SELECT id FROM subjects WHERE name = %s", (subject_name,))
    subject_id = cursor.fetchone()[0]

    paper = icai_syllabus.infer_paper(subject_name)
    syllabus_section = icai_syllabus.suggest_section(paper, chapter_name) if paper else None

    cursor.execute(
        "INSERT INTO chapters (subject_id, name, syllabus_section) VALUES (%s, %s, %s) ON CONFLICT (subject_id, name) DO NOTHING",
        (subject_id, chapter_name, syllabus_section)
    )
    cursor.execute("SELECT id FROM chapters WHERE subject_id = %s AND name = %s", (subject_id, chapter_name))
    chapter_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    return chapter_id


def get_chapters_with_sections(subject_name):
    """Returns [(chapter_id, chapter_name, syllabus_section), ...] for a subject, ordered by name."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.id, c.name, c.syllabus_section FROM chapters c
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE sub.name = %s
        ORDER BY c.name
    ''', (subject_name,))
    rows = cursor.fetchall()
    cursor.close()
    return rows


def set_chapter_syllabus_section(chapter_id, section_code):
    """Sets (or clears, if section_code is None) the syllabus_section for a chapter."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE chapters SET syllabus_section = %s WHERE id = %s", (section_code, chapter_id))
    conn.commit()
    cursor.close()


def save_audio_episode(chapter_id, episode_num, title, audio_url, duration_seconds, word_count, file_size_bytes, notes_url=None):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO audio_episodes
            (chapter_id, episode_num, title, audio_url, duration_seconds, word_count, file_size_bytes, notes_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (chapter_id, episode_num, title, audio_url, duration_seconds, word_count, file_size_bytes, notes_url))
    episode_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    return episode_id


def update_audio_episode_media(episode_id, title, audio_url, duration_seconds, word_count, file_size_bytes):
    """Updates an existing episode's title and audio metadata after regenerating its
    audio from edited markdown notes."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE audio_episodes
        SET title = %s, audio_url = %s, duration_seconds = %s, word_count = %s, file_size_bytes = %s
        WHERE id = %s
    ''', (title, audio_url, duration_seconds, word_count, file_size_bytes, episode_id))
    conn.commit()
    cursor.close()


def get_episodes_for_chapter(chapter_id):
    """Returns [(id, episode_num, title, audio_url, duration_seconds, word_count, created_at, notes_url), ...]."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, episode_num, title, audio_url, duration_seconds, word_count, created_at, notes_url
        FROM audio_episodes WHERE chapter_id = %s ORDER BY episode_num
    ''', (chapter_id,))
    rows = cursor.fetchall()
    cursor.close()
    return rows


def get_all_episodes_for_feed():
    """Returns all episodes for the podcast feed, in intended listening order:
    [(id, title, audio_url, duration_seconds, file_size_bytes, episode_num, subject_name, chapter_name), ...]
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ae.id, ae.title, ae.audio_url, ae.duration_seconds, ae.file_size_bytes,
               ae.episode_num, sub.name, c.name
        FROM audio_episodes ae
        JOIN chapters c ON ae.chapter_id = c.id
        JOIN subjects sub ON c.subject_id = sub.id
        ORDER BY sub.name, c.name, ae.episode_num
    ''')
    rows = cursor.fetchall()
    cursor.close()
    return rows


def get_audio_episode_summary():
    """Returns [(subject, chapter, episode_count, total_duration_seconds), ...]."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT sub.name, c.name, COUNT(ae.id), COALESCE(SUM(ae.duration_seconds), 0)
        FROM audio_episodes ae
        JOIN chapters c ON ae.chapter_id = c.id
        JOIN subjects sub ON c.subject_id = sub.id
        GROUP BY sub.name, c.name
        ORDER BY sub.name, c.name
    ''')
    rows = cursor.fetchall()
    cursor.close()
    return rows


def delete_audio_episode(episode_id):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM audio_episodes WHERE id = %s", (episode_id,))
    deleted = cursor.rowcount
    conn.commit()
    cursor.close()
    return deleted


def remove_duplicate_mock_mcqs():
    """
    Removes duplicate mock MCQs across the whole pool (keeps the oldest per chapter).
    Returns the number of duplicates removed.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, chapter_id, question FROM mock_mcqs WHERE is_retired = 0 ORDER BY id")
    rows = cursor.fetchall()

    seen = {}   # (chapter_id, norm_q) -> first_id
    to_delete = []
    for mcq_id, chapter_id, question in rows:
        key = (chapter_id, _norm_q(question))
        if key in seen:
            to_delete.append(mcq_id)
        else:
            seen[key] = mcq_id

    if to_delete:
        cursor.execute("DELETE FROM mock_mcqs WHERE id = ANY(%s)", (to_delete,))
        conn.commit()

    cursor.close()
    return len(to_delete)
