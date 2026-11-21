"""
Unified Role-Aware Actionable Notifications Builder.

Collects notifications across all 6 roles (Principal/Admin, DOS, Class Teacher,
Subject Teacher, Bursar/Accountant, Student) with metadata for inline quick
responses (reply, approve, reject, forward, resolve, mark read).
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from .models import (
    AIAttendanceNotification,
    Assignment,
    Course,
    Examination,
    FeePayment,
    FeeStructure,
    FeedbackStaff,
    FeedbackStudent,
    LeaveReportStaff,
    LeaveReportStudent,
    NotificationStaff,
    NotificationStudent,
    Staff,
    Student,
    StudentMessage,
    StudentWelfareRecord,
    Subject,
    TeacherPerformanceFeedback,
    TimetableEntry,
)


def _ts(dt):
    if dt is None:
        return timezone.now()
    if hasattr(dt, "hour"):
        return dt
    return timezone.make_aware(
        timezone.datetime.combine(dt, timezone.datetime.min.time())
    )


def get_user_notifications(user, limit=30):
    """Return a list of actionable notification dicts for the given user."""
    if not user or not user.is_authenticated:
        return []

    role = str(getattr(user, "user_type", ""))
    items = []
    now = timezone.now()
    today = timezone.localdate()

    # 1. Principal / Administrator
    if role == "1":
        for alert in AIAttendanceNotification.objects.filter(
            is_resolved=False
        ).order_by("-created_at")[:10]:
            items.append({
                "id": alert.id,
                "kind": "ai_alert",
                "category": f"AI Attendance ({alert.get_target_type_display()})",
                "severity": alert.severity,
                "icon": "fa-robot",
                "title": alert.title,
                "message": alert.message,
                "recommendation": alert.recommendation,
                "created_at": alert.created_at,
                "is_unread": not alert.is_read,
                "needs_action": True,
                "link": reverse("admin_ai_attendance_dashboard"),
            })

        for leave in LeaveReportStaff.objects.filter(status=0).select_related(
            "staff__admin"
        ).order_by("-created_at")[:8]:
            items.append({
                "id": leave.id,
                "kind": "staff_leave",
                "category": "Staff Leave Request",
                "severity": "HIGH",
                "icon": "fa-calendar-minus",
                "title": f"{leave.staff.admin.get_full_name()} requested {leave.get_leave_category_display()}",
                "message": f"Dates: {leave.date} — {leave.message}",
                "created_at": leave.created_at,
                "is_unread": True,
                "needs_action": True,
                "link": reverse("view_staff_leave"),
            })

        for leave in LeaveReportStudent.objects.filter(
            status=0, forwarded_to_admin=True
        ).select_related("student__admin", "student__course").order_by("-created_at")[:8]:
            items.append({
                "id": leave.id,
                "kind": "student_leave_admin",
                "category": "Forwarded Student Leave",
                "severity": "HIGH",
                "icon": "fa-calendar-check",
                "title": f"{leave.student.admin.get_full_name()} ({leave.student.course.name if leave.student.course else 'Student'})",
                "message": f"{leave.get_leave_category_display()} ({leave.date}): {leave.message}",
                "created_at": leave.created_at,
                "is_unread": True,
                "needs_action": True,
                "link": reverse("view_student_leave"),
            })

        for fb in FeedbackStaff.objects.filter(reply__in=["", None]).select_related(
            "staff__admin"
        ).order_by("-created_at")[:8]:
            items.append({
                "id": fb.id,
                "kind": "staff_feedback",
                "category": "Staff Feedback",
                "severity": "MEDIUM",
                "icon": "fa-comment-dots",
                "title": f"Feedback from {fb.staff.admin.get_full_name()}",
                "message": fb.feedback,
                "created_at": fb.created_at,
                "is_unread": True,
                "needs_action": True,
                "link": reverse("staff_feedback_message"),
            })

        for fb in FeedbackStudent.objects.filter(reply__in=["", None]).select_related(
            "student__admin"
        ).order_by("-created_at")[:8]:
            items.append({
                "id": fb.id,
                "kind": "student_feedback",
                "category": "Student Feedback",
                "severity": "MEDIUM",
                "icon": "fa-comments",
                "title": f"Feedback from {fb.student.admin.get_full_name()}",
                "message": fb.feedback,
                "created_at": fb.created_at,
                "is_unread": True,
                "needs_action": True,
                "link": reverse("student_feedback_message"),
            })

    # 2. Director of Studies (DOS)
    elif role == "4":
        for alert in AIAttendanceNotification.objects.filter(
            is_resolved=False
        ).order_by("-created_at")[:8]:
            items.append({
                "id": alert.id,
                "kind": "ai_alert",
                "category": f"AI Attendance ({alert.get_target_type_display()})",
                "severity": alert.severity,
                "icon": "fa-robot",
                "title": alert.title,
                "message": alert.message,
                "recommendation": alert.recommendation,
                "created_at": alert.created_at,
                "is_unread": not alert.is_read,
                "needs_action": True,
                "link": reverse("admin_ai_attendance_dashboard"),
            })

        for exam in Examination.objects.filter(
            status__in=["UPCOMING", "ONGOING", "COMPLETED"]
        ).select_related("course", "session").order_by("start_date")[:8]:
            items.append({
                "id": exam.id,
                "kind": "dos_exam",
                "category": "Examination Schedule",
                "severity": "HIGH" if exam.status == "ONGOING" else "MEDIUM",
                "icon": "fa-file-signature",
                "title": f"{exam.name} ({exam.course.name if exam.course else 'All Classes'})",
                "message": f"Status: {exam.get_status_display()} · {exam.start_date} to {exam.end_date}",
                "current_status": exam.status,
                "created_at": exam.updated_at or exam.created_at,
                "is_unread": exam.status in ("UPCOMING", "ONGOING"),
                "needs_action": True,
                "link": reverse("dos_manage_exams"),
            })

        scheduled_subject_ids = set(
            TimetableEntry.objects.values_list("subject_id", flat=True)
        )
        for subject in Subject.objects.exclude(id__in=scheduled_subject_ids).select_related(
            "course", "staff__admin"
        )[:6]:
            items.append({
                "id": subject.id,
                "kind": "info_link",
                "category": "Timetable Setup",
                "severity": "MEDIUM",
                "icon": "fa-calendar-week",
                "title": f"Unscheduled Subject: {subject.name}",
                "message": f"{subject.course.name} ({subject.staff.admin.get_full_name()}) has no timetable slot yet.",
                "created_at": subject.created_at,
                "is_unread": True,
                "needs_action": True,
                "action_label": "Add to Timetable",
                "link": f"{reverse('dos_manage_timetable')}?course={subject.course_id}",
            })

    # 3 & 4. Class Teacher ('6') and Subject Teacher ('2')
    elif role in ("2", "6"):
        staff = Staff.objects.filter(admin=user).first()
        if staff:
            # Direct student messages (for Class Teachers)
            for msg in StudentMessage.objects.filter(
                receiver=user, is_read=False
            ).select_related("sender").order_by("-created_at")[:8]:
                items.append({
                    "id": msg.id,
                    "sender_id": msg.sender_id,
                    "kind": "direct_message",
                    "category": "Student Message",
                    "severity": "HIGH",
                    "icon": "fa-envelope",
                    "title": f"Message from {msg.sender.get_full_name() or msg.sender.email}",
                    "message": f"{msg.subject}: {msg.message}" if msg.subject != "No Subject" else msg.message,
                    "created_at": msg.created_at,
                    "is_unread": True,
                    "needs_action": True,
                    "link": f"{reverse('class_teacher_messages')}?student_id={msg.sender_id}",
                })

            if staff.class_teacher and staff.course_id:
                for leave in LeaveReportStudent.objects.filter(
                    student__course=staff.course, status=0
                ).select_related("student__admin").order_by("-created_at")[:8]:
                    items.append({
                        "id": leave.id,
                        "kind": "student_leave_teacher",
                        "category": "Student Leave Request",
                        "severity": "HIGH",
                        "icon": "fa-calendar-check",
                        "title": f"{leave.student.admin.get_full_name()} requested {leave.get_leave_category_display()}",
                        "message": f"Dates: {leave.date} — {leave.message}",
                        "created_at": leave.created_at,
                        "is_unread": True,
                        "needs_action": True,
                        "link": reverse("view_student_leave"),
                    })

                for welfare in StudentWelfareRecord.objects.filter(
                    student__course=staff.course, status__in=["OPEN", "IN_PROGRESS"]
                ).select_related("student__admin").order_by("-created_at")[:6]:
                    items.append({
                        "id": welfare.id,
                        "kind": "welfare_case",
                        "category": "Student Welfare",
                        "severity": "MEDIUM",
                        "icon": "fa-hands-helping",
                        "title": f"{welfare.student.admin.get_full_name()} · {welfare.get_category_display()}",
                        "message": welfare.summary,
                        "current_status": welfare.status,
                        "created_at": welfare.created_at,
                        "is_unread": welfare.status == "OPEN",
                        "needs_action": True,
                        "link": reverse("class_teacher_welfare"),
                    })

            # Admin / DOS teacher performance feedback awaiting acknowledgement
            for perf in TeacherPerformanceFeedback.objects.filter(
                staff=staff, acknowledged_by_teacher=False
            ).select_related("reviewer").order_by("-created_at")[:6]:
                reviewer_name = (
                    perf.reviewer.get_full_name()
                    if perf.reviewer else "School Leadership"
                )
                items.append({
                    "id": perf.id,
                    "kind": "teacher_performance",
                    "category": f"Performance Feedback ({perf.rating}/5)",
                    "severity": "HIGH" if perf.rating <= 2 else "MEDIUM",
                    "icon": "fa-star",
                    "title": f"{perf.get_category_display()} Review by {reviewer_name}",
                    "message": perf.strengths + (
                        f" | Improvement: {perf.areas_for_improvement}"
                        if perf.areas_for_improvement else ""
                    ),
                    "recommendation": perf.recommendations,
                    "created_at": perf.created_at,
                    "is_unread": True,
                    "needs_action": True,
                    "link": reverse("staff_performance_feedback"),
                })

            # Admin announcements sent to this staff member
            for notif in NotificationStaff.objects.filter(staff=staff).order_by("-created_at")[:8]:
                items.append({
                    "id": notif.id,
                    "kind": "staff_announcement",
                    "category": "Admin Announcement",
                    "severity": "MEDIUM" if not notif.is_read else "LOW",
                    "icon": "fa-bullhorn",
                    "title": "Announcement from Administration",
                    "message": notif.message,
                    "created_at": notif.created_at,
                    "is_unread": not notif.is_read,
                    "needs_action": not notif.is_read,
                    "link": reverse("staff_view_notification"),
                })

            # Recent replies on staff feedback
            for fb in FeedbackStaff.objects.filter(staff=staff).exclude(
                reply__in=["", None]
            ).order_by("-updated_at")[:4]:
                items.append({
                    "id": fb.id,
                    "kind": "info_link",
                    "category": "Feedback Reply",
                    "severity": "LOW",
                    "icon": "fa-reply",
                    "title": "Admin replied to your feedback",
                    "message": fb.reply,
                    "created_at": fb.updated_at,
                    "is_unread": False,
                    "needs_action": False,
                    "action_label": "View Feedback",
                    "link": reverse("staff_feedback"),
                })

    # 5. Bursar / Accountant
    elif role == "5":
        structures_by_course = {}
        for fs in FeeStructure.objects.all():
            structures_by_course[fs.course_id] = (
                structures_by_course.get(fs.course_id, Decimal("0.00")) + fs.amount
            )

        for course in Course.objects.all():
            if course.id not in structures_by_course:
                items.append({
                    "id": course.id,
                    "kind": "info_link",
                    "category": "Fee Configuration",
                    "severity": "MEDIUM",
                    "icon": "fa-file-invoice-dollar",
                    "title": f"No Fee Structure for {course.name}",
                    "message": f"Configure school fees for {course.name} to enable balance tracking.",
                    "created_at": course.created_at,
                    "is_unread": True,
                    "needs_action": True,
                    "action_label": "Set Fees",
                    "link": reverse("bursar_fee_structures"),
                })

        payments_by_student = {
            row["student_id"]: row["total"] or Decimal("0.00")
            for row in FeePayment.objects.values("student_id").annotate(total=Sum("amount_paid"))
        }
        for student in Student.objects.select_related("admin", "course").all():
            expected = structures_by_course.get(student.course_id, Decimal("0.00"))
            paid = payments_by_student.get(student.id, Decimal("0.00"))
            balance = expected - paid
            if expected > 0 and balance > 0:
                items.append({
                    "id": student.id,
                    "kind": "info_link",
                    "category": "Outstanding Fee Balance",
                    "severity": "HIGH" if paid == 0 else "MEDIUM",
                    "icon": "fa-coins",
                    "title": f"{student.admin.get_full_name()} owes {balance:.2f}",
                    "message": f"Class: {student.course.name if student.course else '—'} · Paid {paid:.2f} of {expected:.2f}",
                    "created_at": now,
                    "is_unread": True,
                    "needs_action": True,
                    "action_label": "Record Payment",
                    "link": reverse("bursar_payments"),
                })

    # 6. Student
    elif role == "3":
        student = Student.objects.filter(admin=user).first()
        if student:
            for msg in StudentMessage.objects.filter(
                receiver=user, is_read=False
            ).select_related("sender").order_by("-created_at")[:8]:
                items.append({
                    "id": msg.id,
                    "sender_id": msg.sender_id,
                    "kind": "direct_message",
                    "category": "Class Teacher Message",
                    "severity": "HIGH",
                    "icon": "fa-envelope",
                    "title": f"Message from {msg.sender.get_full_name() or msg.sender.email}",
                    "message": msg.message,
                    "created_at": msg.created_at,
                    "is_unread": True,
                    "needs_action": True,
                    "link": reverse("student_messages"),
                })

            for notif in NotificationStudent.objects.filter(student=student).order_by("-created_at")[:8]:
                items.append({
                    "id": notif.id,
                    "kind": "student_announcement",
                    "category": "School Announcement",
                    "severity": "MEDIUM" if not notif.is_read else "LOW",
                    "icon": "fa-bullhorn",
                    "title": "Announcement from Administration",
                    "message": notif.message,
                    "created_at": notif.created_at,
                    "is_unread": not notif.is_read,
                    "needs_action": not notif.is_read,
                    "link": reverse("student_view_notification"),
                })

            if student.course_id:
                soon = today + timedelta(days=7)
                for item in Assignment.objects.filter(
                    subject__course=student.course,
                    due_date__gte=today,
                    due_date__lte=soon,
                ).select_related("subject", "staff__admin").order_by("due_date")[:6]:
                    items.append({
                        "id": item.id,
                        "kind": "info_link",
                        "category": "Upcoming Assignment",
                        "severity": "MEDIUM",
                        "icon": "fa-tasks",
                        "title": f"{item.title} ({item.subject.name})",
                        "message": f"Due {item.due_date.strftime('%b %d, %Y')} · {item.description[:100]}",
                        "created_at": item.created_at,
                        "is_unread": True,
                        "needs_action": True,
                        "action_label": "Open Assignments",
                        "link": reverse("student_assignments"),
                    })

            for fb in FeedbackStudent.objects.filter(student=student).exclude(
                reply__in=["", None]
            ).order_by("-updated_at")[:4]:
                items.append({
                    "id": fb.id,
                    "kind": "info_link",
                    "category": "Feedback Reply",
                    "severity": "LOW",
                    "icon": "fa-reply",
                    "title": "Admin replied to your feedback",
                    "message": fb.reply,
                    "created_at": fb.updated_at,
                    "is_unread": False,
                    "needs_action": False,
                    "action_label": "View Feedback",
                    "link": reverse("student_feedback"),
                })

    items.sort(key=lambda x: (not x["is_unread"], -_ts(x["created_at"]).timestamp()))
    return items[:limit]
