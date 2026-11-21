"""
AI Attendance Tracking & Admin Notification Engine.

Monitors attendance behavior for both students and teachers using
multi-factor risk scoring, trend/anomaly detection, consecutive-absence
tracking, same-day class-skip detection, punctuality analysis, and
automatic Admin notifications.
"""

from collections import Counter
from datetime import date as date_cls, time as time_cls, timedelta
import math

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .attendance_audit import audit_student_attendance
from .models import (
    AIAttendanceNotification,
    Attendance,
    AttendanceReport,
    LeaveReportStaff,
    LeaveReportStudent,
    Staff,
    Student,
    Subject,
    TeacherAttendance,
)

# Configurable AI thresholds
TEACHER_LATE_CUTOFF = time_cls(8, 15)
STUDENT_CHRONIC_ABSENCE_THRESHOLD = 75.0
STUDENT_CRITICAL_ABSENCE_THRESHOLD = 60.0
TEACHER_CHRONIC_ABSENCE_THRESHOLD = 85.0
ATTENDANCE_DROP_DELTA_THRESHOLD = 20.0
CONSECUTIVE_ABSENCE_ALERT_MIN = 2
TARDY_RATE_ALERT_THRESHOLD = 25.0


def _coerce_date(value):
    """Normalize a date, datetime, or ISO date string to a datetime.date."""
    if value is None:
        return timezone.localdate()
    if isinstance(value, date_cls):
        return value.date() if hasattr(value, "hour") else value
    if isinstance(value, str):
        try:
            return date_cls.fromisoformat(value.strip())
        except ValueError:
            return timezone.localdate()
    return timezone.localdate()


def _normalize_report_status(report):
    """Return canonical status string for an AttendanceReport."""
    status = (report.attendance_status or "").upper()
    if status and status != "PENDING":
        return status
    return "PRESENT" if report.status else "ABSENT"


def _classify_severity(risk_score):
    if risk_score >= 75:
        return "CRITICAL"
    if risk_score >= 55:
        return "HIGH"
    if risk_score >= 30:
        return "MEDIUM"
    return "LOW"


def _upsert_ai_notification(
    *,
    target_type,
    alert_type,
    reference_date,
    severity,
    risk_score,
    confidence,
    title,
    message,
    recommendation,
    metrics,
    student=None,
    staff=None,
):
    """Create or update an unresolved notification without creating duplicates."""
    lookup = {
        "target_type": target_type,
        "student": student,
        "staff": staff,
        "alert_type": alert_type,
        "reference_date": reference_date,
        "is_resolved": False,
    }
    existing = AIAttendanceNotification.objects.filter(**lookup).first()
    if existing:
        existing.severity = severity
        existing.risk_score = round(risk_score, 1)
        existing.confidence = round(confidence, 2)
        existing.title = title
        existing.message = message
        existing.recommendation = recommendation
        existing.metrics = metrics
        existing.save(update_fields=[
            "severity", "risk_score", "confidence", "title",
            "message", "recommendation", "metrics", "updated_at",
        ])
        return existing, False

    obj = AIAttendanceNotification.objects.create(
        target_type=target_type,
        student=student,
        staff=staff,
        alert_type=alert_type,
        severity=severity,
        risk_score=round(risk_score, 1),
        confidence=round(confidence, 2),
        title=title,
        message=message,
        recommendation=recommendation,
        metrics=metrics,
        reference_date=reference_date,
    )
    return obj, True


def _student_has_approved_leave(student, target_date):
    date_str = target_date.isoformat()
    return LeaveReportStudent.objects.filter(
        student=student,
        status=1,
    ).filter(
        Q(date=date_str)
        | Q(start_date__lte=target_date, end_date__gte=target_date)
    ).exists()


def _teacher_has_approved_leave(staff, target_date):
    date_str = target_date.isoformat()
    return LeaveReportStaff.objects.filter(
        staff=staff,
        status=1,
    ).filter(
        Q(date=date_str)
        | Q(start_date__lte=target_date, end_date__gte=target_date)
    ).exists()


