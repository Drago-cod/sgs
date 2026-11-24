"""
SGS Advanced eLearning & Virtual Classroom Views.

Integrated directly with existing SGS authentication, RBAC, Course (S1-S4),
Subject, Session (Term), TimetableEntry, Assignment, Attendance, Examination,
StudentResult, and NotificationStudent models.
"""

import csv
import json
import os
import random
from datetime import timedelta

from django.contrib import messages
from django.db.models import Avg, Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .elearning_forms import (
    DiscussionThreadForm,
    LearningMaterialForm,
    LearningTopicForm,
    LectureRecordingForm,
    OnlineAssessmentForm,
    SubjectAnnouncementForm,
    VirtualClassroomForm,
)
from .elearning_services import (
    ALLOWED_RECORDING_EXTENSIONS,
    MAX_RECORDING_SIZE_BYTES,
    can_access_classroom,
    can_manage_subject,
    compute_storage_usage_mb,
    compute_student_learning_progress,
    finalize_and_grade_attempt,
    get_authorized_subjects,
    get_user_role,
    is_admin_or_dos,
    is_parent,
    is_student,
    is_teacher,
    notify_course_students,
    record_student_heartbeat,
    record_student_join,
    record_student_leave,
    recalculate_virtual_attendance_status,
    run_ai_learning_assistant,
    sync_classroom_attendance_to_sgs,
)
from .models import (
    AILearningInteraction,
    AIAttendanceNotification,
    AssessmentAnswer,
    AssessmentAttempt,
    Assignment,
    AssignmentSubmission,
    Attendance,
    AttendanceReport,
    Course,
    CustomUser,
    DiscussionReply,
    DiscussionThread,
    Examination,
    LearningMaterial,
    LearningTopic,
    LectureRecording,
    MaterialAccessLog,
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
    VirtualClassroomMessage,
)


def _get_jitsi_domain():
    return os.environ.get("JITSI_DOMAIN", "meet.jit.si").strip() or "meet.jit.si"


def _get_staff_profile(user):
    if is_teacher(user):
        staff, _ = Staff.objects.get_or_create(
            admin=user, defaults={"class_teacher": get_user_role(user) == "6"}
        )
        return staff
    return Staff.objects.filter(admin=user).first()


# ============================================================================
# 1. ROLE-ADAPTIVE eLEARNING DASHBOARD
# ============================================================================

def elearning_dashboard(request):
    user = request.user
    role = get_user_role(user)
    now = timezone.now()
    today = timezone.localdate()

    # Parent / Guardian redirect to dedicated read-only child monitoring portal
    if role == "7":
        return redirect("parent_guardian_portal")

    authorized_subjects = get_authorized_subjects(user)
    current_term = Session.objects.order_by("-id").first()

    # ---- STUDENT DASHBOARD ----
    if role == "3":
        student, _ = Student.objects.get_or_create(admin=user)
        course = student.course
        progress_data = compute_student_learning_progress(student)

        live_classes = VirtualClassroom.objects.filter(
            course=course, status="LIVE"
        ).select_related("subject", "teacher__admin") if course else VirtualClassroom.objects.none()

        upcoming_classes = VirtualClassroom.objects.filter(
            course=course, status="SCHEDULED", scheduled_start__gte=now - timedelta(hours=1)
        ).select_related("subject", "teacher__admin").order_by("scheduled_start")[:8] if course else []

        recent_materials = LearningMaterial.objects.filter(
            subject__in=authorized_subjects, is_published=True
        ).select_related("subject", "topic").order_by("-created_at")[:8]

        recently_accessed = MaterialAccessLog.objects.filter(
            student=student
        ).select_related("material__subject").order_by("-last_accessed_at")[:6]

        upcoming_assignments = Assignment.objects.filter(
            subject__in=authorized_subjects, is_published=True, due_date__gte=today
        ).select_related("subject", "staff__admin").order_by("due_date")[:6]

        upcoming_assessments = OnlineAssessment.objects.filter(
            subject__in=authorized_subjects, is_published=True
        ).select_related("subject").order_by("deadline", "-created_at")[:6]

        upcoming_exams = Examination.objects.filter(
            Q(course=course) | Q(course__isnull=True),
            status__in=["UPCOMING", "ONGOING"],
        ).order_by("start_date")[:5] if course else []

        recent_quiz_results = AssessmentAttempt.objects.filter(
            student=student,
            status="GRADED",
            assessment__results_published=True,
        ).select_related("assessment__subject").order_by("-graded_at")[:6]

        recent_school_results = StudentResult.objects.filter(
            student=student
        ).select_related("subject").order_by("-updated_at")[:6]

        announcements = SubjectAnnouncement.objects.filter(
            subject__in=authorized_subjects
        ).select_related("subject", "author")[:6]

        recordings = LectureRecording.objects.filter(
            subject__in=authorized_subjects, is_published=True
        ).select_related("subject", "teacher__admin").order_by("-recorded_at")[:6]

        att_total = AttendanceReport.objects.filter(student=student).count()
        att_present = AttendanceReport.objects.filter(student=student, status=True).count()
        attendance_rate = round((att_present / att_total * 100.0), 1) if att_total else 0.0

        context = {
            "page_title": "eLearning Student Hub",
            "dashboard_role": "student",
            "student": student,
            "current_term": student.session or current_term,
            "academic_year": today.year,
            "subjects": authorized_subjects,
            "progress_data": progress_data,
            "live_classes": live_classes,
            "upcoming_classes": upcoming_classes,
            "recent_materials": recent_materials,
            "recently_accessed": recently_accessed,
            "upcoming_assignments": upcoming_assignments,
            "upcoming_assessments": upcoming_assessments,
            "upcoming_exams": upcoming_exams,
            "recent_quiz_results": recent_quiz_results,
            "recent_school_results": recent_school_results,
            "announcements": announcements,
            "recordings": recordings,
            "attendance_rate": attendance_rate,
            "att_present": att_present,
            "att_total": att_total,
        }
        return render(request, "elearning_template/dashboard.html", context)

    # ---- TEACHER DASHBOARD ----
    if role in ("2", "6"):
        staff = _get_staff_profile(user)
        assigned_courses = Course.objects.filter(
            Q(subject__staff=staff) | Q(id=staff.course_id)
        ).distinct()

        day_codes = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        today_code = day_codes[today.weekday()]
        todays_timetable = TimetableEntry.objects.filter(
            subject__in=authorized_subjects, day_of_week=today_code
        ).select_related("course", "subject").order_by("start_time")

        live_classes = VirtualClassroom.objects.filter(
            teacher=staff, status="LIVE"
        ).select_related("course", "subject")

        upcoming_classes = VirtualClassroom.objects.filter(
            teacher=staff, status="SCHEDULED"
        ).select_related("course", "subject").order_by("scheduled_start")[:8]

        pending_submissions = AssignmentSubmission.objects.filter(
            assignment__staff=staff, status__in=["TURNED_IN", "LATE"]
        ).select_related("student__admin", "assignment__subject").order_by("-turned_in_at")[:8]

        attempts_to_grade = AssessmentAttempt.objects.filter(
            assessment__teacher=staff, status__in=["SUBMITTED", "TIMED_OUT"]
        ).select_related("student__admin", "assessment__subject").order_by("-submitted_at")[:8]

        materials_uploaded = LearningMaterial.objects.filter(
            subject__in=authorized_subjects
        ).select_related("subject", "topic").order_by("-created_at")[:8]

        att_total = AttendanceReport.objects.filter(attendance__subject__in=authorized_subjects).count()
        att_present = AttendanceReport.objects.filter(
            attendance__subject__in=authorized_subjects, status=True
        ).count()
        class_att_rate = round((att_present / att_total * 100.0), 1) if att_total else 0.0

        avg_quiz_score = (
            AssessmentAttempt.objects.filter(
                assessment__teacher=staff, status="GRADED"
            ).aggregate(a=Avg("percentage"))["a"]
            or 0.0
        )

        recent_activity = MaterialAccessLog.objects.filter(
            material__subject__in=authorized_subjects
        ).select_related("student__admin", "material__subject").order_by("-last_accessed_at")[:8]

        context = {
            "page_title": "eLearning Teacher Studio",
            "dashboard_role": "teacher",
            "staff": staff,
            "current_term": current_term,
            "assigned_courses": assigned_courses,
            "subjects": authorized_subjects,
            "todays_timetable": todays_timetable,
            "live_classes": live_classes,
            "upcoming_classes": upcoming_classes,
            "pending_submissions": pending_submissions,
            "attempts_to_grade": attempts_to_grade,
            "materials_uploaded": materials_uploaded,
            "class_att_rate": class_att_rate,
            "avg_quiz_score": round(avg_quiz_score, 1),
            "recent_activity": recent_activity,
            "total_recordings": LectureRecording.objects.filter(teacher=staff).count(),
            "total_quizzes": OnlineAssessment.objects.filter(teacher=staff).count(),
        }
        return render(request, "elearning_template/dashboard.html", context)

    # ---- ADMINISTRATOR / DOS DASHBOARD ----
    storage = compute_storage_usage_mb()
    total_lessons = VirtualClassroom.objects.count()
    active_classrooms = VirtualClassroom.objects.filter(status="LIVE").select_related(
        "course", "subject", "teacher__admin"
    )
    active_students_count = (
        Student.objects.filter(
            Q(material_access_logs__isnull=False)
            | Q(virtual_attendance_records__isnull=False)
            | Q(assessment_attempts__isnull=False)
            | Q(assignment_submissions__isnull=False)
        ).distinct().count()
    )
    active_teachers_count = (
        Staff.objects.filter(
            Q(virtual_classrooms__isnull=False)
            | Q(online_assessments__isnull=False)
            | Q(assignments__isnull=False)
            | Q(created_topics__isnull=False)
        ).distinct().count()
    )
    classes_with_content = (
        Course.objects.filter(
            Q(subject__learning_materials__isnull=False)
            | Q(virtual_classrooms__isnull=False)
        ).distinct().count()
    )
    recent_classrooms = VirtualClassroom.objects.select_related(
        "course", "subject", "teacher__admin"
    ).order_by("-created_at")[:8]
    system_alerts = AIAttendanceNotification.objects.filter(
        is_resolved=False
    ).order_by("-created_at")[:6]

    context = {
        "page_title": "eLearning Executive Command Center",
        "dashboard_role": "admin",
        "current_term": current_term,
        "subjects": authorized_subjects,
        "total_lessons": total_lessons,
        "active_classrooms": active_classrooms,
        "active_classrooms_count": active_classrooms.count(),
        "active_students_count": active_students_count,
        "total_students_count": Student.objects.count(),
        "active_teachers_count": active_teachers_count,
        "total_teachers_count": Staff.objects.count(),
        "classes_with_content": classes_with_content,
        "total_classes": Course.objects.count(),
        "total_materials": LearningMaterial.objects.count(),
        "total_assignments": Assignment.objects.count(),
        "total_submissions": AssignmentSubmission.objects.filter(
            status__in=["TURNED_IN", "LATE", "GRADED"]
        ).count(),
        "total_assessments": OnlineAssessment.objects.count(),
        "total_attempts": AssessmentAttempt.objects.count(),
        "total_recordings": LectureRecording.objects.count(),
        "storage": storage,
        "recent_classrooms": recent_classrooms,
        "system_alerts": system_alerts,
    }
    return render(request, "elearning_template/dashboard.html", context)


