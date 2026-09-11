"""The shared term vocabulary.

One list, two readers. :mod:`resume_tailor.domain.knowledge` uses it to decide
what counts as a fact worth storing about the candidate;
:mod:`resume_tailor.domain.ats` uses it to decide what a job description is
asking for. Keeping a single copy is what makes the two sides comparable at
all -- two hand-maintained lists would drift, and the drift would show up as a
requirement the candidate demonstrably has being scored as missing.

Curated, not generated. Every entry is a term that appears in real job
descriptions in this candidate's field, written in its canonical form;
alternate spellings live in :data:`resume_tailor.domain.matching.ALIASES`,
which the matcher already consults, rather than being duplicated here.

The vocabulary is not exhaustive and is not meant to be. Anything it misses is
still picked up as a free-text keyword requirement by
:func:`resume_tailor.domain.matching.extract_meaningful_terms`, so an unusual
technology is scored rather than dropped -- it simply lands in the ``keywords``
category instead of ``skills``.
"""

from __future__ import annotations

#: Capabilities: what a person can do. Languages and methods live here; the
#: named products that implement them live in :data:`TOOLS`.
SKILLS: frozenset[str] = frozenset(
    {
        # languages and query
        "python",
        "sql",
        "r",
        "java",
        "scala",
        "c++",
        "c#",
        "javascript",
        "typescript",
        "golang",
        "rust",
        "bash",
        "shell scripting",
        "matlab",
        "sas",
        "vba",
        "julia",
        "perl",
        "php",
        "ruby",
        "kotlin",
        "swift",
        "dax",
        "m query",
        # data and analytics
        "data analysis",
        "data analytics",
        "exploratory data analysis",
        "data visualization",
        "data modeling",
        "data mining",
        "data wrangling",
        "data cleaning",
        "data quality",
        "data governance",
        "data engineering",
        "etl",
        "elt",
        "data pipelines",
        "data warehousing",
        "dimensional modeling",
        "business intelligence",
        "reporting",
        "dashboarding",
        "kpi tracking",
        "statistical analysis",
        "statistics",
        "hypothesis testing",
        "a/b testing",
        "experimental design",
        "causal inference",
        "regression analysis",
        "bayesian statistics",
        "probability",
        "linear algebra",
        "optimization",
        "operations research",
        # machine learning
        "machine learning",
        "supervised learning",
        "unsupervised learning",
        "deep learning",
        "reinforcement learning",
        "classification",
        "regression",
        "clustering",
        "anomaly detection",
        "forecasting",
        "time series",
        "recommendation systems",
        "feature engineering",
        "feature selection",
        "model evaluation",
        "model validation",
        "model monitoring",
        "model deployment",
        "hyperparameter tuning",
        "cross validation",
        "ensemble methods",
        "gradient boosting",
        "random forest",
        "neural networks",
        "transformers",
        "computer vision",
        "natural language processing",
        "text mining",
        "sentiment analysis",
        "embeddings",
        "vector search",
        "semantic search",
        "large language model",
        "generative ai",
        "prompt engineering",
        "retrieval augmented generation",
        "fine tuning",
        "llm evaluation",
        "explainability",
        "interpretability",
        "fairness",
        "responsible ai",
        "bias testing",
        "calibration",
        "drift monitoring",
        "mlops",
        # engineering and platform
        "api development",
        "rest api",
        "microservices",
        "backend development",
        "frontend development",
        "web development",
        "software engineering",
        "object oriented programming",
        "functional programming",
        "test driven development",
        "unit testing",
        "automated testing",
        "integration testing",
        "code review",
        "debugging",
        "profiling",
        "performance tuning",
        "distributed systems",
        "parallel computing",
        "cloud computing",
        "containerization",
        "orchestration",
        "continuous integration",
        "continuous deployment",
        "infrastructure as code",
        "system design",
        "database design",
        "query optimization",
        "data structures",
        "algorithms",
        "version control",
        "security",
        "authentication",
        "authorization",
        "monitoring",
        "observability",
        "logging",
        "incident response",
        # ways of working
        "agile",
        "scrum",
        "kanban",
        "stakeholder management",
        "requirements gathering",
        "technical documentation",
        "presentation",
        "communication",
        "collaboration",
        "mentoring",
        "leadership",
        "project management",
        "problem solving",
        "critical thinking",
        "storytelling",
        "cross functional collaboration",
    }
)

