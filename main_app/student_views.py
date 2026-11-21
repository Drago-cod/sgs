import json
import math
from datetime import datetime

from django.contrib import messages
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import (HttpResponseRedirect, get_object_or_404,
                              redirect, render)
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from django.db import models as db_models
from django.utils import timezone
from .forms import *
from .middleware import get_user_home_url
from .models import *


def student_home(request):
    student = Student.objects.filter(admin=request.user).first()
    if not student:
        messages.error(request, "Access denied. Please log in with a student account.")
        return redirect(get_user_home_url(request.user))
    total_subject = Subject.objects.filter(course=student.course).count()
    total_attendance = AttendanceReport.objects.filter(student=student).count()
    total_present = AttendanceReport.objects.filter(student=student, status=True).count()
    if total_attendance == 0:  # Don't divide. DivisionByZero
        percent_absent = percent_present = 0
    else:
        percent_present = math.floor((total_present/total_attendance) * 100)
        percent_absent = math.ceil(100 - percent_present)
    subject_name = []
    data_present = []
    data_absent = []
    subjects = Subject.objects.filter(course=student.course)
    for subject in subjects:
        attendance = Attendance.objects.filter(subject=subject)
        present_count = AttendanceReport.objects.filter(
            attendance__in=attendance, status=True, student=student).count()
        absent_count = AttendanceReport.objects.filter(
            attendance__in=attendance, status=False, student=student).count()
        subject_name.append(subject.name)
        data_present.append(present_count)
        data_absent.append(absent_count)
    context = {
        'total_attendance': total_attendance,
        'percent_present': percent_present,
        'percent_absent': percent_absent,
        'total_subject': total_subject,
        'subjects': subjects,
        'data_present': data_present,
        'data_absent': data_absent,
        'data_name': subject_name,
        'page_title': 'Student Homepage'

    }
    return render(request, 'student_template/home_content.html', context)


@ csrf_exempt
def student_view_attendance(request):
    student = get_object_or_404(Student, admin=request.user)
    if request.method != 'POST':
        course = get_object_or_404(Course, id=student.course.id)
        context = {
            'subjects': Subject.objects.filter(course=course),
            'page_title': 'View Attendance'
        }
        return render(request, 'student_template/student_view_attendance.html', context)
    else:
        subject_id = request.POST.get('subject')
        start = request.POST.get('start_date')
        end = request.POST.get('end_date')
        try:
            subject = get_object_or_404(Subject, id=subject_id)
            start_date = datetime.strptime(start, "%Y-%m-%d")
            end_date = datetime.strptime(end, "%Y-%m-%d")
            attendance = Attendance.objects.filter(
                date__range=(start_date, end_date), subject=subject)
            attendance_reports = AttendanceReport.objects.filter(
                attendance__in=attendance, student=student)
            json_data = []
            for report in attendance_reports:
                data = {
                    "date":  str(report.attendance.date),
                    "status": report.status
                }
                json_data.append(data)
            return JsonResponse(json.dumps(json_data), safe=False)
        except Exception as e:
            return None


def student_apply_leave(request):
    form = LeaveReportStudentForm(request.POST or None)
    student = get_object_or_404(Student, admin_id=request.user.id)
    context = {
        'form': form,
        'leave_history': LeaveReportStudent.objects.filter(student=student),
        'page_title': 'Apply for leave'
    }
    if request.method == 'POST':
        if form.is_valid():
            try:
                start_date = form.cleaned_data['start_date']
                start_time = form.cleaned_data['start_time']
                end_date = form.cleaned_data['end_date']
                end_time = form.cleaned_data['end_time']
                category = form.cleaned_data['leave_category']
                reason = form.cleaned_data['message']

                obj = LeaveReportStudent(
                    student=student,
                    leave_category=category,
                    start_date=start_date,
                    start_time=start_time,
                    end_date=end_date,
                    end_time=end_time,
                    date=f"{start_date} {start_time} to {end_date} {end_time}",
                    message=f"[{category.title()}] {reason}",
                    status=0,
                )
                obj.save()
                messages.success(
                    request, "Application for leave has been submitted for review")
                return redirect(reverse('student_apply_leave'))
            except Exception:
                messages.error(request, "Could not submit")
        else:
            messages.error(request, "Form has errors!")
    return render(request, "student_template/student_apply_leave.html", context)