# ============================================================================
# 2. SUBJECT PORTAL, TOPICS, MATERIALS & ANNOUNCEMENTS
# ============================================================================

def subject_portal(request, subject_id):
    subject = get_object_or_404(
        Subject.objects.select_related("course", "staff__admin"), id=subject_id
    )
    user = request.user
    authorized_ids = set(get_authorized_subjects(user).values_list("id", flat=True))
    if subject.id not in authorized_ids:
        messages.error(request, "You are not authorized to access this subject portal.")
        return redirect("elearning_dashboard")

    can_manage = can_manage_subject(user, subject)

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "create_topic" and can_manage:
            form = LearningTopicForm(request.POST)
            if form.is_valid():
                topic = form.save(commit=False)
                topic.subject = subject
                topic.created_by = _get_staff_profile(user) or subject.staff
                topic.save()
                messages.success(request, f"Topic '{topic.title}' created.")
            else:
                messages.error(request, "Please check the topic details.")
            return redirect("elearning_subject_portal", subject_id=subject.id)

        elif action == "edit_topic" and can_manage:
            topic = get_object_or_404(LearningTopic, id=request.POST.get("topic_id"), subject=subject)
            topic.title = request.POST.get("title", topic.title).strip() or topic.title
            topic.description = request.POST.get("description", topic.description)
            topic.learning_objectives = request.POST.get("learning_objectives", topic.learning_objectives)
            try:
                topic.order = max(1, int(request.POST.get("order", topic.order)))
            except (TypeError, ValueError):
                pass
            topic.is_published = request.POST.get("is_published") == "on"
            topic.save()
            messages.success(request, f"Topic '{topic.title}' updated.")
            return redirect("elearning_subject_portal", subject_id=subject.id)

        elif action == "delete_topic" and can_manage:
            topic = get_object_or_404(LearningTopic, id=request.POST.get("topic_id"), subject=subject)
            topic.delete()
            messages.success(request, "Topic deleted.")
            return redirect("elearning_subject_portal", subject_id=subject.id)

        elif action == "upload_material" and can_manage:
            form = LearningMaterialForm(request.POST, request.FILES, subject=subject)
            if form.is_valid():
                mat = form.save(commit=False)
                mat.subject = subject
                mat.uploaded_by = user
                if mat.file:
                    mat.file_size_bytes = mat.file.size
                mat.save()
                if mat.is_published:
                    notify_course_students(
                        subject.course,
                        f"New {mat.get_material_type_display()} uploaded in {subject.name}: {mat.title}",
                    )
                messages.success(request, f"Learning material '{mat.title}' uploaded.")
            else:
                messages.error(request, f"Upload error: {form.errors.as_text()}")
            return redirect("elearning_subject_portal", subject_id=subject.id)

        elif action == "delete_material" and can_manage:
            mat = get_object_or_404(LearningMaterial, id=request.POST.get("material_id"), subject=subject)
            mat.delete()
            messages.success(request, "Learning material removed.")
            return redirect("elearning_subject_portal", subject_id=subject.id)

        elif action == "post_announcement" and can_manage:
            form = SubjectAnnouncementForm(request.POST)
            if form.is_valid():
                ann = form.save(commit=False)
                ann.subject = subject
                ann.author = user
                ann.save()
                notify_course_students(
                    subject.course,
                    f"[{subject.name} Announcement] {ann.title}: {ann.body[:120]}",
                )
                messages.success(request, "Announcement posted & students notified.")
            return redirect("elearning_subject_portal", subject_id=subject.id)

        elif action == "toggle_material_complete" and is_student(user):
            student = Student.objects.filter(admin=user).first()
            mat = get_object_or_404(LearningMaterial, id=request.POST.get("material_id"), subject=subject)
            log, _ = MaterialAccessLog.objects.get_or_create(material=mat, student=student)
            log.is_completed = not log.is_completed
            log.completed_at = timezone.now() if log.is_completed else None
            log.save()
            messages.success(
                request,
                f"Marked '{mat.title}' as {'completed' if log.is_completed else 'in progress'}.",
            )
            return redirect("elearning_subject_portal", subject_id=subject.id)

    topics_qs = LearningTopic.objects.filter(subject=subject)
    materials_qs = LearningMaterial.objects.filter(subject=subject).select_related("topic", "uploaded_by")
    assessments_qs = OnlineAssessment.objects.filter(subject=subject)
    recordings_qs = LectureRecording.objects.filter(subject=subject)

    if not can_manage:
        topics_qs = topics_qs.filter(is_published=True)
        materials_qs = materials_qs.filter(is_published=True)
        assessments_qs = assessments_qs.filter(is_published=True)
        recordings_qs = recordings_qs.filter(is_published=True)

    completed_material_ids = set()
    student_progress = None
    if is_student(user):
        student = Student.objects.filter(admin=user).first()
        if student:
            completed_material_ids = set(
                MaterialAccessLog.objects.filter(
                    student=student, material__subject=subject, is_completed=True
                ).values_list("material_id", flat=True)
            )
            prog = compute_student_learning_progress(student)
            for row in prog["subjects"]:
                if row["subject"].id == subject.id:
                    student_progress = row
                    break

    context = {
        "page_title": f"{subject.name} — {subject.course.name}",
        "subject": subject,
        "can_manage": can_manage,
        "topics": topics_qs.prefetch_related("materials"),
        "materials": materials_qs,
        "unassigned_materials": materials_qs.filter(topic__isnull=True),
        "announcements": SubjectAnnouncement.objects.filter(subject=subject).select_related("author")[:10],
        "assignments": Assignment.objects.filter(subject=subject, is_published=True)[:10],
        "assessments": assessments_qs[:10],
        "classrooms": VirtualClassroom.objects.filter(subject=subject).order_by("-scheduled_start")[:8],
        "recordings": recordings_qs[:8],
        "discussions": DiscussionThread.objects.filter(subject=subject, is_hidden=False).select_related("author")[:8],
        "completed_material_ids": completed_material_ids,
        "student_progress": student_progress,
        "topic_form": LearningTopicForm(),
        "material_form": LearningMaterialForm(subject=subject),
        "announcement_form": SubjectAnnouncementForm(),
    }
    return render(request, "elearning_template/subject_portal.html", context)


def material_library(request):
    user = request.user
    subjects = get_authorized_subjects(user)
    qs = LearningMaterial.objects.filter(subject__in=subjects).select_related(
        "subject__course", "subject__staff__admin", "topic", "uploaded_by"
    )
    if is_student(user) or is_parent(user):
        qs = qs.filter(is_published=True)

    q = request.GET.get("q", "").strip()
    subject_id = request.GET.get("subject", "").strip()
    mtype = request.GET.get("type", "").strip()
    topic_id = request.GET.get("topic", "").strip()

    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(rich_content__icontains=q))
    if subject_id.isdigit():
        qs = qs.filter(subject_id=int(subject_id))
    if mtype:
        qs = qs.filter(material_type=mtype)
    if topic_id.isdigit():
        qs = qs.filter(topic_id=int(topic_id))

    completed_ids = set()
    if is_student(user):
        student = Student.objects.filter(admin=user).first()
        if student:
            completed_ids = set(
                MaterialAccessLog.objects.filter(student=student, is_completed=True).values_list("material_id", flat=True)
            )

    context = {
        "page_title": "Learning Materials Library",
        "materials": qs[:100],
        "subjects": subjects,
        "topics": LearningTopic.objects.filter(subject__in=subjects),
        "material_types": LearningMaterial.MATERIAL_TYPES,
        "q": q,
        "selected_subject": subject_id,
        "selected_type": mtype,
        "selected_topic": topic_id,
        "completed_ids": completed_ids,
    }
    return render(request, "elearning_template/material_library.html", context)


