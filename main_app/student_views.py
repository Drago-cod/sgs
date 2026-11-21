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
    assignments = (
        Assignment.objects.filter(subject__course=student.course)
        .select_related('subject', 'staff__admin')
        .order_by('due_date', '-created_at')
        if student.course_id
        else Assignment.objects.none()
    )
    today = timezone.localdate()
    context = {
        "page_title": "My Assignments & Assessments",
        "assignments": assignments,
        "today": today,
        "course": student.course,
    }
    return render(request, "student_template/student_assignments.html", context)
