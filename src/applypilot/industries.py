"""Industries: how the profile's skills are broken down, and how a tailored resume groups them.

Choosing an industry in Setup -> Your profile shows that industry's skill boxes (with examples), and
tells tailoring and scoring which field the candidate is in. The boxes are only for entering skills:
every skill in the profile counts the same, whichever box it's in.
"""

from __future__ import annotations

# name -> {"skills": [(profile key, examples shown in Setup)], "resume": skill groups for a tailored resume}
INDUSTRIES: dict[str, dict] = {
    "Marketing and communications": {
        "skills": [
            ("channels_and_specialisms", "paid search, paid social, SEO, email, CRM, brand, PR, events"),
            ("platforms_and_tools", "Google Ads, Meta Ads, LinkedIn Ads, HubSpot, Salesforce, Canva, Shopify"),
            ("analytics_and_reporting", "GA4, Looker Studio, Tableau, attribution, A/B testing, Excel"),
            ("content_and_creative", "copywriting, video, social content, Adobe Creative Cloud, Figma"),
            ("certifications", "Google Ads certification, HubSpot Inbound, Meta Blueprint"),
        ],
        "resume": ["Channels", "Platforms & Tools", "Analytics", "Content & Creative", "Certifications"],
    },
    "Software and IT": {
        "skills": [
            ("languages", "Python, Java, TypeScript, SQL, Go"),
            ("frameworks", "React, Django, FastAPI, Spring Boot, .NET"),
            ("cloud_and_devops", "AWS, Azure, Docker, Kubernetes, Terraform, CI/CD"),
            ("databases", "PostgreSQL, MySQL, MongoDB, Redis, Snowflake"),
            ("tools_and_practices", "Git, Jira, testing, agile, observability"),
            ("certifications", "AWS Solutions Architect, Azure Fundamentals, CKA"),
        ],
        "resume": ["Languages", "Frameworks", "Cloud & DevOps", "Databases", "Tools"],
    },
    "Engineering (not software)": {
        "skills": [
            ("disciplines_and_methods", "structural design, FEA, HAZOP, commissioning, project delivery"),
            ("design_software", "AutoCAD, Revit, SolidWorks, 12d, MATLAB, ETABS"),
            ("standards_and_codes", "AS 3600, AS/NZS 3000, ISO 9001, Eurocodes"),
            ("tools_and_equipment", "PLCs, SCADA, survey equipment, test rigs"),
            ("registrations_and_licences", "CPEng, RPEQ, NER, electrical licence"),
        ],
        "resume": ["Disciplines & Methods", "Design Software", "Standards & Codes", "Registrations"],
    },
    "Finance and accounting": {
        "skills": [
            ("accounting_and_finance", "month-end close, BAS, payroll, budgeting, forecasting, audit"),
            ("systems_and_software", "Xero, MYOB, SAP, NetSuite, Excel (advanced), Power BI"),
            ("regulation_and_compliance", "GST, FBT, IFRS, AASB, tax returns, SOX"),
            ("analysis_and_reporting", "financial modelling, variance analysis, board reporting"),
            ("qualifications", "CPA, CA, CFA, registered tax agent"),
        ],
        "resume": ["Accounting & Finance", "Systems", "Regulation & Compliance", "Analysis", "Qualifications"],
    },
    "Sales and business development": {
        "skills": [
            ("sales_skills", "prospecting, negotiation, account management, solution selling, tenders"),
            ("crm_and_tools", "Salesforce, HubSpot, LinkedIn Sales Navigator, Gong"),
            ("markets_and_sectors", "SaaS, government, retail, mid-market, enterprise"),
            ("certifications", "MEDDICC, Challenger, product certifications"),
        ],
        "resume": ["Sales", "CRM & Tools", "Markets", "Certifications"],
    },
    "Healthcare": {
        "skills": [
            ("clinical_skills", "patient assessment, wound care, medication administration, triage"),
            ("systems", "Cerner, Epic, Best Practice, Medical Director"),
            ("registrations_and_licences", "AHPRA registration, NMC PIN, working with children check"),
            ("certifications", "ALS, BLS, immunisation certificate"),
        ],
        "resume": ["Clinical Skills", "Systems", "Registrations", "Certifications"],
    },
    "Operations, admin and HR": {
        "skills": [
            ("operations_and_admin", "scheduling, procurement, inventory, process improvement, reporting"),
            ("people_and_hr", "recruitment, onboarding, employee relations, payroll, WHS"),
            ("systems_and_software", "Microsoft 365, SAP, Workday, Xero, Excel"),
            ("certifications", "Certificate IV in WHS, first aid, forklift licence"),
        ],
        "resume": ["Operations", "People & HR", "Systems", "Certifications"],
    },
    "Other": {
        "skills": [
            ("skills", "customer service, project coordination, research, stakeholder management"),
            ("tools_and_software", "Microsoft 365, Canva, Xero, Excel"),
            ("certifications_and_licences", "first aid, RSA, driver's licence, industry certificates"),
        ],
        "resume": ["Core Skills", "Tools & Software", "Certifications"],
    },
}

NAMES = list(INDUSTRIES)


def get(name) -> dict | None:
    """The industry's definition, or None if the profile names none (or one this version doesn't know)."""
    return INDUSTRIES.get(str(name or "").strip())


def of(profile: dict | None) -> str:
    """The profile's industry name ("" if not set)."""
    name = str(((profile or {}).get("experience") or {}).get("industry") or "").strip()
    return name if name in INDUSTRIES else ""


def field(profile: dict | None) -> str:
    """The candidate's field, to tell the AI: the industry chosen, else the field typed under Other
    ("Business analysis", "Nursing"), else "" (the prompts then say to judge it from the resume)."""
    exp = (profile or {}).get("experience") or {}
    name = of(profile)
    if name and name != "Other":
        return name
    typed = " ".join(str(exp.get("field") or "").split())[:60]
    return typed


def skill_layouts() -> dict[str, list[list[str]]]:
    """For the Setup form: industry -> [[profile key, examples], ...]."""
    return {name: [[key, examples] for key, examples in d["skills"]] for name, d in INDUSTRIES.items()}


def resume_groups(profile: dict | None) -> list[str]:
    """The skill groups a tailored resume uses for this candidate's industry ([] if not set)."""
    d = get(of(profile))
    return list(d["resume"]) if d else []
