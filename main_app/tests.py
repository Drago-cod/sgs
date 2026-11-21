from datetime import date, time, timedelta
import json

from django.test import TestCase
from django.urls import reverse

from .ai_attendance import (
    analyze_student_attendance,
    analyze_teacher_attendance,
    run_ai_attendance_scan,
)
from .models import (
    AIAttendanceNotification,
    Attendance,
    AttendanceReport,
    Course,
    CustomUser,
    LeaveReportStaff,
    Session,
    Staff,
    Student,
    Subject,
    TeacherAttendance,
)


class AIAttendanceTrackingTests(TestCase):
    def setUp(self):
        self.admin_user = CustomUser.objects.create_superuser(
            email="admin@test.com",
            password="password123",
            user_type="1",
            first_name="System",
            last_name="Admin",
            gender="M",
            address="School HQ",
        )
        self.course = Course.objects.create(name="Form One")
        self.session = Session.objects.create(
            start_year=date(2026, 1, 1), end_year=date(2026, 12, 31)
        )

        self.teacher_user = CustomUser.objects.create_user(
            email="teacher@test.com",
            password="password123",
            user_type="2",
            first_name="Sarah",
            last_name="Johnson",
            gender="F",
            address="Staff Quarters",
        )
        self.staff = self.teacher_user.staff
        self.staff.course = self.course
        self.staff.class_teacher = True
        self.staff.save()

        self.student_user = CustomUser.objects.create_user(
            email="student@test.com",
            password="password123",
            user_type="3",
            first_name="Alex",
            last_name="Smith",
            gender="M",
            address="Dorm A",
        )
        self.student = self.student_user.student
        self.student.course = self.course
        self.student.session = self.session
        self.student.save()

        self.math = Subject.objects.create(
            name="Mathematics", staff=self.staff, course=self.course, period_number=1
        )
        self.physics = Subject.objects.create(
            name="Physics", staff=self.staff, course=self.course, period_number=2
        )

    def test_student_consecutive_and_chronic_absence_notifies_admin(self):
        base_day = date(2026, 11, 10)
        statuses = ["PRESENT", "ABSENT", "ABSENT", "ABSENT"]
        for idx, st in enumerate(statuses):
            att = Attendance.objects.create(
                session=self.session,
                subject=self.math,
                date=base_day + timedelta(days=idx),
            )
            AttendanceReport.objects.create(
                student=self.student,
                attendance=att,
                status=(st == "PRESENT"),
                attendance_status=st,
            )

        profile = analyze_student_attendance(
            self.student, reference_date=base_day + timedelta(days=3), create_alerts=True
        )
        self.assertEqual(profile["consecutive_absences"], 3)
        self.assertLess(profile["attendance_rate"], 75.0)
        self.assertIn(profile["risk_level"], ("HIGH", "CRITICAL"))

        alert_types = set(
            AIAttendanceNotification.objects.filter(
                target_type="STUDENT", student=self.student
            ).values_list("alert_type", flat=True)
        )
        self.assertIn("CONSECUTIVE_ABSENCE", alert_types)
        self.assertIn("CHRONIC_ABSENCE", alert_types)

    def test_student_same_day_class_skip_notifies_admin(self):
        today = date(2026, 11, 20)
        att1 = Attendance.objects.create(session=self.session, subject=self.math, date=today)
        att2 = Attendance.objects.create(session=self.session, subject=self.physics, date=today)
        AttendanceReport.objects.create(
            student=self.student, attendance=att1, status=True, attendance_status="PRESENT"
        )
        AttendanceReport.objects.create(
            student=self.student, attendance=att2, status=False, attendance_status="ABSENT"
        )

        analyze_student_attendance(self.student, reference_date=today, create_alerts=True)
        self.assertTrue(
            AIAttendanceNotification.objects.filter(
                target_type="STUDENT", student=self.student, alert_type="CLASS_SKIP"
            ).exists()
        )

    def test_teacher_unexcused_absence_and_late_pattern_notifies_admin(self):
        day = date(2026, 11, 20)
        TeacherAttendance.objects.create(
            staff=self.staff, date=day, status="ABSENT", source="ADMIN"
        )
        profile = analyze_teacher_attendance(self.staff, reference_date=day, create_alerts=True)
        self.assertIn(profile["risk_level"], ("HIGH", "CRITICAL"))
        self.assertTrue(
            AIAttendanceNotification.objects.filter(
                target_type="TEACHER",
                staff=self.staff,
                alert_type="TEACHER_ABSENT_UNEXCUSED",
            ).exists()
        )

        # Approved leave should NOT trigger unexcused absence on next day
        next_day = day + timedelta(days=1)
        LeaveReportStaff.objects.create(
            staff=self.staff, date=next_day.isoformat(), message="Conference", status=1
        )
        TeacherAttendance.objects.create(
            staff=self.staff, date=next_day, status="ABSENT", source="ADMIN"
        )
        analyze_teacher_attendance(self.staff, reference_date=next_day, create_alerts=True)
        self.assertFalse(
            AIAttendanceNotification.objects.filter(
                target_type="TEACHER",
                staff=self.staff,
                alert_type="TEACHER_ABSENT_UNEXCUSED",
                reference_date=next_day,
            ).exists()
        )

    def test_school_wide_scan_deduplicates_alerts(self):
        today = date(2026, 11, 21)
        TeacherAttendance.objects.create(
            staff=self.staff,
            date=today,
            status="LATE",
            check_in_time=time(8, 45),
            source="SELF_CHECKIN",
        )
        first = run_ai_attendance_scan(reference_date=today, actor=self.admin_user)
        count_after_first = AIAttendanceNotification.objects.count()
        self.assertGreaterEqual(first["new_alerts"], 2)  # Teacher late + daily digest

        second = run_ai_attendance_scan(reference_date=today, actor=self.admin_user)
        self.assertEqual(second["new_alerts"], 0)
        self.assertEqual(AIAttendanceNotification.objects.count(), count_after_first)

    def test_admin_dashboard_and_notification_lifecycle(self):
        self.client.force_login(self.admin_user)

        # Record teacher absence via Admin view -> immediately notifies Admin
        resp = self.client.post(
            reverse("record_teacher_attendance"),
            {
                "staff_id": self.staff.id,
                "date": "2026-11-21",
                "status": "ABSENT",
                "notes": "Did not arrive",
            },
        )
        self.assertEqual(resp.status_code, 302)
        alert = AIAttendanceNotification.objects.filter(
            target_type="TEACHER", staff=self.staff
        ).first()
        self.assertIsNotNone(alert)
        self.assertFalse(alert.is_read)

        # Admin AI tracker & home page render with unread notification badge
        dash_resp = self.client.get(reverse("admin_ai_attendance_dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertGreaterEqual(dash_resp.context["ai_unread_count"], 1)

        home_resp = self.client.get(reverse("admin_home"))
        self.assertEqual(home_resp.status_code, 200)
        self.assertContains(home_resp, alert.title)

        # Mark read & resolve
        self.client.post(reverse("mark_ai_notification_read", args=[alert.id]))
        alert.refresh_from_db()
        self.assertTrue(alert.is_read)

        self.client.post(reverse("resolve_ai_notification", args=[alert.id]))
        alert.refresh_from_db()
        self.assertTrue(alert.is_resolved)
        self.assertEqual(alert.resolved_by, self.admin_user)

    def test_staff_checkin_and_save_attendance_auto_tracking(self):
        self.client.force_login(self.teacher_user)

        # Teacher checks in via dedicated endpoint
        checkin_resp = self.client.post(
            reverse("staff_attendance_checkin"),
            {"action": "checkin", "notes": "Arrived for class"},
        )
        self.assertEqual(checkin_resp.status_code, 302)
        self.assertTrue(TeacherAttendance.objects.filter(staff=self.staff).exists())

        # Teacher saves student attendance across two days of absence -> triggers student AI alert
        for d in ("2026-11-18", "2026-11-19"):
            save_resp = self.client.post(
                reverse("save_attendance"),
                {
                    "subject": self.math.id,
                    "session": self.session.id,
                    "date": d,
                    "student_ids": json.dumps([{"id": self.student.id, "status": "ABSENT"}]),
                },
            )
            self.assertEqual(save_resp.status_code, 200)
            self.assertEqual(save_resp.content.decode(), "OK")

        # Teacher auto-tracked for 2026-11-18 & 2026-11-19, student alerted for 2 consecutive absences
        self.assertTrue(
            TeacherAttendance.objects.filter(
                staff=self.staff, date=date(2026, 11, 19), source="AUTO_ACTIVITY"
            ).exists()
        )
        self.assertTrue(
            AIAttendanceNotification.objects.filter(
                target_type="STUDENT",
                student=self.student,
                alert_type="CONSECUTIVE_ABSENCE",
            ).exists()
        )
