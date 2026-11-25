"""
SGS Advanced eLearning & Virtual Classroom Service Layer.

Provides:
- Role/course/subject authorization helpers
- Automated virtual classroom attendance tracking & SGS Attendance synchronization
- Objective auto-grading & assessment scoring engine
- Meaningful student learning progress & analytics computation
- Safe, curriculum-grounded AI Learning Assistant (students & teachers)
- Integrated SGS notification dispatching & storage utilization reporting
"""

import json
import os
import re
from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from .models import (
    AILearningInteraction,
    AssessmentAnswer,
    AssessmentAttempt,
    Assignment,
    AssignmentSubmission,
    Attendance,
    AttendanceReport,
    Course,
    Examination,
    LearningMaterial,
    LearningTopic,
    LectureRecording,
    MaterialAccessLog,
    NotificationStaff,
    NotificationStudent,
    OnlineAssessment,
    ParentGuardian,
    QuestionBankItem,
    Session,
    Staff,
    Student,
    StudentResult,
    Subject,
    SubjectAnnouncement,
    TimetableEntry,
    VirtualAttendanceRecord,
    VirtualClassroom,
)

ALLOWED_MATERIAL_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".txt", ".md", ".csv",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".mp3", ".wav", ".ogg", ".m4a",
    ".mp4", ".webm", ".mov",
    ".zip",
}

ALLOWED_RECORDING_EXTENSIONS = {".mp4", ".webm", ".mov", ".ogg"}
MAX_MATERIAL_SIZE_BYTES = 50 * 1024 * 1024      # 50 MB
MAX_RECORDING_SIZE_BYTES = 250 * 1024 * 1024    # 250 MB


def get_user_role(user):
    return str(getattr(user, "user_type", ""))


def is_admin_or_dos(user):
    return get_user_role(user) in ("1", "4")


def is_teacher(user):
    return get_user_role(user) in ("2", "6")


def is_student(user):
    return get_user_role(user) == "3"


def is_parent(user):
    return get_user_role(user) == "7"


def get_authorized_subjects(user):
    """Return QuerySet of existing SGS Subject records the user is permitted to view."""
    role = get_user_role(user)
    qs = Subject.objects.select_related("course", "staff__admin")
    if role in ("1", "4"):
        return qs.all()
    if role in ("2", "6"):
        staff = Staff.objects.filter(admin=user).first()
        if not staff:
            return qs.none()
        if staff.class_teacher and staff.course_id:
            return qs.filter(Q(staff=staff) | Q(course_id=staff.course_id)).distinct()
        return qs.filter(staff=staff)
    if role == "3":
        student = Student.objects.filter(admin=user).first()
        if not student or not student.course_id:
            return qs.none()
        return qs.filter(course_id=student.course_id)
    if role == "7":
        guardian = ParentGuardian.objects.filter(admin=user).first()
        if not guardian:
            return qs.none()
        course_ids = guardian.students.values_list("course_id", flat=True)
        return qs.filter(course_id__in=course_ids)
    return qs.none()


def can_manage_subject(user, subject):
    """Teachers can manage subjects they teach; Principal/DOS have administrative oversight."""
    role = get_user_role(user)
    if role in ("1", "4"):
        return True
    if role in ("2", "6"):
        staff = Staff.objects.filter(admin=user).first()
        return bool(staff and subject.staff_id == staff.id)
    return False


def is_classroom_host(user, classroom):
    """
    Host authorization:
    - Staff meetings ('STAFF'): Principal/Admin ('1'), DOS ('4'), or the host_user.
    - Student classes ('STUDENTS'): Only Teachers ('2'/'6') assigned to that class/subject.
      Admins/DOS cannot host or conduct live student classes.
    """
    role = get_user_role(user)
    if classroom.audience == "STAFF":
        return role in ("1", "4") or classroom.host_user_id == user.id
    if role in ("2", "6"):
        staff = Staff.objects.filter(admin=user).first()
        if not staff:
            return False
        if classroom.teacher_id == staff.id:
            return True
        if classroom.subject_id and classroom.subject.staff_id == staff.id:
            return True
    return False


