import json
import requests
from django.contrib import messages
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import (HttpResponse, HttpResponseRedirect,
                              get_object_or_404, redirect, render)
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import UpdateView

from .forms import *
from .models import *
from .attendance_audit import scan_attendance_date
from .ai_attendance import (
    TEACHER_LATE_CUTOFF,
    analyze_teacher_attendance,
    get_ai_dashboard_summary,
    run_ai_attendance_scan,
)
from django.utils.dateparse import parse_date, parse_time
from django.utils import timezone


def _build_admin_notifications(request):
    notifications = []

    for ai_alert in AIAttendanceNotification.objects.filter(
        is_resolved=False
    ).order_by('-created_at')[:6]:
        target_label = ai_alert.get_target_type_display()
        notifications.append({
            'category': f"AI Attendance ({target_label} · {ai_alert.severity})",
            'title': ai_alert.title,
            'message': ai_alert.message,
            'created_at': ai_alert.created_at,
            'link': reverse('admin_ai_attendance_dashboard'),
            'severity': ai_alert.severity,
            'is_unread': not ai_alert.is_read,
        })

    for leave in LeaveReportStaff.objects.filter(status=0).order_by('-created_at')[:5]:
        notifications.append({
            'category': 'Staff Leave',
            'title': 'Pending staff leave request',
            'message': f"{leave.staff.admin.get_full_name()} requested leave for {leave.date}",
            'created_at': leave.created_at,
            'link': reverse('view_staff_leave'),
        })

    for leave in LeaveReportStudent.objects.filter(status=0, forwarded_to_admin=True).order_by('-created_at')[:5]:
        notifications.append({
            'category': 'Student Leave',
            'title': 'Forwarded student leave request',
            'message': f"{leave.student.admin.get_full_name()} submitted {leave.get_leave_category_display().lower()} leave and it was forwarded to the admin.",
            'created_at': leave.created_at,
            'link': reverse('view_student_leave'),
        })

    for message in StudentMessage.objects.filter(receiver=request.user, is_read=False).order_by('-created_at')[:5]:
        notifications.append({
            'category': 'Message',
            'title': 'New message',
            'message': f"{message.sender.get_full_name()} sent a message: {message.subject}",
            'created_at': message.created_at,
            'link': reverse('student_messages') if message.sender.user_type == '3' else reverse('view_staff_leave'),
        })

    for feedback in FeedbackStaff.objects.filter(reply__in=['', None]).order_by('-created_at')[:5]:
        notifications.append({
            'category': 'Feedback',
            'title': 'Staff feedback awaiting reply',
            'message': f"{feedback.staff.admin.get_full_name()} submitted feedback.",
            'created_at': feedback.created_at,
            'link': reverse('staff_feedback_message'),
        })

    for feedback in FeedbackStudent.objects.filter(reply__in=['', None]).order_by('-created_at')[:5]:
        notifications.append({
            'category': 'Feedback',
            'title': 'Student feedback awaiting reply',
            'message': f"{feedback.student.admin.get_full_name()} submitted feedback.",
            'created_at': feedback.created_at,
            'link': reverse('student_feedback_message'),
        })

    return sorted(notifications, key=lambda item: item['created_at'], reverse=True)[:10]


def admin_home(request):
    total_staff = Staff.objects.all().count()
    total_students = Student.objects.all().count()
    subjects = Subject.objects.all()
    total_subject = subjects.count()
    total_course = Course.objects.all().count()
    attendance_list = Attendance.objects.filter(subject__in=subjects)
    total_attendance = attendance_list.count()
    attendance_list = []
    subject_list = []
    for subject in subjects:
        attendance_count = Attendance.objects.filter(subject=subject).count()
        subject_list.append(subject.name[:7])
        attendance_list.append(attendance_count)
    context = {
        'page_title': "Administrative Dashboard",
        'total_students': total_students,
        'total_staff': total_staff,
        'total_course': total_course,
        'total_subject': total_subject,
        'subject_list': subject_list,
        'attendance_list': attendance_list,
        'admin_notifications': _build_admin_notifications(request)

    }
    return render(request, 'hod_template/home_content.html', context)