#: Named products, services and libraries. Kept apart from :data:`SKILLS`
#: because a job description asking for "Snowflake" is asking for something
#: narrower and more checkable than one asking for "data warehousing", and a
#: breakdown that blends the two hides which kind of gap the candidate has.
TOOLS: frozenset[str] = frozenset(
    {
        # python stack
        "pandas",
        "numpy",
        "scipy",
        "scikit-learn",
        "statsmodels",
        "matplotlib",
        "seaborn",
        "plotly",
        "polars",
        "pyspark",
        "dask",
        "xgboost",
        "lightgbm",
        "catboost",
        "shap",
        "lime",
        "optuna",
        "mlflow",
        "prophet",
        "nltk",
        # "transformers" lives in SKILLS, not here. A term in two vocabularies
        # becomes two requirements in two weighted categories, which
        # double-counts one thing the posting asked for once.
        "spacy",
        "gensim",
        "hugging face",
        "pytorch",
        "tensorflow",
        "keras",
        "jax",
        "langchain",
        "llamaindex",
        "openai",
        "anthropic",
        "streamlit",
        "gradio",
        "dash",
        "flask",
        "fastapi",
        "django",
        "pydantic",
        "pytest",
        "jupyter",
        "anaconda",
        # data platforms and stores
        "postgresql",
        "mysql",
        "sqlite",
        "sql server",
        "oracle",
        "mongodb",
        "redis",
        "cassandra",
        "elasticsearch",
        "neo4j",
        "duckdb",
        "snowflake",
        "bigquery",
        "redshift",
        "databricks",
        "synapse",
        "hadoop",
        "hive",
        "spark",
        "kafka",
        "airflow",
        "dbt",
        "prefect",
        "dagster",
        "fivetran",
        "pinecone",
        "chroma",
        "faiss",
        "weaviate",
        "qdrant",
        # cloud and platform
        "aws",
        "azure",
        "gcp",
        "google cloud",
        "sagemaker",
        "vertex ai",
        "azure ml",
        "lambda",
        "s3",
        "ec2",
        "docker",
        "kubernetes",
        "terraform",
        "ansible",
        "jenkins",
        "github actions",
        "gitlab ci",
        "argo",
        "prometheus",
        "grafana",
        "datadog",
        "sentry",
        # bi and productivity
        "power bi",
        "tableau",
        "looker",
        "qlik",
        "excel",
        "google sheets",
        "google analytics",
        "alteryx",
        "sas viya",
        "spss",
        "stata",
        "jira",
        "confluence",
        "notion",
        "figma",
        "git",
        "github",
        "gitlab",
        "bitbucket",
        "linux",
        "postman",
        "salesforce",
        "sap",
    }
)

#: Industry and problem domains. A domain hit is the signal that the candidate
#: has worked on this *kind* of problem, which a skill list cannot express.
DOMAINS: frozenset[str] = frozenset(
    {
        "fintech",
        "banking",
        "bfsi",
        "financial services",
        "credit risk",
        "lending",
        "insurance",
        "insurtech",
        "payments",
        "fraud detection",
        "anti money laundering",
        "trading",
        "capital markets",
        "wealth management",
        "healthcare",
        "healthtech",
        "clinical",
        "pharmaceutical",
        "life sciences",
        "biotech",
        "medical imaging",
        "public health",
        "ecommerce",
        "retail",
        "supply chain",
        "logistics",
        "manufacturing",
        "marketing",
        "advertising",
        "adtech",
        "customer analytics",
        "crm",
        "telecom",
        "energy",
        "utilities",
        "oil and gas",
        "automotive",
        "aviation",
        "travel",
        "hospitality",
        "real estate",
        "proptech",
        "education",
        "edtech",
        "gaming",
        "media",
        "entertainment",
        "cybersecurity",
        "government",
        "public sector",
        "nonprofit",
        "agriculture",
        "sustainability",
        "climate",
        "human resources",
        "legal",
        "compliance",
        "risk management",
        "saas",
        "b2b",
        "b2c",
    }
)

#: Recurring responsibility phrases. These are what a job description means by
#: "you will..." and they are scored separately from skills because a candidate
#: can know a technique without ever having owned the outcome.
RESPONSIBILITIES: frozenset[str] = frozenset(
    {
        "build models",
        "deploy models",
        "productionize models",
        "model governance",
        "model risk management",
        "model documentation",
        "data pipeline development",
        "pipeline orchestration",
        "dashboard development",
        "report automation",
        "ad hoc analysis",
        "root cause analysis",
        "business partnering",
        "stakeholder communication",
        "requirements analysis",
        "solution design",
        "architecture design",
        "technical mentoring",
        "team leadership",  # "code review" is in SKILLS
        "roadmap planning",
        "backlog grooming",
        "sprint planning",
        "vendor management",
        "client engagement",
        "presenting to executives",
        "on call support",
        "production support",
        "incident management",
        "process improvement",
        "cost optimization",
        "capacity planning",
        "data strategy",
        "experimentation",
        "product analytics",
        "customer segmentation",
        "churn prediction",
        "demand forecasting",
        "pricing analysis",
        "fraud investigation",
        "regulatory reporting",
        "audit",
        "training delivery",
        "content development",
        "research",
    }
)

