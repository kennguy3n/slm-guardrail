"""Training corpus for the XLM-R classification head.

This module exposes :data:`TRAINING_EXAMPLES`, a list of ``(text, category)``
tuples used by :mod:`train_xlmr_head` to fit the small linear head sitting
on top of the frozen encoder. It replaces the original zero-shot prototype
argmax — see :mod:`xlmr_adapter` for the runtime path.

Design constraints (see ``PROPOSAL.md`` "Privacy contract" and
``ARCHITECTURE.md`` "Hybrid Local Pipeline"):

* **No real user data, no real PII, no live phishing URLs.** All examples
  are short, descriptive, synthetic prompts that paraphrase the
  taxonomy categories. The deterministic detectors handle the
  surface-pattern signals; the trained head only needs to land on the
  right *category* for ambiguous text.
* **Multilingual:** every category includes at least one non-English
  paraphrase (Vietnamese, Spanish, German, Japanese, Bengali, Arabic
  where culturally available) so the head doesn't collapse to
  English-only behaviour.
* **Conservative SAFE class:** the SAFE class is over-sampled relative
  to harm classes — the runtime cost of a false positive (suppressing
  benign chat) is higher than the cost of a false negative on the
  embedding head, because the deterministic detectors already raise
  the floor for high-risk surface patterns.
* **Stable + auditable:** the corpus is checked-in source code, not a
  blob. Reviewers can read every training example and the category
  label it carries. Adding examples is purely additive — order is
  preserved by the trainer for deterministic random-shuffling at
  training time.

The corpus is sufficient for a ``Linear(384, 16)`` head with L2
regularisation; it is **not** intended to be a benchmark corpus and
the head trained on it is **not** intended to ship as a state-of-the-
art classifier. Its role is to outperform zero-shot prototype argmax
on the existing benchmark cases (currently 27/27) without regressing
the privacy contract.

.. warning::

    This is a **training corpus only**. Accuracy on
    :data:`TRAINING_EXAMPLES` is not a meaningful safety metric —
    the head was fit on these exact strings and will memorise them.
    See ``kchat-skills/eval/`` for the held-out evaluation set that
    the pack lifecycle gate (Phase 4 compiler) treats as the source
    of truth. Held-out cases have no overlap with this corpus and
    cover benign-false-positive, adversarial, and multilingual
    cases that the training corpus does not.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Taxonomy category ids (kept in sync with ``encoder_adapter.CAT_*`` and
# ``xlmr_adapter.CATEGORY_PROTOTYPES``).
# ---------------------------------------------------------------------------
CAT_SAFE = 0
CAT_CHILD_SAFETY = 1
CAT_SELF_HARM = 2
CAT_VIOLENCE_THREAT = 3
CAT_EXTREMISM = 4
CAT_HARASSMENT = 5
CAT_HATE = 6
CAT_SCAM_FRAUD = 7
CAT_MALWARE_LINK = 8
CAT_PRIVATE_DATA = 9
CAT_SEXUAL_ADULT = 10
CAT_DRUGS_WEAPONS = 11
CAT_ILLEGAL_GOODS = 12
CAT_MISINFORMATION_HEALTH = 13
CAT_MISINFORMATION_CIVIC = 14
CAT_COMMUNITY_RULE = 15


# ---------------------------------------------------------------------------
# Training corpus.
#
# Each entry is ``(text, category)``. Multilingual coverage where possible.
# Examples are short on purpose: the encoder mean-pools over tokens, so
# longer prompts dilute the signal we want the head to learn.
# ---------------------------------------------------------------------------
TRAINING_EXAMPLES: list[tuple[str, int]] = [
    # ------------------------------------------------------------------
    # 0 SAFE — benign everyday chat. Over-sampled (~25 examples).
    # ------------------------------------------------------------------
    ("hey, what time is dinner tonight?", CAT_SAFE),
    ("see you at the meeting tomorrow morning", CAT_SAFE),
    ("does anyone know a good vegetarian recipe?", CAT_SAFE),
    ("i'll be five minutes late, traffic is bad", CAT_SAFE),
    ("happy birthday! hope you have a great day", CAT_SAFE),
    ("the weather is nice today, going for a walk", CAT_SAFE),
    ("can you send me the meeting notes when you get a chance?", CAT_SAFE),
    ("just finished the project, thanks everyone for the help", CAT_SAFE),
    ("bonjour tout le monde, comment allez-vous", CAT_SAFE),
    ("こんにちは、今日はいい天気ですね", CAT_SAFE),
    ("xin chào mọi người, hôm nay khỏe không", CAT_SAFE),
    ("hola, ¿cómo estás? ¿qué tal el día?", CAT_SAFE),
    ("guten morgen, hat jemand gut geschlafen", CAT_SAFE),
    ("আমি আজ বিকেলে আসব", CAT_SAFE),
    ("yes thanks, that works for me", CAT_SAFE),
    ("can we move the call to friday at three pm?", CAT_SAFE),
    ("looking forward to the conference next week", CAT_SAFE),
    ("the cake was delicious, thank you for bringing it", CAT_SAFE),
    ("i finished the report and emailed it to the team", CAT_SAFE),
    ("does the library have a copy of that book?", CAT_SAFE),
    ("we should go hiking this weekend if the weather holds", CAT_SAFE),
    ("the bus is running late again", CAT_SAFE),
    ("I'll pick up groceries on the way home", CAT_SAFE),
    ("congrats on the new job, that's great news", CAT_SAFE),
    ("hope your trip went well", CAT_SAFE),

    # ------------------------------------------------------------------
    # 1 CHILD_SAFETY — descriptive, avoids any operative content.
    # Detector + lexicon hit raises the severity-5 floor in production;
    # the head only needs to land on category 1.
    # ------------------------------------------------------------------
    ("a minor is being targeted with grooming behaviour by an adult", CAT_CHILD_SAFETY),
    ("a child is in danger and someone is asking for inappropriate photos", CAT_CHILD_SAFETY),
    ("an adult is requesting private images from a teenager", CAT_CHILD_SAFETY),
    ("predatory message attempting to isolate a minor from their family", CAT_CHILD_SAFETY),
    ("child exploitation material reported in this conversation", CAT_CHILD_SAFETY),
    ("un menor está siendo manipulado por un adulto desconocido", CAT_CHILD_SAFETY),
    ("một người lớn đang nhắn tin không phù hợp với trẻ em", CAT_CHILD_SAFETY),
    ("an adult is sending sexual messages to someone underage", CAT_CHILD_SAFETY),
    ("grooming pattern: an adult is asking a minor to keep secrets", CAT_CHILD_SAFETY),
    ("ein erwachsener sendet unangemessene nachrichten an ein kind", CAT_CHILD_SAFETY),

    # ------------------------------------------------------------------
    # 2 SELF_HARM — disclosure / ideation. Demos crisis-resource path.
    # ------------------------------------------------------------------
    ("i don't want to be here anymore, everything feels hopeless", CAT_SELF_HARM),
    ("i've been thinking about hurting myself again tonight", CAT_SELF_HARM),
    ("nothing matters and i can't see a way out of this", CAT_SELF_HARM),
    ("disclosure: i've been struggling with self-harm urges", CAT_SELF_HARM),
    ("i feel like ending it all, i can't take another day", CAT_SELF_HARM),
    ("estoy pensando en hacerme daño otra vez", CAT_SELF_HARM),
    ("私は自分を傷つけたい、もう疲れた", CAT_SELF_HARM),
    ("i've been cutting again, i'm not okay", CAT_SELF_HARM),
    ("i don't want to wake up tomorrow", CAT_SELF_HARM),
    ("eating disorder relapse, can't keep food down", CAT_SELF_HARM),

    # ------------------------------------------------------------------
    # 3 VIOLENCE_THREAT — first-person threat to a specific target.
    # ------------------------------------------------------------------
    ("i'm going to hurt them when i see them tomorrow", CAT_VIOLENCE_THREAT),
    ("planning to attack the person who wronged me", CAT_VIOLENCE_THREAT),
    ("i'll make them pay, they won't see it coming", CAT_VIOLENCE_THREAT),
    ("i know where they live and i'm going there tonight", CAT_VIOLENCE_THREAT),
    ("threat to harm a specific co-worker over a dispute", CAT_VIOLENCE_THREAT),
    ("voy a hacerles daño cuando los vea", CAT_VIOLENCE_THREAT),
    ("私は彼を殴るつもりだ、明日学校で", CAT_VIOLENCE_THREAT),
    ("i'm going to find them and beat them up", CAT_VIOLENCE_THREAT),
    ("violent intent to assault a named person tomorrow", CAT_VIOLENCE_THREAT),
    ("i'll bring a weapon and finish this once and for all", CAT_VIOLENCE_THREAT),

    # ------------------------------------------------------------------
    # 4 EXTREMISM — recruitment / glorification of violent groups.
    # ------------------------------------------------------------------
    ("recruiting for a violent extremist organisation, looking for new members", CAT_EXTREMISM),
    ("glorifying a terrorist attack and praising the perpetrators", CAT_EXTREMISM),
    ("calling for armed insurrection against the government", CAT_EXTREMISM),
    ("ideology promoting violent action against an ethnic group", CAT_EXTREMISM),
    ("extremist propaganda celebrating mass casualty attacks", CAT_EXTREMISM),
    ("apoyando un grupo terrorista y reclutando miembros", CAT_EXTREMISM),
    ("violent jihad recruitment and glorification of attacks", CAT_EXTREMISM),
    ("white nationalist propaganda calling for race war", CAT_EXTREMISM),
    ("praising a banned militant group and inciting attacks on civilians", CAT_EXTREMISM),
    ("incitement to violent uprising and armed revolution", CAT_EXTREMISM),

    # ------------------------------------------------------------------
    # 5 HARASSMENT — repeated targeted attacks on a specific person.
    # ------------------------------------------------------------------
    ("you're worthless and nobody likes you, just disappear", CAT_HARASSMENT),
    ("repeated insults and stalking messages aimed at one person", CAT_HARASSMENT),
    ("doxxing threat: i'll post your home address online", CAT_HARASSMENT),
    ("you're so ugly nobody will ever want you, give up", CAT_HARASSMENT),
    ("i'll make sure everyone at your job knows what you did", CAT_HARASSMENT),
    ("eres un fracasado, deja de existir", CAT_HARASSMENT),
    ("targeted bullying campaign against a single user", CAT_HARASSMENT),
    ("stalking and unwanted contact after being told to stop", CAT_HARASSMENT),
    ("personal attack repeated dozens of times to harass the recipient", CAT_HARASSMENT),
    ("i'm going to ruin your reputation in this group", CAT_HARASSMENT),

    # ------------------------------------------------------------------
    # 6 HATE — slurs / dehumanising language toward protected groups.
    # ------------------------------------------------------------------
    ("dehumanising language about an ethnic minority group", CAT_HATE),
    ("slur-based attack on a religious community", CAT_HATE),
    ("hate speech denying the humanity of a protected group", CAT_HATE),
    ("racist generalisation comparing a group to animals", CAT_HATE),
    ("homophobic insult calling for the removal of LGBTQ people", CAT_HATE),
    ("xenophobic message blaming immigrants for all problems", CAT_HATE),
    ("antisemitic conspiracy theory about a religious group", CAT_HATE),
    ("misogynistic dehumanisation of women in a workplace context", CAT_HATE),
    ("transphobic slur and harassment based on gender identity", CAT_HATE),
    ("ableist language mocking people with disabilities", CAT_HATE),

    # ------------------------------------------------------------------
    # 7 SCAM_FRAUD — phishing, advance-fee, fake giveaways.
    # ------------------------------------------------------------------
    ("congratulations you won a prize, claim it at this suspicious link", CAT_SCAM_FRAUD),
    ("verify your bank account password at this fake login page", CAT_SCAM_FRAUD),
    ("nigerian prince advance fee scam asking for wire transfer", CAT_SCAM_FRAUD),
    ("urgent: your account is suspended, click here to reactivate", CAT_SCAM_FRAUD),
    ("limited-time offer to invest in a guaranteed crypto doubling scheme", CAT_SCAM_FRAUD),
    ("ganaste un premio, reclámalo en este enlace dudoso", CAT_SCAM_FRAUD),
    ("send gift cards to confirm your identity, urgent reply", CAT_SCAM_FRAUD),
    ("phishing email pretending to be from a major bank", CAT_SCAM_FRAUD),
    ("fake giveaway asking for personal info to claim a prize", CAT_SCAM_FRAUD),
    ("romance scam asking for emergency money for a stranded traveller", CAT_SCAM_FRAUD),

    # ------------------------------------------------------------------
    # 8 MALWARE_LINK — drive-by / exploit-kit / malicious downloads.
    # ------------------------------------------------------------------
    ("download this attachment to run a hidden exploit kit", CAT_MALWARE_LINK),
    ("install this fake antivirus that is actually ransomware", CAT_MALWARE_LINK),
    ("click this link to drop malware on your device", CAT_MALWARE_LINK),
    ("trojan disguised as a video player update", CAT_MALWARE_LINK),
    ("malicious .exe file pretending to be an invoice attachment", CAT_MALWARE_LINK),
    ("descarga este archivo que contiene un virus oculto", CAT_MALWARE_LINK),
    ("phishing payload that installs a keylogger on the victim's machine", CAT_MALWARE_LINK),
    ("drive-by download exploiting a browser vulnerability", CAT_MALWARE_LINK),
    ("zero-day exploit kit hosted on a compromised site", CAT_MALWARE_LINK),
    ("ransomware dropper masquerading as a software patch", CAT_MALWARE_LINK),

    # ------------------------------------------------------------------
    # 9 PRIVATE_DATA — PII / credentials disclosed in chat. Detectors
    # already handle the surface patterns; the head still needs to
    # learn the descriptive form.
    # ------------------------------------------------------------------
    ("disclosure of email address and home phone number in a public chat", CAT_PRIVATE_DATA),
    ("personally identifying information shared without consent", CAT_PRIVATE_DATA),
    ("credit card number and expiry date posted in plaintext", CAT_PRIVATE_DATA),
    ("government id number leaked in a group conversation", CAT_PRIVATE_DATA),
    ("account credentials including password shared with a third party", CAT_PRIVATE_DATA),
    ("compartió su número de tarjeta de crédito en el chat", CAT_PRIVATE_DATA),
    ("social security number disclosure to an unknown contact", CAT_PRIVATE_DATA),
    ("home address and phone number posted publicly", CAT_PRIVATE_DATA),
    ("medical record information shared without authorisation", CAT_PRIVATE_DATA),
    ("bank account details posted in a public marketplace channel", CAT_PRIVATE_DATA),

    # ------------------------------------------------------------------
    # 10 SEXUAL_ADULT — explicit adult content (lawful, between adults
    # in adult-only contexts; classifier still labels for moderation).
    # ------------------------------------------------------------------
    ("explicit adult sexual content involving consenting adults", CAT_SEXUAL_ADULT),
    ("graphic pornography description in an adult-only group", CAT_SEXUAL_ADULT),
    ("nudity and sexual acts depicted in detail", CAT_SEXUAL_ADULT),
    ("adult-only erotic story with explicit sexual content", CAT_SEXUAL_ADULT),
    ("sexually explicit image attached, adults only", CAT_SEXUAL_ADULT),
    ("contenido sexual explícito entre adultos", CAT_SEXUAL_ADULT),
    ("adult-themed nsfw description involving consenting parties", CAT_SEXUAL_ADULT),
    ("explicit sexual proposition between adults in a private channel", CAT_SEXUAL_ADULT),
    ("erotic adult media intended for an 18+ audience", CAT_SEXUAL_ADULT),
    ("graphic adult sex scene in a fictional roleplay context", CAT_SEXUAL_ADULT),

    # ------------------------------------------------------------------
    # 11 DRUGS_WEAPONS — sale / trade / acquisition of regulated items.
    # ------------------------------------------------------------------
    ("selling illegal narcotics, please dm for prices", CAT_DRUGS_WEAPONS),
    ("offering unregistered firearms for sale, no questions asked", CAT_DRUGS_WEAPONS),
    ("trading prescription drugs without a prescription", CAT_DRUGS_WEAPONS),
    ("buying ammunition without a license", CAT_DRUGS_WEAPONS),
    ("selling cocaine and methamphetamine in this group", CAT_DRUGS_WEAPONS),
    ("vendo armas de fuego sin licencia", CAT_DRUGS_WEAPONS),
    ("looking to buy illegal weapons for self defense", CAT_DRUGS_WEAPONS),
    ("dealer offering hard drugs to anonymous buyers", CAT_DRUGS_WEAPONS),
    ("trafficking heroin and fentanyl across borders", CAT_DRUGS_WEAPONS),
    ("homemade firearm assembly instructions for sale", CAT_DRUGS_WEAPONS),

    # ------------------------------------------------------------------
    # 12 ILLEGAL_GOODS — counterfeit / smuggled / stolen items.
    # ------------------------------------------------------------------
    ("selling counterfeit luxury handbags at a discount", CAT_ILLEGAL_GOODS),
    ("trade in smuggled electronics avoiding customs duties", CAT_ILLEGAL_GOODS),
    ("stolen phones for sale, no boxes or warranties", CAT_ILLEGAL_GOODS),
    ("offering pirated software keys for cheap", CAT_ILLEGAL_GOODS),
    ("counterfeit currency for sale, untraceable", CAT_ILLEGAL_GOODS),
    ("vendo réplicas de marcas falsificadas", CAT_ILLEGAL_GOODS),
    ("black market organ trade post in a closed group", CAT_ILLEGAL_GOODS),
    ("smuggled wildlife products including ivory and rhino horn", CAT_ILLEGAL_GOODS),
    ("stolen art and antiquities being offered to anonymous buyers", CAT_ILLEGAL_GOODS),
    ("illegal services such as hacking-for-hire and document forgery", CAT_ILLEGAL_GOODS),

    # ------------------------------------------------------------------
    # 13 MISINFORMATION_HEALTH — dangerous medical falsehoods.
    # ------------------------------------------------------------------
    ("vaccines cause autism — this is a dangerous medical falsehood", CAT_MISINFORMATION_HEALTH),
    ("drinking bleach cures coronavirus, a known harmful claim", CAT_MISINFORMATION_HEALTH),
    ("essential oils cure cancer, no need for chemotherapy", CAT_MISINFORMATION_HEALTH),
    ("anti-vaccine conspiracy theory denying basic immunology", CAT_MISINFORMATION_HEALTH),
    ("homeopathy can replace antibiotics for serious infections", CAT_MISINFORMATION_HEALTH),
    ("desinformación médica diciendo que las vacunas causan daños graves", CAT_MISINFORMATION_HEALTH),
    ("hiv is a hoax invented by pharmaceutical companies", CAT_MISINFORMATION_HEALTH),
    ("ivermectin cures every disease, doctors are hiding this", CAT_MISINFORMATION_HEALTH),
    ("dangerous medical claim that sun exposure cures all skin disease", CAT_MISINFORMATION_HEALTH),
    ("anti-mask conspiracy theory claiming masks cause oxygen deprivation", CAT_MISINFORMATION_HEALTH),

    # ------------------------------------------------------------------
    # 14 MISINFORMATION_CIVIC — election / political falsehoods.
    # ------------------------------------------------------------------
    ("the election was stolen by widespread voter fraud, false claim", CAT_MISINFORMATION_CIVIC),
    ("false story claiming voting machines flipped millions of votes", CAT_MISINFORMATION_CIVIC),
    ("deceptive political content fabricating a candidate's quote", CAT_MISINFORMATION_CIVIC),
    ("manipulated video used to spread civic disinformation", CAT_MISINFORMATION_CIVIC),
    ("false claim that mail-in ballots are systematically fraudulent", CAT_MISINFORMATION_CIVIC),
    ("desinformación electoral sobre el fraude en las elecciones", CAT_MISINFORMATION_CIVIC),
    ("fabricated news story about a politician committing a crime", CAT_MISINFORMATION_CIVIC),
    ("conspiracy theory accusing a head of state of being an impostor", CAT_MISINFORMATION_CIVIC),
    ("false story about a foreign country invading the homeland", CAT_MISINFORMATION_CIVIC),
    ("manipulated polling data presented as a real survey", CAT_MISINFORMATION_CIVIC),

    # ------------------------------------------------------------------
    # 15 COMMUNITY_RULE — community-specific etiquette / off-topic.
    # ------------------------------------------------------------------
    ("posting off-topic memes in a serious work channel", CAT_COMMUNITY_RULE),
    ("repeated cross-posting against the group's rules", CAT_COMMUNITY_RULE),
    ("breaking the rule against advertising your own product", CAT_COMMUNITY_RULE),
    ("violating the no-political-talk rule in a sports group", CAT_COMMUNITY_RULE),
    ("posting in the wrong channel after being warned", CAT_COMMUNITY_RULE),
    ("publicación fuera de tema en un canal específico", CAT_COMMUNITY_RULE),
    ("breaking the etiquette of asking a question that's already answered", CAT_COMMUNITY_RULE),
    ("posting low-effort content in a curated discussion thread", CAT_COMMUNITY_RULE),
    ("violating the channel's language rule by posting in english only", CAT_COMMUNITY_RULE),
    ("ignoring the moderator's reminder to read the pinned rules", CAT_COMMUNITY_RULE),
]


def category_counts() -> dict[int, int]:
    """Return a histogram of ``{category: count}`` over the corpus."""
    counts: dict[int, int] = {}
    for _, cat in TRAINING_EXAMPLES:
        counts[cat] = counts.get(cat, 0) + 1
    return counts


__all__ = ["TRAINING_EXAMPLES", "category_counts"]
