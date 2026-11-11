import json
from datetime import datetime

from django.db import transaction
from django.utils.dateparse import parse_date

from .models import (
    Attendance,
    AttendanceAnomaly,
    AttendanceAuditEvent,
    AttendanceReport,
    LeaveReportStudent,
    Student,
    StudentAttendancePermission,
)


VALID_STATUSES = {
    "PRESENT", "TARDY", "ABSENT", "EXCUSED", "DISMISSED", "PENDING",
}
NON_INSTRUCTIONAL_TYPES = {"STUDY_HALL", "FREE_PERIOD", "DUAL_ENROLLMENT"}


def _permission_covers_period(permission, period_number):
    if not permission.get("active") or not permission.get("approved"):
        return False
    if permission.get("date") is None:
        return False
    permission_date = permission["date"]
    record_date = permission.get("_record_date")
    if record_date and str(permission_date) != str(record_date):
        return False
    if permission.get("is_all_day"):
        return True
    if permission.get("period_number") is not None:
        return permission.get("period_number") == period_number
    return False


def evaluate_attendance_dataset(data):
    student = data.get("student") or {}
    records = data.get("period_records")
    excuse_logs = data.get("excuse_logs")
    date = data.get("date")
    incomplete = (
        not isinstance(records, list)
        or not records
        or not isinstance(excuse_logs, list)
        or not student.get("id")
        or not date
        or data.get("_schedule_complete") is False
    )
    normalized = []
    seen_periods = set()
    duplicate_period = False
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            incomplete = True
            continue
        period = record.get("period_number")
        status = str(record.get("status", "")).upper()
        if not isinstance(period, int) or isinstance(period, bool) or period < 1:
            incomplete = True
            continue
        if status not in VALID_STATUSES:
            incomplete = True
            continue
        if period in seen_periods:
            duplicate_period = True
        seen_periods.add(period)
        if record.get("is_scheduled") is False:
            continue
        if str(record.get("attendance_type", "ACADEMIC")).upper() in NON_INSTRUCTIONAL_TYPES:
            continue
        normalized.append({**record, "period_number": period, "status": status})

    if duplicate_period:
        incomplete = True

    permissions = []
    for permission in excuse_logs if isinstance(excuse_logs, list) else []:
        if not isinstance(permission, dict):
            incomplete = True
            continue
        if permission.get("student_id") not in (None, student.get("id")):
            continue
        if permission.get("date") not in (None, date):
            continue
        if permission.get("date") is None:
            incomplete = True
        active = permission.get("active") is True
        approval_status = str(permission.get("approval_status", "")).upper()
        approved = permission.get("approved") is True or approval_status == "APPROVED"
        if active and approval_status == "PENDING":
            incomplete = True
        if active and approved:
            permissions.append({
                **permission,
                "date": permission.get("date"),
                "active": True,
                "approved": True,
            })

    normalized.sort(key=lambda item: item["period_number"])
    first_present = next(
        (item["period_number"] for item in normalized
         if item["status"] in ("PRESENT", "TARDY")),
        None,
    )
    timestamps = [item.get("timestamp") for item in normalized]
    timestamp_complete = bool(timestamps) and all(timestamps)
    if timestamp_complete:
        try:
            parsed_times = [
                datetime.fromisoformat(str(value)).time()
                if "T" in str(value)
                else datetime.strptime(str(value), "%H:%M:%S").time()
                for value in timestamps
            ]
            timestamp_consistent = parsed_times == sorted(parsed_times)
        except ValueError:
            timestamp_consistent = False
            incomplete = True
    else:
        timestamp_consistent = False

    def has_permission(period_number):
        return any(
            _permission_covers_period(permission, period_number)
            for permission in permissions
        )

    earlier_present = False
    flagged_periods = []
    for record in normalized:
        status = record["status"]
        if status in ("PRESENT", "TARDY"):
            earlier_present = True
        elif status == "PENDING":
            incomplete = True
        elif status == "ABSENT" and earlier_present and not has_permission(record["period_number"]):
            flagged_periods.append(record["period_number"])

    if incomplete:
        return {
            "anomaly_detected": False,
            "confidence_score": 0.2,
            "flag_type": "NO_ANOMALY",
            "summary": "Attendance or permission records are incomplete or conflicting; an administrator should verify the records before drawing a conclusion.",
            "first_present_period": first_present,
            "flagged_period": None,
            "action_items": [{
                "target_role": "ADMIN",
                "action": "Review the attendance schedule, period records, and any pending permissions before taking further action.",
            }],
            "notification_payload": {
                "sms_text": "Attendance records for {} ({}) on {} need administrative verification. No class-skip alert was generated.".format(
                    student.get("name", "the student"),
                    student.get("id", "unknown ID"),
                    date or "the selected date",
                )
            },
        }

    if flagged_periods:
        confidence = 0.98 if timestamp_consistent else 0.93
        flagged_period = flagged_periods[0]
        earlier_periods = [
            item["period_number"] for item in normalized
            if item["status"] in ("PRESENT", "TARDY")
            and item["period_number"] < flagged_period
        ]
        summary = (
            "The student was present or tardy in Period{} {} but was marked absent "
            "in Period{} {}. No active approved permission covering the earliest "
            "flagged period was found. Human verification is required; this is not "
            "proof of intentional misconduct."
        ).format(
            "s" if len(earlier_periods) != 1 else "",
            ", ".join(str(period) for period in earlier_periods),
            "s" if len(flagged_periods) != 1 else "",
            ", ".join(str(period) for period in flagged_periods),
        )
        return {
            "anomaly_detected": True,
            "confidence_score": confidence,
            "flag_type": "POTENTIAL_CLASS_SKIP",
            "summary": summary,
            "first_present_period": first_present,
            "flagged_period": flagged_period,
            "action_items": [
                {
                    "target_role": "ADMIN",
                    "action": "Review the attendance discrepancy and verify whether an approved permission exists.",
                },
                {
                    "target_role": "DISCIPLINE_OFFICER",
                    "action": "Request an authorized attendance verification; do not take disciplinary action based only on this alert.",
                },
                {
                    "target_role": "TEACHER",
                    "action": "Confirm the student's attendance and submitted record for the flagged period.",
                },
            ],
            "notification_payload": {
                "sms_text": "ATTENDANCE REVIEW: {} ({}) was present in earlier periods but marked absent in Period{} {} on {}. Please verify the records and permissions before taking further action.".format(
                    student.get("name", "Student"),
                    student.get("id", "unknown ID"),
                    "s" if len(flagged_periods) != 1 else "",
                    ", ".join(str(period) for period in flagged_periods),
                    date,
                )
            },
        }

    academic = normalized
    if (academic and academic[0]["period_number"] == 1
            and all(item["status"] == "ABSENT" for item in academic)
            and not any(has_permission(item["period_number"]) for item in academic)):
        return {
            "anomaly_detected": False,
            "confidence_score": 0.95 if timestamp_consistent else 0.9,
            "flag_type": "FULL_DAY_ABSENCE",
            "summary": "The student was marked absent in all recorded scheduled academic periods beginning with Period 1, with no contradictory attendance record.",
            "first_present_period": None,
            "flagged_period": None,
            "action_items": [{
                "target_role": "ADMIN",
                "action": "Verify this full-day absence against the school's approved absence records.",
            }],
            "notification_payload": {
                "sms_text": "FULL-DAY ABSENCE REVIEW: {} ({}) was marked absent in the recorded academic periods beginning with Period 1 on {}. Please verify the record.".format(
                    student.get("name", "Student"), student.get("id", "unknown ID"), date
                )
            },
        }

    return {
        "anomaly_detected": False,
        "confidence_score": 0.95 if timestamp_consistent else 0.9,
        "flag_type": "NO_ANOMALY",
        "summary": "No unexplained absence after a verified present or tardy period was found.",
        "first_present_period": first_present,
        "flagged_period": None,
        "action_items": [],
        "notification_payload": {"sms_text": ""},
    }