def can_access_classroom(user, classroom):
    """
    Strict access check for VirtualClassroom membership & authorization:
    - STAFF meetings: Principal/Admin ('1'), DOS ('4'), and invited Staff/Teachers ('2'/'6').
      Students & Parents are strictly blocked.
    - STUDENTS virtual classes: Assigned Teachers ('2'/'6') and enrolled Students ('3').
      Principal/Admin ('1'), DOS ('4'), and Parents ('7') cannot join/contact live student classes.
    """
    role = get_user_role(user)
    if classroom.audience == "STAFF":
        if role in ("1", "4"):
            return True
        if role in ("2", "6"):
            staff = Staff.objects.filter(admin=user).first()
            if not staff:
                return False
            if classroom.invited_staff.exists():
                return classroom.invited_staff.filter(id=staff.id).exists()
            return True
        return False

    # Student Virtual Class ('STUDENTS')
    if role in ("2", "6"):
        staff = Staff.objects.filter(admin=user).first()
        if not staff:
            return False
        return (
            classroom.teacher_id == staff.id
            or (classroom.subject_id and classroom.subject.staff_id == staff.id)
            or (staff.class_teacher and classroom.course_id and staff.course_id == classroom.course_id)
        )
    if role == "3":
        student = Student.objects.filter(admin=user).first()
        if not student or not classroom.course_id or student.course_id != classroom.course_id:
            return False
        if classroom.invited_students.exists():
            return classroom.invited_students.filter(id=student.id).exists()
        return True
    # Admin/DOS cannot conduct/join live student virtual classes; Parents cannot join either
    return False


def notify_course_students(course, message, students_qs=None):
    """Send integrated SGS NotificationStudent alerts to all students in a course."""
    if not course and students_qs is None:
        return 0
    targets = students_qs if students_qs is not None else Student.objects.filter(course=course)
    notifications = [
        NotificationStudent(student=st, message=message)
        for st in targets
    ]
    if notifications:
        NotificationStudent.objects.bulk_create(notifications)
    return len(notifications)


def notify_staff_members(message, staff_qs=None):
    """Send integrated SGS NotificationStaff alerts to invited or all staff/teachers."""
    targets = staff_qs if staff_qs is not None else Staff.objects.all()
    notifications = [
        NotificationStaff(staff=st, message=message)
        for st in targets
    ]
    if notifications:
        NotificationStaff.objects.bulk_create(notifications)
    return len(notifications)


# ---------------------------------------------------------------------------
# AUTOMATED ONLINE ATTENDANCE ENGINE & SGS ATTENDANCE SYNCHRONIZATION
# ---------------------------------------------------------------------------

def _append_audit(record, event_name, detail=""):
    log = list(record.audit_log or [])
    log.append({
        "event": event_name,
        "at": timezone.now().isoformat(),
        "detail": detail,
    })
    record.audit_log = log[-80:]


def recalculate_virtual_attendance_status(record):
    """Compute participation_percent and status from active_seconds and classroom rules."""
    classroom = record.classroom
    if record.teacher_override:
        return record.status

    expected_seconds = max(60, int(classroom.duration_minutes or 45) * 60)
    if classroom.started_at and classroom.ended_at:
        actual = int((classroom.ended_at - classroom.started_at).total_seconds())
        if actual >= 60:
            expected_seconds = min(expected_seconds, actual)

    pct = min(100.0, round((record.active_seconds / expected_seconds) * 100.0, 1))
    record.participation_percent = pct

    if not record.joined_at:
        record.status = "ABSENT"
        return record.status

    ref_start = classroom.started_at or classroom.scheduled_start
    late_minutes = 0.0
    if ref_start:
        late_minutes = (record.joined_at - ref_start).total_seconds() / 60.0

    threshold = classroom.attendance_threshold_percent or 60
    grace = classroom.late_grace_minutes if classroom.late_grace_minutes is not None else 10

    if pct >= threshold:
        if late_minutes > grace:
            record.status = "LATE"
        else:
            record.status = "PRESENT"
    elif record.active_seconds > 0:
        record.status = "PARTIAL"
    else:
        record.status = "PENDING"
    return record.status


def record_student_join(classroom, student):
    """Create or resume a VirtualAttendanceRecord with server timestamp & audit log."""
    now = timezone.now()
    record, created = VirtualAttendanceRecord.objects.get_or_create(
        classroom=classroom,
        student=student,
        defaults={
            "joined_at": now,
            "last_heartbeat_at": now,
            "status": "PENDING",
            "audit_log": [],
        },
    )
    if record.is_removed:
        return record, False

    if created or not record.joined_at:
        record.joined_at = now
        record.last_heartbeat_at = now
        record.heartbeat_count = max(1, record.heartbeat_count)
        _append_audit(record, "JOINED", "Initial verified classroom join")
    else:
        record.reconnect_count += 1
        record.last_heartbeat_at = now
        record.left_at = None
        _append_audit(record, "RECONNECTED", f"Reconnect #{record.reconnect_count}")

    recalculate_virtual_attendance_status(record)
    record.save()
    return record, True


