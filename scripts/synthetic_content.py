"""Fictional content for the synthetic supplier RFP responses.

Everything here is invented for training. Company names, people and numbers are fictional.
The procurement request is the same for every supplier: a cloud vendor-onboarding platform
for the fictional Northwind Logistics.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

REQUEST_TITLE = "Response to RFP NW-2025-014: Vendor Onboarding Platform for Northwind Logistics"

Section = tuple[str, list[str]]


@dataclass
class Proposal:
    key: str
    supplier: str
    profile: str
    submission_date: str
    experience_rating: float
    outline: bool
    sections: list[Section]
    price_items: list[tuple[str, int]] = field(default_factory=list)
    price_assumptions: list[str] = field(default_factory=list)
    price_after_section: int = 3  # index of the section the price table follows


APEX = Proposal(
    key="apex",
    supplier="Apex Systems",
    profile="Strong technical design and security; higher price; moderate delivery schedule.",
    submission_date="2025-03-03",
    experience_rating=4.5,
    outline=True,
    sections=[
        ("1. Executive Summary", [
            "Northwind Logistics needs a single platform to onboard, verify and monitor its "
            "3,000 vendors across freight, warehousing and last-mile delivery. Apex Systems "
            "proposes a multi-tenant, API-first onboarding platform built on a microservices "
            "architecture, deployed in two cloud regions with automatic failover.",
            "Our design prioritises security, auditability and integration depth. We will "
            "deliver a production-ready platform in 28 weeks, at a fixed price that reflects "
            "a premium engineering and security investment.",
        ]),
        ("2. Proposed Solution and Technical Approach", [
            "The platform uses an event-driven architecture with a managed message broker, "
            "horizontally scalable stateless services and a PostgreSQL cluster with read "
            "replicas. Load tests on comparable deployments sustained 1,200 requests per "
            "second with a 95th percentile latency under 180 ms.",
            "Integrations are delivered through a documented REST and webhook API plus "
            "certified connectors for SAP S/4HANA, Oracle Transportation Management and "
            "Microsoft Entra ID. Each connector ships with contract tests and a sandbox. "
            "Document intake uses configurable workflows with rule-based risk scoring and "
            "automated sanctions and tax-ID checks.",
        ]),
        ("3. Timeline, Team Structure and Milestones", [
            "Delivery runs for 28 weeks in four phases: Discovery and design (weeks 1-5), "
            "Core build (weeks 6-16), Integration and security hardening (weeks 17-24), and "
            "Pilot and go-live (weeks 25-28). The team has 11 people: a delivery lead, a "
            "solution architect, six engineers, a security engineer, and a QA lead.",
            "Milestones: M1 design sign-off (week 5), M2 core workflows demo (week 12), "
            "M3 integrations complete (week 22), M4 penetration test closed (week 25), "
            "M5 production go-live (week 28).",
        ]),
        ("4. Pricing", [
            "Pricing is a fixed fee for a three-year term. The table below itemises each "
            "component.",
        ]),
        ("5. Security, Compliance and Risk Controls", [
            "Apex Systems holds SOC 2 Type II and ISO 27001 certifications, both audited in "
            "the last 12 months. Data is encrypted in transit with TLS 1.3 and at rest with "
            "AES-256, using customer-managed keys. Single sign-on supports SAML 2.0 and "
            "OIDC with enforced multi-factor authentication.",
            "All administrative and data-access events are written to an immutable audit log "
            "retained for seven years. An independent penetration test is performed before "
            "go-live and annually. Personal data is processed under a GDPR-aligned data "
            "processing agreement with regional data residency options.",
            "Key risks and mitigations: integration complexity with legacy ERP "
            "(mitigated by early connector spikes), and data-migration quality (mitigated "
            "by two dry-run migrations).",
        ]),
        ("6. Support Model, Experience and References", [
            "Standard support covers business hours with a one-hour response for severity-1 "
            "incidents; 24x7 coverage is available as an option. A named technical account "
            "manager is assigned to Northwind.",
            "Apex Systems has delivered nine onboarding or supplier-management platforms "
            "since 2019. References: Harborline Freight (supplier portal, 2023) and Meridian "
            "Foods (vendor compliance, 2022); contacts available on request.",
        ]),
    ],
    price_items=[
        ("Platform licence (3-year term)", 540_000),
        ("Implementation services", 420_000),
        ("Integrations (ERP, TMS, SSO)", 210_000),
        ("Security hardening and penetration test", 90_000),
        ("Training", 40_000),
        ("Contingency reserve", 120_000),
    ],
    price_assumptions=[
        "Prices are in USD, exclusive of taxes, fixed for the 3-year term.",
        "Northwind provides timely access to ERP and TMS sandbox environments.",
        "Up to three change requests per phase are included.",
    ],
)

BRIGHTPATH = Proposal(
    key="brightpath",
    supplier="BrightPath Tech",
    profile="Lowest price and fast timeline; weak compliance detail and limited experience.",
    submission_date="2025-03-01",
    experience_rating=2.0,
    outline=False,
    sections=[
        ("1. Executive Summary", [
            "BrightPath Tech offers a fast, low-cost vendor onboarding platform for "
            "Northwind Logistics. Our ready-made product can be configured and launched in "
            "just 14 weeks, at the most competitive price you will see in this process.",
            "We understand that Northwind wants to replace spreadsheet-based vendor "
            "registration with a single online process, reduce manual checking, and give "
            "procurement staff a clear view of which vendors are approved. Our proposal "
            "focuses on getting those basics live quickly rather than on advanced features.",
        ]),
        ("2. Proposed Solution and Approach", [
            "We will configure our standard onboarding product, which supports vendor "
            "registration, document upload, approval workflows and basic reporting. The "
            "product runs on a single cloud region. Integration with Northwind systems can "
            "be done through our import and export files and a basic API.",
            "Customisation beyond configuration is limited to the items listed in the "
            "project scope. Scalability testing has not yet been performed for volumes "
            "above 500 vendors.",
        ]),
        ("3. Timeline and Team", [
            "The project runs for 14 weeks: configuration (weeks 1-6), data load and "
            "testing (weeks 7-11), training and launch (weeks 12-14). A project manager "
            "and three consultants will be assigned.",
            "Status updates are shared by email every two weeks. Northwind is expected to "
            "supply its vendor list in a spreadsheet by week 4 and to nominate a business "
            "contact who can answer questions within two working days. A formal risk plan "
            "will be prepared if requested.",
        ]),
        ("4. Pricing", [
            "The table below shows our all-in pricing for core scope. Integration costs "
            "will be quoted separately once Northwind systems are reviewed.",
        ]),
        ("5. Security and Compliance", [
            "We follow industry best practices for security and take data protection "
            "seriously. ISO certification is planned for next year. Data is stored in "
            "encrypted databases. Details of audit logging and independent testing are "
            "available on request.",
        ]),
        ("6. Support and Experience", [
            "Support is provided by email during business hours. BrightPath Tech was "
            "founded in 2022 and has completed two similar projects for small customers. "
            "References are not included in this response.",
        ]),
    ],
    price_items=[
        ("Platform subscription (3-year term)", 330_000),
        ("Implementation", 240_000),
        ("Training", 20_000),
        ("Email support (3 years)", 100_000),
    ],
    price_assumptions=[
        "Prices in USD.",
        "Integration costs not included.",
    ],
)

NEXAWORKS = Proposal(
    key="nexaworks",
    supplier="NexaWorks",
    profile="Balanced proposal; strongest implementation plan and support model.",
    submission_date="2025-03-04",
    experience_rating=4.0,
    outline=True,
    sections=[
        ("1. Executive Summary", [
            "NexaWorks proposes a configurable vendor onboarding platform that balances "
            "capability, cost and delivery certainty. We have structured the engagement "
            "around six milestones with named owners and a weekly governance cadence so that "
            "Northwind always knows where the project stands.",
        ]),
        ("2. Proposed Solution and Technical Approach", [
            "The platform is a cloud-hosted, multi-tenant application with a modular "
            "workflow engine, a role-based access model and standard REST APIs. Prebuilt "
            "connectors cover SAP and common identity providers; other systems are "
            "integrated through the API gateway. The architecture scales horizontally and "
            "has been run in production with 2,500 active vendors.",
        ]),
        ("3. Implementation Plan, Team and Milestones", [
            "The 22-week plan has six milestones: kickoff and governance (week 2), "
            "requirements baseline (week 4), configuration complete (week 10), integrations "
            "verified (week 15), user acceptance testing passed (week 19), and go-live "
            "with hypercare (week 22). Every milestone has an acceptance checklist signed "
            "by the Northwind sponsor.",
            "The team of 9 comprises a programme manager, a solution architect, four "
            "engineers, a business analyst, a QA lead and a change manager. A RACI "
            "matrix is agreed in week 1. A top-five risk register is reviewed weekly; "
            "current risks include data quality, integration availability and stakeholder "
            "capacity, each with an owner, trigger and mitigation.",
        ]),
        ("4. Pricing", [
            "Pricing is fixed for the core scope. Optional items are priced separately.",
        ]),
        ("5. Security, Compliance and Risk Controls", [
            "NexaWorks is SOC 2 Type II certified. Data is encrypted in transit and at "
            "rest, access is role-based with single sign-on and multi-factor "
            "authentication, and audit logs are retained for three years. Our ISO 27001 "
            "certification audit is scheduled for the second quarter. Annual third-party "
            "penetration testing is performed.",
        ]),
        ("6. Support Model, Experience and References", [
            "Support is delivered 24x7 through three tiers with severity-based response "
            "targets (severity 1: 30 minutes) and a 99.9% availability SLA backed by "
            "service credits. A named customer success manager runs monthly service "
            "reviews, and a 90-day hypercare period follows go-live.",
            "NexaWorks has delivered six similar platforms. References: Brightwater "
            "Logistics (2024) and Summit Retail Group (2023).",
        ]),
    ],
    price_items=[
        ("Platform licence (3-year term)", 390_000),
        ("Implementation", 330_000),
        ("Integrations", 150_000),
        ("Training and change management", 60_000),
        ("Support and customer success (3 years)", 120_000),
    ],
    price_assumptions=[
        "Prices in USD, exclusive of taxes.",
        "Includes up to four system integrations.",
        "Optional 24x7 on-site hypercare is quoted separately.",
    ],
    price_after_section=3,
)

ORBIT = Proposal(
    key="orbit",
    supplier="Orbit Digital",
    profile="Strong experience and references; vague integration plan; medium pricing.",
    submission_date="2025-03-04",
    experience_rating=4.5,
    outline=False,
    sections=[
        ("1. Executive Summary", [
            "Orbit Digital has spent twelve years helping logistics and retail companies "
            "modernise supplier management. We understand Northwind's need for a "
            "trustworthy, scalable onboarding platform and bring a team that has done this "
            "many times before.",
        ]),
        ("2. Proposed Solution and Approach", [
            "Our platform covers vendor registration, document collection, risk screening "
            "and approval workflows. It will be integrated with your existing systems "
            "using standard APIs, and our team will work with your IT department to "
            "determine the best approach during the project. Technical details will be "
            "finalised in the design phase.",
        ]),
        ("3. Timeline, Team and Milestones", [
            "The project is expected to take around 26 weeks, depending on the "
            "complexity of the integrations. We will provide a project manager, a "
            "technical lead and a team of engineers. Milestones will be agreed at "
            "kickoff.",
        ]),
        ("4. Pricing", [
            "Our pricing is based on a three-year term. Integration work is covered by an "
            "allowance until scope is known.",
        ]),
        ("5. Security, Compliance and Risk Controls", [
            "Orbit Digital is ISO 27001 certified and encrypts data in transit and at "
            "rest. Access is role-based, and security events are logged. We conduct "
            "periodic security reviews.",
        ]),
        ("6. Support, Experience and References", [
            "Orbit Digital has completed fourteen supplier-management implementations "
            "since 2013. Named references: Aldergate Shipping (2024), Pinecrest Foods "
            "(2023), Corvus Manufacturing (2022), Lakeshore Retail (2021) and Tidewater "
            "Logistics (2020); contact details are provided in the appendix.",
            "Support is available weekdays 08:00-18:00 with a four-hour target response "
            "for urgent issues, and a named delivery partner.",
        ]),
    ],
    price_items=[
        ("Platform licence (3-year term)", 450_000),
        ("Implementation", 360_000),
        ("Integration allowance", 120_000),
        ("Training", 50_000),
        ("Support (3 years)", 200_000),
    ],
    price_assumptions=[
        "Prices in USD.",
        "Integration allowance may be adjusted once scope is confirmed.",
    ],
)

BASE_PROPOSALS = [APEX, BRIGHTPATH, NEXAWORKS, ORBIT]

# ----------------------------------------------------------------- filler text
# Deterministic generic prose used only to reach realistic page counts. It deliberately
# makes no certification, price or schedule claims.

_POOLS: dict[str, tuple[list[str], list[str], list[str]]] = {
    "architecture": (
        ["The platform", "Each service", "The integration layer", "The workflow engine",
         "The reporting module", "The data layer", "The API gateway"],
        ["exposes", "coordinates", "records", "validates", "schedules", "caches", "routes"],
        ["vendor records through documented interfaces", "approval steps with configurable rules",
         "document metadata for later retrieval", "requests between internal components",
         "background jobs with retry and back-off", "reference data used by several workflows"],
    ),
    "delivery": (
        ["The project team", "The delivery lead", "Each workstream", "The steering group",
         "The quality lead", "The change manager"],
        ["reviews", "prepares", "tracks", "confirms", "escalates", "documents"],
        ["progress against the agreed plan every week", "dependencies that affect upcoming milestones",
         "acceptance criteria before work is released", "open actions and their owners",
         "training material for each user group", "lessons learned at the end of every phase"],
    ),
    "governance": (
        ["The programme office", "A named sponsor", "The risk owner", "The service manager",
         "The security lead"],
        ["maintains", "approves", "monitors", "reports", "reviews"],
        ["the register of risks and assumptions", "changes to scope through a formal request",
         "operational metrics in a monthly report", "outstanding issues at each governance meeting",
         "access rights on a defined schedule"],
    ),
    "support": (
        ["The service desk", "A support engineer", "The customer success manager", "The on-call team"],
        ["triages", "resolves", "tracks", "communicates", "reviews"],
        ["incoming requests by severity", "known problems with a documented workaround",
         "service levels against targets", "status updates to affected users",
         "recurring incidents for permanent fixes"],
    ),
}


def filler(topic: str, paragraphs: int, rng: random.Random) -> list[str]:
    subjects, verbs, objects = _POOLS[topic]
    out = []
    for _ in range(paragraphs):
        sentences = []
        for _ in range(rng.randint(5, 7)):
            sentences.append(
                f"{rng.choice(subjects)} {rng.choice(verbs)} {rng.choice(objects)}, "
                f"{rng.choice(['as described in the project plan', 'in line with agreed standards', 'to keep stakeholders informed', 'so that results can be audited'])}."
            )
        out.append(" ".join(sentences))
    return out


def resume_blocks(count: int, rng: random.Random) -> list[str]:
    roles = ["Solution Architect", "Senior Engineer", "QA Lead", "Delivery Manager", "Security Engineer"]
    out = []
    for i in range(count):
        years = rng.randint(6, 18)
        out.append(
            f"Team member {i + 1:02d} - {rng.choice(roles)}. {years} years of experience in "
            f"enterprise software delivery, including {rng.randint(2, 7)} supplier-management "
            f"or workflow platforms. Responsible for design reviews, quality gates and handover. "
            f"(Fictional profile for training purposes.)"
        )
    return out