#: Degree words. Matched case-insensitively and boundary-anchored like every
#: other term, so ``ms`` does not match ``msgs``.
EDUCATION_TERMS: frozenset[str] = frozenset(
    {
        "bachelor",
        "bachelors",
        "master",
        "masters",
        "phd",
        "doctorate",
        "mba",
        "b.tech",
        "btech",
        "bsc",
        "b.sc",
        "b.a",
        "b.s",
        "msc",
        "m.sc",
        "m.tech",
        "mtech",
        "mca",
        "bca",
        "bcom",
        "mcom",
        "diploma",
        "postgraduate",
        "undergraduate",
        "degree",
        # Fields of study. "statistics" is deliberately absent -- it is a skill
        # first, and a term may only belong to one vocabulary.
        "computer science",
        "engineering",
        "mathematics",
        "economics",
        "physics",
        "data science",
        "information technology",
    }
)

#: Certification words and the well-known certificate names.
CERTIFICATION_TERMS: frozenset[str] = frozenset(
    {
        "certification",
        "certified",
        "certificate",
        "credential",
        "aws certified",
        "azure certified",
        "google cloud certified",
        "solutions architect",
        "data engineer associate",
        "pmp",
        "prince2",
        "six sigma",
        "scrum master",
        "itil",
        "cfa",
        "frm",
        "cpa",
        "cissp",
        "comptia",
        "tensorflow developer",
        "databricks certified",
        "snowflake certified",
        "tableau certified",
        "power bi certified",
    }
)