def build_student_attendance_dataset(student, date):
    attendances = list(
        Attendance.objects.filter(
            date=date,
            session=student.session,
            subject__course=student.course,
        ).select_related("subject", "subject__staff")
        .order_by("subject__period_number", "id")
    )
    period_records = []
    schedule_complete = bool(attendances)
    for attendance in attendances:
        subject = attendance.subject
        if subject.period_number is None:
            schedule_complete = False
            continue
        reports = list(
            AttendanceReport.objects.filter(
                attendance=attendance, student=student
            ).order_by("id")
        )
        if not reports:
            reports = [None]
        for report in reports:
            period_records.append({
                "period_number": subject.period_number,
                "period_name": subject.name,
                "status": report.attendance_status if report else "PENDING",
                "timestamp": None,
                "teacher_id": subject.staff_id,
                "attendance_type": subject.attendance_type,
                "is_scheduled": True,
            })

    permission_logs = []
    for permission in StudentAttendancePermission.objects.filter(
        student=student, date=date, active=True
    ):
        permission_logs.append({
            "student_id": student.admin_id,
            "date": date.isoformat(),
            "active": permission.active,
            "approved": permission.approval_status == "APPROVED",
            "approval_status": permission.approval_status,
            "permission_type": permission.permission_type,
            "period_number": permission.period_number,
            "is_all_day": permission.is_all_day,
        })

    for leave in LeaveReportStudent.objects.filter(
        student=student, date=date.isoformat(), status__in=[0, 1]
    ):
        permission_logs.append({
            "student_id": student.admin_id,
            "date": date.isoformat(),
            "active": True,
            "approved": leave.status == 1,
            "approval_status": "APPROVED" if leave.status == 1 else "PENDING",
            "permission_type": "EXCUSED",
            "is_all_day": True,
        })

    return {
        "student": {
            "id": student.admin_id,
            "name": student.admin.get_full_name().strip() or str(student),
            "grade_level": str(student.course) if student.course else "",
        },
        "date": date.isoformat(),
        "period_records": period_records,
        "excuse_logs": permission_logs,
        "_schedule_complete": schedule_complete,
    }


