"""Per-job tailored cover letters.

Two modes (config: cover_letter.mode):
  - "template": offline, no API key. Fills a template with the company/title and
    the skills from your profile that appear in the job description.
  - "llm": uses the configured LLM (any provider — see src/llm_client.py) to
    write a genuinely tailored letter from your profile + the job description.
    Falls back to "template" if no API key / provider is configured.
  - "off": no cover letter generated.

Generated letters are cached under data/cover_letters/<job_id>.txt so re-runs and
the optional PDF render are free.
"""
from __future__ import annotations

from pathlib import Path

from . import llm_client
from .utils import log

# Skills we can credibly claim — used by template mode to pick relevant ones.
# Maps the lowercase needle searched in the job description -> display form.
_PROFILE_SKILLS = {
    "application security": "application security", "appsec": "AppSec",
    "devsecops": "DevSecOps", "secure sdlc": "secure SDLC",
    "threat modelling": "threat modelling", "threat modeling": "threat modelling",
    "sast": "SAST", "dast": "DAST", "sca": "SCA", "iast": "IAST", "rasp": "RASP",
    "waf": "WAF", "owasp": "OWASP", "burp suite": "Burp Suite",
    "penetration testing": "penetration testing", "vapt": "VAPT", "ci/cd": "CI/CD",
    "snyk": "Snyk", "sonarqube": "SonarQube",
    "github advanced security": "GitHub Advanced Security", "black duck": "Black Duck",
    "veracode": "Veracode", "fortify": "Fortify", "checkmarx": "Checkmarx",
    "aws": "AWS", "azure": "Azure", "gcp": "GCP", "cloud security": "cloud security",
    "zero trust": "Zero Trust", "python": "Python", "java": "Java",
    "javascript": "JavaScript", "siem": "SIEM", "splunk": "Splunk", "iot": "IoT",
    "api security": "API security", "mobile security": "mobile security",
}


def _profile_text(cfg: dict) -> str:
    path = cfg.get("cover_letter", {}).get("profile_path", "data/profile.md")
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def generate(job, cfg: dict) -> str | None:
    """Return cover-letter text for a job, or None if disabled/failed.

    `job` must expose .job_id, .title, .company; `description` is read from cfg-passed
    text if available via job.description (optional attribute).
    """
    cc = cfg.get("cover_letter", {})
    mode = cc.get("mode", "template")
    if mode == "off":
        return None

    cached = _cache_path(job.job_id, cfg)
    if cached.exists():
        return cached.read_text(encoding="utf-8")

    description = getattr(job, "description", "") or ""
    text = None
    if mode == "llm":
        text = _generate_llm(job, description, cfg)
        if text is None:
            log.warning("LLM cover letter unavailable; falling back to template.")
    if text is None:
        text = _generate_template(job, description, cfg)

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


def render_pdf(text: str, job_id: str, cfg: dict) -> str | None:
    """Render letter text to a PDF for file-upload fields. Returns path or None."""
    try:
        from fpdf import FPDF
    except ImportError:
        log.warning("fpdf2 not installed; cannot render cover-letter PDF for upload.")
        return None
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_margins(20, 20, 20)
    pdf.set_font("Helvetica", size=11)
    for para in text.split("\n"):
        # Latin-1 is all FPDF core fonts support; drop anything outside it.
        safe = para.encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 6, safe)
        pdf.ln(2)
    out = _cache_path(job_id, cfg).with_suffix(".pdf")
    pdf.output(str(out))
    return str(out)


# --- template mode -----------------------------------------------------------

def _generate_template(job, description: str, cfg: dict) -> str:
    applicant = cfg["applicant"]
    desc_l = (description or "").lower()
    matched = [disp for needle, disp in _PROFILE_SKILLS.items() if needle in desc_l][:5]
    if not matched:
        matched = ["application security", "DevSecOps", "threat modelling", "secure SDLC"]
    skills_phrase = ", ".join(matched[:-1]) + (f", and {matched[-1]}" if len(matched) > 1 else matched[0])

    company = job.company or "your team"
    title = job.title or "this role"
    name = f"{applicant.get('first_name','')} {applicant.get('last_name','')}".strip()
    return (
        f"Dear Hiring Team at {company},\n\n"
        f"I'm applying for the {title} position. My background lines up closely with what "
        f"you're looking for — {skills_phrase}.\n\n"
        f"I'd welcome the chance to bring this to {company}, including in a remote setting. "
        f"Thank you for your consideration.\n\n"
        f"Best regards,\n{name}\n{applicant.get('email','')}"
    )


# --- llm mode ----------------------------------------------------------------

def _generate_llm(job, description: str, cfg: dict) -> str | None:
    profile = _profile_text(cfg)
    system = (
        "You write concise, specific cover letters for the candidate described below. "
        "Use ONLY facts from the candidate profile — never invent experience, employers, "
        "or numbers. 200-300 words, confident and direct, no clichés. Lead with the "
        "experience most relevant to the job. Output ONLY the final letter text — no "
        "preamble, no notes, no markdown.\n\n"
        f"=== CANDIDATE PROFILE ===\n{profile}"
    )
    user = (
        f"Write a cover letter for this role.\n\n"
        f"Job title: {job.title}\nCompany: {job.company}\n\n"
        f"Job description:\n{(description or '(not available)')[:6000]}"
    )
    return llm_client.complete(cfg, system, user, max_tokens=1024)


def _cache_path(job_id: str, cfg: dict) -> Path:
    d = cfg.get("cover_letter", {}).get("output_dir", "data/cover_letters")
    return Path(d) / f"{job_id}.txt"