#: Terms that are evidence *for* one another without being the same thing.
#:
#: A candidate who has used PyTorch has not used TensorFlow, but a job
#: description asking for TensorFlow is not looking at a blank page either.
#: Those cases score as ``related`` (half credit), which is the honest answer
#: and is why the ATS report has three statuses rather than two.
#:
#: Symmetric by construction -- :func:`related_terms` closes the table both
#: ways, so an entry only has to be written once.
_RELATED_PAIRS: tuple[tuple[str, str], ...] = (
    ("pytorch", "tensorflow"),
    ("pytorch", "keras"),
    ("tensorflow", "keras"),
    ("xgboost", "lightgbm"),
    ("xgboost", "catboost"),
    ("lightgbm", "catboost"),
    ("power bi", "tableau"),
    ("power bi", "looker"),
    ("tableau", "looker"),
    ("qlik", "tableau"),
    ("aws", "azure"),
    ("aws", "gcp"),
    ("azure", "gcp"),
    ("sagemaker", "vertex ai"),
    ("sagemaker", "azure ml"),
    ("snowflake", "bigquery"),
    ("snowflake", "redshift"),
    ("bigquery", "redshift"),
    ("snowflake", "databricks"),
    ("postgresql", "mysql"),
    ("postgresql", "sql server"),
    ("mysql", "sql server"),
    ("mongodb", "cassandra"),
    ("airflow", "prefect"),
    ("airflow", "dagster"),
    ("prefect", "dagster"),
    ("docker", "kubernetes"),
    ("jenkins", "github actions"),
    ("jenkins", "gitlab ci"),
    ("github actions", "gitlab ci"),
    ("flask", "fastapi"),
    ("flask", "django"),
    ("fastapi", "django"),
    ("streamlit", "gradio"),
    ("streamlit", "dash"),
    ("pandas", "polars"),
    ("spark", "pyspark"),
    ("spark", "hadoop"),
    ("nltk", "spacy"),
    ("langchain", "llamaindex"),
    ("scikit-learn", "statsmodels"),
    ("matplotlib", "seaborn"),
    ("matplotlib", "plotly"),
    ("deep learning", "neural networks"),
    ("machine learning", "deep learning"),
    ("natural language processing", "large language model"),
    ("large language model", "generative ai"),
    ("generative ai", "prompt engineering"),
    ("retrieval augmented generation", "vector search"),
    ("vector search", "embeddings"),
    ("explainability", "interpretability"),
    ("shap", "explainability"),
    ("lime", "explainability"),
    ("a/b testing", "experimental design"),
    ("a/b testing", "causal inference"),
    ("forecasting", "time series"),
    ("etl", "elt"),
    ("etl", "data pipelines"),
    ("data warehousing", "dimensional modeling"),
    ("business intelligence", "dashboarding"),
    ("business intelligence", "reporting"),
    ("continuous integration", "continuous deployment"),
    ("continuous integration", "automated testing"),
    ("docker", "containerization"),
    ("kubernetes", "orchestration"),
    ("mlops", "model deployment"),
    ("mlops", "model monitoring"),
    ("model monitoring", "drift monitoring"),
    ("agile", "scrum"),
    ("scrum", "kanban"),
    ("java", "scala"),
    ("javascript", "typescript"),
    ("c++", "c#"),
    ("r", "sas"),
    ("sas", "spss"),
    ("excel", "google sheets"),
    ("git", "github"),
    ("git", "gitlab"),
    ("statistics", "statistical analysis"),
    ("statistics", "hypothesis testing"),
    ("credit risk", "risk management"),
    ("fraud detection", "anomaly detection"),
    ("banking", "fintech"),
    ("banking", "bfsi"),
    ("fintech", "financial services"),
    ("bfsi", "financial services"),
    ("ecommerce", "retail"),
    ("supply chain", "logistics"),
    ("healthcare", "life sciences"),
    ("healthcare", "clinical"),
    # Degrees. A job description asks for a "bachelor's degree" and a resume
    # says "B.Tech"; without these the education category scores zero for a
    # candidate who plainly meets the requirement -- the single most misleading
    # false gap this scorer can produce.
    ("bachelor", "bachelors"),
    ("bachelor", "b.tech"),
    ("bachelor", "btech"),
    ("bachelor", "bsc"),
    ("bachelor", "b.sc"),
    ("bachelor", "bca"),
    ("bachelor", "degree"),
    ("master", "masters"),
    ("master", "m.tech"),
    ("master", "mtech"),
    ("master", "msc"),
    ("master", "m.sc"),
    ("master", "mca"),
    ("master", "mba"),
    ("master", "degree"),
    ("phd", "doctorate"),
    ("degree", "diploma"),
    # "degree" needs the abbreviations too, not just the words it is related to
    # via "bachelor". Relatedness is one hop, not transitive, so without these
    # the same candidate scores "bachelor" as related and "degree" as missing --
    # two contradictory answers about one qualification, in one category.
    ("degree", "b.tech"),
    ("degree", "btech"),
    ("degree", "bsc"),
    ("degree", "b.sc"),
    ("degree", "bca"),
    ("degree", "m.tech"),
    ("degree", "mtech"),
    ("degree", "msc"),
    ("degree", "m.sc"),
    ("degree", "mca"),
    ("degree", "mba"),
    ("degree", "phd"),
    ("certification", "certified"),
    ("certification", "certificate"),
    ("certified", "credential"),
)


def _build_related() -> dict[str, frozenset[str]]:
    table: dict[str, set[str]] = {}
    for left, right in _RELATED_PAIRS:
        table.setdefault(left, set()).add(right)
        table.setdefault(right, set()).add(left)
    return {term: frozenset(peers) for term, peers in table.items()}


_RELATED: dict[str, frozenset[str]] = _build_related()


def related_terms(term: str) -> frozenset[str]:
    """Terms that count as partial evidence for ``term``. Empty if there are none."""
    return _RELATED.get(term.strip().lower(), frozenset())


#: Every vocabulary category, in the order a report should present them.
CATEGORY_TERMS: dict[str, frozenset[str]] = {
    "skills": SKILLS,
    "tools": TOOLS,
    "responsibilities": RESPONSIBILITIES,
    "domains": DOMAINS,
}

#: Longest-first, so a scan that stops at the first hit prefers the more
#: specific term: "machine learning engineer" should not be recorded as the
#: bare "learning", and "azure ml" should not be recorded as "azure".
ALL_TERMS: tuple[str, ...] = tuple(
    sorted(
        SKILLS | TOOLS | DOMAINS | RESPONSIBILITIES,
        key=lambda term: (-len(term), term),
    )
)


def category_of(term: str) -> str | None:
    """Which vocabulary a term belongs to, or ``None`` if it is not in one.

    Order is fixed rather than dependent on set iteration, so a term listed in
    two categories always reports the same one.
    """
    lowered = term.strip().lower()
    for name, terms in CATEGORY_TERMS.items():
        if lowered in terms:
            return name
    return None