def add_staff(request):
    is_admin = str(getattr(request.user, 'user_type', '')) == '1'
    form = StaffForm(
        request.POST or None,
        request.FILES or None,
        allow_all_roles=is_admin,
    )
    context = {
        'form': form,
        'page_title': 'Add Staff / System User' if is_admin else 'Add Teacher',
    }
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password')
            course = form.cleaned_data.get('course')
            role = str(form.cleaned_data.get('role') or '2')

            if not is_admin and role not in ('2', '6'):
                messages.error(request, "DOS can only create Subject Teacher or Class Teacher accounts.")
                return render(request, 'hod_template/add_staff_template.html', context)

            if role in ('2', '6') and not course:
                messages.error(request, "Please select a class/course for teacher roles.")
                return render(request, 'hod_template/add_staff_template.html', context)

            passport = request.FILES.get('profile_pic') or None
            try:
                user = CustomUser.objects.create_user(
                    email=email,
                    password=password,
                    user_type=role,
                    first_name=first_name,
                    last_name=last_name,
                    gender=gender,
                    address=address,
                )
                if passport:
                    user.profile_pic = passport
                    user.save(update_fields=['profile_pic'])

                if role in ('2', '6'):
                    staff, _ = Staff.objects.get_or_create(admin=user)
                    staff.course = course
                    staff.class_teacher = (role == '6')
                    staff.save()
                elif role == '4':
                    DirectorOfStudies.objects.get_or_create(admin=user)
                elif role == '5':
                    Bursar.objects.get_or_create(admin=user)
                elif role == '1':
                    Admin.objects.get_or_create(admin=user)

                messages.success(
                    request,
                    f"Successfully added {user.get_full_name()} as {user.get_user_type_display()}!",
                )
                return redirect(reverse('add_staff'))

            except Exception as e:
                messages.error(request, "Could Not Add " + str(e))
        else:
            messages.error(request, "Please fulfil all requirements")

    return render(request, 'hod_template/add_staff_template.html', context)


def add_student(request):
    student_form = StudentForm(request.POST or None, request.FILES or None)
    context = {'form': student_form, 'page_title': 'Add Student'}
    if request.method == 'POST':
        if student_form.is_valid():
            try:
                # Check if user with this email already exists
                email = student_form.cleaned_data.get('email')
                if CustomUser.objects.filter(email=email).exists():
                    messages.error(request, "Email already exists!")
                    return render(request, 'hod_template/add_student_template.html', context)
                
                # Handle profile picture (optional)
                passport = request.FILES.get('profile_pic') or None

                # Create the user first - this will trigger the signal to create Student profile
                user = CustomUser.objects.create_user(
                    email=email,
                    password=student_form.cleaned_data.get('password'),
                    user_type='3',
                    first_name=student_form.cleaned_data.get('first_name'),
                    last_name=student_form.cleaned_data.get('last_name'),
                    gender=student_form.cleaned_data.get('gender'),
                    address=student_form.cleaned_data.get('address')
                )
                if passport:
                    user.profile_pic = passport
                    user.save(update_fields=['profile_pic'])
                
                # Get the student instance created by the signal and update it
                student = Student.objects.get(admin=user)
                student.course = student_form.cleaned_data.get('course')
                student.session = student_form.cleaned_data.get('session')
                student.phone_number = student_form.cleaned_data.get('phone_number')
                student.save()
                
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_student'))
                
            except Exception as e:
                # If anything goes wrong, clean up the created user
                if 'user' in locals():
                    user.delete()
                messages.error(request, f"Could Not Add: {str(e)}")
                print(f"Error adding student: {str(e)}")  # Add console logging
        else:
            messages.error(request, "Please fill all required fields correctly")
            print(f"Form errors: {student_form.errors}")  # Add form error logging

    return render(request, 'hod_template/add_student_template.html', context)

    