def student_feedback(request):
    form = FeedbackStudentForm(request.POST or None)
    student = get_object_or_404(Student, admin_id=request.user.id)
    context = {
        'form': form,
        'feedbacks': FeedbackStudent.objects.filter(student=student),
        'page_title': 'Student Feedback'

    }
    if request.method == 'POST':
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.student = student
                obj.save()
                messages.success(
                    request, "Feedback submitted for review")
                return redirect(reverse('student_feedback'))
            except Exception:
                messages.error(request, "Could not Submit!")
        else:
            messages.error(request, "Form has errors!")
    return render(request, "student_template/student_feedback.html", context)


def student_view_profile(request):
    student = get_object_or_404(Student, admin=request.user)
    form = StudentEditForm(request.POST or None, request.FILES or None,
                           instance=student)
    context = {'form': form,
               'page_title': 'View/Edit Profile'
               }
    if request.method == 'POST':
        try:
            if form.is_valid():
                first_name = form.cleaned_data.get('first_name')
                last_name = form.cleaned_data.get('last_name')
                password = form.cleaned_data.get('password') or None
                address = form.cleaned_data.get('address')
                gender = form.cleaned_data.get('gender')
                passport = request.FILES.get('profile_pic') or None
                admin = student.admin
                if password != None:
                    admin.set_password(password)
                if passport != None:
                    admin.profile_pic = passport
                admin.first_name = first_name
                admin.last_name = last_name
                admin.address = address
                admin.gender = gender
                admin.save()
                student.save()
                messages.success(request, "Profile Updated!")
                return redirect(reverse('student_view_profile'))
            else:
                messages.error(request, "Invalid Data Provided")
        except Exception as e:
            messages.error(request, "Error Occured While Updating Profile " + str(e))

    return render(request, "student_template/student_view_profile.html", context)


@csrf_exempt
def student_fcmtoken(request):
    token = request.POST.get('token')
    student_user = get_object_or_404(CustomUser, id=request.user.id)
    try:
        student_user.fcm_token = token
        student_user.save()
        return HttpResponse("True")
    except Exception as e:
        return HttpResponse("False")


def student_view_notification(request):
    student = get_object_or_404(Student, admin=request.user)
    notifications = NotificationStudent.objects.filter(student=student)
    context = {
        'notifications': notifications,
        'page_title': "View Notifications"
    }
    return render(request, "student_template/student_view_notification.html", context)


def student_view_result(request):
    student = get_object_or_404(Student, admin=request.user)
    results = StudentResult.objects.filter(student=student)
    context = {
        'results': results,
        'page_title': "View Results"
    }
    return render(request, "student_template/student_view_result.html", context)


def student_messages(request):
    student = get_object_or_404(Student, admin=request.user)
    # Find the class teacher for this student's course
    class_teacher = Staff.objects.filter(course=student.course, class_teacher=True).first()

    if request.method == 'POST':
        subject = request.POST.get('subject', 'No Subject')
        message_text = request.POST.get('message', '')
        parent_id = request.POST.get('parent_message', None)

        if not message_text.strip():
            messages.error(request, "Message cannot be empty")
            return redirect(reverse('student_messages'))

        if not class_teacher:
            messages.error(request, "No class teacher assigned to your class yet.")
            return redirect(reverse('student_messages'))

        parent_msg = None
        if parent_id:
            try:
                parent_msg = StudentMessage.objects.get(id=parent_id)
            except StudentMessage.DoesNotExist:
                pass

        StudentMessage.objects.create(
            sender=request.user,
            receiver=class_teacher.admin,
            subject=subject,
            message=message_text,
            parent_message=parent_msg
        )
        messages.success(request, "Message sent to your class teacher successfully!")
        return redirect(reverse('student_messages'))

    # Get all messages in this conversation (sent and received)
    all_messages = StudentMessage.objects.filter(
        db_models.Q(sender=request.user, receiver=class_teacher.admin if class_teacher else None) |
        db_models.Q(receiver=request.user, sender=class_teacher.admin if class_teacher else None)
    ).order_by('created_at') if class_teacher else StudentMessage.objects.none()

    # Mark received messages as read
    all_messages.filter(receiver=request.user, is_read=False).update(is_read=True)

    # Count unread
    unread_count = StudentMessage.objects.filter(receiver=request.user, is_read=False).count()

    context = {
        'all_messages': all_messages,
        'class_teacher': class_teacher,
        'unread_count': unread_count,
        'page_title': 'Messages'
    }
    return render(request, "student_template/student_messages.html", context)