def open_learning_material(request, material_id):
    material = get_object_or_404(LearningMaterial.objects.select_related("subject__course"), id=material_id)
    authorized_ids = set(get_authorized_subjects(request.user).values_list("id", flat=True))
    if material.subject_id not in authorized_ids:
        messages.error(request, "Access denied to this learning material.")
        return redirect("elearning_material_library")

    if is_student(request.user):
        student = Student.objects.filter(admin=request.user).first()
        if student:
            log, created = MaterialAccessLog.objects.get_or_create(material=material, student=student)
            if not created:
                log.access_count += 1
                log.save(update_fields=["access_count", "last_accessed_at"])

    if request.GET.get("download") == "1" and material.file:
        if not material.allow_download and not can_manage_subject(request.user, material.subject):
            messages.warning(request, "Downloads are restricted for this material by your teacher.")
            return redirect("elearning_subject_portal", subject_id=material.subject_id)
        return redirect(material.file.url)

    if material.file:
        return redirect(material.file.url)
    if material.external_url:
        return redirect(material.external_url)
    return redirect("elearning_subject_portal", subject_id=material.subject_id)


# ============================================================================
# 3. VIRTUAL CLASSROOMS, REAL-TIME WEBRTC (JITSI), WHITEBOARD & ATTENDANCE
# ============================================================================

def virtual_classrooms_list(request):
    user = request.user
    subjects = get_authorized_subjects(user)
    can_create = is_teacher(user) or is_admin_or_dos(user)

    if request.method == "POST" and can_create:
        action = request.POST.get("action", "schedule")

        if action == "instant":
            subj = get_object_or_404(Subject, id=request.POST.get("subject_id"))
            if not can_manage_subject(user, subj):
                messages.error(request, "You can only start instant classes for your assigned subjects.")
                return redirect("elearning_classrooms")
            title = request.POST.get("title", "").strip() or f"Instant Live Lesson — {subj.name}"
            classroom = VirtualClassroom.objects.create(
                title=title,
                subject=subj,
                course=subj.course,
                session=Session.objects.order_by("-id").first(),
                teacher=_get_staff_profile(user) or subj.staff,
                academic_year=str(timezone.localdate().year),
                scheduled_start=timezone.now(),
                started_at=timezone.now(),
                duration_minutes=int(request.POST.get("duration_minutes") or 45),
                is_instant=True,
                status="LIVE",
            )
            notify_course_students(
                subj.course,
                f"LIVE NOW: {classroom.title} ({subj.name}) has started. Join in eLearning -> Virtual Classrooms!",
            )
            messages.success(request, f"Instant virtual classroom '{classroom.title}' is now LIVE!")
            return redirect("elearning_classroom_room", classroom_id=classroom.id)

        form = VirtualClassroomForm(request.POST, request.FILES, subjects_qs=subjects)
        if form.is_valid():
            classroom = form.save(commit=False)
            classroom.course = classroom.subject.course
            classroom.teacher = _get_staff_profile(user) or classroom.subject.staff
            classroom.status = "SCHEDULED"
            classroom.save()

            # Optional recurring copies (next 3 occurrences)
            if classroom.recurrence != "NONE":
                step_days = 1 if classroom.recurrence == "DAILY" else (7 if classroom.recurrence == "WEEKLY" else 14)
                for idx in range(1, 4):
                    VirtualClassroom.objects.create(
                        title=f"{classroom.title} (Part {idx + 1})",
                        subject=classroom.subject,
                        course=classroom.course,
                        session=classroom.session,
                        topic=classroom.topic,
                        teacher=classroom.teacher,
                        academic_year=classroom.academic_year,
                        stream=classroom.stream,
                        description=classroom.description,
                        learning_objectives=classroom.learning_objectives,
                        scheduled_start=classroom.scheduled_start + timedelta(days=step_days * idx),
                        duration_minutes=classroom.duration_minutes,
                        recurrence=classroom.recurrence,
                        status="SCHEDULED",
                        waiting_room_enabled=classroom.waiting_room_enabled,
                        mute_on_entry=classroom.mute_on_entry,
                        allow_student_chat=classroom.allow_student_chat,
                        allow_student_whiteboard=classroom.allow_student_whiteboard,
                        attendance_threshold_percent=classroom.attendance_threshold_percent,
                        late_grace_minutes=classroom.late_grace_minutes,
                    )

            notify_course_students(
                classroom.course,
                f"Scheduled Online Class: {classroom.title} ({classroom.subject.name}) on {classroom.scheduled_start:%b %d, %H:%M}.",
            )
            messages.success(request, f"Virtual classroom '{classroom.title}' scheduled & students notified.")
            return redirect("elearning_classrooms")
        else:
            messages.error(request, f"Please correct the form: {form.errors.as_text()}")

    status_filter = request.GET.get("status", "").strip()
    if is_admin_or_dos(user):
        qs = VirtualClassroom.objects.all()
    elif is_teacher(user):
        staff = _get_staff_profile(user)
        qs = VirtualClassroom.objects.filter(Q(teacher=staff) | Q(subject__in=subjects)).distinct()
    elif is_student(user):
        student = Student.objects.filter(admin=user).first()
        qs = VirtualClassroom.objects.filter(course=student.course) if student and student.course else VirtualClassroom.objects.none()
    elif is_parent(user):
        guardian = ParentGuardian.objects.filter(admin=user).first()
        c_ids = guardian.students.values_list("course_id", flat=True) if guardian else []
        qs = VirtualClassroom.objects.filter(course_id__in=c_ids)
    else:
        qs = VirtualClassroom.objects.none()

    qs = qs.select_related("course", "subject", "teacher__admin", "session").annotate(
        attendees_count=Count("attendance_records", filter=Q(attendance_records__joined_at__isnull=False))
    )
    if status_filter:
        qs = qs.filter(status=status_filter)

    context = {
        "page_title": "Virtual Classrooms",
        "classrooms": qs[:80],
        "subjects": subjects,
        "can_create": can_create,
        "status_filter": status_filter,
        "form": VirtualClassroomForm(subjects_qs=subjects) if can_create else None,
        "live_count": qs.filter(status="LIVE").count(),
        "scheduled_count": qs.filter(status="SCHEDULED").count(),
    }
    return render(request, "elearning_template/virtual_classrooms.html", context)


def virtual_classroom_room(request, classroom_id):
    classroom = get_object_or_404(
        VirtualClassroom.objects.select_related("course", "subject", "teacher__admin", "topic", "session"),
        id=classroom_id,
    )
    user = request.user
    if not can_access_classroom(user, classroom):
        messages.error(request, "You do not have permission to enter this virtual classroom.")
        return redirect("elearning_classrooms")

    is_host = is_admin_or_dos(user) or (
        is_teacher(user) and (_get_staff_profile(user) == classroom.teacher or can_manage_subject(user, classroom.subject))
    )

    if is_student(user):
        if classroom.status == "CANCELLED":
            messages.warning(request, "This virtual class has been cancelled.")
            return redirect("elearning_classrooms")
        if classroom.is_locked:
            messages.warning(request, "This classroom is currently locked by the teacher.")
            return redirect("elearning_classrooms")
        student = Student.objects.filter(admin=user).first()
        if student:
            record, allowed = record_student_join(classroom, student)
            if not allowed:
                messages.error(request, "You were removed from this virtual classroom by the host.")
                return redirect("elearning_classrooms")
    elif is_host and classroom.status == "SCHEDULED":
        # Auto-activate room when teacher launches it
        classroom.status = "LIVE"
        classroom.started_at = classroom.started_at or timezone.now()
        classroom.save(update_fields=["status", "started_at"])
        notify_course_students(
            classroom.course,
            f"LIVE NOW: {classroom.title} ({classroom.subject.name}) has started.",
        )

    attendees = VirtualAttendanceRecord.objects.filter(classroom=classroom).select_related("student__admin")
    chat_messages = VirtualClassroomMessage.objects.filter(classroom=classroom).select_related("sender")[:80]
    lesson_materials = LearningMaterial.objects.filter(
        subject=classroom.subject, is_published=True
    )[:10]

    context = {
        "page_title": classroom.title,
        "classroom": classroom,
        "is_host": is_host,
        "jitsi_domain": _get_jitsi_domain(),
        "display_name": user.get_full_name() or user.email,
        "attendees": attendees,
        "chat_messages": chat_messages,
        "lesson_materials": lesson_materials,
        "enrolled_count": Student.objects.filter(course=classroom.course).count(),
    }
    return render(request, "elearning_template/virtual_classroom_room.html", context)


@require_POST
def classroom_heartbeat(request, classroom_id):
    classroom = get_object_or_404(VirtualClassroom, id=classroom_id)
    if not can_access_classroom(request.user, classroom):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    payload = {
        "classroom_status": classroom.status,
        "is_locked": classroom.is_locked,
        "allow_student_whiteboard": classroom.allow_student_whiteboard,
        "allow_student_chat": classroom.allow_student_chat,
        "server_time": timezone.now().isoformat(),
    }

    if is_student(request.user):
        student = Student.objects.filter(admin=request.user).first()
        if student:
            rec = record_student_heartbeat(classroom, student)
            payload.update({
                "removed": rec.is_removed,
                "muted": rec.is_muted_by_host,
                "active_seconds": rec.active_seconds,
                "participation_percent": rec.participation_percent,
                "attendance_status": rec.get_status_display(),
                "hand_raised": rec.hand_raised,
            })

    # Active participant list for real-time panel
    cutoff = timezone.now() - timedelta(minutes=2)
    participants = []
    for rec in VirtualAttendanceRecord.objects.filter(
        classroom=classroom, joined_at__isnull=False, is_removed=False
    ).select_related("student__admin"):
        participants.append({
            "student_id": rec.student_id,
            "name": rec.student.admin.get_full_name() or rec.student.admin.email,
            "status": rec.status,
            "participation_percent": rec.participation_percent,
            "active_minutes": round(rec.active_seconds / 60.0, 1),
            "hand_raised": rec.hand_raised,
            "muted": rec.is_muted_by_host,
            "online": bool(rec.last_heartbeat_at and rec.last_heartbeat_at >= cutoff and not rec.left_at),
        })
    payload["participants"] = participants
    return JsonResponse(payload)


