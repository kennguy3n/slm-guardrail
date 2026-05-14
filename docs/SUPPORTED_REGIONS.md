# KChat Guardrail Skills — Supported Regions

This page lists all jurisdiction and community overlays shipped in
the repository. It tracks the 59 country / jurisdiction packs and
the 38 community overlays that live under `kchat-skills/`. The
[README](../README.md) references counts and links here so the
top-level landing page stays focused.

## Supported Countries (59)

| ISO-3166 | Country | Primary languages | Key legal references |
| --- | --- | --- | --- |
| US | United States | en | 18 U.S.C. §§ 2251–2260, Patriot Act, FTC Act. |
| DE | Germany | de | StGB § 86a / § 130 (Volksverhetzung), NetzDG, JuSchG. |
| BR | Brazil | pt | ECA (Lei 8.069/1990), Lei 7.716/89, TSE election rules. |
| IN | India | hi, en | POCSO 2012, UAPA, IPC § 153A / § 295A, IT Act § 67. |
| JP | Japan | ja | Child-protection statute, tokushoho, drug / weapon laws. |
| MX | Mexico | es | LGPNNA, Ley Federal contra la Delincuencia Organizada, COFEPRIS. |
| CA | Canada | en, fr | Criminal Code s. 163.1 / terrorism, Competition Act. |
| AR | Argentina | es | Ley 26.061 child protection, Código Penal, Ley 23.592. |
| CO | Colombia | es | Código de la Infancia y Adolescencia, anti-terrorism law. |
| CL | Chile | es | Ley 21.057 child protection, Ley Antiterrorista. |
| PE | Peru | es | Código de los Niños y Adolescentes. |
| FR | France | fr | Loi Avia / DSA, loi Gayssot, Code pénal Art. 225-1. |
| GB | United Kingdom | en | Online Safety Act 2023, Terrorism Act 2000, Equality Act 2010. |
| ES | Spain | es, ca, eu, gl | Ley Orgánica de Protección del Menor, Código Penal. |
| IT | Italy | it | Codice Penale child protection + anti-terrorism. |
| NL | Netherlands | nl | Wetboek van Strafrecht child protection + terrorism. |
| PL | Poland | pl | Kodeks Karny child protection + anti-terrorism. |
| SE | Sweden | sv | Brottsbalk child protection + terrorism. |
| PT | Portugal | pt | Código Penal child protection + terrorism. |
| CH | Switzerland | de, fr, it, rm | StGB child protection, StGB Art. 261bis anti-racism. |
| AT | Austria | de | StGB child protection, Verbotsgesetz 1945. |
| KR | South Korea | ko | Act on Protection of Children and Youth, NSA. |
| ID | Indonesia | id | UU ITE, UU Perlindungan Anak, Anti-Terrorism Law. |
| PH | Philippines | en, tl | RA 7610, Human Security Act. |
| TH | Thailand | th | Child Protection Act B.E. 2546, lèse-majesté, CCA. |
| VN | Vietnam | vi | Law on Children 2016, Anti-Terrorism Law. |
| MY | Malaysia | ms, en | Child Act 2001, SOSMA. |
| SG | Singapore | en, zh, ms, ta | Children and Young Persons Act, ISA. |
| TW | Taiwan | zh | Child and Youth Welfare and Protection Act, anti-terrorism. |
| PK | Pakistan | ur, en | PPC child protection, Anti-Terrorism Act 1997, PECA 2016. |
| BD | Bangladesh | bn | Children Act 2013, Anti-Terrorism Act 2009. |
| NG | Nigeria | en | Child Rights Act 2003, Terrorism Prevention Act, Cybercrimes Act. |
| ZA | South Africa | en, af, zu | Children's Act 38/2005, POCDATARA. |
| EG | Egypt | ar | Child Law 12/1996, Anti-Terrorism Law 94/2015. |
| SA | Saudi Arabia | ar | Child Protection System, Anti-Terrorism Law, Anti-Cyber Crime Law. |
| AE | United Arab Emirates | ar, en | Wadeema's Law, Federal Decree-Law 7/2014. |
| KE | Kenya | en, sw | Children Act 2022, Prevention of Terrorism Act. |
| AU | Australia | en | Criminal Code Act 1995 (child exploitation + terrorism), Online Safety Act 2021. |
| NZ | New Zealand | en, mi | Films, Videos and Publications Classification Act, Terrorism Suppression Act. |
| TR | Turkey | tr | TCK child protection, TMK anti-terrorism. |
| RU | Russia | ru | Federal Law 124-FZ child protection, Federal Law 114-FZ anti-extremism, Roskomnadzor rules. |
| UA | Ukraine | uk, ru | Law on Child Protection (1995), Law on Combating Terrorism (2003). |
| RO | Romania | ro | Legea 272/2004 child protection, Legea 535/2004 anti-terrorism. |
| GR | Greece | el | Greek Penal Code child protection, Law 3251/2004 anti-terrorism. |
| CZ | Czech Republic | cs | Trestní zákoník §§ 192-193 child protection, § 311 anti-terrorism. |
| HU | Hungary | hu | Btk. § 204 child protection, §§ 314-318 terrorism offences. |
| DK | Denmark | da | Straffeloven § 235 child protection, § 114 anti-terrorism. |
| FI | Finland | fi | Rikoslaki 17:18-19 child protection, 34a luku terrorism. |
| NO | Norway | no, nb | Straffeloven § 311 child protection, § 131 anti-terrorism. |
| IE | Ireland | en, ga | Online Safety and Media Regulation Act 2022, Child Trafficking and Pornography Act 1998. |
| IL | Israel | he, ar | Penal Code § 214, Counter-Terrorism Law 5776-2016. |
| IQ | Iraq | ar, ku | Juvenile Welfare Law No. 76/1983, Anti-Terrorism Law No. 13/2005. |
| MA | Morocco | ar, fr | Code Pénal Art. 503 child protection, Loi 03-03 anti-terrorism. |
| DZ | Algeria | ar, fr | Loi 15-12 child protection, Code Pénal Art. 87 bis anti-terrorism. |
| GH | Ghana | en | Children's Act 1998 (Act 560), Anti-Terrorism Act 2008 (Act 762). |
| TZ | Tanzania | sw, en | Law of the Child Act 2009, Prevention of Terrorism Act 2002. |
| ET | Ethiopia | am, en | Criminal Code Art. 644 CSAM, Anti-Terrorism Proclamation 1176/2020. |
| EC | Ecuador | es | Código de la Niñez y Adolescencia (CONA), COIP Art. 366 anti-terrorism. |
| UY | Uruguay | es | Código de la Niñez y la Adolescencia (CNA), Ley 19.293 anti-terrorism. |

