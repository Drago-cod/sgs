"""Forms and file validators for the SGS eLearning & Virtual Classroom module."""

import os

from django import forms
from django.core.exceptions import ValidationError

from .elearning_services import (
    ALLOWED_MATERIAL_EXTENSIONS,
    ALLOWED_RECORDING_EXTENSIONS,
    MAX_MATERIAL_SIZE_BYTES,
    MAX_RECORDING_SIZE_BYTES,
)
from .forms import DateInput, FormSettings
from .models import (
    Course,
    DiscussionThread,
    LearningMaterial,
    LearningTopic,
    LectureRecording,
    OnlineAssessment,
    Session,
    Student,
    Subject,
    SubjectAnnouncement,
    VirtualClassroom,
)


def _validate_uploaded_file(uploaded_file, allowed_exts, max_bytes):
    if not uploaded_file:
        return uploaded_file
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in allowed_exts:
        raise ValidationError(
            f"Unsupported file type '{ext}'. Allowed formats: {', '.join(sorted(allowed_exts))}"
        )
    if uploaded_file.size > max_bytes:
        max_mb = round(max_bytes / (1024 * 1024))
        raise ValidationError(f"File exceeds the {max_mb} MB upload limit.")
    return uploaded_file


class LearningTopicForm(FormSettings):
    class Meta:
        model = LearningTopic
        fields = [
            "title",
            "order",
            "description",
            "learning_objectives",
            "available_from",
            "is_published",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Topic 1: Algebra & Linear Equations"}),
            "description": forms.Textarea(attrs={"rows": 2, "placeholder": "Overview of this topic..."}),
            "learning_objectives": forms.Textarea(attrs={"rows": 3, "placeholder": "What students will master by the end of this topic..."}),
            "available_from": DateInput(attrs={"type": "date"}),
            "is_published": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["is_published"].widget.attrs["class"] = "form-check-input"


class LearningMaterialForm(FormSettings):
    class Meta:
        model = LearningMaterial
        fields = [
            "topic",
            "title",
            "material_type",
            "description",
            "file",
            "external_url",
            "rich_content",
            "allow_download",
            "is_published",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Chapter 2 Lecture Notes (PDF)"}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "external_url": forms.URLInput(attrs={"placeholder": "https://... (optional)"}),
            "rich_content": forms.Textarea(attrs={"rows": 4, "placeholder": "Optional written notes or lesson summary..."}),
            "allow_download": forms.CheckboxInput(),
            "is_published": forms.CheckboxInput(),
        }

    def __init__(self, *args, subject=None, **kwargs):
        super().__init__(*args, **kwargs)
        if subject is not None:
            self.fields["topic"].queryset = LearningTopic.objects.filter(subject=subject)
        self.fields["topic"].required = False
        self.fields["allow_download"].widget.attrs["class"] = "form-check-input"
        self.fields["is_published"].widget.attrs["class"] = "form-check-input"

    def clean_file(self):
        return _validate_uploaded_file(
            self.cleaned_data.get("file"),
            ALLOWED_MATERIAL_EXTENSIONS,
            MAX_MATERIAL_SIZE_BYTES,
        )


class SubjectAnnouncementForm(FormSettings):
    class Meta:
        model = SubjectAnnouncement
        fields = ["title", "body", "is_pinned"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Announcement headline..."}),
            "body": forms.Textarea(attrs={"rows": 3, "placeholder": "Write your announcement for students..."}),
            "is_pinned": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["is_pinned"].widget.attrs["class"] = "form-check-input"


class DiscussionThreadForm(FormSettings):
    class Meta:
        model = DiscussionThread
        fields = ["topic", "title", "body"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Ask a question or start a topic discussion..."}),
            "body": forms.Textarea(attrs={"rows": 3, "placeholder": "Provide details or context..."}),
        }

    def __init__(self, *args, subject=None, **kwargs):
        super().__init__(*args, **kwargs)
        if subject is not None:
            self.fields["topic"].queryset = LearningTopic.objects.filter(subject=subject)
        self.fields["topic"].required = False


class VirtualClassroomForm(FormSettings):
    scheduled_start = forms.DateTimeField(
        label="Lesson Date & Time",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )

    class Meta:
        model = VirtualClassroom
        fields = [
            "title",
            "subject",
            "session",
            "academic_year",
            "stream",
            "topic",
            "scheduled_start",
            "duration_minutes",
            "recurrence",
            "description",
            "learning_objectives",
            "supporting_material",
            "attendance_threshold_percent",
            "late_grace_minutes",
            "waiting_room_enabled",
            "mute_on_entry",
            "allow_student_chat",
            "allow_student_whiteboard",
        ]
        labels = {
            "session": "Academic Term",
            "attendance_threshold_percent": "Attendance Threshold (%)",
            "late_grace_minutes": "Late Grace Period (min)",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Live Lesson: Quadratic Equations"}),
            "stream": forms.TextInput(attrs={"placeholder": "e.g. Stream A / Science (optional)"}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "learning_objectives": forms.Textarea(attrs={"rows": 2}),
            "waiting_room_enabled": forms.CheckboxInput(),
            "mute_on_entry": forms.CheckboxInput(),
            "allow_student_chat": forms.CheckboxInput(),
            "allow_student_whiteboard": forms.CheckboxInput(),
        }

    def __init__(self, *args, subjects_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if subjects_qs is not None:
            self.fields["subject"].queryset = subjects_qs
            self.fields["topic"].queryset = LearningTopic.objects.filter(subject__in=subjects_qs)
        self.fields["topic"].required = False
        self.fields["session"].required = False
        for fname in (
            "waiting_room_enabled",
            "mute_on_entry",
            "allow_student_chat",
            "allow_student_whiteboard",
        ):
            self.fields[fname].widget.attrs["class"] = "form-check-input"

    def clean_supporting_material(self):
        return _validate_uploaded_file(
            self.cleaned_data.get("supporting_material"),
            ALLOWED_MATERIAL_EXTENSIONS,
            MAX_MATERIAL_SIZE_BYTES,
        )


class LectureRecordingForm(FormSettings):
    class Meta:
        model = LectureRecording
        fields = [
            "subject",
            "classroom",
            "session",
            "academic_year",
            "title",
            "description",
            "video_file",
            "external_video_url",
            "thumbnail",
            "duration_seconds",
            "retention_until",
            "is_published",
            "allow_download",
        ]
        labels = {
            "session": "Academic Term",
            "duration_seconds": "Duration (seconds)",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. S2 Physics — Laws of Motion Recording"}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "external_video_url": forms.URLInput(attrs={"placeholder": "Optional external video URL"}),
            "retention_until": DateInput(attrs={"type": "date"}),
            "is_published": forms.CheckboxInput(),
            "allow_download": forms.CheckboxInput(),
        }

    def __init__(self, *args, subjects_qs=None, classrooms_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if subjects_qs is not None:
            self.fields["subject"].queryset = subjects_qs
        if classrooms_qs is not None:
            self.fields["classroom"].queryset = classrooms_qs
        self.fields["classroom"].required = False
        self.fields["session"].required = False
        self.fields["is_published"].widget.attrs["class"] = "form-check-input"
        self.fields["allow_download"].widget.attrs["class"] = "form-check-input"

    def clean_video_file(self):
        return _validate_uploaded_file(
            self.cleaned_data.get("video_file"),
            ALLOWED_RECORDING_EXTENSIONS,
            MAX_RECORDING_SIZE_BYTES,
        )


class OnlineAssessmentForm(FormSettings):
    available_from = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    deadline = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )

    class Meta:
        model = OnlineAssessment
        fields = [
            "title",
            "subject",
            "topic",
            "session",
            "assessment_type",
            "instructions",
            "duration_minutes",
            "pass_marks",
            "max_attempts",
            "available_from",
            "deadline",
            "randomize_questions",
            "randomize_choices",
            "detect_tab_switch",
            "allow_review",
            "is_published",
        ]
        labels = {
            "session": "Academic Term",
            "pass_marks": "Passing Marks",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Topic 1 Algebra Quiz"}),
            "instructions": forms.Textarea(attrs={"rows": 3, "placeholder": "Clear rules, time limit, and grading policy..."}),
            "randomize_questions": forms.CheckboxInput(),
            "randomize_choices": forms.CheckboxInput(),
            "detect_tab_switch": forms.CheckboxInput(),
            "allow_review": forms.CheckboxInput(),
            "is_published": forms.CheckboxInput(),
        }

    def __init__(self, *args, subjects_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if subjects_qs is not None:
            self.fields["subject"].queryset = subjects_qs
            self.fields["topic"].queryset = LearningTopic.objects.filter(subject__in=subjects_qs)
        self.fields["topic"].required = False
        self.fields["session"].required = False
        for fname in (
            "randomize_questions",
            "randomize_choices",
            "detect_tab_switch",
            "allow_review",
            "is_published",
        ):
            self.fields[fname].widget.attrs["class"] = "form-check-input"