@require_POST
def classroom_leave(request, classroom_id):
    classroom = get_object_or_404(VirtualClassroom, id=classroom_id)
    if is_student(request.user):
        student = Student.objects.filter(admin=request.user).first()
        if student:
            record_student_leave(classroom, student)
    return JsonResponse({"ok": True})


def classroom_chat_api(request, classroom_id):
    classroom = get_object_or_404(VirtualClassroom, id=classroom_id)
    if not can_access_classroom(request.user, classroom):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "POST":
        if is_student(request.user) and not classroom.allow_student_chat:
            return JsonResponse({"error": "Chat is currently disabled by the teacher."}, status=403)
        text = request.POST.get("message", "").strip()
        is_question = request.POST.get("is_question") in ("1", "true", "on")
        if text:
            VirtualClassroomMessage.objects.create(
                classroom=classroom,
                sender=request.user,
                message=text[:1500],
                is_question=is_question,
            )

    msgs = [
        {
            "id": m.id,
            "sender": m.sender.get_full_name() or m.sender.email,
            "role": m.sender.get_user_type_display(),
            "is_teacher": str(m.sender.user_type) in ("1", "2", "4", "6"),
            "message": m.message,
            "is_question": m.is_question,
            "time": timezone.localtime(m.created_at).strftime("%H:%M"),
        }
        for m in VirtualClassroomMessage.objects.filter(classroom=classroom).select_related("sender")[:100]
    ]
    return JsonResponse({"messages": msgs})


def classroom_whiteboard_api(request, classroom_id):
    classroom = get_object_or_404(VirtualClassroom, id=classroom_id)
    if not can_access_classroom(request.user, classroom):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    is_host = is_admin_or_dos(request.user) or (
        is_teacher(request.user) and (_get_staff_profile(request.user) == classroom.teacher or can_manage_subject(request.user, classroom.subject))
    )

    if request.method == "POST":
        if not is_host and not classroom.allow_student_whiteboard:
            return JsonResponse({"error": "Whiteboard annotation is restricted to the teacher."}, status=403)
        data = request.POST.get("whiteboard_data", "")
        # Basic safety cap (~2MB base64 PNG)
        if len(data) <= 2_500_000:
            classroom.whiteboard_data = data
            classroom.save(update_fields=["whiteboard_data", "updated_at"])
        return JsonResponse({"ok": True, "updated_at": classroom.updated_at.isoformat()})

    return JsonResponse({
        "whiteboard_data": classroom.whiteboard_data,
        "allow_student_whiteboard": classroom.allow_student_whiteboard,
        "updated_at": classroom.updated_at.isoformat(),
    })


@require_POST
def classroom_control_api(request, classroom_id):
    classroom = get_object_or_404(VirtualClassroom, id=classroom_id)
    user = request.user
    action = request.POST.get("action", "")

    # Student self-action: raise/lower hand
    if action == "toggle_hand" and is_student(user):
        student = Student.objects.filter(admin=user).first()
        if student:
            rec, _ = VirtualAttendanceRecord.objects.get_or_create(classroom=classroom, student=student)
            rec.hand_raised = not rec.hand_raised
            rec.save(update_fields=["hand_raised"])
            return JsonResponse({"ok": True, "hand_raised": rec.hand_raised})

    is_host = is_admin_or_dos(user) or (
        is_teacher(user) and (_get_staff_profile(user) == classroom.teacher or can_manage_subject(user, classroom.subject))
    )
    if not is_host:
        return JsonResponse({"error": "Teacher host permission required."}, status=403)

    if action == "start":
        classroom.status = "LIVE"
        classroom.started_at = classroom.started_at or timezone.now()
        classroom.save(update_fields=["status", "started_at"])
        notify_course_students(classroom.course, f"LIVE NOW: {classroom.title} ({classroom.subject.name})")
    elif action == "end":
        classroom.status = "ENDED"
        classroom.ended_at = timezone.now()
        classroom.save(update_fields=["status", "ended_at"])
        sync_classroom_attendance_to_sgs(classroom, actor=user)
    elif action == "cancel":
        classroom.status = "CANCELLED"
        classroom.save(update_fields=["status"])
        notify_course_students(classroom.course, f"CANCELLED: Virtual lesson '{classroom.title}' was cancelled.")
    elif action == "toggle_lock":
        classroom.is_locked = not classroom.is_locked
        classroom.save(update_fields=["is_locked"])
    elif action == "toggle_whiteboard":
        classroom.allow_student_whiteboard = not classroom.allow_student_whiteboard
        classroom.save(update_fields=["allow_student_whiteboard"])
    elif action == "toggle_chat":
        classroom.allow_student_chat = not classroom.allow_student_chat
        classroom.save(update_fields=["allow_student_chat"])
    elif action == "mute_student":
        st_id = request.POST.get("student_id")
        rec = VirtualAttendanceRecord.objects.filter(classroom=classroom, student_id=st_id).first()
        if rec:
            rec.is_muted_by_host = not rec.is_muted_by_host
            rec.save(update_fields=["is_muted_by_host"])
    elif action == "remove_student":
        st_id = request.POST.get("student_id")
        rec = VirtualAttendanceRecord.objects.filter(classroom=classroom, student_id=st_id).first()
        if rec:
            rec.is_removed = True
            rec.left_at = timezone.now()
            rec.save(update_fields=["is_removed", "left_at"])
    elif action == "sync_attendance":
        _, count = sync_classroom_attendance_to_sgs(classroom, actor=user)
        if not request.headers.get("x-requested-with"):
            messages.success(request, f"Synchronized {count} student attendance records to SGS Attendance.")
            return redirect("elearning_classroom_attendance", classroom_id=classroom.id)
        return JsonResponse({"ok": True, "synced_count": count})
    elif action == "override_attendance":
        st_id = request.POST.get("student_id")
        new_status = request.POST.get("status", "PRESENT")
        note = request.POST.get("note", "").strip()
        rec, _ = VirtualAttendanceRecord.objects.get_or_create(classroom=classroom, student_id=st_id)
        rec.status = new_status
        rec.teacher_override = True
        rec.override_note = note
        log = list(rec.audit_log or [])
        log.append({
            "event": "TEACHER_OVERRIDE",
            "at": timezone.now().isoformat(),
            "detail": f"Set to {new_status} by {user.email}: {note}",
        })
        rec.audit_log = log[-80:]
        rec.save()
        if classroom.attendance_synced:
            sync_classroom_attendance_to_sgs(classroom, actor=user)
        messages.success(request, "Attendance record updated and logged in audit trail.")
        return redirect("elearning_classroom_attendance", classroom_id=classroom.id)

    if not request.headers.get("x-requested-with"):
        return redirect("elearning_classroom_room", classroom_id=classroom.id)
    return JsonResponse({
        "ok": True,
        "status": classroom.status,
        "is_locked": classroom.is_locked,
        "allow_student_whiteboard": classroom.allow_student_whiteboard,
        "allow_student_chat": classroom.allow_student_chat,
    })


def classroom_attendance_report(request, classroom_id):
    classroom = get_object_or_404(
        VirtualClassroom.objects.select_related("course", "subject", "teacher__admin", "session"),
        id=classroom_id,
    )
    if not (is_admin_or_dos(request.user) or is_teacher(request.user)):
        messages.error(request, "Only teachers and administrators can manage virtual attendance reports.")
        return redirect("elearning_classrooms")

    eligible_students = (
        classroom.invited_students.select_related("admin")
        if classroom.invited_students.exists()
        else Student.objects.filter(course=classroom.course).select_related("admin")
    )

    records_by_student = {
        r.student_id: r
        for r in VirtualAttendanceRecord.objects.filter(classroom=classroom).select_related("student__admin")
    }
    rows = []
    cutoff = timezone.now() - timedelta(minutes=2)
    counts = {"enrolled": 0, "live_now": 0, "present": 0, "late": 0, "partial": 0, "absent": 0}

    for st in eligible_students:
        counts["enrolled"] += 1
        rec = records_by_student.get(st.id)
        if rec:
            recalculate_virtual_attendance_status(rec)
            is_live = bool(rec.last_heartbeat_at and rec.last_heartbeat_at >= cutoff and not rec.left_at)
            if is_live:
                counts["live_now"] += 1
            if rec.status == "PRESENT":
                counts["present"] += 1
            elif rec.status == "LATE":
                counts["late"] += 1
            elif rec.status == "PARTIAL":
                counts["partial"] += 1
            elif rec.status == "ABSENT":
                counts["absent"] += 1
            rows.append({"student": st, "record": rec, "is_live": is_live})
        else:
            counts["absent"] += 1
            rows.append({"student": st, "record": None, "is_live": False})

    att_pct = round(((counts["present"] + counts["late"]) / counts["enrolled"] * 100.0), 1) if counts["enrolled"] else 0.0

    context = {
        "page_title": f"Virtual Attendance — {classroom.title}",
        "classroom": classroom,
        "rows": rows,
        "counts": counts,
        "attendance_percent": att_pct,
        "status_choices": VirtualAttendanceRecord.STATUS_CHOICES,
    }
    return render(request, "elearning_template/classroom_attendance.html", context)