def record_student_heartbeat(classroom, student, interval_seconds=30):
    """Increment active participation via verified server-side delta (capped to prevent spoofing)."""
    now = timezone.now()
    record, ok = VirtualAttendanceRecord.objects.get_or_create(
        classroom=classroom,
        student=student,
        defaults={"joined_at": now, "last_heartbeat_at": now},
    )
    if record.is_removed:
        return record

    if record.last_heartbeat_at:
        delta = int((now - record.last_heartbeat_at).total_seconds())
        # Cap delta to 90s so disconnected idle time isn't inflated
        credited = max(5, min(delta, 90))
    else:
        credited = min(interval_seconds, 60)

    record.active_seconds += credited
    record.heartbeat_count += 1
    record.last_heartbeat_at = now
    record.left_at = None
    recalculate_virtual_attendance_status(record)
    record.save()
    return record


def record_student_leave(classroom, student):
    """Mark departure timestamp and credit final active interval."""
    now = timezone.now()
    record = VirtualAttendanceRecord.objects.filter(
        classroom=classroom, student=student
    ).first()
    if not record:
        return None
    if record.last_heartbeat_at:
        delta = int((now - record.last_heartbeat_at).total_seconds())
        if 0 < delta <= 90:
            record.active_seconds += delta
    record.left_at = now
    record.hand_raised = False
    _append_audit(record, "LEFT", f"Active {record.active_seconds}s ({record.participation_percent}%)")
    recalculate_virtual_attendance_status(record)
    record.save()
    return record


def sync_classroom_attendance_to_sgs(classroom, actor=None):
    """
    Synchronize virtual classroom attendance into existing SGS Attendance & AttendanceReport
    tables without creating duplicates. Only applies to student virtual classes.
    """
    if classroom.audience == "STAFF" or not classroom.subject_id or not classroom.course_id:
        return None, 0
    session = classroom.session or Session.objects.order_by("-id").first()
    if not session:
        return None, 0

    lesson_date = (classroom.started_at or classroom.scheduled_start or timezone.now()).date()
    attendance, _ = Attendance.objects.get_or_create(
        session=session,
        subject=classroom.subject,
        date=lesson_date,
    )

    if classroom.invited_students.exists():
        eligible_students = list(classroom.invited_students.select_related("admin"))
    else:
        eligible_students = list(Student.objects.filter(course=classroom.course).select_related("admin"))

    status_map = {
        "PRESENT": (True, "PRESENT"),
        "LATE": (True, "TARDY"),
        "PARTIAL": (True, "TARDY"),
        "EXCUSED": (False, "EXCUSED"),
        "ABSENT": (False, "ABSENT"),
        "PENDING": (False, "PENDING"),
    }

    synced_count = 0
    for st in eligible_students:
        vrec, _ = VirtualAttendanceRecord.objects.get_or_create(
            classroom=classroom,
            student=st,
            defaults={"status": "ABSENT", "audit_log": []},
        )
        recalculate_virtual_attendance_status(vrec)
        is_present, sgs_code = status_map.get(vrec.status, (False, "ABSENT"))

        report, _ = AttendanceReport.objects.update_or_create(
            student=st,
            attendance=attendance,
            defaults={
                "status": is_present,
                "attendance_status": sgs_code,
            },
        )
        vrec.synced_report = report
        _append_audit(
            vrec,
            "SYNCED_SGS",
            f"Synced as {sgs_code} by {actor.email if actor else 'system'}",
        )
        vrec.save()
        synced_count += 1

    classroom.synced_attendance = attendance
    classroom.attendance_synced = True
    classroom.save(update_fields=["synced_attendance", "attendance_synced"])
    return attendance, synced_count


# ---------------------------------------------------------------------------
# ONLINE ASSESSMENT AUTO-GRADING ENGINE
# ---------------------------------------------------------------------------

def _normalize_text(val):
    return re.sub(r"\s+", " ", str(val or "").strip().lower())