def _compute_class_baseline(course):
    """Compute mean and std-dev attendance rate for students in a course."""
    if not course:
        return None, None
    rates = []
    for stu in Student.objects.filter(course=course):
        reps = list(AttendanceReport.objects.filter(student=stu))
        if not reps:
            continue
        present = sum(1 for r in reps if _normalize_report_status(r) in ("PRESENT", "TARDY", "EXCUSED"))
        rates.append((present / len(reps)) * 100.0)
    if len(rates) < 2:
        return (rates[0] if rates else None), 0.0
    mean = sum(rates) / len(rates)
    variance = sum((x - mean) ** 2 for x in rates) / len(rates)
    return mean, math.sqrt(variance)


def analyze_student_attendance(student, reference_date=None, create_alerts=True, actor=None):
    """
    Run AI behavioral & statistical analysis on a single student's attendance.
    Returns a detailed profile dict and optionally emits Admin notifications.
    """
    ref_date = _coerce_date(reference_date)
    reports = list(
        AttendanceReport.objects.filter(student=student)
        .select_related("attendance", "attendance__subject")
        .order_by("attendance__date", "id")
    )

    student_name = student.admin.get_full_name().strip() or str(student)
    course_name = student.course.name if student.course else "Unassigned"
    total = len(reports)

    if total == 0:
        return {
            "student": student,
            "name": student_name,
            "course": course_name,
            "total_records": 0,
            "present_count": 0,
            "absent_count": 0,
            "tardy_count": 0,
            "attendance_rate": 100.0,
            "recent_rate": 100.0,
            "trend_delta": 0.0,
            "trend_label": "No Data",
            "consecutive_absences": 0,
            "risk_score": 0.0,
            "risk_level": "LOW",
            "top_absent_day": None,
            "insight": "No attendance sessions recorded yet.",
            "alerts_created": [],
        }

    statuses = [_normalize_report_status(r) for r in reports]
    present_count = sum(1 for s in statuses if s in ("PRESENT", "TARDY"))
    excused_count = sum(1 for s in statuses if s in ("EXCUSED", "DISMISSED"))
    tardy_count = sum(1 for s in statuses if s == "TARDY")
    absent_count = sum(1 for s in statuses if s == "ABSENT")

    effective_present = present_count + excused_count
    attendance_rate = (effective_present / total) * 100.0
    tardy_rate = (tardy_count / total) * 100.0

    # Consecutive absences at the tail of history
    consecutive_absences = 0
    for s in reversed(statuses):
        if s == "ABSENT":
            consecutive_absences += 1
        else:
            break

    # Recent vs historical trend (last 5 sessions vs prior sessions)
    window = min(5, max(2, total // 2))
    recent_slice = statuses[-window:]
    older_slice = statuses[:-window]
    recent_rate = (
        sum(1 for s in recent_slice if s in ("PRESENT", "TARDY", "EXCUSED")) / len(recent_slice)
    ) * 100.0
    if older_slice:
        older_rate = (
            sum(1 for s in older_slice if s in ("PRESENT", "TARDY", "EXCUSED")) / len(older_slice)
        ) * 100.0
        trend_delta = recent_rate - older_rate
    else:
        trend_delta = 0.0

    if trend_delta <= -10:
        trend_label = "Declining"
    elif trend_delta >= 10:
        trend_label = "Improving"
    else:
        trend_label = "Stable"

    # Day-of-week absence pattern
    absent_days = Counter(
        r.attendance.date.strftime("%A")
        for r, s in zip(reports, statuses)
        if s == "ABSENT" and r.attendance and r.attendance.date
    )
    top_absent_day = None
    if absent_days:
        day_name, day_count = absent_days.most_common(1)[0]
        if day_count >= 2:
            top_absent_day = f"{day_name} ({day_count}x)"

    # Same-day class-skip anomaly detection (works with OR without period_number)
    same_day_reports = [
        (r, _normalize_report_status(r))
        for r in reports
        if r.attendance and r.attendance.date == ref_date
    ]
    same_day_present = [r for r, s in same_day_reports if s in ("PRESENT", "TARDY")]
    same_day_absent = [r for r, s in same_day_reports if s == "ABSENT"]
    same_day_skip = (
        bool(same_day_present)
        and bool(same_day_absent)
        and not _student_has_approved_leave(student, ref_date)
    )

    # Also invoke period-based audit engine for formal audit log compatibility
    if create_alerts and same_day_reports:
        try:
            audit_student_attendance(student, ref_date, actor)
        except Exception:
            pass

    # Class baseline z-score
    class_mean, class_std = _compute_class_baseline(student.course)
    z_score = 0.0
    if class_mean is not None and class_std and class_std > 0:
        z_score = (attendance_rate - class_mean) / class_std

    # Multi-factor AI Risk Score (0 - 100)
    risk = 0.0
    if attendance_rate < 100.0:
        risk += min(45.0, max(0.0, (100.0 - attendance_rate) * 0.9))
    risk += min(30.0, consecutive_absences * 12.0)
    if trend_delta < -10.0:
        risk += min(15.0, abs(trend_delta) * 0.4)
    if tardy_rate >= TARDY_RATE_ALERT_THRESHOLD:
        risk += min(10.0, tardy_rate * 0.25)
    if same_day_skip:
        risk += 22.0
    if z_score <= -1.5:
        risk += 8.0
    risk_score = round(min(100.0, risk), 1)
    risk_level = _classify_severity(risk_score)

    # Build human-readable AI insight
    reasons = []
    if same_day_skip:
        missed_subjects = ", ".join(r.attendance.subject.name for r in same_day_absent)
        reasons.append(f"attended earlier class(es) on {ref_date} but missed {missed_subjects}")
    if consecutive_absences >= CONSECUTIVE_ABSENCE_ALERT_MIN:
        reasons.append(f"{consecutive_absences} consecutive absences")
    if attendance_rate < STUDENT_CHRONIC_ABSENCE_THRESHOLD:
        reasons.append(f"overall attendance at {attendance_rate:.1f}% (below {STUDENT_CHRONIC_ABSENCE_THRESHOLD:.0f}% target)")
    if trend_delta <= -ATTENDANCE_DROP_DELTA_THRESHOLD:
        reasons.append(f"recent attendance dropped by {abs(trend_delta):.1f}%")
    if tardy_rate >= TARDY_RATE_ALERT_THRESHOLD and tardy_count >= 2:
        reasons.append(f"tardy in {tardy_rate:.0f}% of sessions")
    if top_absent_day:
        reasons.append(f"recurring absences on {top_absent_day}")

    if reasons:
        insight = "AI detected: " + "; ".join(reasons) + "."
    else:
        insight = f"Healthy attendance pattern ({attendance_rate:.1f}% across {total} sessions)."

    metrics = {
        "attendance_rate": round(attendance_rate, 1),
        "recent_rate": round(recent_rate, 1),
        "trend_delta": round(trend_delta, 1),
        "consecutive_absences": consecutive_absences,
        "total_records": total,
        "absent_count": absent_count,
        "tardy_count": tardy_count,
        "z_score": round(z_score, 2),
        "top_absent_day": top_absent_day,
    }

    alerts_created = []
    if create_alerts:
        confidence = min(0.98, 0.72 + min(total, 10) * 0.025)

        if same_day_skip:
            missed = ", ".join(r.attendance.subject.name for r in same_day_absent)
            present_subj = ", ".join(r.attendance.subject.name for r in same_day_present)
            alert, created = _upsert_ai_notification(
                target_type="STUDENT",
                student=student,
                alert_type="CLASS_SKIP",
                reference_date=ref_date,
                severity="HIGH" if risk_score < 75 else "CRITICAL",
                risk_score=max(risk_score, 68.0),
                confidence=0.94,
                title=f"Potential Class Skip: {student_name}",
                message=(
                    f"{student_name} ({course_name}) was marked present in {present_subj} "
                    f"on {ref_date}, but absent in {missed} with no approved leave."
                ),
                recommendation=(
                    "Verify attendance with the subject teacher and check with the class teacher "
                    "or parent/guardian before taking any action."
                ),
                metrics=metrics,
            )
            alerts_created.append((alert, created))

        if consecutive_absences >= CONSECUTIVE_ABSENCE_ALERT_MIN:
            sev = "CRITICAL" if consecutive_absences >= 3 else "HIGH"
            alert, created = _upsert_ai_notification(
                target_type="STUDENT",
                student=student,
                alert_type="CONSECUTIVE_ABSENCE",
                reference_date=ref_date,
                severity=sev,
                risk_score=max(risk_score, 60.0 if consecutive_absences == 2 else 80.0),
                confidence=confidence,
                title=f"Consecutive Absences ({consecutive_absences}x): {student_name}",
                message=(
                    f"{student_name} ({course_name}) has missed {consecutive_absences} "
                    f"consecutive attendance sessions. Overall attendance is {attendance_rate:.1f}%."
                ),
                recommendation=(
                    "Contact the student's parent/guardian and alert the class teacher "
                    "to check on the student's welfare."
                ),
                metrics=metrics,
            )
            alerts_created.append((alert, created))

        if total >= 3 and attendance_rate < STUDENT_CHRONIC_ABSENCE_THRESHOLD:
            sev = "CRITICAL" if attendance_rate < STUDENT_CRITICAL_ABSENCE_THRESHOLD else "HIGH"
            alert, created = _upsert_ai_notification(
                target_type="STUDENT",
                student=student,
                alert_type="CHRONIC_ABSENCE",
                reference_date=ref_date,
                severity=sev,
                risk_score=max(risk_score, 65.0 if sev == "HIGH" else 82.0),
                confidence=confidence,
                title=f"Chronic Absence Risk: {student_name} ({attendance_rate:.0f}%)",
                message=(
                    f"{student_name} ({course_name}) has an attendance rate of {attendance_rate:.1f}% "
                    f"({absent_count} absences out of {total} sessions), below the "
                    f"{STUDENT_CHRONIC_ABSENCE_THRESHOLD:.0f}% school policy threshold."
                ),
                recommendation=(
                    "Schedule an academic support check-in with the student, class teacher, "
                    "and guardian to address recurring absences."
                ),
                metrics=metrics,
            )
            alerts_created.append((alert, created))

        if total >= 4 and trend_delta <= -ATTENDANCE_DROP_DELTA_THRESHOLD:
            alert, created = _upsert_ai_notification(
                target_type="STUDENT",
                student=student,
                alert_type="ATTENDANCE_DROP",
                reference_date=ref_date,
                severity="MEDIUM" if risk_score < 55 else "HIGH",
                risk_score=max(risk_score, 45.0),
                confidence=confidence,
                title=f"Sudden Attendance Drop: {student_name}",
                message=(
                    f"AI trend monitor detected a {abs(trend_delta):.1f}% drop in "
                    f"{student_name}'s recent attendance ({recent_rate:.1f}% vs prior baseline)."
                ),
                recommendation=(
                    "Follow up early with the class teacher before this trend becomes chronic absenteeism."
                ),
                metrics=metrics,
            )
            alerts_created.append((alert, created))

        if total >= 3 and tardy_count >= 2 and tardy_rate >= TARDY_RATE_ALERT_THRESHOLD:
            alert, created = _upsert_ai_notification(
                target_type="STUDENT",
                student=student,
                alert_type="TARDINESS_PATTERN",
                reference_date=ref_date,
                severity="MEDIUM",
                risk_score=max(risk_score, 38.0),
                confidence=confidence,
                title=f"Frequent Tardiness: {student_name}",
                message=(
                    f"{student_name} ({course_name}) was marked tardy {tardy_count} times "
                    f"({tardy_rate:.1f}% of recorded sessions)."
                ),
                recommendation="Review morning arrival times with the student and guardian.",
                metrics=metrics,
            )
            alerts_created.append((alert, created))

    return {
        "student": student,
        "name": student_name,
        "course": course_name,
        "total_records": total,
        "present_count": present_count,
        "absent_count": absent_count,
        "tardy_count": tardy_count,
        "attendance_rate": round(attendance_rate, 1),
        "recent_rate": round(recent_rate, 1),
        "trend_delta": round(trend_delta, 1),
        "trend_label": trend_label,
        "consecutive_absences": consecutive_absences,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "top_absent_day": top_absent_day,
        "insight": insight,
        "alerts_created": alerts_created,
    }


def auto_record_teacher_activity(staff, attendance_date):
    """
    Automatically record teacher attendance as PRESENT when they submit
    class attendance, if they haven't already checked in for that date.
    """
    if not staff or not attendance_date:
        return None
    if isinstance(attendance_date, str):
        try:
            attendance_date = date_cls.fromisoformat(attendance_date)
        except ValueError:
            return None
    now_time = timezone.localtime().time().replace(microsecond=0)
    record, created = TeacherAttendance.objects.get_or_create(
        staff=staff,
        date=attendance_date,
        defaults={
            "status": "PRESENT",
            "check_in_time": now_time if attendance_date == timezone.localdate() else None,
            "source": "AUTO_ACTIVITY",
            "notes": "Auto-tracked from submitted student class attendance.",
        },
    )
    return record


def analyze_teacher_attendance(staff, reference_date=None, create_alerts=True):
    """
    Run AI attendance & punctuality analysis for a teacher (Staff).
    Combines TeacherAttendance logs, approved LeaveReportStaff, and
    class attendance submission activity.
    """
    ref_date = _coerce_date(reference_date)
    staff_name = staff.admin.get_full_name().strip() or str(staff)
    course_name = staff.course.name if staff.course else "General"
    subjects = list(Subject.objects.filter(staff=staff))
    subject_names = [s.name for s in subjects]

    records = list(
        TeacherAttendance.objects.filter(staff=staff).order_by("date", "id")
    )
    total = len(records)

    # Also check how many class attendance sessions this teacher logged in the last 14 days
    recent_window_start = ref_date - timedelta(days=14)
    classes_logged_recent = Attendance.objects.filter(
        subject__staff=staff,
        date__gte=recent_window_start,
        date__lte=ref_date,
    ).count()
    classes_logged_today = Attendance.objects.filter(
        subject__staff=staff,
        date=ref_date,
    ).count()

    today_record = next((r for r in records if r.date == ref_date), None)
    approved_leave_today = _teacher_has_approved_leave(staff, ref_date)

    # Check if any other teacher logged attendance on ref_date (indicates a school day)
    school_active_today = (
        Attendance.objects.filter(date=ref_date).exists()
        or TeacherAttendance.objects.filter(date=ref_date).exists()
    )

    present_count = sum(1 for r in records if r.status == "PRESENT")
    late_count = sum(1 for r in records if r.status == "LATE")
    leave_count = sum(1 for r in records if r.status == "ON_LEAVE")
    absent_count = sum(1 for r in records if r.status == "ABSENT")

    if total > 0:
        attendance_rate = ((present_count + late_count + leave_count) / total) * 100.0
        punctuality_rate = (present_count / max(1, present_count + late_count)) * 100.0
    else:
        attendance_rate = 100.0 if (classes_logged_recent > 0 or not school_active_today) else 0.0
        punctuality_rate = 100.0

    # Consecutive teacher absences
    consecutive_absences = 0
    for r in reversed(records):
        if r.status == "ABSENT":
            consecutive_absences += 1
        else:
            break

    unexcused_absent_today = (
        today_record is not None
        and today_record.status == "ABSENT"
        and not approved_leave_today
    )
    late_today = today_record is not None and today_record.status == "LATE"
    missed_logging_today = (
        school_active_today
        and bool(subjects)
        and today_record is None
        and classes_logged_today == 0
        and not approved_leave_today
    )

    # Multi-factor Teacher AI Risk Score (0 - 100)
    risk = 0.0
    if total > 0 and attendance_rate < 100.0:
        risk += min(45.0, (100.0 - attendance_rate) * 1.1)
    risk += min(30.0, consecutive_absences * 18.0)
    if unexcused_absent_today:
        risk += 35.0
    if late_today:
        risk += 18.0
    if late_count >= 2:
        risk += min(20.0, late_count * 7.0)
    if missed_logging_today:
        risk += 28.0
    risk_score = round(min(100.0, risk), 1)
    risk_level = _classify_severity(risk_score)

    reasons = []
    if unexcused_absent_today:
        reasons.append(f"marked ABSENT on {ref_date} without approved leave")
    if missed_logging_today:
        reasons.append(f"no check-in or class attendance recorded on active school day {ref_date}")
    if late_today:
        t_str = today_record.check_in_time.strftime("%H:%M") if today_record.check_in_time else "late"
        reasons.append(f"checked in late ({t_str}) on {ref_date}")
    if consecutive_absences >= 2:
        reasons.append(f"{consecutive_absences} consecutive absences")
    if total >= 3 and attendance_rate < TEACHER_CHRONIC_ABSENCE_THRESHOLD:
        reasons.append(f"attendance rate at {attendance_rate:.1f}% (below {TEACHER_CHRONIC_ABSENCE_THRESHOLD:.0f}% target)")
    if late_count >= 2:
        reasons.append(f"{late_count} late arrivals recorded")

    if reasons:
        insight = "AI flagged: " + "; ".join(reasons) + "."
    elif today_record:
        insight = f"Checked in ({today_record.get_status_display()}) on {ref_date}. Overall rate: {attendance_rate:.1f}%."
    elif approved_leave_today:
        insight = f"On approved leave for {ref_date}."
    else:
        insight = f"Good standing ({attendance_rate:.1f}% attendance, {classes_logged_recent} class logs in 14d)."

    metrics = {
        "attendance_rate": round(attendance_rate, 1),
        "punctuality_rate": round(punctuality_rate, 1),
        "total_records": total,
        "present_count": present_count,
        "late_count": late_count,
        "absent_count": absent_count,
        "leave_count": leave_count,
        "consecutive_absences": consecutive_absences,
        "classes_logged_recent": classes_logged_recent,
        "subjects": subject_names,
    }

    alerts_created = []
    if create_alerts:
        confidence = min(0.97, 0.78 + min(total, 8) * 0.02)

        if unexcused_absent_today:
            alert, created = _upsert_ai_notification(
                target_type="TEACHER",
                staff=staff,
                alert_type="TEACHER_ABSENT_UNEXCUSED",
                reference_date=ref_date,
                severity="CRITICAL" if consecutive_absences >= 2 else "HIGH",
                risk_score=max(risk_score, 72.0),
                confidence=0.95,
                title=f"Teacher Unexcused Absence: {staff_name}",
                message=(
                    f"Teacher {staff_name} ({course_name}) is marked ABSENT on {ref_date} "
                    f"with no approved leave request on file. Assigned subjects: "
                    f"{', '.join(subject_names) or 'None'}."
                ),
                recommendation=(
                    "Confirm teacher availability immediately and arrange substitute class coverage."
                ),
                metrics=metrics,
            )
            alerts_created.append((alert, created))

        if missed_logging_today:
            alert, created = _upsert_ai_notification(
                target_type="TEACHER",
                staff=staff,
                alert_type="TEACHER_MISSED_LOGGING",
                reference_date=ref_date,
                severity="MEDIUM",
                risk_score=max(risk_score, 42.0),
                confidence=0.84,
                title=f"Teacher Check-In & Class Log Missing: {staff_name}",
                message=(
                    f"Teacher {staff_name} ({course_name}) has neither checked in nor submitted "
                    f"class attendance for {', '.join(subject_names)} on {ref_date}, while other "
                    f"school attendance activity was recorded today."
                ),
                recommendation=(
                    "Follow up with the teacher to confirm attendance and remind them to log class attendance."
                ),
                metrics=metrics,
            )
            alerts_created.append((alert, created))

        if late_today or late_count >= 2:
            sev = "HIGH" if late_count >= 3 else "MEDIUM"
            t_str = (
                today_record.check_in_time.strftime("%H:%M")
                if today_record and today_record.check_in_time
                else "past cutoff"
            )
            alert, created = _upsert_ai_notification(
                target_type="TEACHER",
                staff=staff,
                alert_type="TEACHER_LATE_PATTERN",
                reference_date=ref_date,
                severity=sev,
                risk_score=max(risk_score, 45.0 if sev == "MEDIUM" else 62.0),
                confidence=confidence,
                title=f"Teacher Punctuality Alert: {staff_name}",
                message=(
                    f"Teacher {staff_name} ({course_name}) has {late_count} late arrival(s) "
                    f"(latest check-in: {t_str}, punctuality: {punctuality_rate:.1f}%)."
                ),
                recommendation=(
                    "Review arrival schedule with the teacher to protect first-period instruction time."
                ),
                metrics=metrics,
            )
            alerts_created.append((alert, created))

        if total >= 3 and attendance_rate < TEACHER_CHRONIC_ABSENCE_THRESHOLD:
            alert, created = _upsert_ai_notification(
                target_type="TEACHER",
                staff=staff,
                alert_type="CHRONIC_ABSENCE",
                reference_date=ref_date,
                severity="HIGH" if attendance_rate >= 70 else "CRITICAL",
                risk_score=max(risk_score, 68.0),
                confidence=confidence,
                title=f"Low Teacher Attendance: {staff_name} ({attendance_rate:.0f}%)",
                message=(
                    f"Teacher {staff_name} ({course_name}) has an attendance rate of "
                    f"{attendance_rate:.1f}% ({absent_count} absences in {total} tracked days)."
                ),
                recommendation=(
                    "Schedule an administrative review with the teacher and evaluate class coverage."
                ),
                metrics=metrics,
            )
            alerts_created.append((alert, created))

    return {
        "staff": staff,
        "name": staff_name,
        "course": course_name,
        "subjects": subject_names,
        "total_records": total,
        "present_count": present_count,
        "late_count": late_count,
        "absent_count": absent_count,
        "leave_count": leave_count,
        "attendance_rate": round(attendance_rate, 1),
        "punctuality_rate": round(punctuality_rate, 1),
        "consecutive_absences": consecutive_absences,
        "classes_logged_recent": classes_logged_recent,
        "today_status": today_record.get_status_display() if today_record else (
            "On Leave" if approved_leave_today else "Not Logged"
        ),
        "today_checkin": today_record.check_in_time if today_record else None,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "insight": insight,
        "alerts_created": alerts_created,
    }


@transaction.atomic
def run_ai_attendance_scan(reference_date=None, actor=None, include_daily_summary=True):
    """
    Scan ALL students and ALL teachers, generate Admin AI notifications,
    and return a comprehensive summary payload.
    """
    ref_date = _coerce_date(reference_date)

    student_profiles = []
    teacher_profiles = []
    new_alerts_count = 0
    total_alerts_touched = 0

    for student in Student.objects.select_related("admin", "course", "session").all():
        profile = analyze_student_attendance(
            student, reference_date=ref_date, create_alerts=True, actor=actor
        )
        student_profiles.append(profile)
        for _, created in profile["alerts_created"]:
            total_alerts_touched += 1
            if created:
                new_alerts_count += 1

    for staff in Staff.objects.select_related("admin", "course").all():
        profile = analyze_teacher_attendance(
            staff, reference_date=ref_date, create_alerts=True
        )
        teacher_profiles.append(profile)
        for _, created in profile["alerts_created"]:
            total_alerts_touched += 1
            if created:
                new_alerts_count += 1

    avg_student_rate = (
        round(sum(p["attendance_rate"] for p in student_profiles) / len(student_profiles), 1)
        if student_profiles else 100.0
    )
    avg_teacher_rate = (
        round(sum(p["attendance_rate"] for p in teacher_profiles) / len(teacher_profiles), 1)
        if teacher_profiles else 100.0
    )
    at_risk_students = [p for p in student_profiles if p["risk_level"] in ("HIGH", "CRITICAL")]
    at_risk_teachers = [p for p in teacher_profiles if p["risk_level"] in ("HIGH", "CRITICAL")]

    if include_daily_summary and (student_profiles or teacher_profiles):
        summary_severity = (
            "HIGH" if (at_risk_students or at_risk_teachers)
            else "LOW"
        )
        summary_risk = round(
            max(
                [p["risk_score"] for p in student_profiles + teacher_profiles] or [0.0]
            ),
            1,
        )
        digest_msg = (
            f"AI Daily Scan ({ref_date}): Students average {avg_student_rate}% attendance "
            f"({len(at_risk_students)} of {len(student_profiles)} at high/critical risk). "
            f"Teachers average {avg_teacher_rate}% attendance "
            f"({len(at_risk_teachers)} of {len(teacher_profiles)} flagged)."
        )
        digest_rec = (
            "Review all HIGH and CRITICAL alerts in the AI Attendance Tracker and follow up "
            "with affected class teachers or guardians."
            if (at_risk_students or at_risk_teachers)
            else "School-wide attendance looks healthy today. No urgent interventions needed."
        )
        _, created = _upsert_ai_notification(
            target_type="SYSTEM",
            alert_type="DAILY_SUMMARY",
            reference_date=ref_date,
            severity=summary_severity,
            risk_score=summary_risk,
            confidence=0.95,
            title=f"AI Attendance Digest — {ref_date}",
            message=digest_msg,
            recommendation=digest_rec,
            metrics={
                "avg_student_rate": avg_student_rate,
                "avg_teacher_rate": avg_teacher_rate,
                "at_risk_students": len(at_risk_students),
                "at_risk_teachers": len(at_risk_teachers),
                "total_students": len(student_profiles),
                "total_teachers": len(teacher_profiles),
            },
        )
        if created:
            new_alerts_count += 1

    return {
        "reference_date": ref_date,
        "students_scanned": len(student_profiles),
        "teachers_scanned": len(teacher_profiles),
        "avg_student_rate": avg_student_rate,
        "avg_teacher_rate": avg_teacher_rate,
        "at_risk_students": len(at_risk_students),
        "at_risk_teachers": len(at_risk_teachers),
        "new_alerts": new_alerts_count,
        "total_alerts_touched": total_alerts_touched,
        "student_profiles": student_profiles,
        "teacher_profiles": teacher_profiles,
    }


def get_ai_dashboard_summary(reference_date=None):
    """Compute live non-mutating analytics for rendering the Admin AI Dashboard."""
    ref_date = _coerce_date(reference_date)
    student_profiles = [
        analyze_student_attendance(s, reference_date=ref_date, create_alerts=False)
        for s in Student.objects.select_related("admin", "course", "session").all()
    ]
    teacher_profiles = [
        analyze_teacher_attendance(t, reference_date=ref_date, create_alerts=False)
        for t in Staff.objects.select_related("admin", "course").all()
    ]

    student_profiles.sort(key=lambda item: item["risk_score"], reverse=True)
    teacher_profiles.sort(key=lambda item: item["risk_score"], reverse=True)

    avg_student_rate = (
        round(sum(p["attendance_rate"] for p in student_profiles) / len(student_profiles), 1)
        if student_profiles else 100.0
    )
    avg_teacher_rate = (
        round(sum(p["attendance_rate"] for p in teacher_profiles) / len(teacher_profiles), 1)
        if teacher_profiles else 100.0
    )
    at_risk_students = sum(1 for p in student_profiles if p["risk_level"] in ("HIGH", "CRITICAL"))
    at_risk_teachers = sum(1 for p in teacher_profiles if p["risk_level"] in ("HIGH", "CRITICAL"))

    risk_distribution = {
        "LOW": 0,
        "MEDIUM": 0,
        "HIGH": 0,
        "CRITICAL": 0,
    }
    for p in student_profiles + teacher_profiles:
        risk_distribution[p["risk_level"]] += 1

    return {
        "reference_date": ref_date,
        "student_profiles": student_profiles,
        "teacher_profiles": teacher_profiles,
        "avg_student_rate": avg_student_rate,
        "avg_teacher_rate": avg_teacher_rate,
        "at_risk_students": at_risk_students,
        "at_risk_teachers": at_risk_teachers,
        "risk_distribution": risk_distribution,
    }