## Community Overlays (38)

| Overlay | Age mode | Notable tightenings / loosenings |
| --- | --- | --- |
| school | minor_present | tightens HARASSMENT, BULLYING; tightens SEXUAL_ADULT |
| family | mixed_age | tightens HARASSMENT; loosens MISINFORMATION_HEALTH for parents |
| workplace | adult_only | tightens HARASSMENT (workplace civility); tightens PRIVATE_DATA |
| adult_only | adult_only | loosens SEXUAL_ADULT to label_only |
| marketplace | mixed_age | tightens SCAM_FRAUD, ILLEGAL_GOODS, MALWARE_LINK |
| health_support | mixed_age | loosens SELF_HARM (peer support); tightens MISINFORMATION_HEALTH |
| political | adult_only | tightens HATE, MISINFORMATION_CIVIC; tightens HARASSMENT |
| gaming | mixed_age | tightens HARASSMENT (anti-toxicity); tightens VIOLENCE_THREAT |
| religious | mixed_age | tightens HATE, HARASSMENT |
| sports | mixed_age | tightens HARASSMENT (anti-pile-on), VIOLENCE_THREAT |
| creative_arts | mixed_age | warns SEXUAL_ADULT (artistic context), enforces COMMUNITY_RULE |
| education_higher | adult_only | loosens SEXUAL_ADULT, tightens MISINFORMATION_HEALTH |
| volunteer | mixed_age | tightens SCAM_FRAUD, PRIVATE_DATA |
| neighborhood | mixed_age | tightens PRIVATE_DATA, warns SCAM_FRAUD |
| parenting | mixed_age | tightens SEXUAL_ADULT, warns MISINFORMATION_HEALTH |
| dating | adult_only | loosens SEXUAL_ADULT, tightens SCAM_FRAUD, HARASSMENT |
| fitness | mixed_age | warns MISINFORMATION_HEALTH, tightens DRUGS_WEAPONS |
| travel | adult_only | tightens SCAM_FRAUD, warns PRIVATE_DATA |
| book_club | mixed_age | warns HARASSMENT |
| music | mixed_age | warns SEXUAL_ADULT, HARASSMENT |
| photography | mixed_age | warns SEXUAL_ADULT, tightens PRIVATE_DATA |
| cooking | mixed_age | warns SCAM_FRAUD, label-only MISINFORMATION_HEALTH |
| tech_support | adult_only | tightens SCAM_FRAUD, MALWARE_LINK, PRIVATE_DATA |
| language_learning | mixed_age | warns HATE / HARASSMENT (educational context) |
| pet_owners | mixed_age | warns SCAM_FRAUD, tightens ILLEGAL_GOODS |
| environmental | mixed_age | warns MISINFORMATION_HEALTH, MISINFORMATION_CIVIC |
| journalism | adult_only | loosens EXTREMISM, warns VIOLENCE_THREAT |
| legal_support | adult_only | tightens PRIVATE_DATA, SCAM_FRAUD |
| mental_health | adult_only | loosens SELF_HARM (peer support), warns MISINFORMATION_HEALTH |
| startup | adult_only | tightens SCAM_FRAUD, warns PRIVATE_DATA |
| nonprofit | mixed_age | tightens SCAM_FRAUD, warns MISINFORMATION_CIVIC |
| seniors | adult_only | tightens SCAM_FRAUD, PRIVATE_DATA, MISINFORMATION_HEALTH |
| lgbtq_support | adult_only | tightens HATE, HARASSMENT |
| veterans | adult_only | loosens SELF_HARM (peer support), tightens SCAM_FRAUD |
| hobbyist | mixed_age | warns SCAM_FRAUD, ILLEGAL_GOODS |
| science | mixed_age | warns MISINFORMATION_HEALTH, MISINFORMATION_CIVIC |
| open_source | adult_only | tightens MALWARE_LINK, warns SCAM_FRAUD |
| emergency_response | adult_only | tightens MISINFORMATION_HEALTH, SCAM_FRAUD, warns PRIVATE_DATA |