def grade_objective_answer(question, answer):
    """Grade an objective QuestionBankItem answer; returns (is_correct, marks_awarded)."""
    qtype = question.question_type
    key = question.correct_answer or {}
    given = answer.answer_data or {}

    if qtype in ("MCQ", "TRUE_FALSE"):
        expected = _normalize_text(key.get("answer"))
        student_val = _normalize_text(given.get("answer") or answer.text_answer)
        ok = bool(expected and student_val == expected)
        return ok, (question.marks if ok else 0.0)

    if qtype == "MULTI_SELECT":
        expected_set = {_normalize_text(x) for x in (key.get("answers") or []) if _normalize_text(x)}
        given_set = {_normalize_text(x) for x in (given.get("answers") or []) if _normalize_text(x)}
        if not expected_set:
            return False, 0.0
        ok = expected_set == given_set
        if ok:
            return True, question.marks
        # Partial credit when no wrong choices selected
        if given_set and given_set.issubset(expected_set):
            partial = round(question.marks * (len(given_set) / len(expected_set)), 2)
            return False, partial
        return False, 0.0

    if qtype == "FILL_BLANK":
        accepted = key.get("answers") or ([key.get("answer")] if key.get("answer") else [])
        accepted_norm = {_normalize_text(x) for x in accepted if _normalize_text(x)}
        student_val = _normalize_text(given.get("answer") or answer.text_answer)
        ok = bool(student_val and student_val in accepted_norm)
        return ok, (question.marks if ok else 0.0)

    if qtype == "MATCHING":
        pairs = key.get("pairs") or {}
        student_pairs = given.get("pairs") or {}
        if not pairs:
            return False, 0.0
        correct_pairs = sum(
            1 for left, right in pairs.items()
            if _normalize_text(student_pairs.get(left)) == _normalize_text(right)
        )
        ratio = correct_pairs / len(pairs)
        return ratio == 1.0, round(question.marks * ratio, 2)

    return None, None


def finalize_and_grade_attempt(attempt, timed_out=False):
    """Auto-grade all objective questions on an AssessmentAttempt and update totals."""
    assessment = attempt.assessment
    questions = list(assessment.questions.all())
    answers_by_q = {
        a.question_id: a for a in attempt.answers.select_related("question").all()
    }

    obj_score = 0.0
    subj_score = 0.0
    has_ungraded_subjective = False

    for q in questions:
        ans = answers_by_q.get(q.id)
        if not ans:
            ans = AssessmentAnswer.objects.create(attempt=attempt, question=q)

        if q.is_objective:
            is_ok, marks = grade_objective_answer(q, ans)
            ans.is_correct = is_ok
            ans.marks_awarded = marks if marks is not None else 0.0
            ans.save(update_fields=["is_correct", "marks_awarded"])
            obj_score += ans.marks_awarded
        else:
            if ans.marks_awarded is not None:
                subj_score += float(ans.marks_awarded)
            else:
                has_ungraded_subjective = True

    total_possible = assessment.total_marks or sum(q.marks for q in questions) or 100.0
    attempt.objective_score = round(obj_score, 2)
    attempt.subjective_score = round(subj_score, 2)
    attempt.total_score = round(obj_score + subj_score, 2)
    attempt.percentage = round((attempt.total_score / total_possible) * 100.0, 1) if total_possible else 0.0
    attempt.passed = attempt.total_score >= (assessment.pass_marks if assessment.pass_marks <= total_possible else (total_possible * 0.5))
    attempt.submitted_at = attempt.submitted_at or timezone.now()

    if has_ungraded_subjective:
        attempt.status = "TIMED_OUT" if timed_out else "SUBMITTED"
    else:
        attempt.status = "GRADED"
        attempt.graded_at = timezone.now()

    log = list(attempt.audit_log or [])
    log.append({
        "event": "AUTO_TIMED_OUT" if timed_out else "SUBMITTED",
        "at": timezone.now().isoformat(),
        "objective_score": attempt.objective_score,
    })
    attempt.audit_log = log[-40:]
    attempt.save()
    return attempt


# ---------------------------------------------------------------------------
# MEANINGFUL LEARNING PROGRESS & STORAGE ANALYTICS
# ---------------------------------------------------------------------------