@require_POST
def upload_classroom_recording(request, classroom_id):
    classroom = get_object_or_404(VirtualClassroom, id=classroom_id)
    if not (is_admin_or_dos(request.user) or is_teacher(request.user)):
        return JsonResponse({"error": "Only teachers can upload lecture recordings."}, status=403)

    video = request.FILES.get("video_file")
    if not video:
        return JsonResponse({"error": "No recording file provided."}, status=400)
    ext = os.path.splitext(video.name)[1].lower()
    if ext not in ALLOWED_RECORDING_EXTENSIONS:
        return JsonResponse({"error": f"Unsupported video format '{ext}'."}, status=400)
    if video.size > MAX_RECORDING_SIZE_BYTES:
        return JsonResponse({"error": "Recording exceeds maximum file size limit."}, status=400)

    duration = int(float(request.POST.get("duration_seconds") or 0))
    rec = LectureRecording.objects.create(
        classroom=classroom,
        subject=classroom.subject,
        course=classroom.course,
        teacher=_get_staff_profile(request.user) or classroom.teacher,
        session=classroom.session,
        academic_year=classroom.academic_year,
        title=request.POST.get("title", "").strip() or f"{classroom.title} (Recording)",
        description=f"Recorded live virtual lesson for {classroom.course.name} — {classroom.subject.name}.",
        video_file=video,
        duration_seconds=duration,
        file_size_bytes=video.size,
        status="READY",
        is_published=False,
    )
    return JsonResponse({
        "ok": True,
        "recording_id": rec.id,
        "library_url": reverse("elearning_recordings"),
    })


# ============================================================================
# 4. LECTURE RECORDING LIBRARY
# ============================================================================

def recordings_library(request):
    user = request.user
    subjects = get_authorized_subjects(user)
    can_manage = is_teacher(user) or is_admin_or_dos(user)

    if request.method == "POST" and can_manage:
        action = request.POST.get("action", "upload")
        if action == "toggle_publish":
            rec = get_object_or_404(LectureRecording, id=request.POST.get("recording_id"))
            if can_manage_subject(user, rec.subject):
                rec.is_published = not rec.is_published
                rec.save(update_fields=["is_published"])
                if rec.is_published:
                    notify_course_students(
                        rec.course,
                        f"New Lecture Recording published in {rec.subject.name}: {rec.title}",
                    )
                messages.success(request, f"Recording {'published' if rec.is_published else 'restricted'}.")
            return redirect("elearning_recordings")
        elif action == "toggle_download":
            rec = get_object_or_404(LectureRecording, id=request.POST.get("recording_id"))
            if can_manage_subject(user, rec.subject):
                rec.allow_download = not rec.allow_download
                rec.save(update_fields=["allow_download"])
                messages.success(request, "Download permission updated.")
            return redirect("elearning_recordings")
        elif action == "delete":
            rec = get_object_or_404(LectureRecording, id=request.POST.get("recording_id"))
            if can_manage_subject(user, rec.subject):
                rec.delete()
                messages.success(request, "Recording deleted.")
            return redirect("elearning_recordings")
        else:
            classrooms_qs = VirtualClassroom.objects.filter(subject__in=subjects)
            form = LectureRecordingForm(
                request.POST, request.FILES, subjects_qs=subjects, classrooms_qs=classrooms_qs
            )
            if form.is_valid():
                rec = form.save(commit=False)
                rec.course = rec.subject.course
                rec.teacher = _get_staff_profile(user) or rec.subject.staff
                if rec.video_file:
                    rec.file_size_bytes = rec.video_file.size
                rec.status = "READY"
                rec.save()
                if rec.is_published:
                    notify_course_students(
                        rec.course,
                        f"New Lecture Recording available in {rec.subject.name}: {rec.title}",
                    )
                messages.success(request, f"Recording '{rec.title}' saved to library.")
                return redirect("elearning_recordings")
            else:
                messages.error(request, f"Recording error: {form.errors.as_text()}")

    qs = LectureRecording.objects.filter(subject__in=subjects).select_related(
        "subject", "course", "teacher__admin", "classroom", "session"
    )
    if not can_manage:
        qs = qs.filter(is_published=True)

    subject_id = request.GET.get("subject", "").strip()
    if subject_id.isdigit():
        qs = qs.filter(subject_id=int(subject_id))

    classrooms_qs = VirtualClassroom.objects.filter(subject__in=subjects)
    context = {
        "page_title": "Lecture Recordings Library",
        "recordings": qs[:60],
        "subjects": subjects,
        "selected_subject": subject_id,
        "can_manage": can_manage,
        "form": LectureRecordingForm(subjects_qs=subjects, classrooms_qs=classrooms_qs) if can_manage else None,
    }
    return render(request, "elearning_template/recordings_library.html", context)


# ============================================================================
# 5. ONLINE QUIZZES, EXAMINATIONS, QUESTION BANK & GRADING ENGINE
# ============================================================================

def assessments_list(request):
    user = request.user
    subjects = get_authorized_subjects(user)
    can_manage = is_teacher(user) or is_admin_or_dos(user)

    if request.method == "POST" and can_manage:
        form = OnlineAssessmentForm(request.POST, subjects_qs=subjects)
        if form.is_valid():
            assessment = form.save(commit=False)
            assessment.course = assessment.subject.course
            assessment.teacher = _get_staff_profile(user) or assessment.subject.staff
            assessment.save()
            if assessment.is_published:
                notify_course_students(
                    assessment.course,
                    f"New {assessment.get_assessment_type_display()} published in {assessment.subject.name}: {assessment.title}",
                )
            messages.success(request, f"Assessment '{assessment.title}' created. Now add questions below.")
            return redirect("elearning_assessment_manage", assessment_id=assessment.id)
        else:
            messages.error(request, f"Could not create assessment: {form.errors.as_text()}")

    qs = OnlineAssessment.objects.filter(subject__in=subjects).select_related(
        "subject", "course", "topic", "teacher__admin"
    ).annotate(
        question_count=Count("questions", distinct=True),
        attempt_count=Count("attempts", distinct=True),
    )
    if not can_manage:
        qs = qs.filter(is_published=True)

    student_attempts = {}
    if is_student(user):
        student = Student.objects.filter(admin=user).first()
        if student:
            for att in AssessmentAttempt.objects.filter(student=student).order_by("-attempt_number"):
                student_attempts.setdefault(att.assessment_id, att)

    rows = [
        {"assessment": a, "student_attempt": student_attempts.get(a.id)}
        for a in qs[:80]
    ]

    context = {
        "page_title": "Online Quizzes & Examinations",
        "rows": rows,
        "subjects": subjects,
        "can_manage": can_manage,
        "form": OnlineAssessmentForm(subjects_qs=subjects) if can_manage else None,
    }
    return render(request, "elearning_template/assessments_list.html", context)


