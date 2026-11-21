"""
AI-Generated & Copy-Paste Assignment Detection Engine.

Analyzes student assignment documents (inline rich text + uploaded .txt/.docx
files), editor copy-paste telemetry, and peer submission overlap to detect
AI-generated or copy-pasted work before submission and automatically reject
forced AI submissions.
"""

from html.parser import HTMLParser
import math
import re
import zipfile
from xml.etree import ElementTree


# Explicit LLM / chatbot giveaway phrases
EXPLICIT_AI_PHRASES = [
    "as an ai language model",
    "as an artificial intelligence",
    "as a large language model",
    "i am an ai",
    "i'm an ai",
    "certainly! here is",
    "sure! here is",
    "sure, here is",
    "here is a comprehensive",
    "here's a comprehensive",
    "here is an essay",
    "here's an essay",
    "hope this helps!",
    "feel free to ask if you need",
    "let me know if you would like me to",
    "in summary, it is important to note",
    "in conclusion, it is evident that",
]

# High-frequency LLM cliché phrases & markers
AI_CLICHE_PHRASES = [
    "delve into",
    "delving into",
    "tapestry of",
    "testament to",
    "in today's fast-paced world",
    "in today's digital age",
    "ever-evolving landscape",
    "plays a pivotal role",
    "plays a crucial role",
    "it is crucial to understand",
    "it is important to note that",
    "it is worth noting that",
    "fostering a deeper understanding",
    "navigating the complexities",
    "a myriad of",
    "a plethora of",
    "multifaceted nature",
    "serves as a cornerstone",
    "paramount importance",
    "underscore the importance",
    "underscores the significance",
    "by leveraging",
    "harnessing the power of",
    "sheds light on",
    "paves the way for",
    "cannot be overstated",
    "holistic approach",
    "transformative impact",
    "nuanced understanding",
    "in essence,",
    "ultimately, understanding",
]

AI_TRANSITION_WORDS = {
    "furthermore",
    "moreover",
    "additionally",
    "consequently",
    "nevertheless",
    "nonetheless",
    "ultimately",
    "notably",
    "significantly",
    "pivotal",
    "crucial",
    "paramount",
    "comprehensive",
    "multifaceted",
    "intricate",
    "holistic",
    "transformative",
    "foster",
    "fostering",
    "underscore",
    "underscores",
    "delve",
    "delves",
    "leverage",
    "leveraging",
    "myriad",
    "plethora",
    "seamless",
    "seamlessly",
}

AI_FLAG_THRESHOLD = 60.0
AI_CAUTION_THRESHOLD = 35.0
PEER_SIMILARITY_THRESHOLD = 0.45


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def get_text(self):
        return "".join(self.parts)


def strip_html(html_text):
    if not html_text:
        return ""
    stripper = _HTMLStripper()
    try:
        stripper.feed(html_text)
        text = stripper.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html_text)
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_text_from_uploaded_file(uploaded_file):
    """Best-effort plain text extraction from uploaded .txt or .docx files."""
    if not uploaded_file:
        return ""
    name = (getattr(uploaded_file, "name", "") or "").lower()
    try:
        pos = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
    except Exception:
        pos = None

    extracted = ""
    try:
        if name.endswith((".txt", ".md", ".csv")):
            raw = uploaded_file.read()
            if isinstance(raw, bytes):
                extracted = raw.decode("utf-8", errors="ignore")
            else:
                extracted = str(raw)
        elif name.endswith(".docx"):
            with zipfile.ZipFile(uploaded_file) as zf:
                if "word/document.xml" in zf.namelist():
                    xml_bytes = zf.read("word/document.xml")
                    root = ElementTree.fromstring(xml_bytes)
                    texts = [el.text for el in root.iter() if el.text]
                    extracted = " ".join(texts)
    except Exception:
        extracted = ""
    finally:
        if pos is not None and hasattr(uploaded_file, "seek"):
            try:
                uploaded_file.seek(pos)
            except Exception:
                pass
    return extracted.strip()