def compute_student_learning_progress(student):
    """
    Calculate meaningful progress per subject and overall from:
    - Completed learning materials (25%)
    - Submitted/graded assignments (35%)
    - Completed online quizzes/assessments (25%)
    - Virtual/in-person attendance rate (15%)
    """
    if not student or not student.course_id:
        return {"overall_percent": 0.0, "subjects": [], "learning_hours": 0.0}

    subjects = Subject.objects.filter(course_id=student.course_id).select_related("staff__admin")
    subject_rows = []
    total_pct_sum = 0.0

    for subj in subjects:
        mats = LearningMaterial.objects.filter(subject=subj, is_published=True)
        mat_total = mats.count()
        mat_done = MaterialAccessLog.objects.filter(
            student=student, material__in=mats, is_completed=True
        ).count()

        assigns = Assignment.objects.filter(subject=subj, is_published=True)
        assign_total = assigns.count()
        assign_done = AssignmentSubmission.objects.filter(
            student=student,
            assignment__in=assigns,
            status__in=["TURNED_IN", "LATE", "GRADED"],
        ).count()

        quizzes = OnlineAssessment.objects.filter(subject=subj, is_published=True)
        quiz_total = quizzes.count()
        quiz_done = (
            AssessmentAttempt.objects.filter(
                student=student,
                assessment__in=quizzes,
                status__in=["SUBMITTED", "GRADED", "TIMED_OUT"],
            )
            .values("assessment_id")
            .distinct()
            .count()
        )
        avg_quiz = (
            AssessmentAttempt.objects.filter(
                student=student,
                assessment__in=quizzes,
                status="GRADED",
            ).aggregate(avg=Avg("percentage"))["avg"]
            or 0.0
        )

        att_reports = AttendanceReport.objects.filter(
            student=student, attendance__subject=subj
        )
        att_total = att_reports.count()
        att_present = att_reports.filter(status=True).count()
        att_pct = (att_present / att_total * 100.0) if att_total else 0.0

        # Weighted meaningful progress across active components
        weights = []
        if mat_total:
            weights.append((0.25, (mat_done / mat_total) * 100.0))
        if assign_total:
            weights.append((0.35, (assign_done / assign_total) * 100.0))
        if quiz_total:
            weights.append((0.25, (quiz_done / quiz_total) * 100.0))
        if att_total:
            weights.append((0.15, att_pct))

        if weights:
            w_sum = sum(w for w, _ in weights)
            subj_pct = round(sum((w / w_sum) * val for w, val in weights), 1)
        else:
            subj_pct = 0.0

        topics_total = LearningTopic.objects.filter(subject=subj, is_published=True).count()
        total_pct_sum += subj_pct
        subject_rows.append({
            "subject": subj,
            "topics_total": topics_total,
            "materials_done": mat_done,
            "materials_total": mat_total,
            "assignments_done": assign_done,
            "assignments_total": assign_total,
            "quizzes_done": quiz_done,
            "quizzes_total": quiz_total,
            "avg_quiz_percent": round(avg_quiz, 1),
            "attendance_percent": round(att_pct, 1),
            "progress_percent": subj_pct,
        })

    overall = round(total_pct_sum / len(subject_rows), 1) if subject_rows else 0.0
    virtual_secs = (
        VirtualAttendanceRecord.objects.filter(student=student).aggregate(
            s=Sum("active_seconds")
        )["s"]
        or 0
    )
    material_visits = (
        MaterialAccessLog.objects.filter(student=student).aggregate(
            c=Sum("access_count")
        )["c"]
        or 0
    )
    # Conservative estimate: actual virtual class seconds + 12 min per material study visit
    learning_hours = round((virtual_secs / 3600.0) + (material_visits * 0.2), 1)

    return {
        "overall_percent": overall,
        "subjects": subject_rows,
        "learning_hours": learning_hours,
    }


def compute_storage_usage_mb():
    """Calculate total eLearning material and recording storage in MB."""
    mat_bytes = LearningMaterial.objects.aggregate(s=Sum("file_size_bytes"))["s"] or 0
    rec_bytes = LectureRecording.objects.aggregate(s=Sum("file_size_bytes"))["s"] or 0
    return {
        "materials_mb": round(mat_bytes / (1024 * 1024), 2),
        "recordings_mb": round(rec_bytes / (1024 * 1024), 2),
        "total_mb": round((mat_bytes + rec_bytes) / (1024 * 1024), 2),
    }


# ---------------------------------------------------------------------------
# AI LEARNING ASSISTANT (Safe, Curriculum-Grounded, Optional LLM Upgrade)
# ---------------------------------------------------------------------------

