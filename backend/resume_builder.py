"""
resume_builder.py -- assembles a resume from pre-verified project-bank
content + static personal info, renders the Jinja2 LaTeX template, and
drives the page-fit compiler.

Design principle (see README): this module NEVER generates new prose.
Every bullet comes verbatim from project_bank.json (pre-verified, hand-
written, fact-checked content). This module's job is selection,
ordering, and formatting -- not writing.
"""
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from latex_utils import escape_latex
from pdf_compiler import compile_with_page_fit, CompileResult

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_DATA_DIR = Path(__file__).parent.parent / "data"


def get_jinja_env() -> Environment:
    """LaTeX-safe Jinja2 environment: uses \\VAR{} and \\BLOCK{} instead
    of the default {{ }} / {% %}, since LaTeX's own brace syntax would
    otherwise collide with Jinja2's default delimiters."""
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        trim_blocks=True,
        autoescape=False,  # we handle LaTeX escaping ourselves via escape_latex()
    )


def load_project_bank(path: Path = None) -> dict:
    path = path or (_DATA_DIR / "project_bank.json")
    with open(path, encoding="utf-8") as f:
        bank = json.load(f)

    # Validate structure eagerly -- fail fast and clearly rather than
    # letting a malformed entry surface as a confusing LaTeX compile error.
    for key, project in bank.items():
        missing = [f for f in ("title", "github", "bullets") if f not in project]
        if missing:
            raise ValueError(f"project_bank.json entry '{key}' is missing required field(s): {missing}")
        if not isinstance(project["bullets"], list) or len(project["bullets"]) == 0:
            raise ValueError(f"project_bank.json entry '{key}' has no bullets")

    return bank


STATIC_EXPERIENCE = [
    {
        "title": "Data Scientist",
        "dates": "Sep 2025 -- Dec 2025",
        "company": "Upgrad School of Technology",
        "subtitle": "Promoted from Data Science Content Strategist Associate following upGrad's acquisition of Aimerz.ai -- continued with the same team.",
        "bullets": [
            r"Designed and executed \textbf{SQL and Python-based analytical workflows} to extract, clean, and engineer features across structured and unstructured datasets, assessing \textbf{data quality and integrity} to ensure reliable, reproducible outputs.",
            r"Performed \textbf{anomaly detection} using the Interquartile Range (IQR) method during data preprocessing, followed by trend analysis and correlation studies to identify key business drivers and validate model performance.",
            r"Developed \textbf{KPI dashboards} and business performance reports using Python, SQL, Pandas, NumPy, Matplotlib, and Seaborn, enabling stakeholder visibility into key metrics driving business decisions.",
            r"Built and tested \textbf{machine learning and predictive analytics workflows} using Python, SQL, Pandas, and Scikit-learn, including data preprocessing and feature engineering.",
            r"Partnered with internal and cross-functional business stakeholders, gathering requirements and structuring analysis to solve real-world business problems.",
        ],
    },
    {
        "title": "Data Science Content Strategist Associate",
        "dates": "Aug 2024 -- Sep 2025",
        "company": "Aimerz.ai (acquired by upGrad in 2025)",
        "subtitle": "",
        "bullets": [
            r"Joined as the \textbf{first member of Aimerz's Data Science team}, designing and developing \textbf{15+ Python-based analytical workflows, predictive modelling projects, and machine learning solutions} in Google Colab.",
            r"Built and improved analytical workflows using Python and SQL, automating data cleaning, exploratory data analysis (EDA), and feature engineering.",
            r"Applied \textbf{regression, classification, and statistical modelling} techniques for predictive modelling, validating model performance.",
            r"Conducted \textbf{model evaluation}, data validation, and technical reporting across 15+ Python-based projects.",
        ],
    },
]