def assessment_manage(request, assessment_id):
    assessment = get_object_or_404(
        OnlineAssessment.objects.select_related("subject", "course", "topic", "teacher__admin"),
        id=assessment_id,
    )
    if not can_manage_subject(request.user, assessment.subject):
        messages.error(request, "Only authorized teachers or administrators can manage this assessment.")
        return redirect("elearning_assessments")

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "add_question":
            qtype = request.POST.get("question_type", "MCQ")
            prompt = request.POST.get("prompt", "").strip()
            marks = float(request.POST.get("marks") or 2.0)
            difficulty = request.POST.get("difficulty", "MEDIUM")
            marking_guide = request.POST.get("marking_guide", "").strip()
            choices_raw = request.POST.get("choices_text", "").strip()
            correct_raw = request.POST.get("correct_answer_text", "").strip()

            choices = []
            correct_answer = {}
            if qtype == "TRUE_FALSE":
                choices = ["True", "False"]
                correct_answer = {"answer": correct_raw or "True"}
            elif qtype == "MCQ":
                choices = [line.strip() for line in choices_raw.splitlines() if line.strip()]
                correct_answer = {"answer": correct_raw or (choices[0] if choices else "")}
            elif qtype == "MULTI_SELECT":
                choices = [line.strip() for line in choices_raw.splitlines() if line.strip()]
                correct_answer = {
                    "answers": [x.strip() for x in correct_raw.split(",") if x.strip()]
                }
            elif qtype == "FILL_BLANK":
                correct_answer = {
                    "answers": [x.strip() for x in correct_raw.split(",") if x.strip()]
                }
            elif qtype == "MATCHING":
                pairs = {}
                for line in choices_raw.splitlines():
                    if "=" in line:
                        left, right = line.split("=", 1)
                        pairs[left.strip()] = right.strip()
                choices = list(pairs.keys())
                correct_answer = {"pairs": pairs}

            if prompt:
                QuestionBankItem.objects.create(
                    subject=assessment.subject,
                    topic=assessment.topic,
                    assessment=assessment,
                    created_by=_get_staff_profile(request.user) or assessment.teacher,
                    question_type=qtype,
                    prompt=prompt,
                    choices=choices,
                    correct_answer=correct_answer,
                    marking_guide=marking_guide,
                    marks=marks,
                    order=assessment.questions.count() + 1,
                    difficulty=difficulty,
                )
                assessment.recalculate_total_marks()
                messages.success(request, "Question added to assessment.")
            else:
                messages.error(request, "Question prompt cannot be empty.")
            return redirect("elearning_assessment_manage", assessment_id=assessment.id)

        elif action == "import_bank_question":
            bank_q = get_object_or_404(QuestionBankItem, id=request.POST.get("bank_question_id"))
            QuestionBankItem.objects.create(
                subject=assessment.subject,
                topic=bank_q.topic or assessment.topic,
                assessment=assessment,
                created_by=_get_staff_profile(request.user) or assessment.teacher,
                question_type=bank_q.question_type,
                prompt=bank_q.prompt,
                choices=bank_q.choices,
                correct_answer=bank_q.correct_answer,
                marking_guide=bank_q.marking_guide,
                marks=bank_q.marks,
                order=assessment.questions.count() + 1,
                difficulty=bank_q.difficulty,
            )
            assessment.recalculate_total_marks()
            messages.success(request, "Question imported from Question Bank.")
            return redirect("elearning_assessment_manage", assessment_id=assessment.id)

        elif action == "delete_question":
            q = get_object_or_404(QuestionBankItem, id=request.POST.get("question_id"), assessment=assessment)
            q.delete()
            assessment.recalculate_total_marks()
            messages.success(request, "Question removed.")
            return redirect("elearning_assessment_manage", assessment_id=assessment.id)

        elif action == "toggle_publish":
            assessment.is_published = not assessment.is_published
            assessment.save(update_fields=["is_published"])
            if assessment.is_published:
                notify_course_students(
                    assessment.course,
                    f"New {assessment.get_assessment_type_display()} available in {assessment.subject.name}: {assessment.title}",
                )
            messages.success(request, f"Assessment {'published to students' if assessment.is_published else 'set to draft'}.")
            return redirect("elearning_assessment_manage", assessment_id=assessment.id)

        elif action == "toggle_publish_results":
            assessment.results_published = not assessment.results_published
            assessment.save(update_fields=["results_published"])
            if assessment.results_published:
                notify_course_students(
                    assessment.course,
                    f"Results published for {assessment.title} ({assessment.subject.name}).",
                )
            messages.success(request, f"Results {'published' if assessment.results_published else 'hidden'}.")
            return redirect("elearning_assessment_manage", assessment_id=assessment.id)

        elif action == "grade_attempt":
            attempt = get_object_or_404(AssessmentAttempt, id=request.POST.get("attempt_id"), assessment=assessment)
            subj_total = 0.0
            for ans in attempt.answers.select_related("question").all():
                if not ans.question.is_objective:
                    raw = request.POST.get(f"marks_{ans.id}")
                    comment = request.POST.get(f"comment_{ans.id}", "").strip()
                    if raw is not None and raw != "":
                        val = max(0.0, min(float(raw), ans.question.marks))
                        ans.marks_awarded = val
                        ans.teacher_comment = comment
                        ans.save(update_fields=["marks_awarded", "teacher_comment"])
                        subj_total += val
                    elif ans.marks_awarded is not None:
                        subj_total += float(ans.marks_awarded)

            total_possible = assessment.total_marks or sum(q.marks for q in assessment.questions.all()) or 100.0
            attempt.subjective_score = round(subj_total, 2)
            attempt.total_score = round(attempt.objective_score + attempt.subjective_score, 2)
            attempt.percentage = round((attempt.total_score / total_possible) * 100.0, 1) if total_possible else 0.0
            attempt.passed = attempt.total_score >= (assessment.pass_marks if assessment.pass_marks <= total_possible else total_possible * 0.5)
            attempt.teacher_feedback = request.POST.get("teacher_feedback", "").strip()
            attempt.status = "GRADED"
            attempt.graded_by = request.user
            attempt.graded_at = timezone.now()
            attempt.save()
            messages.success(request, f"Graded {attempt.student}'s submission ({attempt.total_score}/{total_possible}).")
            return redirect("elearning_assessment_manage", assessment_id=assessment.id)

    bank_questions = QuestionBankItem.objects.filter(
        subject=assessment.subject, assessment__isnull=True
    )[:30]
    attempts = AssessmentAttempt.objects.filter(assessment=assessment).select_related(
        "student__admin"
    ).prefetch_related("answers__question")

    context = {
        "page_title": f"Manage Assessment — {assessment.title}",
        "assessment": assessment,
        "questions": assessment.questions.all(),
        "bank_questions": bank_questions,
        "attempts": attempts,
        "question_types": QuestionBankItem.QUESTION_TYPES,
        "difficulty_choices": QuestionBankItem.DIFFICULTY_CHOICES,
    }
    return render(request, "elearning_template/assessment_manage.html", context)


def question_bank_view(request):
    user = request.user
    if not (is_teacher(user) or is_admin_or_dos(user)):
        messages.error(request, "Question Bank is accessible to teachers and academic administrators.")
        return redirect("elearning_dashboard")

    subjects = get_authorized_subjects(user)
    if request.method == "POST":
        subj = get_object_or_404(Subject, id=request.POST.get("subject_id"))
        if not can_manage_subject(user, subj):
            messages.error(request, "Unauthorized subject.")
            return redirect("elearning_question_bank")
        qtype = request.POST.get("question_type", "MCQ")
        prompt = request.POST.get("prompt", "").strip()
        choices_raw = request.POST.get("choices_text", "").strip()
        correct_raw = request.POST.get("correct_answer_text", "").strip()
        choices = [l.strip() for l in choices_raw.splitlines() if l.strip()] if qtype != "TRUE_FALSE" else ["True", "False"]
        correct_answer = {"answer": correct_raw} if qtype in ("MCQ", "TRUE_FALSE") else (
            {"answers": [x.strip() for x in correct_raw.split(",") if x.strip()]}
            if qtype in ("MULTI_SELECT", "FILL_BLANK") else {}
        )
        topic_id = request.POST.get("topic_id")
        topic = LearningTopic.objects.filter(id=topic_id, subject=subj).first() if topic_id else None
        if prompt:
            QuestionBankItem.objects.create(
                subject=subj,
                topic=topic,
                assessment=None,
                created_by=_get_staff_profile(user) or subj.staff,
                question_type=qtype,
                prompt=prompt,
                choices=choices,
                correct_answer=correct_answer,
                marking_guide=request.POST.get("marking_guide", "").strip(),
                marks=float(request.POST.get("marks") or 2.0),
                difficulty=request.POST.get("difficulty", "MEDIUM"),
            )
            messages.success(request, "Question saved to reusable Question Bank.")
        return redirect("elearning_question_bank")

    subject_id = request.GET.get("subject", "").strip()
    qs = QuestionBankItem.objects.filter(subject__in=subjects, assessment__isnull=True).select_related("subject", "topic")
    if subject_id.isdigit():
        qs = qs.filter(subject_id=int(subject_id))

    context = {
        "page_title": "Subject Question Bank",
        "questions": qs[:100],
        "subjects": subjects,
        "topics": LearningTopic.objects.filter(subject__in=subjects),
        "selected_subject": subject_id,
        "question_types": QuestionBankItem.QUESTION_TYPES,
        "difficulty_choices": QuestionBankItem.DIFFICULTY_CHOICES,
    }
    return render(request, "elearning_template/question_bank.html", context)


def take_assessment(request, assessment_id):
    assessment = get_object_or_404(
        OnlineAssessment.objects.select_related("subject", "course", "topic"),
        id=assessment_id,
    )
    if not is_student(request.user):
        messages.info(request, "Previewing assessment management view.")
        return redirect("elearning_assessment_manage", assessment_id=assessment.id)

    student = Student.objects.filter(admin=request.user).first()
    if not student or student.course_id != assessment.course_id or not assessment.is_published:
        messages.error(request, "This assessment is not currently available to you.")
        return redirect("elearning_assessments")

    now = timezone.now()
    if assessment.available_from and now < assessment.available_from:
        messages.warning(request, f"This assessment opens on {assessment.available_from:%b %d, %Y %H:%M}.")
        return redirect("elearning_assessments")

    # Resume active attempt or start a new one within max_attempts
    attempt = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student, status="IN_PROGRESS"
    ).first()

    if not attempt:
        completed_count = AssessmentAttempt.objects.filter(
            assessment=assessment, student=student
        ).count()
        if completed_count >= assessment.max_attempts:
            last_att = AssessmentAttempt.objects.filter(
                assessment=assessment, student=student
            ).order_by("-attempt_number").first()
            return redirect("elearning_attempt_result", attempt_id=last_att.id)
        if assessment.deadline and now > assessment.deadline:
            messages.error(request, "The deadline for this assessment has passed.")
            return redirect("elearning_assessments")

        attempt = AssessmentAttempt.objects.create(
            assessment=assessment,
            student=student,
            attempt_number=completed_count + 1,
            status="IN_PROGRESS",
            audit_log=[{"event": "STARTED", "at": now.isoformat()}],
        )

    elapsed_secs = int((now - attempt.started_at).total_seconds())
    remaining_secs = max(0, (assessment.duration_minutes * 60) - elapsed_secs)

    if request.method == "POST":
        if attempt.status != "IN_PROGRESS":
            return redirect("elearning_attempt_result", attempt_id=attempt.id)
        _save_attempt_answers_from_request(attempt, request)
        timed_out = request.POST.get("timed_out") == "1" or remaining_secs <= 0
        finalize_and_grade_attempt(attempt, timed_out=timed_out)
        messages.success(request, "Assessment submitted successfully!")
        return redirect("elearning_attempt_result", attempt_id=attempt.id)

    questions = list(assessment.questions.all())
    if assessment.randomize_questions:
        rng = random.Random(attempt.id)
        rng.shuffle(questions)

    existing_answers = {
        a.question_id: a for a in attempt.answers.all()
    }
    question_rows = []
    for idx, q in enumerate(questions, start=1):
        choices = list(q.choices or [])
        if assessment.randomize_choices and q.question_type in ("MCQ", "MULTI_SELECT"):
            rng = random.Random(attempt.id * 100 + q.id)
            rng.shuffle(choices)
        matching_options = []
        if q.question_type == "MATCHING" and isinstance(q.correct_answer, dict):
            matching_options = sorted(set((q.correct_answer.get("pairs") or {}).values()))
        question_rows.append({
            "index": idx,
            "question": q,
            "choices": choices,
            "matching_options": matching_options,
            "answer": existing_answers.get(q.id),
        })

    context = {
        "page_title": assessment.title,
        "assessment": assessment,
        "attempt": attempt,
        "question_rows": question_rows,
        "remaining_seconds": remaining_secs,
    }
    return render(request, "elearning_template/take_assessment.html", context)


