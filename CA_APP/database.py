import sqlite3
import datetime
import json
import random

DB_NAME = "ca_practice.db"

DIFFICULTY_POINTS = {'easy': 1, 'medium': 2, 'hard': 3}
MOCK_TEST_MIN_QUESTIONS = 50  # Minimum questions for a valid proficiency assessment


def _norm_q(text: str) -> str:
    """Normalised question key used for duplicate detection."""
    import re
    return re.sub(r'\s+', ' ', (text or "").lower().strip())


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER,
            name TEXT NOT NULL,
            UNIQUE(subject_id, name),
            FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mcqs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER,
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            correct_option TEXT NOT NULL,
            explanation TEXT,
            difficulty TEXT DEFAULT 'medium',
            is_retired INTEGER DEFAULT 0,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS srs_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mcq_id INTEGER UNIQUE,
            repetitions INTEGER DEFAULT 0,
            interval INTEGER DEFAULT 0,
            ease_factor REAL DEFAULT 2.5,
            next_review TEXT NOT NULL,
            FOREIGN KEY(mcq_id) REFERENCES mcqs(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mock_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER,
            taken_at TEXT NOT NULL,
            total_questions INTEGER,
            earned_score REAL,
            max_score REAL,
            percentage REAL,
            is_proficient INTEGER,
            time_taken_seconds INTEGER,
            FOREIGN KEY(subject_id) REFERENCES subjects(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mock_test_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            mcq_id INTEGER,
            selected_option TEXT,
            is_correct INTEGER,
            difficulty TEXT,
            FOREIGN KEY(test_id) REFERENCES mock_tests(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS practice_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mcq_id INTEGER,
            reviewed_at TEXT NOT NULL,
            time_spent_seconds REAL DEFAULT 0,
            srs_rating INTEGER,
            attempts INTEGER DEFAULT 1,
            FOREIGN KEY(mcq_id) REFERENCES mcqs(id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()


def save_generated_mcqs(subject_name, chapter_name, mcq_list):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("INSERT OR IGNORE INTO subjects (name) VALUES (?)", (subject_name,))
    cursor.execute("SELECT id FROM subjects WHERE name = ?", (subject_name,))
    subject_id = cursor.fetchone()[0]

    cursor.execute("INSERT OR IGNORE INTO chapters (subject_id, name) VALUES (?, ?)", (subject_id, chapter_name))
    cursor.execute("SELECT id FROM chapters WHERE subject_id = ? AND name = ?", (subject_id, chapter_name))
    chapter_id = cursor.fetchone()[0]

    # Load existing normalised question texts for this chapter to skip duplicates
    cursor.execute("SELECT question FROM mcqs WHERE chapter_id = ?", (chapter_id,))
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
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (chapter_id, item['question'], json.dumps(options_dict),
              correct_option, item['explanation'], difficulty))

        mcq_id = cursor.lastrowid
        today_str = datetime.date.today().isoformat()
        cursor.execute('INSERT INTO srs_states (mcq_id, next_review) VALUES (?, ?)', (mcq_id, today_str))
        added += 1

    conn.commit()
    conn.close()
    return added


def update_srs_item(mcq_id, q, time_spent_seconds=0, attempts=1):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT repetitions, interval, ease_factor FROM srs_states WHERE mcq_id = ?", (mcq_id,))
    row = cursor.fetchone()
    repetitions, interval, ease_factor = row if row else (0, 0, 2.5)

    if q < 3:
        new_repetitions, new_interval = 0, 1
    else:
        new_repetitions = repetitions + 1
        if new_repetitions == 1:   new_interval = 1
        elif new_repetitions == 2: new_interval = 6
        else:                      new_interval = int(round(interval * ease_factor))

    new_ease_factor = max(ease_factor + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)), 1.3)
    next_date = (datetime.date.today() + datetime.timedelta(days=new_interval)).isoformat()

    cursor.execute('''
        UPDATE srs_states SET repetitions = ?, interval = ?, ease_factor = ?, next_review = ?
        WHERE mcq_id = ?
    ''', (new_repetitions, new_interval, round(new_ease_factor, 2), next_date, mcq_id))

    cursor.execute('''
        INSERT INTO practice_log (mcq_id, reviewed_at, time_spent_seconds, srs_rating, attempts)
        VALUES (?, ?, ?, ?, ?)
    ''', (mcq_id, datetime.date.today().isoformat(), time_spent_seconds, q, attempts))

    conn.commit()
    conn.close()


def get_mock_test_questions(subject_name, count):
    """
    Returns up to `count` MCQs balanced by difficulty (30% easy, 40% medium, 30% hard).
    Falls back to any available questions if a difficulty bucket is too small.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    base_query = '''
        SELECT m.id, m.question, m.options, m.correct_option, m.explanation,
               COALESCE(m.difficulty, 'medium') as difficulty, c.name, sub.name
        FROM mcqs m
        JOIN chapters c ON m.chapter_id = c.id
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE m.is_retired = 0
    '''
    params = []
    if subject_name != "All Subjects":
        base_query += " AND sub.name = ?"
        params.append(subject_name)

    easy   = cursor.execute(base_query + " AND COALESCE(m.difficulty,'medium') = 'easy'",   params).fetchall()
    medium = cursor.execute(base_query + " AND COALESCE(m.difficulty,'medium') = 'medium'", params).fetchall()
    hard   = cursor.execute(base_query + " AND COALESCE(m.difficulty,'medium') = 'hard'",   params).fetchall()
    conn.close()

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


def save_mock_test(subject_name, questions, answers, time_taken_seconds):
    """
    Persists mock test results and returns (earned_score, max_score, percentage, is_proficient).
    Scoring: easy=1pt, medium=2pts, hard=3pts. Proficient if percentage >= 75.
    """
    subject_id = None
    if subject_name != "All Subjects":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM subjects WHERE name = ?", (subject_name,))
        row = cursor.fetchone()
        subject_id = row[0] if row else None
        conn.close()

    earned = 0
    max_score = 0
    answer_rows = []

    for q in questions:
        pts = DIFFICULTY_POINTS.get(q['difficulty'], 2)
        max_score += pts
        user_ans = answers.get(q['mcq_id'])
        is_correct = 1 if user_ans == q['correct'] else 0
        if is_correct:
            earned += pts
        answer_rows.append((q['mcq_id'], user_ans, is_correct, q['difficulty']))

    pct = round((earned / max_score * 100), 1) if max_score > 0 else 0.0
    is_proficient = 1 if (pct >= 75 and len(questions) >= MOCK_TEST_MIN_QUESTIONS) else 0

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO mock_tests
            (subject_id, taken_at, total_questions, earned_score, max_score, percentage, is_proficient, time_taken_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (subject_id, datetime.date.today().isoformat(), len(questions),
          earned, max_score, pct, is_proficient, time_taken_seconds))

    test_id = cursor.lastrowid
    for mcq_id, selected_option, is_correct, difficulty in answer_rows:
        cursor.execute('''
            INSERT INTO mock_test_answers (test_id, mcq_id, selected_option, is_correct, difficulty)
            VALUES (?, ?, ?, ?, ?)
        ''', (test_id, mcq_id, selected_option, is_correct, difficulty))

    conn.commit()
    conn.close()

    return earned, max_score, pct, bool(is_proficient)


def get_mock_test_history(limit=10):
    """Returns recent mock test rows: (id, subject, date, total_q, earned, max, pct, is_proficient, seconds)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT mt.id, COALESCE(sub.name, 'All Subjects'), mt.taken_at,
               mt.total_questions, mt.earned_score, mt.max_score,
               mt.percentage, mt.is_proficient, mt.time_taken_seconds
        FROM mock_tests mt
        LEFT JOIN subjects sub ON mt.subject_id = sub.id
        ORDER BY mt.taken_at DESC
        LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_analytics_data():
    """
    Returns a dict with comprehensive analytics:
      overall   : (total, easy, medium, hard, total_reviews, due_today)
      stages    : (new_cards, learning, mature)
      subjects  : list of (name, total, easy, medium, hard, due, avg_ease, total_reps)
      weak_chapters : list of (label, avg_ease, count, due)
      mock_history  : list of (date, subject, pct, is_proficient, total_q, earned, max_score)
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.date.today().isoformat()

    cursor.execute('''
        SELECT
            COUNT(m.id),
            SUM(CASE WHEN COALESCE(m.difficulty,'medium')='easy'   THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(m.difficulty,'medium')='medium' THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(m.difficulty,'medium')='hard'   THEN 1 ELSE 0 END),
            COALESCE(SUM(s.repetitions), 0),
            SUM(CASE WHEN s.next_review <= ? THEN 1 ELSE 0 END)
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
               SUM(CASE WHEN s.next_review <= ? THEN 1 ELSE 0 END),
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
               SUM(CASE WHEN s.next_review <= ? THEN 1 ELSE 0 END)
        FROM chapters c
        JOIN mcqs m ON m.chapter_id = c.id
        JOIN srs_states s ON s.mcq_id = m.id
        JOIN subjects sub ON c.subject_id = sub.id
        WHERE m.is_retired = 0 AND s.repetitions >= 2
        GROUP BY c.id
        HAVING COUNT(m.id) >= 3
        ORDER BY AVG(s.ease_factor) ASC
        LIMIT 5
    ''', (today,))
    weak_chapters = cursor.fetchall()

    cursor.execute('''
        SELECT mt.taken_at, COALESCE(sub.name, 'All Subjects'),
               mt.percentage, mt.is_proficient, mt.total_questions,
               mt.earned_score, mt.max_score
        FROM mock_tests mt
        LEFT JOIN subjects sub ON mt.subject_id = sub.id
        ORDER BY mt.taken_at DESC
        LIMIT 10
    ''')
    mock_history = cursor.fetchall()

    # ── Study time ─────────────────────────────────────────────────────────────
    cursor.execute('''
        SELECT
            ROUND(SUM(time_spent_seconds) / 3600.0, 1),
            ROUND(SUM(CASE WHEN reviewed_at >= date('now','-7 days')
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

    cursor.execute(_score_sql("pl.reviewed_at >= date('now','-7 days')"))
    current_score = (cursor.fetchone() or (None,))[0] or 0.0

    cursor.execute(_score_sql(
        "pl.reviewed_at >= date('now','-14 days') AND pl.reviewed_at < date('now','-7 days')"
    ))
    prev_score = (cursor.fetchone() or (None,))[0] or 0.0

    cursor.execute(f'''
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
        WHERE pl.reviewed_at >= date('now','-30 days')
        GROUP BY pl.reviewed_at
        ORDER BY pl.reviewed_at
    ''')
    daily_trend = cursor.fetchall()   # [(date_str, score), ...]

    # ── Chapter performance with confidence score ──────────────────────────────
    cursor.execute('''
        SELECT
            sub.name,
            c.name,
            COUNT(DISTINCT m.id)                      AS total_mcqs,
            ROUND(AVG(s.ease_factor), 2)              AS avg_ease,
            COALESCE(pl_agg.total_reviews, 0)         AS total_reviews,
            COALESCE(pl_agg.correct_reviews, 0)       AS correct_reviews,
            SUM(CASE WHEN s.next_review <= ? THEN 1 ELSE 0 END) AS due_count,
            COALESCE(pl_agg.total_time_sec, 0)        AS total_time_sec
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
        GROUP BY c.id, sub.name, c.name
        ORDER BY sub.name, c.name
    ''', (today,))
    chapter_perf = cursor.fetchall()
    # (subject, chapter, total_mcqs, avg_ease, total_reviews, correct_reviews, due_count, total_time_sec)

    conn.close()
    return {
        'overall':          overall,
        'stages':           stages,
        'subjects':         subjects,
        'weak_chapters':    weak_chapters,
        'mock_history':     mock_history,
        'study_time':       study_time,
        'trending_current': current_score,
        'trending_prev':    prev_score,
        'trending_daily':   daily_trend,
        'chapter_perf':     chapter_perf,
    }


# ── Question-bank management ──────────────────────────────────────────────────

def get_question_bank_summary():
    """Returns list of (subject, chapter, mcq_count) sorted by subject, chapter."""
    conn = sqlite3.connect(DB_NAME)
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
    conn.close()
    return rows  # [(subject, chapter, count), ...]


def delete_chapter_mcqs(subject_name, chapter_name):
    """Hard-deletes all MCQs (+ SRS states + practice log) for one chapter."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM mcqs WHERE chapter_id = (
            SELECT c.id FROM chapters c
            JOIN subjects sub ON c.subject_id = sub.id
            WHERE sub.name = ? AND c.name = ?
        )
    ''', (subject_name, chapter_name))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_subject_mcqs(subject_name):
    """Hard-deletes all MCQs (+ SRS states + practice log) for an entire subject."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM mcqs WHERE chapter_id IN (
            SELECT c.id FROM chapters c
            JOIN subjects sub ON c.subject_id = sub.id
            WHERE sub.name = ?
        )
    ''', (subject_name,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def remove_duplicate_mcqs():
    """
    Removes duplicate MCQs across the whole bank (keeps the oldest per chapter).
    Returns the number of duplicates removed.
    """
    conn = sqlite3.connect(DB_NAME)
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
        cursor.executemany("DELETE FROM mcqs WHERE id = ?", [(i,) for i in to_delete])
        conn.commit()

    conn.close()
    return len(to_delete)
