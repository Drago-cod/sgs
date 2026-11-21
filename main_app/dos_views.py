from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import (
    CourseForm,
    DirectorOfStudiesForm,
    ExaminationForm,
    SessionForm,
    SubjectForm,
    TimetableEntryForm,
)
from .models import (
    Course,
    DirectorOfStudies,
    Examination,
    Session,
    Staff,
    Student,
    StudentResult,
    Subject,
    TimetableEntry,
)
from .staff_views import calculate_grade


def dos_home(request):
    total_courses = Course.objects.count()
    total_subjects = Subject.objects.count()
    total_students = Student.objects.count()
    total_teachers = Staff.objects.count()
    total_exams = Examination.objects.count()
    total_timetable_slots = TimetableEntry.objects.count()

    results = list(StudentResult.objects.select_related("student__course", "subject"))
    total_results = len(results)
    passed = sum(1 for r in results if (float(r.test) + float(r.exam)) >= 50.0)
    pass_rate = round((passed / total_results) * 100.0, 1) if total_results else 0.0

    # Class average summary
    class_stats = []
    for course in Course.objects.all():
        c_results = [r for r in results if r.student.course_id == course.id]
        avg = (
            round(
                sum(float(r.test) + float(r.exam) for r in c_results) / len(c_results),
                1,
            )
            if c_results
            else 0.0
        )
        class_stats.append({
            "course": course,
            "students": Student.objects.filter(course=course).count(),
            "subjects": Subject.objects.filter(course=course).count(),
            "results_count": len(c_results),
            "avg_score": avg,
            "avg_grade": calculate_grade(avg) if c_results else "—",
        })

    upcoming_exams = Examination.objects.select_related("course", "session")[:6]

    context = {
        "page_title": "Director of Studies (DOS) Dashboard",
        "total_courses": total_courses,
        "total_subjects": total_subjects,
        "total_students": total_students,
        "total_teachers": total_teachers,
        "total_exams": total_exams,
        "total_timetable_slots": total_timetable_slots,
        "total_results": total_results,
        "pass_rate": pass_rate,
        "class_stats": class_stats,
        "upcoming_exams": upcoming_exams,
    }
    return render(request, "dos_template/home_content.html", context)


def dos_manage_exams(request):
    form = ExaminationForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            exam = form.save(commit=False)
            exam.created_by = request.user
            exam.save()
            messages.success(request, f"Examination '{exam.name}' scheduled successfully!")
            return redirect("dos_manage_exams")
        messages.error(request, "Please correct the errors in the examination form.")

    exams = Examination.objects.select_related("session", "course", "created_by").all()
    context = {
        "page_title": "Examinations Management",
        "form": form,
        "exams": exams,
    }
    return render(request, "dos_template/manage_exams.html", context)


@require_POST
def dos_update_exam_status(request, exam_id):
    exam = get_object_or_404(Examination, id=exam_id)
    new_status = request.POST.get("status", "").upper()
    valid = {c[0] for c in Examination.STATUS_CHOICES}
    if new_status in valid:
        exam.status = new_status
        exam.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Updated '{exam.name}' status to {exam.get_status_display()}.")
    else:
        messages.error(request, "Invalid examination status.")
    return redirect("dos_manage_exams")


@require_POST
def dos_delete_exam(request, exam_id):
    exam = get_object_or_404(Examination, id=exam_id)
    name = exam.name
    exam.delete()
    messages.success(request, f"Examination '{name}' deleted.")
    return redirect("dos_manage_exams")


def dos_manage_timetable(request):
    form = TimetableEntryForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            entry = form.save()
            messages.success(
                request,
                f"Added {entry.subject.name} to {entry.course.name} timetable ({entry.get_day_of_week_display()}).",
            )
            return redirect(f"{reverse('dos_manage_timetable')}?course={entry.course_id}")
        messages.error(request, "Please check timetable slot fields (end time must be after start time).")

    selected_course_id = request.GET.get("course")
    courses = Course.objects.all()
    entries_qs = TimetableEntry.objects.select_related("course", "subject", "subject__staff__admin")
    selected_course = None
    if selected_course_id:
        selected_course = Course.objects.filter(id=selected_course_id).first()
        if selected_course:
            entries_qs = entries_qs.filter(course=selected_course)

    day_order = ["MON", "TUE", "WED", "THU", "FRI", "SAT"]
    day_labels = dict(TimetableEntry.DAY_CHOICES)
    grouped_timetable = [
        {
            "code": day,
            "label": day_labels[day],
            "entries": [e for e in entries_qs if e.day_of_week == day],
        }
        for day in day_order
    ]

    context = {
        "page_title": "Academic Timetables",
        "form": form,
        "courses": courses,
        "selected_course": selected_course,
        "grouped_timetable": grouped_timetable,
    }
    return render(request, "dos_template/manage_timetable.html", context)