def _save_attempt_answers_from_request(attempt, request):
    for q in attempt.assessment.questions.all():
        ans, _ = AssessmentAnswer.objects.get_or_create(attempt=attempt, question=q)
        if q.question_type in ("MCQ", "TRUE_FALSE", "FILL_BLANK"):
            val = request.POST.get(f"q_{q.id}", "").strip()
            ans.answer_data = {"answer": val}
            ans.text_answer = val
        elif q.question_type == "MULTI_SELECT":
            vals = request.POST.getlist(f"q_{q.id}")
            ans.answer_data = {"answers": vals}
            ans.text_answer = ", ".join(vals)
        elif q.question_type == "MATCHING":
            pairs = {}
            for left in (q.choices or []):
                pairs[left] = request.POST.get(f"q_{q.id}_match_{left}", "").strip()
            ans.answer_data = {"pairs": pairs}
        elif q.question_type == "FILE_UPLOAD":
            if f"q_{q.id}_file" in request.FILES:
                ans.uploaded_file = request.FILES[f"q_{q.id}_file"]
            ans.text_answer = request.POST.get(f"q_{q.id}", "").strip()
        else:
            val = request.POST.get(f"q_{q.id}", "").strip()
            ans.text_answer = val
            ans.answer_data = {"answer": val}
        ans.save()


@require_POST
def assessment_autosave(request, assessment_id):
    assessment = get_object_or_404(OnlineAssessment, id=assessment_id)
    student = Student.objects.filter(admin=request.user).first()
    attempt = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student, status="IN_PROGRESS"
    ).first()
    if not attempt:
        return JsonResponse({"error": "No active attempt"}, status=404)

    _save_attempt_answers_from_request(attempt, request)
    if request.POST.get("tab_switch") == "1":
        attempt.tab_switch_count += 1
        log = list(attempt.audit_log or [])
        log.append({"event": "TAB_SWITCH", "at": timezone.now().isoformat()})
        attempt.audit_log = log[-40:]
        attempt.save(update_fields=["tab_switch_count", "audit_log"])

    return JsonResponse({"ok": True, "saved_at": timezone.localtime(timezone.now()).strftime("%H:%M:%S")})


def assessment_attempt_result(request, attempt_id):
    attempt = get_object_or_404(
        AssessmentAttempt.objects.select_related("assessment__subject", "student__admin"),
        id=attempt_id,
    )
    user = request.user
    allowed = (
        is_admin_or_dos(user)
        or can_manage_subject(user, attempt.assessment.subject)
        or (is_student(user) and attempt.student.admin_id == user.id)
        or (is_parent(user) and ParentGuardian.objects.filter(admin=user, students=attempt.student).exists())
    )
    if not allowed:
        messages.error(request, "You are not authorized to view this assessment result.")
        return redirect("elearning_assessments")

    can_see_details = (
        is_admin_or_dos(user)
        or can_manage_subject(user, attempt.assessment.subject)
        or (attempt.assessment.results_published and attempt.assessment.allow_review)
    )
    context = {
        "page_title": f"Assessment Result — {attempt.assessment.title}",
        "attempt": attempt,
        "answers": attempt.answers.select_related("question").all(),
        "can_see_details": can_see_details,
    }
    return render(request, "elearning_template/assessment_result.html", context)


# ============================================================================
# 6. SUBJECT DISCUSSION FORUMS
# ============================================================================

def discussion_forums(request):
    user = request.user
    subjects = get_authorized_subjects(user)
    selected_subject_id = request.GET.get("subject", "").strip()
    selected_subject = subjects.filter(id=int(selected_subject_id)).first() if selected_subject_id.isdigit() else subjects.first()

    if request.method == "POST" and not is_parent(user):
        subj = get_object_or_404(Subject, id=request.POST.get("subject_id"))
        if subj.id not in set(subjects.values_list("id", flat=True)):
            messages.error(request, "Unauthorized subject.")
            return redirect("elearning_discussions")
        form = DiscussionThreadForm(request.POST, subject=subj)
        if form.is_valid():
            thread = form.save(commit=False)
            thread.subject = subj
            thread.author = user
            thread.save()
            messages.success(request, "Discussion topic posted.")
            return redirect("elearning_discussion_detail", thread_id=thread.id)

    qs = DiscussionThread.objects.filter(subject__in=subjects, is_hidden=False).select_related(
        "subject", "topic", "author"
    ).annotate(reply_count=Count("replies", filter=Q(replies__is_hidden=False)))
    if selected_subject and selected_subject_id:
        qs = qs.filter(subject=selected_subject)

    context = {
        "page_title": "Subject Discussion Forums",
        "threads": qs[:60],
        "subjects": subjects,
        "selected_subject": selected_subject,
        "selected_subject_id": selected_subject_id,
        "form": DiscussionThreadForm(subject=selected_subject) if selected_subject else None,
        "can_post": not is_parent(user),
    }
    return render(request, "elearning_template/discussion_forums.html", context)


def discussion_thread_detail(request, thread_id):
    thread = get_object_or_404(
        DiscussionThread.objects.select_related("subject", "topic", "author"),
        id=thread_id,
    )
    authorized_ids = set(get_authorized_subjects(request.user).values_list("id", flat=True))
    if thread.subject_id not in authorized_ids:
        messages.error(request, "You are not authorized to view this discussion.")
        return redirect("elearning_discussions")

    can_moderate = can_manage_subject(request.user, thread.subject)

    if request.method == "POST":
        action = request.POST.get("action", "reply")
        if action == "reply" and not thread.is_closed and not is_parent(request.user):
            body = request.POST.get("body", "").strip()
            if body:
                DiscussionReply.objects.create(thread=thread, author=request.user, body=body)
                thread.save(update_fields=["updated_at"])
                messages.success(request, "Reply posted.")
        elif can_moderate:
            if action == "toggle_pin":
                thread.is_pinned = not thread.is_pinned
                thread.save(update_fields=["is_pinned"])
            elif action == "toggle_close":
                thread.is_closed = not thread.is_closed
                thread.save(update_fields=["is_closed"])
            elif action == "hide_thread":
                thread.is_hidden = True
                thread.save(update_fields=["is_hidden"])
                messages.success(request, "Thread removed by moderator.")
                return redirect("elearning_discussions")
            elif action == "endorse_reply":
                rep = get_object_or_404(DiscussionReply, id=request.POST.get("reply_id"), thread=thread)
                rep.is_teacher_endorsed = not rep.is_teacher_endorsed
                rep.save(update_fields=["is_teacher_endorsed"])
            elif action == "hide_reply":
                rep = get_object_or_404(DiscussionReply, id=request.POST.get("reply_id"), thread=thread)
                rep.is_hidden = True
                rep.save(update_fields=["is_hidden"])
        return redirect("elearning_discussion_detail", thread_id=thread.id)

    context = {
        "page_title": thread.title,
        "thread": thread,
        "replies": thread.replies.filter(is_hidden=False).select_related("author"),
        "can_moderate": can_moderate,
        "can_reply": not thread.is_closed and not is_parent(request.user),
    }
    return render(request, "elearning_template/discussion_detail.html", context)


# ============================================================================
# 7. AI LEARNING ASSISTANT (STUDENTS & TEACHERS)
# ============================================================================

def ai_learning_assistant(request):
    user = request.user
    if is_parent(user):
        messages.info(request, "AI Learning Assistant is available to students and teachers.")
        return redirect("parent_guardian_portal")

    subjects = get_authorized_subjects(user)
    teacher_tools = is_teacher(user) or is_admin_or_dos(user)
    active_interaction = None

    if request.method == "POST":
        action = request.POST.get("action", "ask")
        if action == "import_ai_quiz" and teacher_tools:
            inter = get_object_or_404(AILearningInteraction, id=request.POST.get("interaction_id"), user=user)
            draft_qs = (inter.structured_payload or {}).get("draft_questions") or []
            if inter.subject and draft_qs:
                assessment = OnlineAssessment.objects.create(
                    title=f"AI Draft Quiz: {inter.topic.title if inter.topic else inter.subject.name}",
                    subject=inter.subject,
                    course=inter.subject.course,
                    topic=inter.topic,
                    teacher=_get_staff_profile(user) or inter.subject.staff,
                    assessment_type="QUIZ",
                    instructions="AI-generated draft — review and edit all questions before publishing to students.",
                    is_published=False,
                )
                for idx, dq in enumerate(draft_qs, start=1):
                    QuestionBankItem.objects.create(
                        subject=inter.subject,
                        topic=inter.topic,
                        assessment=assessment,
                        created_by=_get_staff_profile(user) or inter.subject.staff,
                        question_type=dq.get("question_type", "MCQ"),
                        prompt=dq.get("prompt", ""),
                        choices=dq.get("choices", []),
                        correct_answer=dq.get("correct_answer", {}),
                        marking_guide=dq.get("marking_guide", ""),
                        marks=float(dq.get("marks", 2)),
                        order=idx,
                        is_ai_generated=True,
                        teacher_approved=False,
                    )
                assessment.recalculate_total_marks()
                inter.teacher_approved = True
                inter.save(update_fields=["teacher_approved"])
                messages.success(request, "AI draft imported as an UNPUBLISHED quiz for your review.")
                return redirect("elearning_assessment_manage", assessment_id=assessment.id)
            return redirect("elearning_ai_assistant")

        mode = request.POST.get("mode", "ASK")
        prompt = request.POST.get("prompt", "").strip() or "Help me study this topic"
        subject_id = request.POST.get("subject_id", "").strip()
        topic_id = request.POST.get("topic_id", "").strip()

        subject = subjects.filter(id=int(subject_id)).first() if subject_id.isdigit() else subjects.first()
        topic = LearningTopic.objects.filter(id=int(topic_id), subject=subject).first() if (subject and topic_id.isdigit()) else None

        active_interaction = run_ai_learning_assistant(
            user=user, mode=mode, prompt=prompt, subject=subject, topic=topic
        )

    student_modes = [
        ("ASK", "Ask & Explain Concept"),
        ("SUMMARIZE", "Summarize Learning Materials"),
        ("REVISION_NOTES", "Generate Revision Notes"),
        ("PRACTICE_QUESTIONS", "Practice Exam Questions"),
        ("STUDY_PLAN", "Create Revision Schedule"),
    ]
    teacher_only_modes = [
        ("LESSON_PLAN", "Generate Lesson Plan & Objectives"),
        ("GENERATE_QUIZ", "Draft Quiz (Requires Teacher Review)"),
        ("GENERATE_ASSIGNMENT", "Draft Assignment & Marking Rubric"),
        ("CLASS_ANALYTICS", "Analyze Class Performance"),
    ]

    history = AILearningInteraction.objects.filter(user=user).select_related("subject", "topic")[:15]
    context = {
        "page_title": "AI Learning Assistant",
        "subjects": subjects,
        "topics": LearningTopic.objects.filter(subject__in=subjects),
        "modes": (teacher_only_modes + student_modes) if teacher_tools else student_modes,
        "teacher_tools": teacher_tools,
        "active_interaction": active_interaction or history.first(),
        "history": history,
    }
    return render(request, "elearning_template/ai_assistant.html", context)