def student_timetable(request):
    student = get_object_or_404(Student, admin=request.user)
    entries = (
        TimetableEntry.objects.filter(course=student.course)
        .select_related('subject', 'subject__staff__admin')
        .order_by('day_of_week', 'start_time')
        if student.course_id
        else TimetableEntry.objects.none()
    )
    day_order = ["MON", "TUE", "WED", "THU", "FRI", "SAT"]
    day_labels = dict(TimetableEntry.DAY_CHOICES)
    grouped_timetable = [
        {
            "code": day,
            "label": day_labels[day],
            "entries": [e for e in entries if e.day_of_week == day],
        }
        for day in day_order
    ]
    context = {
        "page_title": "Class Timetable",
        "course": student.course,
        "grouped_timetable": grouped_timetable,
    }
    return render(request, "student_template/student_timetable.html", context)


def student_assignments(request):
    student = get_object_or_404(Student, admin=request.user)
    filter_mode = request.GET.get("filter", "all").lower()
    today = timezone.localdate()

    assignments = (
        Assignment.objects.filter(subject__course=student.course, is_published=True)
        .select_related('subject', 'staff__admin')
        .order_by('due_date', '-created_at')
        if student.course_id
        else Assignment.objects.none()
    )
    submissions_map = {
        sub.assignment_id: sub
        for sub in AssignmentSubmission.objects.filter(student=student)
    }

    rows = []
    counts = {"all": 0, "todo": 0, "turned_in": 0, "graded": 0, "missing": 0}
    for item in assignments:
        sub = submissions_map.get(item.id)
        is_past_due = item.due_date < today
        if sub:
            state = sub.status
        else:
            state = "MISSING" if is_past_due else "ASSIGNED"

        counts["all"] += 1
        if state in ("ASSIGNED", "DRAFT", "RETURNED"):
            counts["todo"] += 1
        elif state in ("TURNED_IN", "LATE"):
            counts["turned_in"] += 1
        elif state == "GRADED":
            counts["graded"] += 1
        elif state == "MISSING":
            counts["missing"] += 1

        row = {
            "assignment": item,
            "submission": sub,
            "state": state,
            "is_past_due": is_past_due,
        }
        if (
            filter_mode == "all"
            or (filter_mode == "todo" and state in ("ASSIGNED", "DRAFT", "RETURNED"))
            or (filter_mode == "turned_in" and state in ("TURNED_IN", "LATE"))
            or (filter_mode == "graded" and state == "GRADED")
            or (filter_mode == "missing" and state == "MISSING")
        ):
            rows.append(row)

    context = {
        "page_title": "Classwork & Assignments",
        "rows": rows,
        "counts": counts,
        "filter_mode": filter_mode,
        "today": today,
        "course": student.course,
    }
    return render(request, "student_template/student_assignments.html", context)