def _tokenize_words(text):
    return re.findall(r"[a-zA-Z0-9']+", text.lower())


def _split_sentences(text):
    raw = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [s.strip() for s in raw if len(_tokenize_words(s)) >= 3]


def _word_ngrams(words, n=4):
    if len(words) < n:
        return set()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _compute_peer_similarity(words, peer_texts):
    """Return max 4-gram Jaccard similarity against other students' submissions."""
    my_grams = _word_ngrams(words, n=4)
    if len(my_grams) < 6 or not peer_texts:
        return 0.0
    max_sim = 0.0
    for peer_text in peer_texts:
        peer_words = _tokenize_words(strip_html(peer_text))
        peer_grams = _word_ngrams(peer_words, n=4)
        if not peer_grams:
            continue
        inter = len(my_grams & peer_grams)
        union = len(my_grams | peer_grams)
        if union > 0:
            sim = inter / union
            if sim > max_sim:
                max_sim = sim
    return round(max_sim, 3)


def analyze_assignment_originality(
    document_content="",
    uploaded_file=None,
    paste_ratio=0.0,
    paste_events=0,
    peer_texts=None,
):
    """
    Analyze student assignment text & paste telemetry for AI generation and copy-paste.

    Returns a dict:
        - ai_score (0.0 - 100.0)
        - is_flagged (bool): True if work should be warned & auto-rejected on forced submit
        - verdict: 'AI_OR_COPY_PASTE_DETECTED' | 'SUSPICIOUS' | 'ORIGINAL'
        - summary: concise human-readable explanation
        - reasons: list of specific signals triggered
        - metrics: word_count, paste_ratio, peer_similarity, etc.
    """
    inline_text = strip_html(document_content or "")
    file_text = extract_text_from_uploaded_file(uploaded_file)
    combined_text = "\n".join(part for part in (inline_text, file_text) if part).strip()

    try:
        paste_ratio = max(0.0, min(1.0, float(paste_ratio or 0.0)))
    except (TypeError, ValueError):
        paste_ratio = 0.0
    try:
        paste_events = max(0, int(paste_events or 0))
    except (TypeError, ValueError):
        paste_events = 0

    words = _tokenize_words(combined_text)
    word_count = len(words)
    lower_text = combined_text.lower()
    sentences = _split_sentences(combined_text)

    score = 0.0
    reasons = []
    explicit_hit = False

    # 1. Explicit AI chatbot phrases (immediate high confidence)
    matched_explicit = [p for p in EXPLICIT_AI_PHRASES if p in lower_text]
    if matched_explicit:
        explicit_hit = True
        score += 75.0
        reasons.append(
            f"Contains direct AI assistant phrasing ({', '.join(repr(p) for p in matched_explicit[:2])})."
        )

    # 2. High-frequency AI cliché phrases
    matched_cliches = [p for p in AI_CLICHE_PHRASES if p in lower_text]
    if matched_cliches:
        cliche_pts = min(45.0, len(matched_cliches) * 16.0)
        score += cliche_pts
        reasons.append(
            f"Detected {len(matched_cliches)} common AI-generated phrase(s): "
            + ", ".join(f'"{p}"' for p in matched_cliches[:3])
            + "."
        )

    # 3. AI transition & buzzword density
    transition_hits = sum(1 for w in words if w in AI_TRANSITION_WORDS)
    transition_density = (transition_hits / word_count) if word_count > 0 else 0.0
    if word_count >= 25 and transition_hits >= 3 and transition_density >= 0.035:
        pts = min(28.0, transition_density * 450.0)
        score += pts
        reasons.append(
            f"Unusually high density of formal AI transition/buzzwords ({transition_hits} in {word_count} words)."
        )

    # 4. Markdown / structured AI formatting patterns (e.g. **1. Point:** or ### Header)
    md_bold_bullets = re.findall(r"(?:^|\n)\s*(?:[-*]|\d+\.)\s+\*\*[^*]{2,60}\*\*:", combined_text)
    colon_bullets = re.findall(r"(?:^|\n)\s*(?:[-*•]|\d+\.)\s+[A-Z][A-Za-z0-9 &/-]{2,40}:\s+\S+", combined_text)
    structured_list_count = max(len(md_bold_bullets), len(colon_bullets))
    if structured_list_count >= 3:
        score += min(24.0, structured_list_count * 6.5)
        reasons.append(
            f"Detected {structured_list_count} formulaic 'Heading: explanation' bullet points typical of AI output."
        )

    # 5. Low sentence-length burstiness (uniform AI cadence)
    burstiness_cv = None
    if len(sentences) >= 4:
        lengths = [len(_tokenize_words(s)) for s in sentences]
        mean_len = sum(lengths) / len(lengths)
        if mean_len >= 12.0:
            variance = sum((x - mean_len) ** 2 for x in lengths) / len(lengths)
            std_dev = math.sqrt(variance)
            burstiness_cv = round(std_dev / mean_len, 3) if mean_len > 0 else 0.0
            if burstiness_cv <= 0.22:
                score += 18.0
                reasons.append(
                    f"Highly uniform sentence length pattern (burstiness CV={burstiness_cv}, avg {mean_len:.1f} words/sentence)."
                )
            elif burstiness_cv <= 0.30 and matched_cliches:
                score += 10.0

    # 6. Copy-paste telemetry from the student document editor
    if paste_ratio >= 0.80 and word_count >= 20:
        score += 42.0
        reasons.append(
            f"{int(round(paste_ratio * 100))}% of the document was copy-pasted directly into the editor instead of written."
        )
    elif paste_ratio >= 0.55 and word_count >= 20:
        score += 24.0
        reasons.append(
            f"Significant copy-paste activity detected ({int(round(paste_ratio * 100))}% pasted across {max(1, paste_events)} paste event(s))."
        )

    # 7. Peer copy-paste similarity check
    peer_similarity = _compute_peer_similarity(words, peer_texts or [])
    peer_copied = peer_similarity >= PEER_SIMILARITY_THRESHOLD
    if peer_copied:
        score = max(score + 35.0, 72.0)
        reasons.append(
            f"High text overlap ({int(round(peer_similarity * 100))}%) matching another student's submission on this assignment."
        )

    ai_score = round(min(100.0, score), 1)
    is_flagged = explicit_hit or peer_copied or ai_score >= AI_FLAG_THRESHOLD

    if is_flagged:
        verdict = "AI_OR_COPY_PASTE_DETECTED"
        summary = (
            f"AI-generated or copy-pasted content detected ({ai_score:.0f}% risk). "
            "Please rewrite this assignment in your own words before turning it in; "
            "forcing submission will be automatically rejected."
        )
    elif ai_score >= AI_CAUTION_THRESHOLD:
        verdict = "SUSPICIOUS"
        summary = (
            f"Some AI-like or copy-paste patterns were noticed ({ai_score:.0f}% risk). "
            "Consider revising into your own words before submitting."
        )
    else:
        verdict = "ORIGINAL"
        summary = (
            f"Original work verified ({ai_score:.0f}% AI risk). Ready to submit."
            if word_count > 0
            else "Start typing your assignment document to run an originality check."
        )

    return {
        "ai_score": ai_score,
        "is_flagged": is_flagged,
        "verdict": verdict,
        "summary": summary,
        "reasons": reasons,
        "metrics": {
            "word_count": word_count,
            "sentence_count": len(sentences),
            "paste_ratio": round(paste_ratio, 3),
            "paste_events": paste_events,
            "peer_similarity": peer_similarity,
            "cliche_count": len(matched_cliches),
            "transition_hits": transition_hits,
            "burstiness_cv": burstiness_cv,
        },
    }