STATIC_SKILLS = [
    {"name": "Languages \\& Tools", "content": "Python, R, SQL, SQLite, DuckDB, FastAPI, Advanced Excel, Git/GitHub, Streamlit, Docker, Jupyter"},
    {"name": "Machine Learning \\& Predictive Modelling", "content": "Supervised Learning (Logistic Regression, Random Forest, XGBoost, LightGBM), Classification, Regression, Anomaly Detection, Clustering (KMeans), Hyperparameter Optimization (Optuna, GridSearchCV), Time-Series Forecasting, Cross-Validation"},
    {"name": "Model Governance \\& Responsible AI", "content": "Fairness Auditing, Fair-Lending Testing (ECOA/Reg B awareness), Model Calibration (Isotonic/Platt), Explainability (SHAP), Production Drift Monitoring (PSI), Leakage Detection"},
    {"name": "Gen AI / LLM \\& Prompt Engineering", "content": "Generative AI, RAG Architecture, Google Gemini, LangChain, FAISS (Vector Database), LLM Evaluation"},
    {"name": "Data Analysis \\& Statistics", "content": "Hypothesis / Significance Testing, Statistical Modelling, Statistical Inference, EDA, Data Verification \\& Audit, Quantitative Analysis"},
    {"name": "Model Lifecycle, MLOps \\& Deployment", "content": "End-to-End Model Development, Model Deployment (FastAPI, Streamlit), Containerization (Docker), Experiment Tracking (MLflow), CI/CD Pipelines (GitHub Actions), Automated Regression Testing, Reproducibility"},
    {"name": "Databases \\& Data Engineering", "content": "MySQL, PostgreSQL, SQLite, DuckDB, SQL Query Writing, Multi-table Joins \\& CTEs, ETL Workflows, Graph Analytics (NetworkX), Data Modelling"},
    {"name": "Libraries \\& Frameworks", "content": "Pandas, NumPy, Scikit-learn, XGBoost, LightGBM, TensorFlow, Keras, Statsmodels, SciPy, Matplotlib, Seaborn"},
    {"name": "Visualization \\& Collaboration", "content": "Tableau, Power BI, Dashboard Development, Data Storytelling, Stakeholder Management, Cross-functional Collaboration, Requirements Gathering, Agile/Scrum"},
]

STATIC_EDUCATION = [
    {"degree": "BSc Mathematics", "institution": "Amrita Vishwa Vidyapeetham, Coimbatore", "dates": "2017 -- 2020"},
    {"degree": "Data Science Bootcamp", "institution": "Brototype, Bengaluru", "dates": "2023 -- 2024"},
]

DEFAULT_SUMMARY = (
    r"\textbf{Data Scientist} with hands-on experience in \textbf{Python, SQL, Machine Learning, "
    r"and Predictive Modeling}, building and independently validating models for classification, "
    r"regression, anomaly detection, and forecasting. Skilled in data analysis, feature engineering, "
    r"and statistical modeling, with a consistent record of auditing and correcting flawed models -- "
    r"finding leakage, governance violations, and evaluation bugs -- before they reach a business "
    r"decision. Builds \textbf{GenAI/LLM applications} with real evaluation harnesses. Strong "
    r"communicator, translating technical findings clearly to both technical and business stakeholders."
)

PERSONAL_INFO = {
    "name": "Melvin Mathew",
    "title": "Data Scientist",
    "phone": "+91 9400725200",
    "location": "Bengaluru, India",
    "email": "melvinmathew9991@gmail.com",
    "linkedin_url": "https://www.linkedin.com/in/mathew-melvin/",
    "linkedin_display": "linkedin.com/in/mathew-melvin",
    "github_url": "https://github.com/melvinmathew9991",
    "github_display": "github.com/melvinmathew9991",
}


def build_resume(
    selected_project_keys: list,
    bank: dict = None,
    summary: str = None,
    personal_info: dict = None,
    max_pages: int = 2,
) -> CompileResult:
    """Assembles and compiles a resume from selected project keys.

    selected_project_keys: ordered list of keys into the project bank --
        order here is the order they'll appear in the resume.
    Raises KeyError if a selected key isn't in the bank (fail loudly,
    not silently drop a requested project).
    """
    bank = bank or load_project_bank()
    summary = summary or DEFAULT_SUMMARY
    info = {**PERSONAL_INFO, **(personal_info or {})}

    unknown_keys = [k for k in selected_project_keys if k not in bank]
    if unknown_keys:
        raise KeyError(f"Unknown project key(s) not found in bank: {unknown_keys}")

    projects = []
    for key in selected_project_keys:
        p = bank[key]
        projects.append({
            "title": p["title"],
            "github": p["github"],  # raw -- safe inside \href{URL}{...}, hyperref doesn't typeset it
            "github_display": escape_latex(p["github"]),  # escaped -- this IS typeset as visible text
            "bullets": p["bullets"],
        })

    env = get_jinja_env()
    template = env.get_template("resume.tex.j2")

    def render_fn(font_size, line_spacing):
        return template.render(
            **info,
            summary=summary,
            experience=STATIC_EXPERIENCE,
            projects=projects,
            skills=STATIC_SKILLS,
            education=STATIC_EDUCATION,
            font_size=font_size,
            line_spacing=line_spacing,
        )

    return compile_with_page_fit(render_fn, max_pages=max_pages)