def student_assignment_workspace(request, assignment_id):
    """Google Classroom-style document workspace where a student works on and turns in an assignment."""
    student = get_object_or_404(Student, admin=request.user)
    assignment = get_object_or_404(
        Assignment.objects.select_related('subject', 'subject__course', 'staff__admin'),
        id=assignment_id,
        subject__course=student.course,
        is_published=True,
    )
    submission = AssignmentSubmission.objects.filter(
        assignment=assignment, student=student
    ).first()
    today = timezone.localdate()
    is_past_due = assignment.due_date < today

    if request.method == "POST":
        action = request.POST.get("action", "save_draft")

        if action == "class_comment":
            body = request.POST.get("body", "").strip()
            if body:
                AssignmentComment.objects.create(
                    assignment=assignment,
                    submission=None,
                    author=request.user,
                    body=body,
                )
                messages.success(request, "Class comment posted.")
            return redirect("student_assignment_workspace", assignment_id=assignment.id)

        if action == "private_comment":
            body = request.POST.get("body", "").strip()
            if body:
                if not submission:
                    submission = AssignmentSubmission.objects.create(
                        assignment=assignment,
                        student=student,
                        document_title=f"{assignment.title} - {student.admin.get_full_name()}",
                        status="DRAFT",
                    )
                AssignmentComment.objects.create(
                    assignment=assignment,
                    submission=submission,
                    author=request.user,
                    body=body,
                )
                messages.success(request, "Private comment sent to your teacher.")
            return redirect("student_assignment_workspace", assignment_id=assignment.id)

        if action == "unsubmit":
            if submission and submission.status in ("TURNED_IN", "LATE"):
                submission.status = "DRAFT"
                submission.save(update_fields=["status", "updated_at"])
                messages.info(request, "Submission unsubmitted. You can edit your document and turn it in again.")
            else:
                messages.error(request, "Cannot unsubmit once graded.")
            return redirect("student_assignment_workspace", assignment_id=assignment.id)

        # Save Draft or Turn In
        if submission and submission.status in ("TURNED_IN", "LATE", "GRADED"):
            messages.error(request, "Unsubmit your work first before editing.")
            return redirect("student_assignment_workspace", assignment_id=assignment.id)

        if action == "turn_in" and is_past_due and not assignment.allow_late_submission:
            messages.error(request, "Late submissions are closed for this assignment.")
            return redirect("student_assignment_workspace", assignment_id=assignment.id)

        doc_title = request.POST.get("document_title", "").strip() or f"{assignment.title} - {student.admin.get_full_name()}"
        doc_content = request.POST.get("document_content", "").strip()
        uploaded = request.FILES.get("uploaded_file")

        if not submission:
            submission = AssignmentSubmission(assignment=assignment, student=student)

        submission.document_title = doc_title
        submission.document_content = doc_content
        if uploaded:
            submission.uploaded_file = uploaded

        has_work = bool(submission.document_content.strip() or submission.uploaded_file)
        if action == "turn_in":
            if not has_work:
                messages.error(request, "Please write your document or attach a file before turning in.")
                return redirect("student_assignment_workspace", assignment_id=assignment.id)
            submission.status = "LATE" if is_past_due else "TURNED_IN"
            submission.turned_in_at = timezone.now()
            submission.save()
            NotificationStaff.objects.create(
                staff=assignment.staff,
                message=(
                    f"{student.admin.get_full_name()} turned in '{assignment.title}' "
                    f"({assignment.subject.name}){' [LATE]' if submission.status == 'LATE' else ''}."
                ),
            )
            messages.success(
                request,
                "Assignment turned in successfully!" if submission.status == "TURNED_IN"
                else "Assignment turned in (marked late).",
            )
        else:
            submission.status = "DRAFT"
            submission.save()
            messages.success(request, "Document draft saved! Remember to click 'Turn In' when finished.")

        return redirect("student_assignment_workspace", assignment_id=assignment.id)

    can_edit = (
        not submission or submission.status in ("DRAFT", "RETURNED")
    ) and (not is_past_due or assignment.allow_late_submission or (submission and submission.status == "RETURNED"))

    class_comments = AssignmentComment.objects.filter(
        assignment=assignment, submission__isnull=True
    ).select_related("author")
    private_comments = (
        AssignmentComment.objects.filter(assignment=assignment, submission=submission)
        .select_related("author")
        if submission else AssignmentComment.objects.none()
    )

    context = {
        "page_title": assignment.title,
        "assignment": assignment,
        "submission": submission,
        "is_past_due": is_past_due,
        "can_edit": can_edit,
        "class_comments": class_comments,
        "private_comments": private_comments,
    }
    return render(request, "student_template/student_assignment_workspace.html", context)