# ============================================================================
# 8. ANALYTICS, PROGRESS & CSV / PRINTABLE PDF REPORTING
# ============================================================================

def elearning_analytics(request):
    user = request.user
    subjects = get_authorized_subjects(user)
    courses = Course.objects.all() if is_admin_or_dos(user) else Course.objects.filter(
        id__in=subjects.values_list("course_id", flat=True)
    ).distinct()
    terms = Session.objects.order_by("-id")

    course_id = request.GET.get("course", "").strip()
    subject_id = request.GET.get("subject", "").strip()
    term_id = request.GET.get("term", "").strip()

    filtered_subjects = subjects
    if course_id.isdigit():
        filtered_subjects = filtered_subjects.filter(course_id=int(course_id))
    if subject_id.isdigit():
        filtered_subjects = filtered_subjects.filter(id=int(subject_id))

    subject_analytics = []
    for subj in filtered_subjects:
        v_qs = VirtualClassroom.objects.filter(subject=subj)
        if term_id.isdigit():
            v_qs = v_qs.filter(session_id=int(term_id))
        lessons_count = v_qs.count()
        materials_count = LearningMaterial.objects.filter(subject=subj, is_published=True).count()
        assign_count = Assignment.objects.filter(subject=subj).count()
        sub_count = AssignmentSubmission.objects.filter(
            assignment__subject=subj, status__in=["TURNED_IN", "LATE", "GRADED"]
        ).count()
        quiz_avg = (
            AssessmentAttempt.objects.filter(
                assessment__subject=subj, status="GRADED"
            ).aggregate(a=Avg("percentage"))["a"]
            or 0.0
        )
        att_qs = AttendanceReport.objects.filter(attendance__subject=subj)
        if term_id.isdigit():
            att_qs = att_qs.filter(attendance__session_id=int(term_id))
        att_total = att_qs.count()
        att_present = att_qs.filter(status=True).count()
        att_rate = round((att_present / att_total * 100.0), 1) if att_total else 0.0

        subject_analytics.append({
            "subject": subj,
            "lessons_count": lessons_count,
            "materials_count": materials_count,
            "assignments_count": assign_count,
            "submissions_count": sub_count,
            "quiz_avg": round(quiz_avg, 1),
            "attendance_rate": att_rate,
            "students_count": Student.objects.filter(course=subj.course).count(),
        })

    # Export Excel-compatible CSV report
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="jass_elearning_analytics.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Class", "Subject", "Teacher", "Students", "Virtual Lessons",
            "Learning Materials", "Assignments", "Submissions", "Avg Quiz %", "Attendance %"
        ])
        for row in subject_analytics:
            writer.writerow([
                row["subject"].course.name,
                row["subject"].name,
                row["subject"].staff.admin.get_full_name(),
                row["students_count"],
                row["lessons_count"],
                row["materials_count"],
                row["assignments_count"],
                row["submissions_count"],
                row["quiz_avg"],
                row["attendance_rate"],
            ])
        return response

    student_progress = None
    if is_student(user):
        student = Student.objects.filter(admin=user).first()
        student_progress = compute_student_learning_progress(student)

    total_v_seconds = VirtualAttendanceRecord.objects.filter(
        classroom__subject__in=filtered_subjects
    ).aggregate(s=Sum("active_seconds"))["s"] or 0

    context = {
        "page_title": "eLearning Analytics & Reports",
        "subject_analytics": subject_analytics,
        "courses": courses,
        "subjects": subjects,
        "terms": terms,
        "selected_course": course_id,
        "selected_subject": subject_id,
        "selected_term": term_id,
        "storage": compute_storage_usage_mb(),
        "total_learning_hours": round(total_v_seconds / 3600.0, 1),
        "student_progress": student_progress,
        "chart_labels": json.dumps([r["subject"].name for r in subject_analytics[:10]]),
        "chart_attendance": json.dumps([r["attendance_rate"] for r in subject_analytics[:10]]),
        "chart_quiz_avg": json.dumps([r["quiz_avg"] for r in subject_analytics[:10]]),
    }
    return render(request, "elearning_template/analytics.html", context)


# ============================================================================
# 9. PARENT / GUARDIAN READ-ONLY LEARNING MONITORING & ADMIN LINKING
# ============================================================================

def parent_guardian_portal(request):
    user = request.user
    if not (is_parent(user) or is_admin_or_dos(user)):
        messages.error(request, "Parent monitoring portal is restricted to registered guardians.")
        return redirect("elearning_dashboard")

    guardian, _ = ParentGuardian.objects.get_or_create(admin=user)
    linked_students = guardian.students.select_related("admin", "course", "session")
    now = timezone.now()
    today = timezone.localdate()

    children_data = []
    for st in linked_students:
        progress = compute_student_learning_progress(st)
        upcoming_classes = VirtualClassroom.objects.filter(
            course=st.course, status__in=["SCHEDULED", "LIVE"]
        ).select_related("subject", "teacher__admin").order_by("scheduled_start")[:5] if st.course else []
        assignments = Assignment.objects.filter(
            subject__course=st.course, is_published=True
        ).select_related("subject").order_by("due_date")[:6] if st.course else []
        subs = {
            s.assignment_id: s
            for s in AssignmentSubmission.objects.filter(student=st)
        }
        quiz_results = AssessmentAttempt.objects.filter(
            student=st, status="GRADED", assessment__results_published=True
        ).select_related("assessment__subject").order_by("-graded_at")[:6]
        exam_results = StudentResult.objects.filter(student=st).select_related("subject")
        att_total = AttendanceReport.objects.filter(student=st).count()
        att_present = AttendanceReport.objects.filter(student=st, status=True).count()
        att_rate = round((att_present / att_total * 100.0), 1) if att_total else 0.0
        announcements = SubjectAnnouncement.objects.filter(
            subject__course=st.course
        ).select_related("subject")[:5] if st.course else []

        children_data.append({
            "student": st,
            "progress": progress,
            "upcoming_classes": upcoming_classes,
            "assignment_rows": [{"assignment": a, "submission": subs.get(a.id)} for a in assignments],
            "quiz_results": quiz_results,
            "exam_results": exam_results,
            "attendance_rate": att_rate,
            "announcements": announcements,
        })

    context = {
        "page_title": "Parent / Guardian Learning Monitor",
        "guardian": guardian,
        "children_data": children_data,
        "today": today,
    }
    return render(request, "elearning_template/parent_portal.html", context)


def manage_parent_links(request):
    if not is_admin_or_dos(request.user):
        messages.error(request, "Only Principal or DOS can manage Parent/Guardian student links.")
        return redirect("elearning_dashboard")

    if request.method == "POST":
        action = request.POST.get("action", "link")
        if action == "create_guardian":
            email = request.POST.get("email", "").strip().lower()
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            password = request.POST.get("password", "").strip() or "123try"
            phone = request.POST.get("phone_number", "").strip()
            student_ids = request.POST.getlist("student_ids")
            if email and not CustomUser.objects.filter(email__iexact=email).exists():
                u = CustomUser.objects.create_user(
                    email=email,
                    password=password,
                    first_name=first_name or "Parent",
                    last_name=last_name or "Guardian",
                    gender="M",
                    address="Juba",
                    user_type="7",
                )
                g, _ = ParentGuardian.objects.get_or_create(admin=u)
                g.phone_number = phone
                g.save()
                if student_ids:
                    g.students.set(Student.objects.filter(id__in=student_ids))
                messages.success(request, f"Guardian account created for {email}.")
            else:
                messages.error(request, "Valid unique email is required.")
        elif action == "update_links":
            g = get_object_or_404(ParentGuardian, id=request.POST.get("guardian_id"))
            student_ids = request.POST.getlist("student_ids")
            g.students.set(Student.objects.filter(id__in=student_ids))
            messages.success(request, f"Updated linked students for {g}.")
        return redirect("elearning_manage_guardians")

    context = {
        "page_title": "Parent / Guardian Student Links",
        "guardians": ParentGuardian.objects.select_related("admin").prefetch_related("students__admin", "students__course"),
        "students": Student.objects.select_related("admin", "course").all(),
    }
    return render(request, "elearning_template/manage_guardians.html", context)