@transaction.atomic
def audit_student_attendance(student, date, actor):
    dataset = build_student_attendance_dataset(student, date)
    result = evaluate_attendance_dataset(dataset)
    if not result["anomaly_detected"]:
        return result

    period_key = result["flagged_period"] or 0
    anomaly, created = AttendanceAnomaly.objects.get_or_create(
        student=student,
        date=date,
        flagged_period=period_key,
        defaults={
            "flag_type": result["flag_type"],
            "confidence_score": result["confidence_score"],
            "summary": result["summary"],
            "source_records": dataset["period_records"],
        },
    )
    if created:
        AttendanceAuditEvent.objects.create(
            anomaly=anomaly,
            event_type="DETECTED",
            actor=actor,
            details={
                "confidence_score": result["confidence_score"],
                "flag_type": result["flag_type"],
                "source_records": dataset["period_records"],
            },
        )
    elif anomaly.review_status == "PENDING":
        anomaly.flag_type = result["flag_type"]
        anomaly.confidence_score = result["confidence_score"]
        anomaly.summary = result["summary"]
        anomaly.source_records = dataset["period_records"]
        anomaly.save(update_fields=[
            "flag_type", "confidence_score", "summary", "source_records", "updated_at"
        ])
    return result


def scan_attendance_date(date, actor):
    students = Student.objects.filter(
        session__isnull=False, course__isnull=False,
        attendancereport__attendance__date=date,
    ).distinct()
    results = []
    for student in students:
        results.append({
            "student": student,
            "result": audit_student_attendance(student, date, actor),
        })
    return results


def dataset_from_request_body(body):
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("student"), dict):
        return None
    if not parse_date(str(payload.get("date", ""))):
        return None
    if not isinstance(payload.get("period_records"), list):
        return None
    if not isinstance(payload.get("excuse_logs"), list):
        return None
    return payload