@require_POST
def dos_delete_timetable_entry(request, entry_id):
    entry = get_object_or_404(TimetableEntry, id=entry_id)
    course_id = entry.course_id
    entry.delete()
    messages.success(request, "Timetable slot removed.")
    return redirect(f"{reverse('dos_manage_timetable')}?course={course_id}")


def dos_academic_results(request):
    courses = Course.objects.all()
    subjects = Subject.objects.select_related("course", "staff__admin").all()

    course_id = request.GET.get("course")
    subject_id = request.GET.get("subject")

    results_qs = StudentResult.objects.select_related(
        "student__admin", "student__course", "subject", "subject__staff__admin"
    ).order_by("student__course__name", "student__admin__last_name", "subject__name")

    if course_id:
        results_qs = results_qs.filter(student__course_id=course_id)
    if subject_id:
        results_qs = results_qs.filter(subject_id=subject_id)

    rows = []
    for r in results_qs:
        total = round(float(r.test) + float(r.exam), 1)
        rows.append({
            "result": r,
            "total": total,
            "grade": calculate_grade(total),
            "passed": total >= 50.0,
        })

    avg_score = round(sum(row["total"] for row in rows) / len(rows), 1) if rows else 0.0
    pass_rate = (
        round((sum(1 for row in rows if row["passed"]) / len(rows)) * 100.0, 1)
        if rows
        else 0.0
    )

    context = {
        "page_title": "Academic Results & Performance",
        "courses": courses,
        "subjects": subjects,
        "selected_course_id": course_id or "",
        "selected_subject_id": subject_id or "",
        "rows": rows,
        "avg_score": avg_score,
        "pass_rate": pass_rate,
    }
    return render(request, "dos_template/academic_results.html", context)


def dos_manage_academics(request):
    """Allow DOS to manage Classes, Subjects, Terms, and Class Teacher assignments."""
    course_form = CourseForm()
    subject_form = SubjectForm()
    session_form = SessionForm()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_course":
            course_form = CourseForm(request.POST)
            if course_form.is_valid():
                course_form.save()
                messages.success(request, "Class added successfully.")
                return redirect("dos_manage_academics")
        elif action == "add_subject":
            subject_form = SubjectForm(request.POST)
            if subject_form.is_valid():
                subject_form.save()
                messages.success(request, "Subject added successfully.")
                return redirect("dos_manage_academics")
        elif action == "add_session":
            session_form = SessionForm(request.POST)
            if session_form.is_valid():
                session_form.save()
                messages.success(request, "Academic term added successfully.")
                return redirect("dos_manage_academics")
        elif action == "toggle_class_teacher":
            staff_id = request.POST.get("staff_id")
            staff = get_object_or_404(Staff, id=staff_id)
            staff.class_teacher = not staff.class_teacher
            staff.save()
            messages.success(
                request,
                f"Updated Class Teacher status for {staff.admin.get_full_name()}.",
            )
            return redirect("dos_manage_academics")

    context = {
        "page_title": "Academic Structure (Classes, Subjects & Terms)",
        "course_form": course_form,
        "subject_form": subject_form,
        "session_form": session_form,
        "courses": Course.objects.all(),
        "subjects": Subject.objects.select_related("course", "staff__admin").all(),
        "sessions": Session.objects.all(),
        "staff_list": Staff.objects.select_related("admin", "course").all(),
    }
    return render(request, "dos_template/manage_academics.html", context)


def dos_view_profile(request):
    dos, _ = DirectorOfStudies.objects.get_or_create(admin=request.user)
    form = DirectorOfStudiesForm(request.POST or None, request.FILES or None, instance=dos)
    if request.method == "POST":
        if form.is_valid():
            custom_user = dos.admin
            password = form.cleaned_data.get("password") or None
            passport = request.FILES.get("profile_pic") or None
            if password:
                custom_user.set_password(password)
            if passport:
                custom_user.profile_pic = passport
            custom_user.first_name = form.cleaned_data.get("first_name")
            custom_user.last_name = form.cleaned_data.get("last_name")
            custom_user.address = form.cleaned_data.get("address")
            custom_user.gender = form.cleaned_data.get("gender")
            custom_user.save()
            messages.success(request, "DOS Profile Updated!")
            return redirect("dos_view_profile")
        messages.error(request, "Invalid data provided.")
    return render(
        request,
        "dos_template/dos_view_profile.html",
        {"form": form, "page_title": "DOS Profile"},
    )
