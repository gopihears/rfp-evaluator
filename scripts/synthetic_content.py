"""Fictional content for the synthetic supplier RFP responses.

Everything here is invented for training. Company names, people and numbers are fictional.
Each supplier answers the same fictional request but with its own structure, section names,
order, voice, layout style, level of evidence and deliberately missing information.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

REQUEST_TITLE = "Response to RFP NW-2025-014: Vendor Onboarding Platform for Northwind Logistics"


@dataclass(frozen=True)
class Table:
    rows: list[list[str]]
    widths: list[int]  # millimetres
    caption: str = ""
    total_row: bool = False
    right_cols: tuple[int, ...] = ()


Item = str | list[str] | Table  # paragraph | bullet list | table


def price_table(items: list[tuple[str, int]], header: tuple[str, str] = ("Item", "Price (USD)"),
                caption: str = "") -> Table:
    rows = [list(header)] + [[name, f"{amount:,}"] for name, amount in items]
    rows.append(["Total", f"{sum(a for _, a in items):,}"])
    return Table(rows, [110, 40], caption, total_row=True, right_cols=(1,))


@dataclass(frozen=True)
class Style:
    body_font: str = "Helvetica"
    bold_font: str = "Helvetica-Bold"
    body_size: float = 10.5
    leading: float = 14.5
    heading_size: float = 13.0
    heading_color: str = "#1f2a44"
    heading_case: str = "title"  # title | upper
    numbering: str = "1."  # "1." | "A." | "Part I -" | "" (none)
    table_style: str = "grid"  # grid | zebra | minimal
    margin_mm: int = 20
    header: str = ""
    cover: bool = False
    space_after: float = 5.0


@dataclass
class Proposal:
    key: str
    supplier: str
    profile: str
    submission_date: str
    experience_rating: float
    outline: bool
    style: Style
    sections: list[tuple[str, list[Item]]]
    price_items: list[tuple[str, int]]
    voice: str
    expected: dict[int, tuple[int, int]]  # criterion_id -> (low, high) score band
    rationale: dict[int, str]
    facts: dict[str, object]
    key_facts: list[str]
    missing: list[str] = field(default_factory=list)


# ====================================================================== APEX SYSTEMS

APEX_PRICE = [
    ("Platform licence (3-year term)", 540_000),
    ("Implementation services", 420_000),
    ("Integrations (ERP, TMS, SSO)", 210_000),
    ("Security hardening and penetration test", 90_000),
    ("Training", 40_000),
    ("Contingency reserve", 120_000),
]

APEX = Proposal(
    key="apex",
    supplier="Apex Systems",
    profile="Strong technical design and security; higher price; moderate delivery schedule.",
    submission_date="2025-03-03",
    experience_rating=4.5,
    outline=True,
    voice="apex",
    style=Style(body_size=11, leading=15.5, heading_size=14, space_after=6,
                heading_color="#14284b", numbering="1.", table_style="grid",
                header="APEX SYSTEMS  |  Confidential proposal  |  RFP NW-2025-014", cover=True),
    sections=[
        ("Executive Summary", [
            "Northwind Logistics needs one platform to onboard, verify and continuously monitor "
            "roughly 3,000 vendors across freight, warehousing and last-mile delivery. Apex Systems "
            "proposes a multi-tenant, API-first onboarding platform on a microservices architecture, "
            "deployed across two cloud regions with automatic failover.",
            "Our design puts security, auditability and integration depth first. We will deliver a "
            "production-ready platform in 28 weeks for a fixed fee. The price reflects a premium "
            "engineering and security investment, and we explain below what it buys.",
        ]),
        ("Our Understanding of Northwind's Requirements", [
            "From the RFP and the clarification session we understand that Northwind must:",
            ["cut vendor onboarding from the current 12 working days to under 5;",
             "automate sanctions, tax-ID and bank-detail verification with a full audit trail;",
             "synchronise approved vendors with SAP S/4HANA and Oracle Transportation Management;",
             "meet internal audit expectations for access control and data retention."],
        ]),
        ("Solution Architecture", [
            "The platform is event-driven. Stateless services scale horizontally behind an API "
            "gateway, a managed message broker decouples workflow steps, and a PostgreSQL cluster "
            "with read replicas stores vendor records. Load tests on a comparable deployment "
            "sustained 1,200 requests per second with a 95th-percentile latency under 180 ms.",
            Table([["Component", "Technology", "Purpose"],
                   ["API gateway", "Managed gateway, OAuth 2.0", "Authentication, rate limiting"],
                   ["Workflow engine", "Stateless services, message broker", "Onboarding steps and approvals"],
                   ["Data store", "PostgreSQL cluster, read replicas", "Vendor records, audit trail"],
                   ["Document service", "Object storage, content hashing", "Contracts and certificates"],
                   ["Observability", "Central logging and tracing", "Monitoring and alerting"]],
                  [38, 62, 60], "Table 1 - Core components"),
        ]),
        ("Integration Approach", [
            "Integrations use a documented REST and webhook API plus certified connectors for SAP "
            "S/4HANA, Oracle Transportation Management and Microsoft Entra ID. Each connector ships "
            "with contract tests and a sandbox so Northwind can verify behaviour before go-live.",
            ["Inbound: vendor master data and payment terms from SAP;",
             "Outbound: approved-vendor events to Oracle TM within 60 seconds;",
             "Identity: SAML 2.0 and OIDC single sign-on with enforced MFA."],
        ]),
        ("Security and Compliance Framework", [
            "Apex Systems holds SOC 2 Type II and ISO 27001 certifications, both audited in the last "
            "12 months. Data is encrypted in transit with TLS 1.3 and at rest with AES-256 using "
            "customer-managed keys.",
            Table([["Standard", "Status", "Last audit"],
                   ["SOC 2 Type II", "Certified", "November 2024"],
                   ["ISO 27001", "Certified", "January 2025"],
                   ["GDPR data processing agreement", "Available", "Standard terms"]],
                  [70, 45, 45], "Table 2 - Assurance status"),
            "All administrative and data-access events are written to an immutable audit log "
            "retained for seven years. An independent penetration test is performed before go-live "
            "and annually. Principal risks are legacy ERP complexity, mitigated by early connector "
            "spikes, and data-migration quality, mitigated by two dry-run migrations.",
        ]),
        ("Hosting, Resilience and Operations", [
            "The platform is hosted in two cloud regions in an active-passive arrangement. "
            "Databases are backed up daily with 35-day retention, and backups are restored into an "
            "isolated environment every quarter to prove they work. Releases follow a blue-green "
            "pattern so that deployments do not interrupt vendors or buyers.",
            Table([["Objective", "Target", "Measured by"],
                   ["Availability", "99.95% monthly", "Synthetic transaction checks"],
                   ["API latency (p95)", "Under 250 ms", "Gateway metrics"],
                   ["Planned maintenance", "Outside 06:00-20:00 UTC", "Change calendar"]],
                  [50, 50, 60], "Table 5 - Service objectives"),
        ]),
        ("Data Migration and Testing Strategy", [
            "Vendor data will be migrated from three sources: the current ERP vendor master, "
            "procurement spreadsheets and the document share. We profile each source in discovery, "
            "agree matching rules with Northwind data owners, and run two full dry-run migrations "
            "with automated reconciliation reports before the production load.",
            "Testing combines unit and contract tests in the build pipeline, a 1,200 requests per "
            "second load test against production-sized data, an independent penetration test, and "
            "two cycles of user acceptance testing supported by scripted scenarios agreed with "
            "Northwind process owners. Defects are classified by severity and no severity-1 or "
            "severity-2 defect may remain open at go-live.",
            ["Entry criteria: environments ready, test data approved, scripts baselined;",
             "Exit criteria: all milestone tests passed, defects triaged, sponsor sign-off;",
             "Evidence: test reports and reconciliation logs retained for the audit file."],
        ]),
        ("Delivery Plan, Team and Milestones", [
            "Delivery runs for 28 weeks in four phases: discovery and design (weeks 1-5), core build "
            "(weeks 6-16), integration and security hardening (weeks 17-24), and pilot and go-live "
            "(weeks 25-28). The team of 11 comprises a delivery lead, a solution architect, six "
            "engineers, a security engineer and a QA lead.",
            Table([["Milestone", "Week", "Acceptance evidence"],
                   ["M1 Design sign-off", "5", "Approved architecture and data model"],
                   ["M2 Core workflows demo", "12", "Working onboarding flow in staging"],
                   ["M3 Integrations complete", "22", "Passing contract tests for all connectors"],
                   ["M4 Penetration test closed", "25", "No open critical or high findings"],
                   ["M5 Production go-live", "28", "Signed go-live checklist"]],
                  [55, 20, 85], "Table 3 - Milestones"),
        ]),
        ("Commercial Proposal", [
            "Pricing is a fixed fee for a three-year term, itemised below.",
            price_table(APEX_PRICE, caption="Table 4 - Fixed price"),
            ["Prices are in USD, exclusive of taxes, and fixed for the 3-year term.",
             "Northwind provides timely access to ERP and TMS sandbox environments.",
             "Up to three change requests per phase are included."],
        ]),
        ("Training and Adoption", [
            "Training is delivered in three tracks: administrators (two days, hands-on in staging), "
            "procurement users (half a day, workflow-based) and vendors (self-service guides and a "
            "recorded walkthrough). A train-the-trainer session equips Northwind staff to onboard "
            "new colleagues after go-live.",
        ]),
        ("Support and Customer Success", [
            "Standard support covers business hours with a one-hour response target for severity-1 "
            "incidents; 24x7 coverage is available as a priced option. A named technical account "
            "manager is assigned to Northwind.",
        ]),
        ("Experience and References", [
            "Apex Systems has delivered nine onboarding or supplier-management platforms since 2019. "
            "Reference engagements include Harborline Freight (supplier portal, 2023) and Meridian "
            "Foods (vendor compliance, 2022). Contact details can be supplied on request.",
        ]),
    ],
    price_items=APEX_PRICE,
    expected={1: (8, 10), 2: (6, 8), 3: (5, 7), 4: (8, 10), 5: (6, 8)},
    rationale={
        1: "Detailed event-driven architecture, tested throughput, named connectors and sandboxes.",
        2: "Phased 28-week plan with milestones and a defined team, but a longer schedule and no risk register.",
        3: "Transparent itemised fixed price with assumptions, but the highest total of the field.",
        4: "SOC 2 Type II and ISO 27001, encryption, customer-managed keys, seven-year audit log, annual pen test.",
        5: "Nine similar projects and two named references, but standard support is business hours only.",
    },
    facts={"total_price_usd": 1_420_000, "timeline_weeks": 28, "currency": "USD"},
    key_facts=[
        "SOC 2 Type II and ISO 27001 certifications",
        "sustained 1,200 requests per second",
        "immutable audit log retained for seven years",
        "28 weeks",
    ],
    missing=["reference contact details", "24x7 support included in price", "formal risk register"],
)

# ==================================================================== BRIGHTPATH TECH

BRIGHT_PRICE = [
    ("Platform subscription (3-year term)", 330_000),
    ("Implementation", 240_000),
    ("Training", 20_000),
    ("Email support (3 years)", 100_000),
]

BRIGHTPATH = Proposal(
    key="brightpath",
    supplier="BrightPath Tech",
    profile="Lowest price and fast timeline; weak compliance detail and limited experience.",
    submission_date="2025-03-01",
    experience_rating=2.0,
    outline=False,
    voice="brightpath",
    style=Style(body_size=11.5, leading=17, heading_size=15, heading_color="#d9480f",
                numbering="", table_style="zebra", margin_mm=24, space_after=7),
    sections=[
        ("Why BrightPath?", [
            "BrightPath Tech offers a fast, low-cost vendor onboarding platform for Northwind "
            "Logistics. Our ready-made product can be configured and launched in just 14 weeks, at "
            "the most competitive price you will see in this process.",
            "We understand that Northwind wants to replace spreadsheet-based vendor registration "
            "with one online process, reduce manual checking, and give procurement staff a clear "
            "view of which vendors are approved. We focus on getting those basics live quickly "
            "rather than on advanced features.",
        ]),
        ("What You Get", [
            ["Vendor self-registration and document upload", "Approval workflows with email alerts",
             "Basic reporting and exports to spreadsheet", "Standard integration via file import and export"],
            "The product runs on a single cloud region. Integration with Northwind systems can be "
            "done through import and export files and a basic API. Scalability testing has not yet "
            "been performed for volumes above 500 vendors.",
        ]),
        ("How We Deliver", [
            "The project runs for 14 weeks: configuration (weeks 1-6), data load and testing "
            "(weeks 7-11), and training and launch (weeks 12-14). A project manager and three "
            "consultants will be assigned. Status updates are shared by email every two weeks.",
            "Northwind supplies its vendor list in a spreadsheet by week 4 and nominates a business "
            "contact who can answer questions within two working days.",
        ]),
        ("Pricing", [
            price_table(BRIGHT_PRICE, header=("What", "USD")),
            "Integration costs will be quoted separately once Northwind systems are reviewed.",
        ]),
        ("Security", [
            "We follow industry best practices and take data protection seriously. ISO certification "
            "is planned for next year. Data is stored in encrypted databases. Details of audit "
            "logging and independent testing are available on request.",
        ]),
        ("About Us", [
            "BrightPath Tech was founded in 2022 and has completed two similar projects for small "
            "customers. Support is provided by email during business hours. After go-live, software "
            "updates are applied to all customers at the same time, normally once a month. Training "
            "is two online sessions for administrators plus a user guide for vendors.",
        ]),
    ],
    price_items=BRIGHT_PRICE,
    expected={1: (3, 5), 2: (4, 6), 3: (4, 6), 4: (1, 3), 5: (2, 4)},
    rationale={
        1: "Standard product with file-based integration; no scalability evidence beyond 500 vendors.",
        2: "Short, clear 14-week phasing but no milestones, named roles or risk plan.",
        3: "Lowest price but integration is excluded, so total cost is unclear; assumptions are thin.",
        4: "Generic 'best practices' claims; certification only planned; no controls or audit detail.",
        5: "Email-only support, two small projects, no references provided.",
    },
    facts={"total_price_usd": 690_000, "timeline_weeks": 14, "currency": "USD"},
    key_facts=[
        "most competitive price",
        "ISO certification is planned for next year",
        "Integration costs will be quoted separately",
        "not yet been performed for volumes above 500 vendors",
    ],
    missing=["references", "risk plan", "named team roles", "integration pricing",
             "security controls and audit detail"],
)

# ======================================================================== NEXAWORKS

NEXA_PRICE = [
    ("Platform licence (3-year term)", 390_000),
    ("Implementation", 330_000),
    ("Integrations (up to four systems)", 150_000),
    ("Training and change management", 60_000),
    ("Support and customer success (3 years)", 120_000),
]

NEXAWORKS = Proposal(
    key="nexaworks",
    supplier="NexaWorks",
    profile="Balanced proposal; strongest implementation plan and support model.",
    submission_date="2025-03-04",
    experience_rating=4.0,
    outline=True,
    voice="nexaworks",
    style=Style(body_font="Times-Roman", bold_font="Times-Bold", body_size=11.5, leading=16,
                heading_size=14, space_after=6, heading_color="#0b6e4f", numbering="A.", table_style="minimal",
                header="NexaWorks  -  Response to RFP NW-2025-014"),
    sections=[
        ("Executive Summary", [
            "NexaWorks proposes a configurable vendor onboarding platform that balances capability, "
            "cost and delivery certainty. We have structured the engagement around six milestones "
            "with named owners and a weekly governance cadence, so Northwind always knows where the "
            "project stands.",
        ]),
        ("Understanding the Requirement", [
            "Northwind's procurement team spends most of its onboarding effort chasing documents "
            "and re-keying data. The need is a single front door for vendors, consistent risk "
            "checks, and a clean hand-off to finance and operations systems, delivered without "
            "disrupting current vendor relationships.",
        ]),
        ("Solution Overview", [
            "The platform is a cloud-hosted, multi-tenant application with a modular workflow "
            "engine, a role-based access model and standard REST APIs. Prebuilt connectors cover "
            "SAP and common identity providers; other systems are integrated through the API "
            "gateway. The architecture scales horizontally and has run in production with 2,500 "
            "active vendors.",
        ]),
        ("Data Migration, Testing and Training", [
            "We profile Northwind's vendor data in week 3, agree cleansing rules with the data "
            "owners, and run two migration rehearsals before cutover. Testing runs in three layers: "
            "automated checks in the build, a structured integration test in weeks 13 to 15, and "
            "two rounds of user acceptance testing with scenarios written together with the "
            "procurement team.",
            "Training is role-based. Administrators receive two hands-on sessions, buyers one "
            "workshop on the approval flow, and vendors get short guides and a help page. The "
            "change manager runs a communication plan so that vendors hear about the new process "
            "at least four weeks before they are asked to use it.",
            ["Week 3: data profiling report and cleansing rules agreed;",
             "Weeks 13-15: integration test cycle with Northwind IT;",
             "Weeks 16-19: two UAT rounds, defects triaged daily;",
             "Weeks 18-21: training sessions and vendor communications;",
             "Week 21: final cutover rehearsal and go/no-go review."],
        ]),
        ("Implementation Plan and Governance", [
            "The 22-week plan has six milestones, each with an acceptance checklist signed by the "
            "Northwind sponsor.",
            Table([["Milestone", "Week", "Owner", "Acceptance"],
                   ["Kickoff and governance", "2", "Programme manager", "Charter and RACI signed"],
                   ["Requirements baseline", "4", "Business analyst", "Approved requirements"],
                   ["Configuration complete", "10", "Solution architect", "Demo of all workflows"],
                   ["Integrations verified", "15", "Engineering lead", "End-to-end test pass"],
                   ["User acceptance passed", "19", "QA lead", "Zero open severity-1 or 2"],
                   ["Go-live and hypercare", "22", "Programme manager", "Sponsor sign-off"]],
                  [48, 16, 45, 51], "Table 1 - Milestones"),
            "The team of nine comprises a programme manager, a solution architect, four engineers, "
            "a business analyst, a QA lead and a change manager. A RACI matrix is agreed in week 1 "
            "and the programme manager reports progress, risks and decisions weekly.",
            Table([["Activity", "Northwind", "Programme mgr", "Architect", "QA lead"],
                   ["Requirements sign-off", "A", "R", "C", "I"],
                   ["Configuration", "C", "A", "R", "I"],
                   ["Integration testing", "C", "A", "R", "R"],
                   ["User acceptance", "R", "A", "C", "R"],
                   ["Go-live decision", "A", "R", "C", "C"]],
                  [48, 26, 32, 26, 28], "Table 2 - RACI (R responsible, A accountable, C consulted, I informed)"),
            "Governance has three layers: a weekly delivery review, a monthly steering group chaired "
            "by the Northwind sponsor, and a change-control board that estimates the impact of any "
            "scope change on cost and schedule before it is accepted.",
        ]),
        ("Security, Compliance and Risk Management", [
            "NexaWorks is SOC 2 Type II certified. Data is encrypted in transit and at rest, access "
            "is role-based with single sign-on and multi-factor authentication, and audit logs are "
            "retained for three years. Our ISO 27001 certification audit is scheduled for the "
            "second quarter. Annual third-party penetration testing is performed.",
            Table([["Risk", "Owner", "Mitigation"],
                   ["Poor vendor data quality", "Business analyst", "Profiling in week 3, cleansing sprint"],
                   ["Integration endpoint availability", "Engineering lead", "Sandbox agreed by week 6"],
                   ["Stakeholder capacity", "Programme manager", "Named Northwind delegates, weekly review"],
                   ["Scope growth", "Programme manager", "Change control with impact estimate"],
                   ["Go-live data migration", "Solution architect", "Two rehearsals before cutover"]],
                  [55, 40, 65], "Table 3 - Top five risks"),
        ]),
        ("Service and Support Model", [
            "Support is delivered 24x7 through three tiers with a 99.9% monthly availability SLA "
            "backed by service credits. A named customer success manager runs monthly service "
            "reviews, and a 90-day hypercare period follows go-live.",
            Table([["Severity", "First response", "Update cadence"],
                   ["1 - Service down", "30 minutes", "Every hour"],
                   ["2 - Major impairment", "2 hours", "Every 4 hours"],
                   ["3 - Minor issue", "1 business day", "Twice weekly"]],
                  [55, 50, 55], "Table 4 - Response targets"),
            "During the 90-day hypercare period an engineer is assigned to Northwind for daily "
            "check-ins, vendor questions are answered within four working hours, and a "
            "knowledge-transfer plan hands day-to-day administration to Northwind staff with "
            "recorded sessions and runbooks. After hypercare, service reviews move to monthly.",
        ]),
        ("Pricing and Assumptions", [
            price_table(NEXA_PRICE, caption="Table 5 - Fixed price for core scope"),
            ["Prices in USD, exclusive of taxes, fixed for the core scope.",
             "Includes up to four system integrations.",
             "Optional four-week on-site hypercare is priced separately at USD 38,000."],
        ]),
        ("Experience and References", [
            "NexaWorks has delivered six similar platforms. Brightwater Logistics (2024) onboarded "
            "1,800 vendors and cut onboarding time from 12 days to 4. Summit Retail Group (2023) "
            "moved 900 vendors from spreadsheets in 16 weeks. Both will take reference calls.",
            "Lessons from those projects shape this plan: we start vendor communications early, "
            "involve buyers in acceptance testing from the first round, and keep a single "
            "accountable owner for every milestone.",
        ]),
    ],
    price_items=NEXA_PRICE,
    expected={1: (6, 8), 2: (8, 10), 3: (6, 8), 4: (6, 8), 5: (8, 10)},
    rationale={
        1: "Sound modular architecture and production scale (2,500 vendors) but less technical depth than Apex.",
        2: "Six milestones with owners and acceptance, RACI, top-five risk register, weekly governance.",
        3: "Clear fixed price, defined inclusions and an itemised optional extra at a mid-range total.",
        4: "SOC 2 Type II and annual pen tests; ISO 27001 still pending; shorter log retention.",
        5: "24x7 tiered support with SLA table and credits, named CSM, hypercare, referenceable outcomes.",
    },
    facts={"total_price_usd": 1_050_000, "timeline_weeks": 22, "currency": "USD"},
    key_facts=[
        "SOC 2 Type II certified",
        "99.9% monthly availability SLA",
        "ISO 27001 certification audit is scheduled for the second quarter",
        "cut onboarding time from 12 days to 4",
    ],
    missing=["ISO 27001 certificate (pending)", "team CVs"],
)

# ===================================================================== ORBIT DIGITAL

ORBIT_PRICE = [
    ("Platform licence (3-year term)", 450_000),
    ("Implementation", 360_000),
    ("Integration allowance", 120_000),
    ("Training", 50_000),
    ("Support (3 years)", 200_000),
]

ORBIT = Proposal(
    key="orbit",
    supplier="Orbit Digital",
    profile="Strong experience and references; vague integration plan; medium pricing.",
    submission_date="2025-03-04",
    experience_rating=4.5,
    outline=False,
    voice="orbit",
    style=Style(body_size=10.5, leading=15, heading_size=12.5, space_after=6, heading_color="#6a1b9a",
                heading_case="upper", numbering="Part I -", table_style="zebra",
                header="Orbit Digital", margin_mm=22),
    sections=[
        ("About Orbit Digital", [
            "Orbit Digital has spent twelve years helping logistics and retail companies modernise "
            "supplier management. Since 2013 we have completed fourteen supplier-management "
            "implementations, and most of our work comes from customers who return to us for the "
            "next phase. We believe that experience is the best predictor of a smooth project, and "
            "we have built this response around what our customers say about working with us.",
        ]),
        ("Track Record and References", [
            "The projects below are the most relevant to Northwind. Each customer has agreed to be "
            "contacted, and contact details are provided to evaluators on request.",
            Table([["Customer", "Year", "Scope", "Outcome"],
                   ["Aldergate Shipping", "2024", "Vendor onboarding, 2,200 vendors", "Onboarding time halved"],
                   ["Pinecrest Foods", "2023", "Supplier compliance portal", "Zero audit findings in year 1"],
                   ["Corvus Manufacturing", "2022", "Supplier master data clean-up", "30% fewer duplicates"],
                   ["Lakeshore Retail", "2021", "Onboarding and scorecards", "Live in 5 months"],
                   ["Tidewater Logistics", "2020", "Carrier onboarding", "Renewed for second phase"]],
                  [42, 14, 58, 46], "Selected references"),
        ]),
        ("Two Stories in More Detail", [
            "Aldergate Shipping came to us with 2,200 vendors registered in four regional "
            "spreadsheets. Over five months we consolidated the records, introduced one "
            "registration page for suppliers, and trained forty procurement staff. Onboarding time "
            "fell by half, and the compliance team stopped maintaining its own side register.",
            "Pinecrest Foods needed a compliance portal ahead of an external audit. We reused our "
            "standard workflow, added the customer's own document checklist, and delivered a "
            "working portal six weeks before the audit window. The first audit under the new "
            "process closed with no findings, and the customer extended the contract for a second "
            "year.",
        ]),
        ("What Our Customers Say", [
            "\"Orbit Digital understood our supplier base from the first workshop and never lost "
            "sight of the people who had to use the system.\" - Head of Procurement, Aldergate Shipping",
            "\"The team stayed with us through two scope changes and still went live on the date we "
            "had promised the board.\" - Programme Director, Lakeshore Retail",
            "\"We have worked with Orbit on three projects now. They are the first call we make.\" "
            "- CIO, Tidewater Logistics",
        ]),
        ("Understanding of the Requirement", [
            "We understand that Northwind wants a trustworthy, scalable onboarding platform that "
            "replaces manual document chasing and gives procurement and finance a shared, reliable "
            "view of vendor status. We have worked with several logistics customers on exactly this "
            "problem and recognise the pressure of a fixed go-live date.",
        ]),
        ("Proposed Approach", [
            "Our platform covers vendor registration, document collection, risk screening and "
            "approval workflows. It will be integrated with your existing systems using standard "
            "APIs, and our team will work with your IT department to determine the best approach "
            "during the project. Technical details will be finalised in the design phase.",
            "Over twelve years we have refined a delivery method we call the Orbit Way. It rests on "
            "three habits: listening first, showing working software early, and keeping customers "
            "informed in plain language. Each customer receives a delivery partner who stays with "
            "the account after go-live, so that knowledge built during the project is not lost.",
            "We adapt the platform to each customer's vocabulary and approval rules rather than "
            "asking the customer to adapt to us. Our customers tell us this is the main reason "
            "adoption is high among both buyers and suppliers, and we would bring the same care "
            "to Northwind's categories of vendor, from carriers to warehouse operators.",
        ]),
        ("Timeline and Team", [
            "The project is expected to take around 26 weeks, depending on the complexity of the "
            "integrations. We provide a project manager, a technical lead and a team of engineers. "
            "Milestones will be agreed at kickoff.",
            "We work in short cycles and show progress at the end of each one. The delivery "
            "partner joins the weekly call and acts as the customer's voice inside our team, "
            "which is how we keep small concerns from becoming large ones. Customers regularly "
            "tell us that this steady rhythm is what they remember most about working with us.",
        ]),
        ("Security and Compliance", [
            "Orbit Digital is ISO 27001 certified and encrypts data in transit and at rest. Access "
            "is role-based and security events are logged. We conduct periodic security reviews.",
        ]),
        ("Support", [
            "Support is available weekdays 08:00-18:00 with a four-hour target response for urgent "
            "issues, and each customer has a named delivery partner for escalations.",
        ]),
        ("Commercial Terms", [
            "Our pricing is based on a three-year term. Integration work is covered by an allowance "
            "until scope is known.",
            price_table(ORBIT_PRICE, header=("Component", "USD")),
            "Prices in USD. The allowance may be adjusted once scope is confirmed.",
        ]),
    ],
    price_items=ORBIT_PRICE,
    expected={1: (3, 5), 2: (3, 5), 3: (4, 6), 4: (4, 6), 5: (7, 9)},
    rationale={
        1: "Generic platform description; integration approach is deferred to the design phase.",
        2: "Approximate 26 weeks, no milestones, no named roles beyond a manager and a lead, no risk plan.",
        3: "Mid-range total, but the integration allowance is open-ended and assumptions are minimal.",
        4: "ISO 27001 certified, but controls, logging and testing are described only in general terms.",
        5: "Fourteen implementations and five named, contactable references; support is weekdays only.",
    },
    facts={"total_price_usd": 1_180_000, "timeline_weeks": 26, "currency": "USD"},
    key_facts=[
        "fourteen supplier-management implementations",
        "Technical details will be finalised in the design phase",
        "Milestones will be agreed at kickoff",
        "Integration work is covered by an allowance",
    ],
    missing=["integration design", "milestones", "risk plan", "pricing assumptions", "SLA targets"],
)

BASE_PROPOSALS = [APEX, BRIGHTPATH, NEXAWORKS, ORBIT]

# Weights of the seeded criteria (ids 1-5); used by ground-truth consistency checks.
SEED_WEIGHTS = {1: 30, 2: 20, 3: 20, 4: 20, 5: 10}
CRITERION_NAMES = {1: "Technical Capability", 2: "Implementation Plan", 3: "Commercial Value",
                   4: "Security & Compliance", 5: "Support & Experience"}

# ===================================================================== filler voices
# Deterministic prose used only to reach realistic page counts. It makes no certification,
# price or schedule claims. Each long document uses its own supplier's voice so the filler
# is not a shared template.

Pool = tuple[list[str], list[str], list[str], list[str], list[str]]

_VOICES: dict[str, dict[str, Pool]] = {
    "apex": {
        "architecture": (
            ["The platform engineering group", "Each microservice", "The event backbone",
             "The identity service", "The data access layer", "The workflow orchestrator"],
            ["enforces", "instruments", "replicates", "versions", "isolates", "validates"],
            ["tenant boundaries across every request path", "schema changes as backward-compatible releases",
             "vendor documents using content hashing", "configuration state in signed manifests",
             "latency budgets for synchronous calls", "regional failover through health-based routing",
             "service credentials with short-lived tokens", "audit events before any state change commits"],
            ["in accordance with the reference architecture", "as verified by automated contract tests",
             "under the change control process", "with evidence retained for audit",
             "using infrastructure defined as code", "subject to peer review before release"],
            ["Findings are tracked to closure in the engineering backlog", "Metrics are published to a shared operations dashboard",
             "Exceptions require written approval from the architecture board", "Results are reviewed in the monthly technical forum"],
        ),
        "delivery": (
            ["The delivery lead", "Each squad", "The quality gate", "The release manager",
             "The solution architect", "The test automation team"],
            ["sequences", "signs off", "rehearses", "baselines", "reconciles", "burns down"],
            ["integration work against the connector roadmap", "environment readiness before each phase",
             "defect backlogs by severity and age", "data-migration dry runs with reconciliation reports",
             "acceptance evidence for every milestone", "dependencies on Northwind-owned systems",
             "performance test results against agreed budgets", "cutover steps in a timed runbook"],
            ["during the weekly delivery review", "ahead of each steering committee", "before the release candidate is cut",
             "with traceability to the signed requirements", "within the agreed change window", "against the approved baseline plan"],
            ["Variances above two days are escalated the same day", "Open decisions are listed with an owner and a due date",
             "Lessons learned feed the next phase plan", "Status is summarised in a one-page dashboard"],
        ),
        "governance": (
            ["The security officer", "The compliance lead", "The architecture board", "The data protection officer",
             "The internal audit liaison", "The change advisory group"],
            ["attests", "reviews", "certifies", "samples", "approves", "retains"],
            ["access reviews for privileged accounts", "records of processing for personal data",
             "control evidence for the external auditor", "third-party risk assessments for sub-processors",
             "emergency changes after the event", "key-rotation logs on a fixed schedule",
             "segregation-of-duties conflicts in role design", "retention schedules for audit records"],
            ["every quarter", "under documented procedures", "against the control framework", "with independent challenge",
             "through the governance calendar", "before the next audit cycle"],
            ["Gaps are logged as corrective actions with due dates", "Reports are shared with the Northwind sponsor",
             "Evidence is stored in a tamper-evident repository", "Sampling results are summarised for the board"],
        ),
        "support": (
            ["The technical account manager", "The on-call engineer", "The service desk", "The problem manager",
             "The platform reliability team", "The customer success lead"],
            ["triages", "escalates", "documents", "trends", "closes", "briefs"],
            ["incidents by severity and customer impact", "known errors with an approved workaround",
             "capacity forecasts for the coming quarter", "post-incident reviews within five working days",
             "service credits against the agreed targets", "release notes for planned maintenance",
             "recurring alerts into permanent fixes", "usage patterns across vendor cohorts"],
            ["in the shared service portal", "during the monthly service review", "following the incident procedure",
             "with customer-visible timelines", "using the standard severity matrix", "as part of continual improvement"],
            ["Actions are reviewed at the next service meeting", "Trends are reported alongside availability figures",
             "Customer feedback is recorded and answered", "Runbooks are updated after every major incident"],
        ),
    },
    "nexaworks": {
        "architecture": (
            ["Our solution team", "The configuration layer", "The integration gateway", "The reporting service",
             "The vendor portal", "The rules engine"],
            ["keeps", "maps", "exposes", "surfaces", "stores", "applies"],
            ["each onboarding step as a configurable template", "vendor fields to the right downstream systems",
             "simple screens that suppliers can complete unaided", "approval rules without writing code",
             "document types that vary by vendor category", "status changes in plain language for staff",
             "a single record that finance and operations share", "reminders when documents are about to expire"],
            ["so teams can adjust the process themselves", "which keeps training time short",
             "and avoids custom development", "in a way that is easy to audit", "while keeping data in one place",
             "to reduce repeated data entry"],
            ["Changes are tested in a sandbox first", "Users see only what their role needs",
             "Standard reports are included from day one", "New fields can be added without downtime"],
        ),
        "delivery": (
            ["Our programme manager", "The delivery team", "Each sprint", "The business analyst",
             "The change manager", "The QA lead"],
            ["walks through", "agrees", "demonstrates", "checks", "plans", "collects"],
            ["progress with the Northwind sponsor every week", "the next two weeks of work in a short planning session",
             "working software at the end of each sprint", "training needs for each user group",
             "open questions that block a milestone", "test results before anything is released",
             "feedback from vendors in the pilot group", "readiness for each go-live step"],
            ["so there are no surprises", "using plain, shared checklists", "with the people who will use it",
             "before moving to the next phase", "in a one-hour session", "and records the decisions made"],
            ["Anything slipping is raised early", "Owners and dates are written down", "Northwind approves each milestone in writing",
             "The plan is updated after every review"],
        ),
        "governance": (
            ["The steering group", "The programme office", "A named sponsor", "The risk owner",
             "The service manager", "The project board"],
            ["meets", "tracks", "decides on", "reviews", "publishes", "keeps"],
            ["scope changes with a short impact note", "the register of risks and assumptions",
             "budget against the agreed milestones", "benefits against the original business case",
             "a log of decisions and who made them", "supplier-facing communications ahead of launch",
             "escalations that need executive attention", "dependencies on other Northwind projects"],
            ["once a month", "every Friday", "ahead of each milestone", "with Northwind finance present",
             "using a one-page template", "so everyone sees the same facts"],
            ["Minutes are shared within a day", "Actions have one owner each", "Open risks are reviewed first",
             "The sponsor can ask for a deeper review at any time"],
        ),
        "support": (
            ["Our service desk", "The customer success manager", "A support engineer", "The on-call team",
             "The hypercare team", "The service manager"],
            ["answers", "follows up", "explains", "tracks", "checks in on", "reports on"],
            ["every request with a reference number", "open tickets until the user confirms they are resolved",
             "what changed in each release in everyday language", "response times against the agreed table",
             "how vendors are using the portal", "questions from new administrators during the first weeks",
             "problems that keep coming back", "availability and incident figures each month"],
            ["around the clock", "within the stated response time", "in the first ninety days after go-live",
             "by phone, email or chat", "with a named contact", "and agrees next steps"],
            ["Customers can rate each ticket", "Reviews include a short improvement plan", "Credits are applied automatically",
             "Training refreshers are offered each quarter"],
        ),
    },
}

_TOPICS = ("architecture", "delivery", "governance", "support")
_OPENERS = ["In practice", "For each release", "Where needed", "As agreed with Northwind",
            "Throughout the engagement", "At every stage", "In day-to-day work", "As a rule"]


def filler(voice: str, topic: str, paragraphs: int, rng: random.Random) -> list[str]:
    subjects, verbs, objects, qualifiers, follows = _VOICES[voice][topic]
    out = []
    for _ in range(paragraphs):
        sentences = []
        for _ in range(rng.randint(4, 6)):
            s, v, o, q = rng.choice(subjects), rng.choice(verbs), rng.choice(objects), rng.choice(qualifiers)
            if rng.random() < 0.5:
                sentence = f"{s} {v} {o}, {q}."
            else:
                sentence = f"{q[0].upper()}{q[1:]}, {s[0].lower()}{s[1:]} {v} {o}."
            if rng.random() < 0.4:
                if rng.random() < 0.2:
                    sentence += f" {rng.choice(follows)}."
                else:  # a second, freshly composed sentence keeps repetition low
                    s2, v2, o2 = rng.choice(subjects), rng.choice(verbs), rng.choice(objects)
                    sentence += f" {rng.choice(_OPENERS)}, {s2[0].lower()}{s2[1:]} {v2} {o2}."
            sentences.append(sentence)
        out.append(" ".join(sentences))
    return out


_RESUME = {
    "apex": (
        ["Principal Engineer", "Security Architect", "Staff Engineer", "QA Automation Lead",
         "Delivery Director"],
        ["{years} years in secure enterprise platforms, with {n} regulated-industry deployments.",
         "Led {n} integration programmes over {years} years, mostly SAP and transport systems.",
         "{years} years of platform engineering; owner of {n} production services at scale."],
        ["Accountable for design authority and release quality.",
         "Runs threat-modelling workshops and signs off penetration test scope.",
         "Mentors engineers and chairs the architecture review.",
         "Responsible for performance budgets and capacity planning."],
        ["Holds an MSc in computer science.", "Speaks at industry meetups on API design.",
         "Previously worked in banking technology.", "Previously worked in logistics software."],
    ),
    "nexaworks": (
        ["Programme Manager", "Business Analyst", "Change Manager", "Solution Consultant",
         "Customer Success Manager"],
        ["{years} years guiding customers through onboarding and workflow projects, including "
         "{n} supplier-facing rollouts.",
         "Has run {n} go-lives across {years} years of service delivery.",
         "{years} years in customer-facing delivery; {n} programmes closed on the agreed date."],
        ["Known for clear plans and steady communication.",
         "Keeps sponsors informed with one-page weekly updates.",
         "Specialises in training and vendor communications.",
         "Writes the acceptance criteria that milestones are signed against."],
        ["Certified in a recognised project method.", "Facilitates workshops with buyers and finance.",
         "Previously worked in retail operations.", "Previously worked in third-party logistics."],
    ),
}


def resume_blocks(voice: str, count: int, rng: random.Random) -> list[str]:
    """Varied profile paragraphs; the 'fictional' note is stated once by the caller."""
    roles, openers, middles, closers = _RESUME[voice]
    out = []
    for i in range(count):
        years, n = rng.randint(6, 18), rng.randint(2, 7)
        out.append(
            f"Team member {i + 1:02d} - {rng.choice(roles)}. "
            f"{rng.choice(openers).format(years=years, n=n)} {rng.choice(middles)} "
            f"{rng.choice(closers)}"
        )
    return out