def add_course(request):
    form = CourseForm(request.POST or None)
    context = {
        'form': form,
        'page_title': 'Add Class'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            try:
                course = Course()
                course.name = name
                course.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_course'))
            except:
                messages.error(request, "Could Not Add")
        else:
            messages.error(request, "Could Not Add")
    return render(request, 'hod_template/add_course_template.html', context)


def add_subject(request):
    form = SubjectForm(request.POST or None)
    context = {
        'form': form,
        'page_title': 'Add Subject'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            course = form.cleaned_data.get('course')
            staff = form.cleaned_data.get('staff')
            try:
                subject = Subject()
                subject.name = name
                subject.staff = staff
                subject.course = course
                subject.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_subject'))

            except Exception as e:
                messages.error(request, "Could Not Add " + str(e))
        else:
            messages.error(request, "Fill Form Properly")

    return render(request, 'hod_template/add_subject_template.html', context)


def manage_staff(request):
    allStaff = Staff.objects.all()
    context = {
        'allStaff': allStaff,
        'page_title': 'Manage Staff'
    }
    return render(request, "hod_template/manage_staff.html", context)


def print_staff_list(request):
    if getattr(request.user, 'user_type', None) not in ('1', '4'):
        return HttpResponse("Forbidden", status=403)

    all_staff = Staff.objects.select_related('admin', 'course').order_by(
        'admin__last_name', 'admin__first_name')
    return render(request, "hod_template/print_staff_list.html", {
        'all_staff': all_staff,
        'page_title': 'Staff List',
    })


def manage_student(request):
    students = CustomUser.objects.filter(user_type=3)
    context = {
        'students': students,
        'page_title': 'Manage Students'
    }
    return render(request, "hod_template/manage_student.html", context)


def print_student_list(request):
    if getattr(request.user, 'user_type', None) not in ('1', '4'):
        return HttpResponse("Forbidden", status=403)

    students = Student.objects.select_related(
        'admin', 'course', 'session'
    ).order_by('admin__last_name', 'admin__first_name')
    return render(request, "hod_template/print_student_list.html", {
        'students': students,
        'page_title': 'Registered Student List',
    })


def manage_course(request):
    courses = Course.objects.all()
    context = {
        'courses': courses,
        'page_title': 'Manage Classes'
    }
    return render(request, "hod_template/manage_course.html", context)


def manage_subject(request):
    subjects = Subject.objects.all()
    context = {
        'subjects': subjects,
        'page_title': 'Manage Subjects'
    }
    return render(request, "hod_template/manage_subject.html", context)


def edit_staff(request, staff_id):
    staff = get_object_or_404(Staff, id=staff_id)
    form = StaffForm(request.POST or None, instance=staff)
    context = {
        'form': form,
        'staff_id': staff_id,
        'page_title': 'Edit Staff'
    }
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            username = form.cleaned_data.get('username')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password') or None
            course = form.cleaned_data.get('course')
            passport = request.FILES.get('profile_pic') or None
            try:
                user = CustomUser.objects.get(id=staff.admin.id)
                user.username = username
                user.email = email
                if password != None:
                    user.set_password(password)
                if passport != None:
                    fs = FileSystemStorage()
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    user.profile_pic = passport_url
                user.first_name = first_name
                user.last_name = last_name
                user.gender = gender
                user.address = address
                staff.course = course
                user.save()
                staff.save()
                messages.success(request, "Successfully Updated")
                return redirect(reverse('edit_staff', args=[staff_id]))
            except Exception as e:
                messages.error(request, "Could Not Update " + str(e))
        else:
            messages.error(request, "Please fil form properly")
    else:
        user = CustomUser.objects.get(id=staff_id)
        staff = Staff.objects.get(id=user.id)
        return render(request, "hod_template/edit_staff_template.html", context)


def edit_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    form = StudentForm(request.POST or None, instance=student)
    context = {
        'form': form,
        'student_id': student_id,
        'page_title': 'Edit Student'
    }
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            username = form.cleaned_data.get('username')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password') or None
            course = form.cleaned_data.get('course')
            session = form.cleaned_data.get('session')
            passport = request.FILES.get('profile_pic') or None
            try:
                user = CustomUser.objects.get(id=student.admin.id)
                if passport != None:
                    fs = FileSystemStorage()
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    user.profile_pic = passport_url
                user.username = username
                user.email = email
                if password != None:
                    user.set_password(password)
                user.first_name = first_name
                user.last_name = last_name
                student.session = session
                student.course = course
                user.gender = gender
                user.address = address
                user.save()
                student.save()
                messages.success(request, "Successfully Updated")
                return redirect(reverse('edit_student', args=[student_id]))
            except Exception as e:
                messages.error(request, "Could Not Update " + str(e))
        else:
            messages.error(request, "Please Fill Form Properly!")
    else:
        return render(request, "hod_template/edit_student_template.html", context)

#This is the newest view added
def manage_class_teachers(request):
    if request.method == 'POST':
        staff_id = request.POST.get('staff_id')
        staff = get_object_or_404(Staff, id=staff_id)
        staff.class_teacher = not staff.class_teacher
        staff.save()
        messages.success(request, f"{staff.admin.get_full_name()} {'is now' if staff.class_teacher else 'is no longer'} a class teacher")
        return redirect('manage_class_teachers')

    staff_list = Staff.objects.all()
    context = {
        'staff_list': staff_list,
        'page_title': 'Manage Class Teachers'
    }
    return render(request, 'hod_template/manage_class_teachers.html', context)

def edit_course(request, course_id):
    instance = get_object_or_404(Course, id=course_id)
    form = CourseForm(request.POST or None, instance=instance)
    context = {
        'form': form,
        'course_id': course_id,
        'page_title': 'Edit Course'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            try:
                course = Course.objects.get(id=course_id)
                course.name = name
                course.save()
                messages.success(request, "Successfully Updated")
            except:
                messages.error(request, "Could Not Update")
        else:
            messages.error(request, "Could Not Update")

    return render(request, 'hod_template/edit_course_template.html', context)


def edit_subject(request, subject_id):
    instance = get_object_or_404(Subject, id=subject_id)
    form = SubjectForm(request.POST or None, instance=instance)
    context = {
        'form': form,
        'subject_id': subject_id,
        'page_title': 'Edit Subject'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            course = form.cleaned_data.get('course')
            staff = form.cleaned_data.get('staff')
            try:
                subject = Subject.objects.get(id=subject_id)
                subject.name = name
                subject.staff = staff
                subject.course = course
                subject.save()
                messages.success(request, "Successfully Updated")
                return redirect(reverse('edit_subject', args=[subject_id]))
            except Exception as e:
                messages.error(request, "Could Not Add " + str(e))
        else:
            messages.error(request, "Fill Form Properly")
    return render(request, 'hod_template/edit_subject_template.html', context)


def add_session(request):
    form = SessionForm(request.POST or None)
    context = {'form': form, 'page_title': 'Add Term'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Term Created")
                return redirect(reverse('add_session'))
            except Exception as e:
                messages.error(request, 'Could Not Add ' + str(e))
        else:
            messages.error(request, 'Fill Form Properly ')
    return render(request, "hod_template/add_session_template.html", context)


def manage_session(request):
    sessions = Session.objects.all()
    context = {'sessions': sessions, 'page_title': 'Manage Terms'}
    return render(request, "hod_template/manage_session.html", context)


def edit_session(request, session_id):
    instance = get_object_or_404(Session, id=session_id)
    form = SessionForm(request.POST or None, instance=instance)
    context = {'form': form, 'session_id': session_id,
               'page_title': 'Edit Term'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Term Updated")
                return redirect(reverse('edit_session', args=[session_id]))
            except Exception as e:
                messages.error(
                    request, "Term Could Not Be Updated " + str(e))
                return render(request, "hod_template/edit_session_template.html", context)
        else:
            messages.error(request, "Invalid Form Submitted ")
            return render(request, "hod_template/edit_session_template.html", context)

    else:
        return render(request, "hod_template/edit_session_template.html", context)


@csrf_exempt
def check_email_availability(request):
    email = request.POST.get("email")
    try:
        user = CustomUser.objects.filter(email=email).exists()
        if user:
            return HttpResponse(True)
        return HttpResponse(False)
    except Exception as e:
        return HttpResponse(False)


@csrf_exempt
def student_feedback_message(request):
    if request.method != 'POST':
        feedbacks = FeedbackStudent.objects.all()
        context = {
            'feedbacks': feedbacks,
            'page_title': 'Student Feedback Messages'
        }
        return render(request, 'hod_template/student_feedback_template.html', context)
    else:
        feedback_id = request.POST.get('id')
        try:
            feedback = get_object_or_404(FeedbackStudent, id=feedback_id)
            reply = request.POST.get('reply')
            feedback.reply = reply
            feedback.save()
            return HttpResponse(True)
        except Exception as e:
            return HttpResponse(False)


@csrf_exempt
def staff_feedback_message(request):
    if request.method != 'POST':
        feedbacks = FeedbackStaff.objects.all()
        context = {
            'feedbacks': feedbacks,
            'page_title': 'Staff Feedback Messages'
        }
        return render(request, 'hod_template/staff_feedback_template.html', context)
    else:
        feedback_id = request.POST.get('id')
        try:
            feedback = get_object_or_404(FeedbackStaff, id=feedback_id)
            reply = request.POST.get('reply')
            feedback.reply = reply
            feedback.save()
            return HttpResponse(True)
        except Exception as e:
            return HttpResponse(False)


@csrf_exempt
def view_staff_leave(request):
    if request.method != 'POST':
        if getattr(request.user, 'user_type', None) in ('2', '6'):
            return redirect('staff_home')

        allLeave = LeaveReportStaff.objects.all()
        context = {
            'allLeave': allLeave,
            'page_title': 'Leave Applications From Staff'
        }
        return render(request, "hod_template/staff_leave_view.html", context)
    else:
        if getattr(request.user, 'user_type', None) != '1':
            return HttpResponse(False)

        id = request.POST.get('id')
        status = request.POST.get('status')
        if (status == '1'):
            status = 1
        else:
            status = -1
        try:
            leave = get_object_or_404(LeaveReportStaff, id=id)
            leave.status = status
            leave.save()
            return HttpResponse(True)
        except Exception as e:
            return False


@csrf_exempt
def view_student_leave(request):
    if request.method != 'POST':
        if getattr(request.user, 'user_type', None) == '1':
            allLeave = LeaveReportStudent.objects.all()
        elif getattr(request.user, 'user_type', None) in ('2', '6'):
            staff = get_object_or_404(Staff, admin=request.user)
            allLeave = LeaveReportStudent.objects.filter(student__course=staff.course)
        else:
            return redirect('login_page')

        context = {
            'allLeave': allLeave,
            'page_title': 'Leave Applications From Students'
        }
        return render(request, "hod_template/student_leave_view.html", context)
    else:
        leave = get_object_or_404(LeaveReportStudent, id=request.POST.get('id'))
        if getattr(request.user, 'user_type', None) == '1':
            allowed = True
        elif getattr(request.user, 'user_type', None) in ('2', '6'):
            staff = get_object_or_404(Staff, admin=request.user)
            allowed = staff.class_teacher and leave.student.course == staff.course
        else:
            allowed = False

        if not allowed:
            return HttpResponse(False)

        status = request.POST.get('status')
        if status == 'forward':
            leave.status = 0
            leave.forwarded_to_admin = True
            leave.save()
            return HttpResponse(True)

        leave.status = 1 if status == '1' else -1
        leave.forwarded_to_admin = False
        leave.save()
        return HttpResponse(True)


def admin_view_attendance(request):
    subjects = Subject.objects.all()
    sessions = Session.objects.all()
    context = {
        'subjects': subjects,
        'sessions': sessions,
        'page_title': 'View Attendance'
    }

    return render(request, "hod_template/admin_view_attendance.html", context)


def admin_attendance_audit(request):
    anomalies = AttendanceAnomaly.objects.select_related(
        "student__admin", "reviewed_by"
    )
    context = {
        "page_title": "Attendance Anomaly Audit",
        "anomalies": anomalies,
    }
    return render(request, "hod_template/attendance_audit.html", context)


@require_POST
def scan_attendance_anomalies(request):
    audit_date = parse_date(request.POST.get("date", ""))
    if not audit_date:
        return JsonResponse({"error": "A valid attendance date is required."}, status=400)
    results = scan_attendance_date(audit_date, request.user)
    flagged = sum(1 for item in results if item["result"]["anomaly_detected"])
    return JsonResponse({
        "scanned": len(results),
        "flagged": flagged,
        "message": "Attendance audit completed. Alerts require human verification.",
    })


@require_POST
def review_attendance_anomaly(request, anomaly_id):
    outcome = request.POST.get("outcome")
    notes = request.POST.get("notes", "").strip()
    valid_outcomes = {choice[0] for choice in AttendanceAnomaly.REVIEW_OUTCOMES}
    if outcome not in valid_outcomes:
        return JsonResponse({"error": "Select a valid review outcome."}, status=400)
    anomaly = get_object_or_404(AttendanceAnomaly, id=anomaly_id)
    anomaly.review_status = "REVIEWED"
    anomaly.review_outcome = outcome
    anomaly.review_notes = notes
    anomaly.reviewed_by = request.user
    anomaly.reviewed_at = timezone.now()
    anomaly.save()
    AttendanceAuditEvent.objects.create(
        anomaly=anomaly,
        event_type="REVIEWED",
        actor=request.user,
        details={"outcome": outcome, "notes": notes},
    )
    return redirect("admin_attendance_audit")


@csrf_exempt
def get_admin_attendance(request):
    subject_id = request.POST.get('subject')
    session_id = request.POST.get('session')
    attendance_date_id = request.POST.get('attendance_date_id')
    try:
        subject = get_object_or_404(Subject, id=subject_id)
        session = get_object_or_404(Session, id=session_id)
        attendance = get_object_or_404(
            Attendance, id=attendance_date_id, session=session)
        attendance_reports = AttendanceReport.objects.filter(
            attendance=attendance)
        json_data = []
        for report in attendance_reports:
            data = {
                "status": report.attendance_status,
                "name": str(report.student)
            }
            json_data.append(data)
        return JsonResponse(json.dumps(json_data), safe=False)
    except Exception as e:
        return None


def admin_view_profile(request):
    admin, _ = Admin.objects.get_or_create(admin=request.user)
    form = AdminForm(request.POST or None, request.FILES or None,
                     instance=admin)
    context = {'form': form,
               'page_title': 'View/Edit Profile'
               }
    if request.method == 'POST':
        try:
            if form.is_valid():
                first_name = form.cleaned_data.get('first_name')
                last_name = form.cleaned_data.get('last_name')
                password = form.cleaned_data.get('password') or None
                passport = request.FILES.get('profile_pic') or None
                custom_user = admin.admin
                if password != None:
                    custom_user.set_password(password)
                if passport != None:
                    custom_user.profile_pic = passport
                custom_user.first_name = first_name
                custom_user.last_name = last_name
                custom_user.save()
                messages.success(request, "Profile Updated!")
                return redirect(reverse('admin_view_profile'))
            else:
                messages.error(request, "Invalid Data Provided")
        except Exception as e:
            messages.error(
                request, "Error Occured While Updating Profile " + str(e))
    return render(request, "hod_template/admin_view_profile.html", context)


def admin_notify_staff(request):
    staff = CustomUser.objects.filter(user_type=2)
    context = {
        'page_title': "Send Notifications To Staff",
        'allStaff': staff
    }
    return render(request, "hod_template/staff_notification.html", context)


def admin_notify_student(request):
    student = CustomUser.objects.filter(user_type=3)
    context = {
        'page_title': "Send Notifications To Students",
        'students': student
    }
    return render(request, "hod_template/student_notification.html", context)


@csrf_exempt
def send_student_notification(request):
    id = request.POST.get('id')
    message = request.POST.get('message')
    student = get_object_or_404(Student, admin_id=id)
    try:
        url = "https://fcm.googleapis.com/fcm/send"
        body = {
            'notification': {
                'title': "Student Management System",
                'body': message,
                'click_action': reverse('student_view_notification'),
                'icon': static('dist/img/AdminLTELogo.png')
            },
            'to': student.admin.fcm_token
        }
        headers = {'Authorization':
                   'key=AAAA3Bm8j_M:APA91bElZlOLetwV696SoEtgzpJr2qbxBfxVBfDWFiopBWzfCfzQp2nRyC7_A2mlukZEHV4g1AmyC6P_HonvSkY2YyliKt5tT3fe_1lrKod2Daigzhb2xnYQMxUWjCAIQcUexAMPZePB',
                   'Content-Type': 'application/json'}
        data = requests.post(url, data=json.dumps(body), headers=headers)
        notification = NotificationStudent(student=student, message=message)
        notification.save()
        return HttpResponse("True")
    except Exception as e:
        return HttpResponse("False")


@csrf_exempt
def send_staff_notification(request):
    id = request.POST.get('id')
    message = request.POST.get('message')
    staff = get_object_or_404(Staff, admin_id=id)
    try:
        url = "https://fcm.googleapis.com/fcm/send"
        body = {
            'notification': {
                'title': "Student Management System",
                'body': message,
                'click_action': reverse('staff_view_notification'),
                'icon': static('dist/img/AdminLTELogo.png')
            },
            'to': staff.admin.fcm_token
        }
        headers = {'Authorization':
                   'key=AAAA3Bm8j_M:APA91bElZlOLetwV696SoEtgzpJr2qbxBfxVBfDWFiopBWzfCfzQp2nRyC7_A2mlukZEHV4g1AmyC6P_HonvSkY2YyliKt5tT3fe_1lrKod2Daigzhb2xnYQMxUWjCAIQcUexAMPZePB',
                   'Content-Type': 'application/json'}
        data = requests.post(url, data=json.dumps(body), headers=headers)
        notification = NotificationStaff(staff=staff, message=message)
        notification.save()
        return HttpResponse("True")
    except Exception as e:
        return HttpResponse("False")


def delete_staff(request, staff_id):
    staff = get_object_or_404(CustomUser, staff__id=staff_id)
    staff.delete()
    messages.success(request, "Staff deleted successfully!")
    return redirect(reverse('manage_staff'))


def delete_student(request, student_id):
    student = get_object_or_404(CustomUser, student__id=student_id)
    student.delete()
    messages.success(request, "Student deleted successfully!")
    return redirect(reverse('manage_student'))


def delete_course(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    try:
        course.delete()
        messages.success(request, "Course deleted successfully!")
    except Exception:
        messages.error(
            request, "Sorry, some students are assigned to this course already. Kindly change the affected student course and try again")
    return redirect(reverse('manage_course'))


def delete_subject(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    subject.delete()
    messages.success(request, "Subject deleted successfully!")
    return redirect(reverse('manage_subject'))


def delete_session(request, session_id):
    session = get_object_or_404(Session, id=session_id)
    try:
        session.delete()
        messages.success(request, "Term deleted successfully!")
    except Exception:
        messages.error(
            request, "There are students assigned to this term. Please move them to another term.")
    return redirect(reverse('manage_session'))


def admin_ai_attendance_dashboard(request):
    """Admin AI Attendance Tracker for both students and teachers."""
    date_param = request.GET.get('date', '').strip()
    ref_date = parse_date(date_param) if date_param else timezone.localdate()
    if not ref_date:
        ref_date = timezone.localdate()

    target_filter = request.GET.get('target', 'ALL').upper()
    status_filter = request.GET.get('status', 'OPEN').upper()

    notifications_qs = AIAttendanceNotification.objects.select_related(
        'student__admin', 'student__course', 'staff__admin', 'staff__course', 'resolved_by'
    )
    if target_filter in ('STUDENT', 'TEACHER', 'SYSTEM'):
        notifications_qs = notifications_qs.filter(target_type=target_filter)

    if status_filter == 'UNREAD':
        notifications_qs = notifications_qs.filter(is_read=False, is_resolved=False)
    elif status_filter == 'RESOLVED':
        notifications_qs = notifications_qs.filter(is_resolved=True)
    elif status_filter == 'OPEN':
        notifications_qs = notifications_qs.filter(is_resolved=False)

    summary = get_ai_dashboard_summary(reference_date=ref_date)
    all_staff = Staff.objects.select_related('admin', 'course').order_by('admin__last_name')
    today_teacher_records = {
        rec.staff_id: rec
        for rec in TeacherAttendance.objects.filter(date=ref_date)
    }
    staff_attendance_rows = [
        {
            'staff': staff_member,
            'record': today_teacher_records.get(staff_member.id),
        }
        for staff_member in all_staff
    ]

    context = {
        'page_title': 'AI Attendance Tracker',
        'reference_date': ref_date,
        'target_filter': target_filter,
        'status_filter': status_filter,
        'ai_notifications': notifications_qs[:50],
        'open_alert_count': AIAttendanceNotification.objects.filter(is_resolved=False).count(),
        'unread_alert_count': AIAttendanceNotification.objects.filter(is_read=False, is_resolved=False).count(),
        'critical_high_count': AIAttendanceNotification.objects.filter(
            is_resolved=False, severity__in=['HIGH', 'CRITICAL']
        ).count(),
        'student_profiles': summary['student_profiles'],
        'teacher_profiles': summary['teacher_profiles'],
        'avg_student_rate': summary['avg_student_rate'],
        'avg_teacher_rate': summary['avg_teacher_rate'],
        'at_risk_students': summary['at_risk_students'],
        'at_risk_teachers': summary['at_risk_teachers'],
        'risk_distribution': summary['risk_distribution'],
        'staff_attendance_rows': staff_attendance_rows,
        'late_cutoff': TEACHER_LATE_CUTOFF.strftime('%H:%M'),
    }
    return render(request, 'hod_template/ai_attendance_dashboard.html', context)


@require_POST
def run_ai_attendance_scan_view(request):
    """Trigger a full AI attendance scan across all students and teachers."""
    date_str = request.POST.get('date', '').strip()
    ref_date = parse_date(date_str) if date_str else timezone.localdate()
    if not ref_date:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Invalid date format.'}, status=400)
        messages.error(request, 'Invalid date selected for AI scan.')
        return redirect('admin_ai_attendance_dashboard')

    scan_result = run_ai_attendance_scan(
        reference_date=ref_date,
        actor=request.user,
        include_daily_summary=True,
    )
    msg = (
        f"AI scan completed for {ref_date}: scanned {scan_result['students_scanned']} students "
        f"({scan_result['at_risk_students']} at risk) and {scan_result['teachers_scanned']} teachers "
        f"({scan_result['at_risk_teachers']} flagged). {scan_result['new_alerts']} new admin alert(s) generated."
    )
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'message': msg,
            'students_scanned': scan_result['students_scanned'],
            'teachers_scanned': scan_result['teachers_scanned'],
            'at_risk_students': scan_result['at_risk_students'],
            'at_risk_teachers': scan_result['at_risk_teachers'],
            'new_alerts': scan_result['new_alerts'],
        })
    messages.success(request, msg)
    return redirect(f"{reverse('admin_ai_attendance_dashboard')}?date={ref_date.isoformat()}")


@require_POST
def record_teacher_attendance(request):
    """Admin records or updates daily attendance for a teacher and triggers AI analysis."""
    staff_id = request.POST.get('staff_id')
    date_str = request.POST.get('date', '').strip()
    status = request.POST.get('status', 'PRESENT').upper()
    checkin_str = request.POST.get('check_in_time', '').strip()
    notes = request.POST.get('notes', '').strip()

    valid_statuses = {choice[0] for choice in TeacherAttendance.STATUS_CHOICES}
    if status not in valid_statuses:
        messages.error(request, "Invalid teacher attendance status.")
        return redirect('admin_ai_attendance_dashboard')

    att_date = parse_date(date_str) if date_str else timezone.localdate()
    if not att_date:
        messages.error(request, "Please provide a valid date.")
        return redirect('admin_ai_attendance_dashboard')

    staff = get_object_or_404(Staff, id=staff_id)
    checkin_time = parse_time(checkin_str) if checkin_str else None
    if status == 'PRESENT' and checkin_time and checkin_time > TEACHER_LATE_CUTOFF:
        status = 'LATE'

    TeacherAttendance.objects.update_or_create(
        staff=staff,
        date=att_date,
        defaults={
            'status': status,
            'check_in_time': checkin_time,
            'source': 'ADMIN',
            'notes': notes,
        },
    )
    profile = analyze_teacher_attendance(staff, reference_date=att_date, create_alerts=True)
    alert_note = (
        f" AI generated {len(profile['alerts_created'])} admin alert(s)."
        if profile['alerts_created'] else ""
    )
    messages.success(
        request,
        f"Saved attendance for {staff.admin.get_full_name()} on {att_date} ({status}).{alert_note}",
    )
    return redirect(f"{reverse('admin_ai_attendance_dashboard')}?date={att_date.isoformat()}")


@require_POST
def mark_ai_notification_read(request, notification_id):
    notification = get_object_or_404(AIAttendanceNotification, id=notification_id)
    notification.is_read = True
    notification.save(update_fields=['is_read', 'updated_at'])
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok'})
    return redirect(request.META.get('HTTP_REFERER') or reverse('admin_ai_attendance_dashboard'))


@require_POST
def resolve_ai_notification(request, notification_id):
    notification = get_object_or_404(AIAttendanceNotification, id=notification_id)
    notification.is_read = True
    notification.is_resolved = True
    notification.resolved_by = request.user
    notification.resolved_at = timezone.now()
    notification.save(update_fields=['is_read', 'is_resolved', 'resolved_by', 'resolved_at', 'updated_at'])
    messages.success(request, f"Resolved AI alert: {notification.title}")
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok'})
    return redirect(request.META.get('HTTP_REFERER') or reverse('admin_ai_attendance_dashboard'))


@require_POST
def mark_all_ai_notifications_read(request):
    AIAttendanceNotification.objects.filter(is_read=False).update(is_read=True)
    messages.success(request, "All AI attendance notifications marked as read.")
    return redirect(request.META.get('HTTP_REFERER') or reverse('admin_ai_attendance_dashboard'))


def manage_user_roles(request):
    """Principal / Administrator view to oversee all 6 user roles and reassign roles."""
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        new_role = str(request.POST.get('user_type', ''))
        valid_roles = {c[0] for c in CustomUser.USER_TYPE}
        if new_role not in valid_roles:
            messages.error(request, "Invalid user role selected.")
            return redirect('manage_user_roles')

        target_user = get_object_or_404(CustomUser, id=user_id)
        if target_user.id == request.user.id and new_role != '1':
            messages.error(request, "You cannot remove your own Principal / Administrator role.")
            return redirect('manage_user_roles')

        target_user.user_type = new_role
        target_user.save(update_fields=['user_type', 'updated_at'])

        if new_role == '1':
            Admin.objects.get_or_create(admin=target_user)
        elif new_role == '2':
            staff, _ = Staff.objects.get_or_create(admin=target_user)
            staff.class_teacher = False
            staff.save(update_fields=['class_teacher', 'updated_at'])
        elif new_role == '3':
            Student.objects.get_or_create(admin=target_user)
        elif new_role == '4':
            DirectorOfStudies.objects.get_or_create(admin=target_user)
        elif new_role == '5':
            Bursar.objects.get_or_create(admin=target_user)
        elif new_role == '6':
            staff, _ = Staff.objects.get_or_create(admin=target_user)
            staff.class_teacher = True
            staff.save(update_fields=['class_teacher', 'updated_at'])

        messages.success(
            request,
            f"Updated {target_user.get_full_name() or target_user.email} to {target_user.get_user_type_display()}.",
        )
        return redirect('manage_user_roles')

    role_filter = request.GET.get('role', 'ALL')
    users_qs = CustomUser.objects.all().order_by('user_type', 'last_name', 'first_name')
    if role_filter in {c[0] for c in CustomUser.USER_TYPE}:
        users_qs = users_qs.filter(user_type=role_filter)

    role_counts = [
        {
            'code': code,
            'label': label,
            'count': CustomUser.objects.filter(user_type=code).count(),
        }
        for code, label in CustomUser.USER_TYPE
    ]
    context = {
        'page_title': 'User Roles & Access Control',
        'users': users_qs,
        'role_choices': CustomUser.USER_TYPE,
        'role_counts': role_counts,
        'role_filter': role_filter,
    }
    return render(request, 'hod_template/manage_user_roles.html', context)


def teacher_performance_feedback(request):
    """Principal / Admin and DOS evaluate teacher performance and give structured feedback."""
    form = TeacherPerformanceFeedbackForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            feedback_obj = form.save(commit=False)
            feedback_obj.reviewer = request.user
            feedback_obj.save()
            reviewer_label = request.user.get_user_type_display()
            NotificationStaff.objects.create(
                staff=feedback_obj.staff,
                message=(
                    f"New {feedback_obj.get_category_display()} performance feedback "
                    f"({feedback_obj.rating}/5) from {request.user.get_full_name() or reviewer_label}: "
                    f"{feedback_obj.strengths[:140]}"
                ),
            )
            messages.success(
                request,
                f"Performance feedback ({feedback_obj.rating}/5) sent to {feedback_obj.staff.admin.get_full_name()}!",
            )
            return redirect('teacher_performance_feedback')
        messages.error(request, "Please correct the errors in the performance feedback form.")

    selected_staff_id = request.GET.get('staff_id')
    feedbacks_qs = TeacherPerformanceFeedback.objects.select_related(
        'staff__admin', 'staff__course', 'reviewer', 'session'
    )
    if selected_staff_id:
        feedbacks_qs = feedbacks_qs.filter(staff_id=selected_staff_id)

    # Build live performance metrics per teacher
    teacher_metrics = []
    for staff in Staff.objects.select_related('admin', 'course').order_by('admin__last_name'):
        ai_profile = analyze_teacher_attendance(staff, create_alerts=False)
        subject_results = list(StudentResult.objects.filter(subject__staff=staff))
        avg_student_score = (
            round(
                sum(float(r.test) + float(r.exam) for r in subject_results) / len(subject_results),
                1,
            )
            if subject_results else None
        )
        reviews = list(TeacherPerformanceFeedback.objects.filter(staff=staff))
        avg_rating = (
            round(sum(r.rating for r in reviews) / len(reviews), 1)
            if reviews else None
        )
        teacher_metrics.append({
            'staff': staff,
            'attendance_rate': ai_profile['attendance_rate'],
            'punctuality_rate': ai_profile['punctuality_rate'],
            'classes_logged_recent': ai_profile['classes_logged_recent'],
            'subjects_count': len(ai_profile['subjects']),
            'avg_student_score': avg_student_score,
            'avg_rating': avg_rating,
            'review_count': len(reviews),
        })

    context = {
        'page_title': 'Teacher Performance & Feedback',
        'form': form,
        'teacher_metrics': teacher_metrics,
        'feedbacks': feedbacks_qs[:40],
        'selected_staff_id': selected_staff_id or '',
    }
    return render(request, 'hod_template/teacher_performance_feedback.html', context)