def _strip_html(text):
    return re.sub(r"<[^>]+>", " ", str(text or "")).strip()


def _gather_subject_context(subject, topic=None):
    """Collect ONLY authorized curriculum content from the selected subject/topic."""
    if not subject:
        return {"topics": [], "materials": [], "context_text": ""}
    topics_qs = LearningTopic.objects.filter(subject=subject, is_published=True)
    if topic:
        topics_qs = topics_qs.filter(id=topic.id)
    mats_qs = LearningMaterial.objects.filter(subject=subject, is_published=True)
    if topic:
        mats_qs = mats_qs.filter(topic=topic)

    topics_list = list(topics_qs[:8])
    mats_list = list(mats_qs[:10])
    chunks = [f"Subject: {subject.name} ({subject.course.name})"]
    for t in topics_list:
        chunks.append(f"Topic: {t.title}. Objectives: {t.learning_objectives}. Summary: {t.description}")
    for m in mats_list:
        note_snippet = _strip_html(m.rich_content)[:600]
        chunks.append(f"Material: {m.title} ({m.get_material_type_display()}) — {m.description} {note_snippet}")
    return {
        "topics": topics_list,
        "materials": mats_list,
        "context_text": "\n".join(chunks)[:5000],
    }


def _call_optional_llm(system_prompt, user_prompt):
    """If OPENAI_API_KEY is configured, invoke it safely; otherwise return None to use local engine."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import requests
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.4,
                "max_tokens": 900,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
    return None


def run_ai_learning_assistant(user, mode, prompt, subject=None, topic=None):
    """
    Generate safe, secondary-school-appropriate AI learning assistance grounded in
    authorized SGS curriculum records. Enforces teacher-review flags on drafts.
    """
    role = get_user_role(user)
    teacher_modes = {"LESSON_PLAN", "GENERATE_QUIZ", "GENERATE_ASSIGNMENT", "CLASS_ANALYTICS"}
    if mode in teacher_modes and role not in ("1", "2", "4", "6"):
        mode = "ASK"

    ctx = _gather_subject_context(subject, topic)
    subj_name = subject.name if subject else "Secondary School Curriculum"
    class_name = subject.course.name if subject else "S1–S4"
    topic_title = topic.title if topic else (ctx["topics"][0].title if ctx["topics"] else prompt[:60] or "Core Concepts")
    requires_review = mode in ("GENERATE_QUIZ", "GENERATE_ASSIGNMENT")

    system_prompt = (
        "You are the JASS Secondary School AI Learning Assistant for S1-S4 students and teachers. "
        "Stay age-appropriate, pedagogically sound, concise, and grounded in the authorized course materials provided. "
        "Never fabricate student grades or reveal confidential records."
    )
    llm_input = f"Mode: {mode}\nClass: {class_name}\nSubject: {subj_name}\nTopic: {topic_title}\n"
    if ctx["context_text"]:
        llm_input += f"\nAuthorized Course Materials:\n{ctx['context_text']}\n"
    llm_input += f"\nRequest: {prompt}"

    llm_answer = _call_optional_llm(system_prompt, llm_input)
    structured = {}

    if llm_answer:
        response_text = llm_answer
    else:
        # Grounded curriculum-aware generator (zero external dependency failure)
        material_titles = [m.title for m in ctx["materials"][:5]]
        topic_titles = [t.title for t in ctx["topics"][:6]]
        objectives = [t.learning_objectives for t in ctx["topics"] if t.learning_objectives]
        mat_bullets = "\n".join(f"- {t}" for t in material_titles) or "- Teacher-uploaded notes and textbook chapters"
        topic_bullets = "\n".join(f"- {t}" for t in topic_titles) or f"- {topic_title}"

        if mode == "SUMMARIZE":
            snippets = []
            for m in ctx["materials"][:4]:
                clean = _strip_html(m.rich_content) or m.description
                if clean:
                    snippets.append(f"**{m.title}**: {clean[:280]}")
            summary_body = "\n\n".join(snippets) or (
                f"Focus on the core principles of **{topic_title}** in **{subj_name} ({class_name})**, "
                "connecting definitions, worked examples, and real-world applications."
            )
            response_text = (
                f"### Material Summary — {subj_name} ({topic_title})\n\n"
                f"{summary_body}\n\n"
                f"**Key Resources Referenced:**\n{mat_bullets}\n\n"
                f"**Quick Check:** Try explaining {topic_title} in your own words without looking at your notes."
            )

        elif mode == "REVISION_NOTES":
            obj_line = objectives[0] if objectives else f"Understand key definitions, formulas/rules, and problem-solving methods in {topic_title}."
            response_text = (
                f"### Revision Notes — {subj_name}: {topic_title} ({class_name})\n\n"
                f"1. **Learning Objectives**\n   {obj_line}\n\n"
                f"2. **Core Concepts Covered**\n{topic_bullets}\n\n"
                f"3. **Authorized Study Materials**\n{mat_bullets}\n\n"
                f"4. **Exam Tips**\n"
                f"   - Review worked examples step by step.\n"
                f"   - Memorize key terminology and show all working clearly.\n"
                f"   - Attempt at least 5 timed practice questions on {topic_title}."
            )

        elif mode == "PRACTICE_QUESTIONS":
            questions = [
                f"1. Define the core concepts of **{topic_title}** in {subj_name} and give one practical example.",
                f"2. Explain two common mistakes students make when solving problems in **{topic_title}**.",
                f"3. Compare and contrast two key sub-concepts covered under **{topic_title}**.",
                f"4. Apply what you learned in **{topic_title}** to solve a multi-step examination question.",
                f"5. True or False (with justification): Every principle in **{topic_title}** applies under standard conditions.",
            ]
            structured["practice_questions"] = questions
            response_text = (
                f"### Practice Questions — {subj_name} ({topic_title})\n\n"
                + "\n".join(questions)
                + "\n\n*Tip: Write your answers in your notebook first, then cross-check against your uploaded class notes.*"
            )

        elif mode == "STUDY_PLAN":
            plan_topics = topic_titles or [topic_title, "Past Paper Practice", "Concept Review"]
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
            schedule = []
            for idx, day in enumerate(days):
                t_name = plan_topics[idx % len(plan_topics)]
                activity = (
                    "Read lecture notes & make flashcards (35 min)"
                    if idx % 2 == 0
                    else "Timed practice questions & review errors (40 min)"
                )
                schedule.append(f"- **{day}**: {subj_name} — *{t_name}* ({activity})")
            response_text = (
                f"### 6-Day Revision Schedule — {subj_name} ({class_name})\n\n"
                + "\n".join(schedule)
                + "\n- **Sunday**: Light recap & rest before the new school week."
            )

        elif mode == "LESSON_PLAN":
            response_text = (
                f"### Structured Lesson Plan — {subj_name} ({class_name})\n"
                f"**Topic:** {topic_title} | **Duration:** 45 Minutes\n\n"
                f"**1. Learning Objectives**\n"
                f"- Define and explain the foundational concepts of {topic_title}.\n"
                f"- Demonstrate guided mastery through class examples.\n"
                f"- Complete an exit check independently.\n\n"
                f"**2. Lesson Flow**\n"
                f"- **0–5 min (Starter):** Recap prior knowledge & pose an engaging warm-up question.\n"
                f"- **5–20 min (Direct Instruction):** Present core theory using whiteboard/slides.\n"
                f"- **20–35 min (Guided Practice):** Group/individual exercises with live Q&A.\n"
                f"- **35–42 min (Assessment Check):** 3-question quick quiz or whiteboard poll.\n"
                f"- **42–45 min (Plenary & Homework):** Summarize key takeaways & assign follow-up work.\n\n"
                f"**3. Supporting Materials**\n{mat_bullets}"
            )

        elif mode == "GENERATE_QUIZ":
            draft_questions = [
                {
                    "question_type": "MCQ",
                    "prompt": f"Which statement best describes a primary principle of {topic_title} in {subj_name}?",
                    "choices": [
                        f"Core verified concept of {topic_title}",
                        "Unrelated secondary assumption",
                        "Outdated contradiction",
                        "None of the above",
                    ],
                    "correct_answer": {"answer": f"Core verified concept of {topic_title}"},
                    "marks": 2,
                },
                {
                    "question_type": "TRUE_FALSE",
                    "prompt": f"Understanding {topic_title} builds directly on foundational {subj_name} concepts.",
                    "choices": ["True", "False"],
                    "correct_answer": {"answer": "True"},
                    "marks": 1,
                },
                {
                    "question_type": "SHORT_ANSWER",
                    "prompt": f"Explain in 3–4 sentences how {topic_title} is applied in real-world {subj_name} problems.",
                    "choices": [],
                    "correct_answer": {},
                    "marking_guide": "Award 1 mark for accurate definition, 2 marks for clear explanation, 1 mark for valid example.",
                    "marks": 4,
                },
            ]
            structured["draft_questions"] = draft_questions
            response_text = (
                f"### AI Draft Quiz — {subj_name}: {topic_title} (Requires Teacher Review)\n\n"
                "1. **[MCQ — 2 marks]** " + draft_questions[0]["prompt"] + "\n"
                "2. **[True/False — 1 mark]** " + draft_questions[1]["prompt"] + "\n"
                "3. **[Short Answer — 4 marks]** " + draft_questions[2]["prompt"] + "\n"
                f"   *Marking Guide:* {draft_questions[2]['marking_guide']}\n\n"
                "> **Safety Notice:** This AI draft requires teacher review and editing before publishing to students."
            )

        elif mode == "GENERATE_ASSIGNMENT":
            response_text = (
                f"### AI Draft Assignment & Marking Rubric — {subj_name}: {topic_title} (Requires Teacher Review)\n\n"
                f"**Title:** Applied Mastery Task — {topic_title}\n"
                f"**Instructions:** Write a structured response analyzing {topic_title} ({prompt or 'standard curriculum scope'}). Include clear steps, diagrams/examples where relevant, and original explanations in your own words.\n\n"
                f"**Marking Rubric (20 Marks):**\n"
                f"- Conceptual Accuracy & Terminology: **6 marks**\n"
                f"- Method / Step-by-Step Reasoning: **6 marks**\n"
                f"- Applied Examples & Depth: **5 marks**\n"
                f"- Clarity, Organization & Originality: **3 marks**\n\n"
                "> **Safety Notice:** Review and customize before creating the published assignment."
            )

        elif mode == "CLASS_ANALYTICS" and subject:
            avg_res = StudentResult.objects.filter(subject=subject).aggregate(
                t=Avg("test"), e=Avg("exam")
            )
            avg_quiz = AssessmentAttempt.objects.filter(
                assessment__subject=subject, status="GRADED"
            ).aggregate(a=Avg("percentage"))["a"] or 0.0
            att_total = AttendanceReport.objects.filter(attendance__subject=subject).count()
            att_pres = AttendanceReport.objects.filter(attendance__subject=subject, status=True).count()
            att_rate = round((att_pres / att_total * 100.0), 1) if att_total else 0.0
            assign_count = Assignment.objects.filter(subject=subject).count()
            sub_count = AssignmentSubmission.objects.filter(
                assignment__subject=subject, status__in=["TURNED_IN", "LATE", "GRADED"]
            ).count()
            response_text = (
                f"### Class Performance Insights — {subject.name} ({subject.course.name})\n\n"
                f"- **Attendance Rate:** {att_rate}% ({att_pres}/{att_total} records present)\n"
                f"- **Average Test / Exam Score:** {round(avg_res['t'] or 0, 1)} / {round(avg_res['e'] or 0, 1)}\n"
                f"- **Online Quiz Average:** {round(avg_quiz, 1)}%\n"
                f"- **Assignments Published / Submissions:** {assign_count} / {sub_count}\n\n"
                f"**Recommended Support Actions:**\n"
                f"- Schedule a targeted revision virtual class for students below 50%.\n"
                f"- Upload worked-example PDF notes under the most challenging topic.\n"
                f"- Follow up on irregular attendance via the AI Attendance Tracker."
            )

        else:
            response_text = (
                f"### Guided Explanation — {subj_name} ({topic_title})\n\n"
                f"Great question: *\"{prompt}\"*\n\n"
                f"1. **Core Idea:** Break down **{topic_title}** by identifying the key definitions and rules from your {class_name} {subj_name} syllabus.\n"
                f"2. **Step-by-Step Approach:** Read the problem carefully, list known facts, apply the relevant principle/formula, and verify your final answer.\n"
                f"3. **Authorized Class Resources to Review:**\n{mat_bullets}\n\n"
                f"*Need more help? Try switching to **Practice Questions** or **Revision Notes**, or post a question in your subject's Discussion Forum for your teacher.*"
            )

    interaction = AILearningInteraction.objects.create(
        user=user,
        subject=subject,
        topic=topic,
        mode=mode,
        prompt=prompt,
        response=response_text,
        structured_payload=structured,
        requires_teacher_review=requires_review,
        teacher_approved=not requires_review,
    )
    return interaction
