"""
polyphony.generators
====================
Generate realistic synthetic QDA datasets for training and practice.

Two modes:
  - **Template-based** (no LLM needed): Pre-built domains with randomized
    interview excerpt templates.
  - **LLM-based** (advanced): Uses a local Ollama model to generate custom
    topic data on the fly.
"""

from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Pre-built domain data
# ---------------------------------------------------------------------------

DOMAINS: Dict[str, Dict[str, Any]] = {
    # -----------------------------------------------------------------------
    # HOUSING PRECARITY
    # -----------------------------------------------------------------------
    "housing": {
        "name": "Housing Precarity",
        "description": (
            "Interview excerpts about housing instability, rent burden, "
            "eviction, substandard living conditions, and coping strategies."
        ),
        "templates": [
            # FINANCIAL_STRESS
            "I can't make ends meet anymore. The rent went up again and {name} and I don't know how we'll pay it next month.",
            "They raised my rent by two hundred dollars, just like that. I'm already working two jobs. Where is that money supposed to come from?",
            "I had to choose between paying rent and buying groceries last month. {name} told me to skip the electric bill instead but that's not a real solution either.",
            "Every month it's the same panic. I sit at the kitchen table and stare at the numbers and they never add up. {name} says I should ask for help but I don't even know who to ask.",
            "My whole paycheck goes to rent. I'm not exaggerating — ninety-three percent of what I make goes straight to the landlord. {name} picks up odd jobs to cover food.",
            "We're one emergency away from losing everything. If the car breaks down or one of the kids gets sick, that's it. We can't cover rent and a surprise bill.",
            "I never thought I'd be in this situation. I have a degree, I have a steady job, and I still can't afford a decent apartment in this city.",
            # HOUSING_INSTABILITY
            "We've moved four times in three years. {name} just started at a new school and I had to pull her out again. It's not fair to the kids.",
            "I got the notice taped to my door on a Tuesday morning. Thirty days to vacate. I've lived there eleven years and they want to convert the building to condos.",
            "My lease is month-to-month now and the landlord won't renew for a full year. I think he's trying to push me out so he can charge more.",
            "We're doubled up at {name}'s mother's place. There's six of us in a two-bedroom. It's temporary but temporary keeps stretching.",
            "I've been on the housing waitlist for two years and seven months. They told me it could be another year. Where am I supposed to go in the meantime?",
            "After the eviction we stayed in the shelter for three weeks. Then we couch-surfed. Then we were in the car for a few nights. People don't understand how fast it happens.",
            "{name} and I slept in the car for a week last winter before we found a room. I kept the engine running for the heat and prayed we had enough gas.",
            # LANDLORD_CONFLICT
            "My landlord hasn't fixed the heating in three months. We're sleeping in coats. It's not right.",
            "I've called about the mold six times. Six. He just says he'll send someone and nobody comes. My daughter has asthma and it's getting worse.",
            "The ceiling in the bathroom collapsed and he told me to put a tarp over it. A tarp. Like that's a permanent solution.",
            "I'm afraid to complain because last time someone on our floor called the city, the landlord started eviction proceedings against them. {name} saw it happen.",
            "He lets himself into the apartment without notice. I've told him to stop, I've sent letters, but he keeps doing it. I don't feel safe.",
            "When I asked him to fix the broken lock on the front door he said I should be grateful the rent is as low as it is. That was his answer.",
            "{name} confronted the landlord about the cockroach situation and he basically laughed at her. Said that's what you get at this price point.",
            # COPING_STRATEGY
            "The food bank has been a lifeline. Without it I don't know what we'd do.",
            "I started a side hustle selling clothes online just to cover the gap. It's exhausting but it keeps us housed.",
            "{name} watches the kids so I can work the night shift. We trade off like that. It's the only way we make it work.",
            "My church helps with utility bills sometimes. I hate asking but pride doesn't keep the lights on.",
            "I've learned to cook everything from scratch. Rice and beans, rice and beans. The kids are tired of it but it stretches the budget.",
            "We cancelled everything — streaming, the gym, even the kids' after-school program. That freed up about eighty dollars a month. It's something.",
            # SOCIAL_SUPPORT
            "{name} has been incredible through all of this. She checks on us, brings groceries, watches the baby. I'd be lost without her.",
            "My neighbor and I look out for each other. When I can't make the electric bill, she runs an extension cord from her unit. I know that sounds crazy but it works.",
            "The caseworker at the shelter connected me to a rental assistance program I didn't even know existed. That woman saved my family.",
            "I don't have family in this city so I've had to build my own network. {name} and a few others from the support group — they're my people now.",
            "Nobody at work knows what I'm going through. I keep that wall up. But {name} — she figured it out and she's been slipping me grocery gift cards. I almost cried the first time.",
            # HEALTH_IMPACT
            "The stress is making me sick. I'm not sleeping. My blood pressure is through the roof. The doctor told me I need to reduce stress — like I can just do that.",
            "My kids have been acting out since the last move. {name} started wetting the bed again and the older one won't talk to anyone. They feel the instability.",
            "I had a panic attack at work last month thinking about the rent. I literally could not breathe. I thought I was having a heart attack.",
            "Living in a place with mold and no heat — of course we're sick all the time. {name} has had bronchitis twice this winter.",
            "The anxiety never goes away. Even when I've paid rent for the month, I'm already dreading next month. It's like this weight I carry everywhere.",
            "I stopped taking my medication because I couldn't afford the copay and the rent. The doctor said that's dangerous but what choice do I have?",
            "My daughter's school called because she keeps falling asleep in class. She shares a bed with two siblings in a room with no heat. Of course she's tired.",
            "{name} told me she cries every night. She's twelve. No twelve-year-old should worry about whether they'll have a home next month.",
            "I gained forty pounds this year. I eat garbage because that's what's cheap and available. I don't have a working stove so it's all microwave and fast food.",
            "Some nights I just lie there and stare at the ceiling, running the numbers over and over. The mental load of being this close to the edge — it's crushing.",
        ],
        "names": [
            "Maria", "James", "Aisha", "Carlos", "Denise",
            "Marcus", "Linh", "Terrence", "Fatima", "Diego",
            "Keisha", "Andrei", "Rosa", "DeShawn", "Priya",
            "Tomás", "Aaliyah", "Wei", "Natasha", "Jerome",
            "Luz", "Hassan", "Brenda",
        ],
        "codes": [
            {
                "name": "FINANCIAL_STRESS",
                "description": "References to money-related pressures tied to housing costs.",
                "inclusion_criteria": "Mentions of rent burden, inability to pay bills, trade-offs between housing costs and other necessities, income inadequacy relative to housing expenses.",
                "exclusion_criteria": "General financial discussion unrelated to housing; references to savings or investments.",
            },
            {
                "name": "HOUSING_INSTABILITY",
                "description": "Experiences of frequent moves, displacement, or precarious housing situations.",
                "inclusion_criteria": "Mentions of eviction, forced moves, doubling up, shelter stays, homelessness, housing waitlists, lease insecurity.",
                "exclusion_criteria": "Voluntary relocations for positive reasons (e.g., job opportunity, upgrading).",
            },
            {
                "name": "LANDLORD_CONFLICT",
                "description": "Disputes, neglect, or power imbalances between tenants and landlords.",
                "inclusion_criteria": "Unresponsive landlords, refusal to make repairs, harassment, illegal entry, retaliation against complaints.",
                "exclusion_criteria": "Positive landlord interactions; neutral business transactions.",
            },
            {
                "name": "COPING_STRATEGY",
                "description": "Actions taken to manage or survive housing precarity.",
                "inclusion_criteria": "Budgeting tactics, side jobs, cutting expenses, seeking assistance, informal resource sharing.",
                "exclusion_criteria": "Long-term solutions that fully resolve the housing issue (e.g., purchasing a home).",
            },
            {
                "name": "SOCIAL_SUPPORT",
                "description": "Help received from personal networks, community organizations, or service providers.",
                "inclusion_criteria": "Assistance from family, friends, neighbors, churches, caseworkers, or community groups specifically related to housing needs.",
                "exclusion_criteria": "Professional services unrelated to housing (e.g., medical care for non-housing-related conditions).",
            },
            {
                "name": "HEALTH_IMPACT",
                "description": "Physical or mental health consequences of housing instability.",
                "inclusion_criteria": "Stress-related illness, mental health effects (anxiety, depression, insomnia), children's behavioral or health issues linked to housing, inability to afford medication due to housing costs.",
                "exclusion_criteria": "Health conditions clearly unrelated to housing situation.",
            },
        ],
    },

    # -----------------------------------------------------------------------
    # HEALTHCARE ACCESS
    # -----------------------------------------------------------------------
    "healthcare": {
        "name": "Healthcare Access",
        "description": (
            "Interview excerpts about barriers to healthcare including insurance "
            "gaps, cost barriers, delayed care, rural access, and medication costs."
        ),
        "templates": [
            # COST_BARRIER
            "I got the bill and it was over four thousand dollars. For a two-hour ER visit. I just stared at it. There's no way I can pay that.",
            "I need a root canal but the dentist wants eight hundred dollars up front. So I just take ibuprofen and hope it doesn't get worse.",
            "{name} needs physical therapy after the accident but our insurance only covers six sessions. The doctor recommended twenty-four.",
            "I skipped my mammogram this year because even with insurance the copay is seventy-five dollars and I can't spare that right now.",
            "The generic version of my medication went from twelve dollars to sixty-eight dollars overnight. Nobody could explain why.",
            "I split my pills in half to make them last longer. My doctor would be furious if she knew, but I can't afford to refill on schedule.",
            "We have insurance but the deductible is so high it might as well be nothing. I have to spend five thousand before it kicks in. Who has five thousand?",
            # DELAYED_CARE
            "I waited eight months to see the specialist. By the time I got in, what started as a minor issue was a whole different situation.",
            "I knew something was wrong for a year before I went to the doctor. I just couldn't face the bill. {name} finally dragged me in.",
            "My tooth was infected for weeks before I went to the ER. They gave me antibiotics and told me to see a dentist. I can't afford a dentist.",
            "I put off the knee surgery for three years because I couldn't take time off work. By the time I had it done, the damage was much worse.",
            "{name} ignored chest pains for two weeks because she was worried about the cost. She had a mild heart attack. Two weeks of ignoring it.",
            "The lump was there for months before I got it checked. I was scared, sure, but I was more scared of what the bill would look like.",
            "I know I should go in for regular checkups but every visit means a hundred-dollar copay, labs on top of that. So I just don't go.",
            # INSURANCE_GAP
            "I make too much for Medicaid but not enough to afford the marketplace plans. I fall right in that gap. It's like the system isn't designed for people like me.",
            "When I lost my job I lost my insurance the same day. COBRA was going to be fourteen hundred a month. That's more than my rent.",
            "I aged out of my parents' insurance at twenty-six and I've been uninsured ever since. That was three years ago.",
            "My employer offers insurance but the family plan is six hundred dollars a month. I can only afford the individual plan so my kids are on the state program.",
            "{name} is between jobs right now and the coverage gap is terrifying. One accident, one diagnosis, and we're bankrupt.",
            "They changed the formulary and my medication isn't covered anymore. Just like that. No warning, no transition period.",
            "I was on Medicaid but then I got a small raise and I no longer qualified. A fifty-cent raise cost me my health insurance.",
            # PROVIDER_SHORTAGE
            "The closest doctor is forty-five minutes away. If the roads are bad in winter, forget it. {name} missed two appointments last year because of snow.",
            "Our town lost its only pediatrician last year. Now we drive an hour each way for the kids' checkups.",
            "I called twelve therapists on my insurance list. Eight weren't taking new patients. Three didn't return my call. One had a four-month wait.",
            "The clinic closed at the end of last year and nothing replaced it. The whole county has one urgent care now and it's packed every day.",
            "There's no OB-GYN in our county. {name} drove ninety minutes each way for her prenatal appointments. Every two weeks toward the end.",
            "My psychiatrist retired and there's nobody in network within a reasonable distance. I've been without medication management for five months.",
            # HEALTH_OUTCOME
            "{name}'s diabetes went unmanaged for over a year because she couldn't get in to see an endocrinologist. She ended up in the hospital.",
            "I ignored the warning signs because I didn't want another bill. Now it's stage three and the treatment is going to be ten times what the screening would have cost.",
            "My blood pressure was out of control for months because I couldn't afford the medication. I ended up having a stroke. I'm forty-two.",
            "The infection spread because I waited too long to come in. The doctor said if I'd come in a week earlier it would have been a simple course of antibiotics.",
            "I lost two teeth because I couldn't afford the fillings five years ago. Now I need implants which are thousands of dollars. Preventive care would have been so much cheaper.",
            # SYSTEM_NAVIGATION
            "Nobody tells you how any of this works. I didn't know I could appeal the denial. {name} at the clinic helped me figure it out and they ended up covering it.",
            "I spent three hours on the phone trying to get a prior authorization. Three hours. For medication my doctor already prescribed.",
            "The billing department sent me to collections without ever sending me a bill. I only found out when I checked my credit report.",
            "I applied for the hospital's financial assistance program and they cut the bill by sixty percent. But I only knew about it because {name} told me. It's not advertised anywhere.",
            "{name} helped me sign up for a patient assistance program through the drug manufacturer. My medication is free now. But I almost gave up before she stepped in.",
            "I got three different bills from three different entities for one ER visit. The hospital, the doctor, and the radiologist. All separate. I didn't understand any of them.",
            "Every time I call the insurance company I get a different answer. I asked the same question four times and got four different responses. How is anyone supposed to navigate this?",
            "The social worker at the clinic walked me through every form. Without her I would have just given up. The system is not designed for regular people to figure out on their own.",
        ],
        "names": [
            "Patricia", "Robert", "Yolanda", "Kevin", "Tanya",
            "Miguel", "Sandra", "Darius", "Elena", "Winston",
            "Lakisha", "Raj", "Carmen", "Tyrone", "Mai",
            "Antonio", "Crystal", "Oleg", "Diane", "Jamal",
            "Ingrid", "Hector", "Tamika",
        ],
        "codes": [
            {
                "name": "COST_BARRIER",
                "description": "Financial obstacles preventing access to healthcare services.",
                "inclusion_criteria": "Mentions of unaffordable bills, high copays or deductibles, medication costs, inability to pay for recommended treatments, choosing between healthcare and other expenses.",
                "exclusion_criteria": "Healthcare costs that are manageable or do not affect access to care.",
            },
            {
                "name": "DELAYED_CARE",
                "description": "Postponement of needed medical treatment or preventive services.",
                "inclusion_criteria": "Skipping or postponing appointments, ignoring symptoms, waiting to seek care due to cost or access barriers, conditions worsening due to delays.",
                "exclusion_criteria": "Scheduled follow-ups or waiting periods that are medically appropriate.",
            },
            {
                "name": "INSURANCE_GAP",
                "description": "Periods or situations without adequate health insurance coverage.",
                "inclusion_criteria": "Loss of coverage, inability to afford premiums, falling between eligibility thresholds, coverage exclusions, formulary changes.",
                "exclusion_criteria": "Voluntary decisions to forgo insurance despite affordability; supplemental coverage discussions.",
            },
            {
                "name": "PROVIDER_SHORTAGE",
                "description": "Insufficient availability of healthcare providers or facilities.",
                "inclusion_criteria": "Long distances to providers, clinic closures, providers not accepting new patients, extended wait times for specialists, lack of specialists in region.",
                "exclusion_criteria": "Preference-based provider switching; wait times within normal ranges.",
            },
            {
                "name": "HEALTH_OUTCOME",
                "description": "Health consequences resulting from access barriers.",
                "inclusion_criteria": "Worsened conditions due to delayed or foregone care, hospitalizations that could have been prevented, complications from unmanaged chronic conditions.",
                "exclusion_criteria": "Health outcomes unrelated to access barriers; normal disease progression despite adequate care.",
            },
            {
                "name": "SYSTEM_NAVIGATION",
                "description": "Challenges understanding and working within the healthcare system.",
                "inclusion_criteria": "Difficulty with insurance claims, billing confusion, prior authorization burdens, lack of awareness of assistance programs, reliance on advocates to navigate bureaucracy.",
                "exclusion_criteria": "Routine administrative interactions without difficulty.",
            },
        ],
    },

    # -----------------------------------------------------------------------
    # EDUCATION EQUITY
    # -----------------------------------------------------------------------
    "education": {
        "name": "Education Equity",
        "description": (
            "Interview excerpts about educational disparities, resource access, "
            "teacher quality, family involvement, and systemic barriers."
        ),
        "templates": [
            # RESOURCE_INEQUALITY
            "The school across town has a computer lab, a library, and a science wing. We have textbooks from 2008 and a leaky roof. Same district.",
            "{name}'s school doesn't have enough desks. Some kids sit on the floor during third period. In 2024. It's unbelievable.",
            "They cut the art program, the music program, and the after-school tutoring all in one year. But they found money for a new football scoreboard.",
            "I buy supplies for my classroom out of my own pocket. Markers, paper, tissues — I spent over seven hundred dollars last year. {name} in the next room does the same.",
            "Our school library hasn't gotten new books in six years. The kids go online for everything now, but half of them don't have reliable internet at home either.",
            "The AP classes are only offered at two schools in the district and neither is on the bus route from our neighborhood. So our kids just don't take AP.",
            "When I visited {name}'s school for parent night, half the lights in the hallway were out and the bathroom didn't have soap. That tells you everything about priorities.",
            "The rich schools have counselors, psychologists, speech therapists. We share one counselor across three buildings. She's there maybe one day a week.",
            # FAMILY_ENGAGEMENT
            "I want to be involved but every meeting is at three in the afternoon. I work until five. Nobody considers that maybe parents have jobs.",
            "The school sends everything home in English only. {name}'s parents don't read English. So they miss half of what's going on.",
            "{name}'s teacher called me at work to tell me he's falling behind. I appreciate the call but she acted like I don't care. I work nights. I'm doing my best.",
            "I go to every parent-teacher conference, every fundraiser. But I notice who else is there and who isn't, and it's always the same families. The school doesn't try to reach the others.",
            "They expect parents to volunteer ten hours a semester. When? I'm a single mom with two jobs. I can't take a morning off to shelve library books.",
            "Nobody from the school came to the community center meeting. We organized it specifically to talk about the new curriculum and not one administrator showed up.",
            "{name} told me the teacher said her parents don't care about education because we didn't sign a reading log. We read together every night. We just forgot the log.",
            "I don't feel welcome at that school. Every time I walk in they look at me like I don't belong. {name} notices it too.",
            # TEACHER_QUALITY
            "My son had three different substitute teachers in one semester. Three. There's no continuity. How is he supposed to learn?",
            "{name} had a teacher who clearly didn't want to be there. She handed out worksheets every day and sat at her desk. That was the whole year.",
            "The good teachers leave after two or three years. They go to the suburban schools where they get paid more and the classes are smaller. I don't blame them but it hurts us.",
            "We finally got an amazing science teacher and she left after one year because the working conditions were unbearable. No lab equipment, thirty-eight kids in a class.",
            "{name}'s math teacher is wonderful — patient, creative, stays after school for tutoring. But she's one person. She can't fix the whole system by herself.",
            "I've seen teachers who genuinely care get burned out in two years at our school. The class sizes, the lack of support, the discipline issues — it's too much for one person.",
            # ACHIEVEMENT_GAP
            "My daughter is smart — really smart — but she's two grade levels behind in reading because she never got the early support she needed.",
            "{name} tested into the gifted program but there's no gifted program at our school. The district told us he could transfer but that's a forty-minute bus ride each way.",
            "The kids at the magnet school are doing robotics and coding. Our kids are still doing multiplication tables in fifth grade. The gap starts early and it just grows.",
            "By high school the damage is done. The kids from underfunded schools are competing against kids who had tutors, small classes, and every advantage. It's not a fair race.",
            "I looked at the test scores and there's a thirty-point gap between schools on the east side and schools on the west side. Same city, same district, different planets.",
            "{name} wants to go to college but she's never had a guidance counselor sit down with her and talk about applications. The kids at the other school start that process in ninth grade.",
            "They put my son in the remedial track without testing him. Just looked at his zip code and made assumptions. {name} had to fight to get him moved.",
            # SCHOOL_CLIMATE
            "There's a metal detector at the front door and a police officer in the hallway. It feels like a prison, not a school. The kids internalize that.",
            "{name} got suspended for three days for talking back. At the school across town, that's a conversation with the counselor. The discipline is not applied equally.",
            "My daughter told me she doesn't feel safe at school. Not because of the other kids — because of how the adults treat them. Like they're problems to manage, not children to teach.",
            "The school-to-prison pipeline is real. I've watched it happen. {name}'s friend got expelled in seventh grade and never came back to any school.",
            "They hired a restorative justice coordinator last year and it made a huge difference. But the position was grant-funded and the grant ended. So we're back to zero-tolerance.",
            "The bathrooms are locked during class because of vandalism. So if your kid needs to go, tough luck. {name} holds it all day. That's not a healthy environment.",
            # SYSTEMIC_BARRIER
            "The zoning is designed to keep certain kids in certain schools. Everyone knows it but nobody says it out loud.",
            "They redrew the district boundaries last year and magically all the low-income housing ended up in one school zone. That's not an accident.",
            "I tried to get {name} into the lottery for the charter school. Three years in a row, no luck. Meanwhile the kids whose parents know the right people seem to get in just fine.",
            "{name} needs special education services but the evaluation process took fourteen months. Fourteen months of her falling further behind while we waited for paperwork.",
            "The standardized tests determine everything — funding, teacher evaluations, school ratings. So they teach to the test and call it education.",
            "Our school board hasn't had a member from this neighborhood in over a decade. The decisions are made by people who don't send their kids to our schools.",
            "When COVID hit, the wealthy schools went seamlessly online. Our kids got packets of worksheets. {name} didn't have a laptop until October. School started in August.",
            "I keep hearing about equity initiatives but nothing changes on the ground. My kid's classroom still has thirty-six students and one teacher. That's the equity.",
        ],
        "names": [
            "Angela", "David", "Latoya", "Brandon", "Irene",
            "Rafael", "Shanice", "Cody", "Monique", "Liam",
            "Valentina", "Deshawn", "Noor", "Trevor", "Gabriela",
            "Kwame", "Samira", "Derek", "Alma", "Jaylen",
            "Ruth", "Omar", "Bianca",
        ],
        "codes": [
            {
                "name": "RESOURCE_INEQUALITY",
                "description": "Disparities in funding, facilities, materials, and services between schools.",
                "inclusion_criteria": "Mentions of outdated textbooks, inadequate facilities, program cuts, lack of technology, teacher-funded supplies, unequal distribution of counselors or specialists.",
                "exclusion_criteria": "Resource limitations affecting all schools equally; budget discussions without equity implications.",
            },
            {
                "name": "FAMILY_ENGAGEMENT",
                "description": "Barriers and dynamics around parental and family involvement in education.",
                "inclusion_criteria": "Scheduling conflicts, language barriers, unwelcoming school environments, assumptions about parental involvement, lack of outreach to underserved families.",
                "exclusion_criteria": "Positive family-school partnerships without barriers; voluntary non-participation.",
            },
            {
                "name": "TEACHER_QUALITY",
                "description": "Issues related to teacher recruitment, retention, and effectiveness.",
                "inclusion_criteria": "High teacher turnover, reliance on substitutes, burnout, departure to better-resourced schools, individual teacher excellence amid systemic failure.",
                "exclusion_criteria": "Normal career transitions; teacher evaluations unrelated to equity.",
            },
            {
                "name": "ACHIEVEMENT_GAP",
                "description": "Measurable differences in academic outcomes across demographic groups.",
                "inclusion_criteria": "Test score disparities, grade-level gaps, unequal access to advanced coursework, tracking based on demographics, unequal college preparation.",
                "exclusion_criteria": "Individual student struggles unrelated to systemic factors.",
            },
            {
                "name": "SCHOOL_CLIMATE",
                "description": "The social, emotional, and physical environment of the school.",
                "inclusion_criteria": "Punitive discipline, surveillance, student feelings of unsafety, unequal discipline policies, school-to-prison pipeline dynamics, bathroom or facility access issues.",
                "exclusion_criteria": "Positive school culture descriptions; isolated disciplinary incidents.",
            },
            {
                "name": "SYSTEMIC_BARRIER",
                "description": "Structural and policy-level factors perpetuating educational inequity.",
                "inclusion_criteria": "Zoning and boundary manipulation, special education delays, testing-driven curricula, unrepresentative governance, digital divide, policy-practice gaps.",
                "exclusion_criteria": "Policy discussions without equity implications; individual-level barriers.",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_domains() -> Dict[str, str]:
    """Return ``{domain_key: description}`` for every pre-built domain."""
    return {key: d["description"] for key, d in DOMAINS.items()}


def generate_template_data(
    domain: str,
    n_segments: int = 20,
    seed: int | None = None,
) -> dict:
    """Generate synthetic segments using pre-built templates.

    Parameters
    ----------
    domain : str
        Key into :data:`DOMAINS` (e.g. ``"housing"``).
    n_segments : int
        Number of segments to generate.
    seed : int | None
        Random seed for reproducibility.

    Returns
    -------
    dict
        ``{"segments": [{"text": str, "metadata": dict}, ...], "codes": [...]}``
    """
    if domain not in DOMAINS:
        raise ValueError(
            f"Unknown domain {domain!r}. "
            f"Available: {', '.join(DOMAINS)}"
        )

    cfg = DOMAINS[domain]
    rng = random.Random(seed)

    templates = cfg["templates"]
    names = cfg["names"]

    segments: List[Dict[str, Any]] = []
    for _ in range(n_segments):
        tpl = rng.choice(templates)
        name = rng.choice(names)
        text = tpl.replace("{name}", name)
        segments.append(
            {
                "text": text,
                "metadata": {
                    "participant": name,
                    "domain": cfg["name"],
                    "generated": True,
                },
            }
        )

    return {"segments": segments, "codes": cfg["codes"]}


# ---------------------------------------------------------------------------
# LLM-based generation (requires Ollama)
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
You are a qualitative research data generator. Your task is to create \
realistic interview transcript excerpts for qualitative data analysis practice.

Generate the requested number of interview excerpts on the given topic. \
Each excerpt should:
- Be written in FIRST PERSON as if spoken by an interview participant
- Sound natural and conversational, not formal or academic
- Be 2-5 sentences long
- Include emotional content, hedging, specific details, and varied tones \
(frustrated, resigned, hopeful, matter-of-fact)
- Feel like they could appear in a real semi-structured interview transcript

Also suggest 4-6 qualitative codes that a researcher might use to analyze \
these excerpts.

Respond in JSON with this exact structure:
{
  "segments": [
    {"text": "the excerpt text", "participant": "a realistic first name"}
  ],
  "codes": [
    {
      "name": "CODE_NAME",
      "description": "what this code captures",
      "inclusion_criteria": "when to apply this code",
      "exclusion_criteria": "when NOT to apply this code"
    }
  ]
}
"""


def generate_llm_data(
    topic: str,
    n_segments: int = 20,
    model: str = "llama3.2",
    host: str = "http://localhost:11434",
    seed: int | None = None,
) -> dict:
    """Generate synthetic segments using a local Ollama LLM.

    Parameters
    ----------
    topic : str
        Free-text description of the interview topic.
    n_segments : int
        Number of segments to request.
    model : str
        Ollama model name (default ``"llama3.2"``).
    host : str
        Ollama server URL.
    seed : int | None
        Random seed for reproducibility.

    Returns
    -------
    dict
        ``{"segments": [{"text": str, "metadata": dict}, ...], "codes": [...]}``

    Raises
    ------
    RuntimeError
        If Ollama is not installed or unreachable.
    """
    try:
        import ollama
    except ImportError:
        raise RuntimeError(
            "The 'ollama' package is required for LLM-based generation. "
            "Install it with: pip install ollama"
        )

    client = ollama.Client(host=host)

    user_prompt = (
        f"Generate {n_segments} realistic interview transcript excerpts "
        f"about the following topic: {topic}\n\n"
        f"Remember: first-person voice, conversational tone, 2-5 sentences each, "
        f"varied emotional tones. Also suggest 4-6 qualitative codes."
    )

    options: Dict[str, Any] = {"temperature": 0.7}
    if seed is not None:
        options["seed"] = seed

    try:
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            options=options,
            format="json",
        )
    except Exception as e:
        raise RuntimeError(
            f"Ollama call failed for model '{model}'. "
            f"Is Ollama running? Try: ollama serve\n"
            f"Original error: {e}"
        ) from e

    raw = response.message.content or ""
    parsed = _parse_llm_response(raw, topic)
    return parsed


def _parse_llm_response(raw: str, topic: str) -> dict:
    """Parse the JSON response from the LLM, with fallback handling."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        import re

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    # Normalise segments
    raw_segments = data.get("segments", [])
    segments = []
    for seg in raw_segments:
        if isinstance(seg, dict) and "text" in seg:
            segments.append(
                {
                    "text": seg["text"],
                    "metadata": {
                        "participant": seg.get("participant", "Unknown"),
                        "domain": topic,
                        "generated": True,
                    },
                }
            )
        elif isinstance(seg, str):
            segments.append(
                {
                    "text": seg,
                    "metadata": {
                        "participant": "Unknown",
                        "domain": topic,
                        "generated": True,
                    },
                }
            )

    codes = data.get("codes", [])

    return {"segments": segments, "codes": codes}
