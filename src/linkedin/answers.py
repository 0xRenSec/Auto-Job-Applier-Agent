"""Resolve a screening-question label to an answer using config.

The golden rule: if we cannot confidently answer a REQUIRED question, we return
None and the caller skips the whole application. The bot never guesses on
required fields — that's how you end up sending nonsense to employers.
"""
from __future__ import annotations

import re

from ..utils import log


# Yes/No option labels across the form languages we encounter. A yes_no map
# hit used to match only the literal "yes"/"no", so localised forms
# (Sim/Sì/Ja/Oui vs Não/Nein/Nej) never got their mapped answer (2026-08-12).
POSITIVE_OPTIONS = {"yes", "sí", "si", "sim", "ja", "oui", "sì", "da", "tak", "true"}
NEGATIVE_OPTIONS = {"no", "não", "nao", "nein", "non", "nej", "nie", "false"}


def _match(label: str, mapping: dict):
    """Return the value whose key is a substring of label (longest key wins)."""
    label_l = label.lower()
    best_key = None
    for key in mapping:
        if key.lower() in label_l and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return mapping[best_key] if best_key is not None else None


def numeric_answer(label: str, answers: dict) -> str | None:
    exp = answers.get("experience_years", {})
    val = _match(label, exp.get("overrides", {}))
    if val is None:
        val = exp.get("default")
    return str(val) if val is not None else None


def yes_no_answer(label: str, answers: dict, job_location: str | None = None) -> bool | None:
    # Right-to-work / sponsorship questions depend on the job's country and
    # are computed from it when it is known; otherwise the map applies as-is.
    wa = work_authorization_answer(label, answers, job_location)
    if wa is not None:
        return wa
    return _match(label, answers.get("yes_no", {}))


# --- Work authorisation ----------------------------------------------------------
# "Do you have the right to work in the country where you are applying?" is
# only answerable from the JOB's country, which the job card knows and the
# config maps cannot. Deterministic on purpose (Codex/Grok review 2026-08-29):
# employer-controlled title/company/location text never reaches the LLM for
# these, the answer is computed from the parsed country against
# answers.work_authorization.countries, and an unknown country changes nothing.
# English names plus the native / major-language names LinkedIn forms use.
# Aliases are whole-word and unambiguous on purpose: no bare "Korea" (north or
# south?), no "Georgia" (state or country?), no two-letter codes here.
_COUNTRIES: dict[str, tuple[str, ...]] = {
    "sweden": ("sweden", "sverige", "schweden", "suède", "suecia", "suécia", "svezia",
               "zweden", "szwecja", "sverige"),
    "norway": ("norway", "norge", "noreg", "norwegen", "norvège", "noruega", "norwegia"),
    "denmark": ("denmark", "danmark", "dänemark", "danemark", "dinamarca", "dania"),
    "finland": ("finland", "suomi", "finnland", "finlande", "finlandia"),
    "iceland": ("iceland", "ísland"), "ireland": ("ireland", "irland", "irlande", "irlanda"),
    "united kingdom": ("united kingdom", "great britain", "britain", "england", "scotland",
                       "wales", "northern ireland", "royaume-uni", "reino unido",
                       "regno unito", "vereinigtes königreich", "verenigd koninkrijk",
                       "wielka brytania", "storbritannien"),
    "germany": ("germany", "deutschland", "allemagne", "alemania", "alemanha", "germania",
                "duitsland", "niemcy", "tyskland"),
    "austria": ("austria", "österreich", "autriche"),
    "switzerland": ("switzerland", "schweiz", "suisse", "svizzera", "suiza", "szwajcaria"),
    "netherlands": ("netherlands", "the netherlands", "holland", "nederland", "pays-bas",
                    "países bajos", "paesi bassi", "niederlande", "holandia"),
    "belgium": ("belgium", "belgië", "belgique", "belgien", "bélgica", "belgia"),
    "luxembourg": ("luxembourg", "luxemburg"),
    "france": ("france", "francia", "frankreich", "frankrijk", "francja", "frankrike", "frança"),
    "spain": ("spain", "españa", "espagne", "spanien", "spagna", "spanje", "hiszpania", "espanha"),
    "portugal": ("portugal", "portogallo", "portugalia"),
    "italy": ("italy", "italia", "italie", "italien", "italië", "włochy"),
    "greece": ("greece", "grèce", "grecia", "griechenland", "grecja"),
    "poland": ("poland", "polska", "pologne", "polonia", "polen", "polónia"),
    "czech republic": ("czech republic", "czechia", "česko", "tschechien", "chequia", "czechy"),
    "slovakia": ("slovakia", "slovensko"), "hungary": ("hungary", "magyarország", "ungarn"),
    "romania": ("romania", "românia", "rumänien"), "bulgaria": ("bulgaria",),
    "croatia": ("croatia", "hrvatska"), "slovenia": ("slovenia", "slovenija"),
    "serbia": ("serbia", "srbija"), "estonia": ("estonia", "eesti"),
    "latvia": ("latvia", "latvija"), "lithuania": ("lithuania", "lietuva"),
    "ukraine": ("ukraine", "україна"), "turkey": ("turkey", "türkiye"),
    "cyprus": ("cyprus",), "malta": ("malta",),
    "united states": ("united states", "united states of america", "états-unis", "etats-unis",
                      "estados unidos", "stati uniti", "vereinigte staaten", "verenigde staten",
                      "stany zjednoczone"),
    "canada": ("canada", "kanada"), "mexico": ("mexico", "méxico"),
    "brazil": ("brazil", "brasil", "brésil", "brasile", "brasilien"),
    "argentina": ("argentina",), "chile": ("chile",), "colombia": ("colombia",),
    "australia": ("australia", "australie", "australien"), "new zealand": ("new zealand",),
    "india": ("india", "indien", "inde"), "singapore": ("singapore", "singapur"),
    "japan": ("japan", "japon", "japón"), "south korea": ("south korea", "republic of korea"),
    "china": ("china", "chine"), "hong kong": ("hong kong",), "taiwan": ("taiwan",),
    "philippines": ("philippines",), "indonesia": ("indonesia",), "malaysia": ("malaysia",),
    "vietnam": ("vietnam",), "thailand": ("thailand",), "pakistan": ("pakistan",),
    "united arab emirates": ("united arab emirates", "dubai"), "saudi arabia": ("saudi arabia",),
    "qatar": ("qatar",), "israel": ("israel",), "egypt": ("egypt",),
    "south africa": ("south africa",), "nigeria": ("nigeria",), "kenya": ("kenya",),
    "morocco": ("morocco",),
}
_ALIAS_TO_COUNTRY = {a: c for c, aliases in _COUNTRIES.items() for a in aliases}

# Nationality / adjective forms ("Swedish residents only", "US citizens").
DEMONYMS: dict[str, tuple[str, ...]] = {
    "sweden": ("swedish",), "norway": ("norwegian",), "denmark": ("danish",),
    "finland": ("finnish",), "iceland": ("icelandic",), "ireland": ("irish",),
    "united kingdom": ("british", "uk", "u.k.", "english", "scottish", "welsh"),
    "germany": ("german",), "austria": ("austrian",), "switzerland": ("swiss",),
    "netherlands": ("dutch",), "belgium": ("belgian",), "luxembourg": ("luxembourgish",),
    "france": ("french",), "spain": ("spanish",), "portugal": ("portuguese",),
    "italy": ("italian",), "greece": ("greek",), "poland": ("polish",),
    "czech republic": ("czech",), "slovakia": ("slovak",), "hungary": ("hungarian",),
    "romania": ("romanian",), "bulgaria": ("bulgarian",), "croatia": ("croatian",),
    "slovenia": ("slovenian",), "serbia": ("serbian",), "estonia": ("estonian",),
    "latvia": ("latvian",), "lithuania": ("lithuanian",), "ukraine": ("ukrainian",),
    "turkey": ("turkish",), "cyprus": ("cypriot",), "malta": ("maltese",),
    "united states": ("american", "us", "u.s.", "usa", "u.s.a."),
    "canada": ("canadian",), "mexico": ("mexican",), "brazil": ("brazilian",),
    "argentina": ("argentine", "argentinian"), "chile": ("chilean",),
    "colombia": ("colombian",), "australia": ("australian",),
    "new zealand": ("kiwi", "nz"), "india": ("indian",), "singapore": ("singaporean",),
    "japan": ("japanese",), "south korea": ("korean",), "china": ("chinese",),
    "philippines": ("filipino", "philippine"), "indonesia": ("indonesian",),
    "malaysia": ("malaysian",), "vietnam": ("vietnamese",), "thailand": ("thai",),
    "pakistan": ("pakistani",), "united arab emirates": ("uae", "emirati"),
    "saudi arabia": ("saudi",), "israel": ("israeli",), "egypt": ("egyptian",),
    "south africa": ("south african",), "nigeria": ("nigerian",), "kenya": ("kenyan",),
}
_DEMONYM_TO_COUNTRY = {d: c for c, ds in DEMONYMS.items() for d in ds}

EU_MEMBERS = frozenset({
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czech republic", "denmark",
    "estonia", "finland", "france", "germany", "greece", "hungary", "ireland", "italy",
    "latvia", "lithuania", "luxembourg", "malta", "netherlands", "poland", "portugal",
    "romania", "slovakia", "slovenia", "spain", "sweden",
})
EEA_MEMBERS = EU_MEMBERS | {"norway", "iceland", "liechtenstein"}


def canonical_country(name: str | None) -> str:
    """'Sverige', 'USA' or 'Swedish' -> the English key used in the tables above.
    Unknown names come back lowercased and stripped, never dropped."""
    key = str(name or "").strip().lower()
    return _ALIAS_TO_COUNTRY.get(key) or _DEMONYM_TO_COUNTRY.get(key) or key


def demonyms_in(text: str | None) -> set[str]:
    """Countries referred to by nationality in `text` ("US citizens only")."""
    t = (text or "").lower()
    return {c for d, c in _DEMONYM_TO_COUNTRY.items()
            if re.search(r"(?<![a-z])" + re.escape(d) + r"(?![a-z])", t)}
_COUNTRY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(a) for a in sorted(_ALIAS_TO_COUNTRY, key=len, reverse=True)) + r")\b",
    re.I,
)
# Two-letter codes only count in upper case: "us" is usually the pronoun.
_US_RE = re.compile(r"(?<![A-Za-z])U\.?S\.?A?(?![A-Za-z])")
_UK_RE = re.compile(r"(?<![A-Za-z])U\.?K\.?(?![A-Za-z])")
# "EU" only in upper case: lower-case "eu" is Portuguese "I" and French "had".
_EU_RE = re.compile(r"(?<![A-Za-z])E\.?U\.?(?![A-Za-z])|(?i:european union|\beurope\b|\bemea\b|schengen)")

# Positive form: "do you have the right / are you authorised to work …" ->
# answer is "authorised". Checked first because such questions often go on
# "… without requiring sponsorship".
_AUTH_POSITIVE = re.compile(
    r"right to work|authori[sz]ed to work|eligib\w* to work"
    r"|legally (?:able|allowed|permitted|entitled) to work|permitted to work|allowed to work"
    r"|have (?:a |an )?(?:valid |current )?(?:work permit|work visa|work authori[sz]ation|permit to work)"
    # de / es / pt / sv / da / no / fr / it / nl / pl
    r"|arbeitserlaubnis|arbeitsgenehmigung|berechtigt.{0,30}zu arbeiten|arbeiten d[üu]rfen"
    r"|permiso de trabajo|autoriza\w+ (?:a|para) trabajar|derecho a trabajar"
    r"|autorização de trabalho|autoriza\w+ (?:a|para) trabalhar|direito de trabalhar"
    r"|arbetstillstånd|rätt att arbeta|ret til at arbejde|rett til å (?:arbeide|jobbe)|arbeidstillatelse"
    r"|droit de travailler|autoris\w+ (?:à|a) travailler|permis de travail"
    r"|diritto di lavorare|autorizzat\w+ a lavorare|permesso di lavoro"
    r"|werkvergunning|recht om te werken|gerechtigd om te werken"
    r"|prawo do pracy|pozwolenie na pracę|zezwolenie na pracę",
    re.I,
)
# Negative form: "do you require / need sponsorship / a visa / a permit" ->
# answer is "NOT authorised".
_AUTH_NEGATIVE = re.compile(
    r"\b(?:requir\w*|need\w*|necessit\w*|precisa\w*|necesit\w*|ben[öo]tig\w*|brauch\w*|beh[öo]v\w*"
    r"|potrzeb\w*|wymaga\w*|avez-vous besoin|a besoin|hai bisogno|heb je|nodig)\b"
    r".{0,40}?\b(?:sponsor\w*|visa|visum|visto|permit|patroc[ií]nio|patrocinio|parrainage|sponsorizzazion\w*"
    r"|sponsorowani\w*|wiza|wizy)"
    r"|\bsponsor\w*\s+(?:is\s+)?(?:required|needed|necessary)|\bvisa\s+(?:is\s+)?(?:required|needed)",
    re.I,
)
# Wordings that flip or blur the polarity. Never guess these (Codex review
# 2026-08-29): "Are you NOT authorised…", "…or would you require sponsorship?".
# A negation counts only when it sits (within three words) before an
# authorisation word: "not authorised", "do you not have the right", "unable
# to work". "We do not sponsor visas" or "non-EU countries" leave the polarity
# alone (Codex review 2026-08-29).
_AUTH_NEGATION = re.compile(
    r"\b(?:not|no|unable|ineligible|lack\w*|nicht|kein\w*|não|nao|inte|pas|non|niet|nie|ikke)\b(?!-)"
    r"(?:\W+\w+){0,3}?\W+(?:authori\w*|eligib\w*|right|droit|diritto|recht|prawo|rätt|ret|berechtigt"
    r"|permit\w*|permis|permesso|erlaubnis|tillstånd|vergunning|autoriza\w*|have|has|hold\w*|tienes|tem"
    r"|hai|heb|masz|har|avez|able|allowed|work\w*|travailler|arbeiten|lavorare|werken|pracy|arbeta"
    r"|trabalhar|trabajar)\b",
    re.I,
)


# LinkedIn drops the country on many listings ("Lisbon (Hybrid)", "Greater
# Stockholm Metropolitan Area", "Pittsburgh, PA (Remote)"). Geography is not
# a candidate fact, so a card LOCATION (never a question) may be resolved
# through unambiguous major cities and US/Canadian state codes. Cities that
# exist in more than one country (Cambridge, Santiago, San Jose, Portland …)
# are left out on purpose.
_CITIES: dict[str, tuple[str, ...]] = {
    "sweden": ("stockholm", "göteborg", "gothenburg", "malmö", "malmo", "uppsala", "lund",
               "linköping", "linkoping", "västerås", "örebro", "helsingborg", "umeå"),
    "norway": ("oslo", "bergen", "trondheim", "stavanger"),
    "denmark": ("copenhagen", "københavn", "aarhus", "odense", "aalborg"),
    "finland": ("helsinki", "espoo", "tampere", "turku", "oulu"),
    "iceland": ("reykjavik", "reykjavík"), "ireland": ("dublin", "cork", "galway", "limerick"),
    "united kingdom": ("london", "manchester", "birmingham", "edinburgh", "glasgow", "belfast",
                       "leeds", "bristol", "liverpool", "sheffield", "newcastle upon tyne",
                       "cardiff", "nottingham", "leicester", "reading", "milton keynes"),
    "germany": ("berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne", "köln",
                "düsseldorf", "dusseldorf", "stuttgart", "hannover", "hanover", "nuremberg",
                "nürnberg", "leipzig", "dresden", "bremen", "essen", "dortmund", "bonn",
                "karlsruhe", "mannheim", "heidelberg", "aachen", "münster", "wiesbaden"),
    "austria": ("vienna", "wien", "graz", "linz", "salzburg"),
    "switzerland": ("zurich", "zürich", "geneva", "genève", "basel", "bern", "lausanne", "zug",
                    "lugano"),
    "netherlands": ("amsterdam", "rotterdam", "the hague", "den haag", "utrecht", "eindhoven",
                    "amersfoort", "amstelveen", "groningen", "leiden", "delft", "haarlem",
                    "hoofddorp", "nijmegen"),
    "belgium": ("brussels", "bruxelles", "brussel", "antwerp", "antwerpen", "ghent", "gent",
                "leuven", "liège", "mechelen"),
    "france": ("paris", "lyon", "marseille", "toulouse", "nantes", "bordeaux", "lille",
               "strasbourg", "montpellier", "grenoble", "rennes", "sophia antipolis"),
    "spain": ("madrid", "barcelona", "seville", "sevilla", "bilbao", "málaga", "malaga",
              "zaragoza", "alicante", "palma"),
    "portugal": ("lisbon", "lisboa", "porto", "oeiras", "braga", "coimbra", "aveiro"),
    "italy": ("rome", "roma", "milan", "milano", "turin", "torino", "naples", "napoli",
              "bologna", "florence", "firenze", "genoa", "genova", "padua", "padova", "verona"),
    "greece": ("athens", "thessaloniki"),
    "poland": ("warsaw", "warszawa", "krakow", "kraków", "cracow", "wroclaw", "wrocław",
               "gdansk", "gdańsk", "poznan", "poznań", "lodz", "łódź", "katowice", "lublin",
               "szczecin"),
    "czech republic": ("prague", "praha", "brno", "ostrava"),
    "slovakia": ("bratislava", "košice", "kosice"), "hungary": ("budapest", "debrecen"),
    "romania": ("bucharest", "bucurești", "cluj-napoca", "cluj", "timișoara", "timisoara",
                "iași", "iasi", "brașov", "brasov"),
    "bulgaria": ("sofia", "plovdiv", "varna"), "croatia": ("zagreb", "split"),
    "slovenia": ("ljubljana",), "serbia": ("belgrade", "beograd", "novi sad"),
    "estonia": ("tallinn", "tartu"), "latvia": ("riga",), "lithuania": ("vilnius", "kaunas"),
    "ukraine": ("kyiv", "kiev", "lviv"), "turkey": ("istanbul", "ankara", "izmir"),
    "cyprus": ("nicosia", "limassol"), "malta": ("valletta",),
    "united states": ("new york", "san francisco", "los angeles", "chicago", "boston", "seattle",
                      "austin", "dallas", "houston", "atlanta", "denver", "washington", "miami",
                      "philadelphia", "pittsburgh", "phoenix", "san diego", "minneapolis",
                      "detroit", "charlotte", "raleigh", "salt lake city", "las vegas"),
    "canada": ("toronto", "vancouver", "montreal", "montréal", "ottawa", "calgary", "edmonton"),
    "india": ("bangalore", "bengaluru", "hyderabad", "pune", "chennai", "mumbai", "new delhi",
              "delhi", "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad"),
    "australia": ("sydney", "melbourne", "brisbane", "perth"),
    "new zealand": ("auckland", "wellington"),
    "united arab emirates": ("abu dhabi",), "israel": ("tel aviv", "haifa"),
    "japan": ("tokyo", "osaka"), "south korea": ("seoul",), "taiwan": ("taipei",),
    "philippines": ("manila",), "indonesia": ("jakarta",), "malaysia": ("kuala lumpur",),
    "thailand": ("bangkok",), "vietnam": ("ho chi minh city", "hanoi"),
    "pakistan": ("karachi", "lahore", "islamabad"), "saudi arabia": ("riyadh", "jeddah"),
    "qatar": ("doha",), "egypt": ("cairo",), "south africa": ("johannesburg", "cape town"),
    "nigeria": ("lagos",), "kenya": ("nairobi",), "morocco": ("casablanca",),
    "mexico": ("mexico city", "guadalajara", "monterrey"),
    "brazil": ("são paulo", "sao paulo", "rio de janeiro"), "argentina": ("buenos aires",),
    "colombia": ("bogotá", "bogota", "medellín", "medellin"),
}
_CITY_TO_COUNTRY = {c: k for k, cities in _CITIES.items() for c in cities}
_CITY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in sorted(_CITY_TO_COUNTRY, key=len, reverse=True)) + r")\b",
    re.I,
)
_US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
                 "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
_CA_PROVINCES = set("ON BC QC AB MB SK NS NB NL PE NT YT NU".split())
_STATE_CODE_RE = re.compile(r",\s*([A-Z]{2})(?=[\s,(]|$)")
_LOCATION_NOISE = re.compile(r"\((?:remote|hybrid|on-?site)\)|\bgreater\b|\bmetropolitan area\b|\barea\b", re.I)


def country_of_location(location: str | None) -> str | None:
    """Canonical country of a job-card location, or None when it cannot be
    resolved without guessing (regions, ambiguous cities, empty)."""
    if not location:
        return None
    named = countries_in(location)
    if len(named) == 1:
        return named.pop()
    if named:
        return None
    m = _STATE_CODE_RE.search(location)
    if m:
        if m.group(1) in _US_STATES:
            return "united states"
        if m.group(1) in _CA_PROVINCES:
            return "canada"
    cleaned = _LOCATION_NOISE.sub(" ", location)
    found = {_CITY_TO_COUNTRY[c.lower()] for c in _CITY_RE.findall(cleaned)}
    return found.pop() if len(found) == 1 else None


def countries_in(text: str | None) -> set[str]:
    """Canonical country names mentioned in a label or job location."""
    if not text:
        return set()
    found = {_ALIAS_TO_COUNTRY[m.lower()] for m in _COUNTRY_RE.findall(text)}
    if _US_RE.search(text):
        found.add("united states")
    if _UK_RE.search(text):
        found.add("united kingdom")
    return found


def work_authorization_answer(label: str, answers: dict,
                              job_location: str | None = None) -> bool | None:
    """Yes/No for a right-to-work or sponsorship question, from the job's
    country; None when it is not such a question, the feature is off, or the
    country is unknown or ambiguous (the caller falls back to the yes_no map)."""
    wa = answers.get("work_authorization") or {}
    allowed = {canonical_country(c) for c in (wa.get("countries") or []) if str(c).strip()}
    # Regional rights (EU citizens, EEA nationals) cover every member state.
    regions = {str(r).strip().lower() for r in (wa.get("regions") or [])}
    if regions & {"eu", "european union", "europe"}:
        allowed |= EU_MEMBERS
    if regions & {"eea", "european economic area"}:
        allowed |= EEA_MEMBERS
    if not allowed:
        return None
    eu_wide_ok = allowed >= EU_MEMBERS
    pos, neg = _AUTH_POSITIVE.search(label), _AUTH_NEGATIVE.search(label)
    if pos:
        positive = True
        # "Do you have the right to work in X, OR would you require sponsorship?"
        # is two questions; only "…without requiring sponsorship" is one.
        if neg:
            first, second = sorted((pos, neg), key=lambda m: m.start())
            between = label[first.end():second.start()]
            if re.search(r"\bor\b|\bou\b|\boder\b|\boppure\b|\blub\b|\beller\b", between, re.I):
                return None
    elif neg:
        positive = False
    else:
        return None
    if _AUTH_NEGATION.search(label):
        return None
    # A country named in the question wins over the card; an EU/Europe-wide
    # question is only a Yes when the configured rights cover the whole EU
    # (work_authorization.regions) — a single country's permit is a No. Both
    # named ("Sweden and anywhere in the EU") is two questions -> None.
    named = countries_in(label)
    eu_wide = bool(_EU_RE.search(label))
    if named and eu_wide:
        return None
    if named:
        verdicts = {c in allowed for c in named}
        if len(verdicts) != 1:
            return None
        authorised = verdicts.pop()
    elif eu_wide:
        authorised = eu_wide_ok
    else:
        country = country_of_location(job_location)
        if country is None:
            return None
        authorised = country in allowed
    return authorised if positive else not authorised


# "How many years of experience…" in the languages LinkedIn localises forms to.
# A label counts as numeric only on a QUANTITY cue (how many / years / años /
# Jahre / år / lat …). A bare mention of "experience" is not one: "Describe
# your experience with SAST" is a free-text field, and routing it here used to
# answer it with the years default (found 2026-08-29). Numeric-validated
# fields that slip through are still corrected by easy_apply._fix_numeric_errors.
# Either a "years" cue in a quantity position …
_YEARS_CUE = re.compile(
    # en: "years of/in/with/experience", "years' experience", "(in years)",
    # "number of years", "Years:" — a bare "year" is a calendar year.
    r"number of years|\byears?\s+(?:of|in|with|experience|hands)\b"
    r"|\byears['’]\s+experience|\(\s*(?:in\s+)?years?\s*\)|\bin\s+years\b|\byears?\s*:"
    # sv/da/no: "antal år", "års erfarenhet/erfaring"
    r"|antal år|\bårs\s+erfar"
    # es/pt/it/fr: plural years (años/anos/anni/années); de: Jahre(n); nl; pl
    r"|\baños\b|\banos\b|\banni\b|\bann[ée]es\b|\bjahren?\b|\bjaar\s+ervaring|\blat\b",
    re.I,
)
# … or a "how many / how long" word TOGETHER with an experience/years word.
# "How many certifications do you hold?" is a count, not years, and must not
# get the years default (found in the LLM cache 2026-08-29).
_HOW_MANY = re.compile(
    r"how many|how long|wie viele|hoeveel|hur många|hvor mange"
    r"|\bcu[aá]nt[oa]s?\b|\bquant[oaie]s?\b|combien|\bile\b",
    re.I,
)
_EXPERIENCE_WORD = re.compile(
    r"experien|expérien|experiên|erfahrung|ervaring|erfarenhet|erfaring|esperienza"
    r"|doświadczen|\byears?\b|\bår|\baño|\bano|\banni\b|\bann[ée]e|\bjahr|\bjaar\b|\blat\b"
    r"|\bworked\b|\bused\b|\busing\b|\bwork(?:ing)?\s+with",
    re.I,
)
# Calendar-year questions that the cues above would otherwise catch ("Year of
# graduation" has "year of"). Grok review 2026-08-29: the years default landed
# in these, and dropdown_answer's substring match then turned "11" into "2011".
_CALENDAR_Q = re.compile(
    r"graduat|\bbirth|\bborn\b|which year|what year|start(?:ing)? year|end year"
    r"|year of (?:completion|study|birth)|examensår|vilket år|welchem jahr|welk jaar"
    r"|qu[eé]l+e? ann[ée]e|dipl[oô]me|qu[eé] a[nñ]o|quale anno|kt[oó]rym roku",
    re.I,
)


def looks_numeric_question(label: str) -> bool:
    if _CALENDAR_Q.search(label):
        return False
    if _YEARS_CUE.search(label):
        return True
    return bool(_HOW_MANY.search(label) and _EXPERIENCE_WORD.search(label))


def text_answer(label: str, answers: dict) -> str | None:
    # An explicit text mapping is the user's stated answer for that label, so
    # it beats the generic years routing ("How long is your notice period?"
    # must give the notice period, not the years default).
    mapped = _match(label, answers.get("text", {}))
    if mapped is not None:
        return mapped
    # Numeric-looking questions ("how many years…") route to the experience map.
    if looks_numeric_question(label):
        return numeric_answer(label, answers)
    return None


# Salary computation: derive the number in the currency/period the question
# asks for, from the anchors in config answers.salary. Approximate EUR rates.
_EUR_RATES = {
    "eur": 1.0, "usd": 1.08, "gbp": 0.85, "chf": 0.94,
    "sek": 11.0, "nok": 11.5, "dkk": 7.45,
    "pln": 4.3, "ron": 5.0, "czk": 25.0, "huf": 395.0,
}
_CUR_HINTS = [  # order matters: explicit codes before symbols
    ("sek", ("sek",)), ("nok", ("nok",)), ("dkk", ("dkk",)),
    ("pln", ("pln", "zł", "zloty")), ("ron", ("ron", "lei")),
    ("czk", ("czk",)), ("huf", ("huf", "forint")), ("chf", ("chf",)),
    ("gbp", ("gbp", "£", "pound")), ("usd", ("usd", "$", "dollar")),
    ("eur", ("eur", "€")),
]
# Short tokens are whole-word: "rate" used to fire inside "integrate",
# "operated", "corporate" and put the salary figure into free-text fields
# (found 2026-08-29); "tag" (German "pro Tag") likewise inside "stage".
_SALARY_RE = re.compile(
    r"salar|compensation|remuneration|wage|\brates?\b|gehalt|lön|wynagrodzenie"
    r"|stipendio|sueldo",
    re.I,
)
_HOUR_WORDS = ("hour", "stunde", "hora", "ora", "godz", "timme", "/h")
_DAY_RE = re.compile(r"per day|/day|daily|per diem|\btag\b|tagessatz|/d\b", re.I)
_MONTH_WORDS = ("month", "mensu", "mensil", "monat", "miesi", "månad", "lunar", "/mo")


def salary_answer(label: str, answers: dict) -> str | None:
    """Compute a salary answer in the label's currency and period, or None."""
    sal = answers.get("salary", {})
    annual, hourly = sal.get("annual_eur"), sal.get("hourly_eur")
    if not annual:
        return None
    l = label.lower()
    if "current" in l:  # never disclose current salary as a number
        return None
    if not _SALARY_RE.search(l):
        return None
    cur = next((c for c, hints in _CUR_HINTS if any(h in l for h in hints)), "eur")
    rate = _EUR_RATES[cur]
    # Per-currency pinned annual (e.g. gbp: 150000 = ask 150k GBP, not a
    # conversion). Scales the hourly/daily/monthly answers proportionally.
    pinned = (sal.get("overrides") or {}).get(cur)
    if pinned:
        rate = pinned / annual
    # Optional pinned monthly figure for Swedish forms (answers.salary.sek_monthly):
    # used as-is for monthly asks, x12 for annual — never converted from EUR.
    if cur == "sek" and sal.get("sek_monthly"):
        if any(w in l for w in _MONTH_WORDS):
            return str(sal["sek_monthly"])
        if not any(w in l for w in _HOUR_WORDS) and not _DAY_RE.search(l):
            return str(sal["sek_monthly"] * 12)  # annual SEK
    if any(w in l for w in _HOUR_WORDS):
        val, step = (hourly or 100) * rate, 5
    elif _DAY_RE.search(l):
        val, step = (hourly or 100) * 8 * rate, 50
    elif any(w in l for w in _MONTH_WORDS):
        val, step = annual / 12 * rate, 100
    else:
        val, step = annual * rate, 1000
    return str(int(round(val / step) * step))


# --- Language proficiency ------------------------------------------------------
# Answered ONLY from config answers.languages ({language: level}); nothing is
# assumed about which languages the candidate speaks. Language names as the
# forms write them, in the languages LinkedIn localises to.
_LANGUAGE_WORDS: dict[str, tuple[str, ...]] = {
    "english": ("english", "inglés", "ingles", "inglês", "inglese", "anglais",
                "englisch", "engelska", "angielski", "engels"),
    "spanish": ("spanish", "español", "espanol", "castellano", "spanisch", "espagnol"),
    "german": ("german", "deutsch", "alemán", "aleman", "allemand", "tedesco", "tyska"),
    "french": ("french", "français", "francais", "französisch", "francés", "frances"),
    "italian": ("italian", "italiano", "italienisch", "italien"),
    "portuguese": ("portuguese", "português", "portugues", "portugiesisch"),
    "polish": ("polish", "polski", "polnisch"), "dutch": ("dutch", "nederlands", "niederländisch"),
    "swedish": ("swedish", "svenska", "schwedisch"), "danish": ("danish", "dansk"),
    "norwegian": ("norwegian", "norsk"), "finnish": ("finnish", "suomi"),
    "russian": ("russian", "русский"), "ukrainian": ("ukrainian",),
    "czech": ("czech", "čeština"), "romanian": ("romanian", "română"),
    "greek": ("greek",), "arabic": ("arabic",), "hindi": ("hindi",),
    "chinese": ("chinese", "mandarin"), "japanese": ("japanese",),
    "turkish": ("turkish",), "hungarian": ("hungarian", "magyar"),
}
# Proficiency ladder used to rank BOTH the configured level and each option.
# The first matching keyword decides the rank, so multi-word levels come first.
_LEVEL_RANKS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (5, ("native", "mother tongue", "bilingual", "nativo", "nativa", "muttersprach",
         "langue maternelle", "madrelingua", "modersmål", "ojczysty")),
    (4, ("full professional", "fluent", "c2", "proficient", "fluente", "fluido",
         "fließend", "courant", "flytande", "biegły")),
    (3, ("professional working", "professional", "advanced", "c1", "business",
         "verhandlungssicher", "avanzado", "avançado", "avancé", "avanzato",
         "profesional", "profissional", "professionnel", "zaawansowany", "avancerad")),
    (2, ("limited working", "intermediate", "conversational", "b2", "b1", "intermedio",
         "intermediário", "intermédiaire", "mittel", "medel", "średnio")),
    (1, ("elementary", "basic", "beginner", "a2", "a1", "básico", "basico",
         "grundkenntnisse", "débutant", "principiante", "podstawow", "grundläggande")),
    (0, ("none", "no knowledge", "not at all", "keine", "ingen", "ninguno", "ninguna",
         "nenhum", "nessuna", "aucun", "brak", "geen")),
)


def _level_rank(text: str) -> int | None:
    t = (text or "").lower()
    for rank, words in _LEVEL_RANKS:
        if any(w in t for w in words):
            return rank
    return None


def language_level_answer(label: str, options: list[str],
                          answers: dict | None = None) -> str | None:
    """Pick a proficiency option from config answers.languages, e.g.
    {"english": "fluent", "german": "basic", "french": "none"}.

    Never overclaims: the chosen option is the highest one at or below the
    configured level, and at most one step below it. A language that is not
    configured, or options that don't fit, return None (LLM / skip)."""
    configured = {str(k).lower(): str(v) for k, v in
                  ((answers or {}).get("languages") or {}).items()}
    if not configured:
        return None
    l = label.lower()
    lang = next((name for name, words in _LANGUAGE_WORDS.items()
                 if any(w in l for w in words)), None)
    if lang is None:  # a language configured under its own name ("svenska")
        lang = next((k for k in configured if k and k in l), None)
    if lang is None or lang not in configured:
        return None
    want = _level_rank(configured[lang])
    if want is None:
        return None
    best: tuple[int, str] | None = None
    for opt in options:
        r = _level_rank(opt)
        if r is None or r > want or want - r > 1:
            continue
        if best is None or r > best[0]:
            best = (r, opt)
    return best[1] if best else None


# --- Checkboxes -------------------------------------------------------------------
# A required checkbox is NOT automatically consent. Factual declarations
# ("I am a US citizen", "I hold a valid driving licence") need an explicit
# yes_no answer; consent / acknowledgement boxes are ticked.
_CHECKBOX_DECLARATION = re.compile(
    r"citizen|nationalit|authori[sz]ed|right to work|work permit|visa|sponsor|clearance"
    r"|licen[cs]e|convict|felon|criminal|over 18|18 years|18\+|age of|relocat|veteran"
    r"|disabilit|gender|ethnic|drug|degree|certif(?!y that the information)|experience"
    r"|available|notice period|salary|\byears\b|resident|based in|located in",
    re.I,
)
_CHECKBOX_CONSENT = re.compile(
    r"consent|agree|accept|acknowledg|privacy|terms|policy|gdpr|data protection"
    r"|process(?:ing)?\s+(?:of\s+)?(?:my\s+)?(?:personal\s+)?(?:data|information)"
    r"|i have read|i understand|i confirm|certify that the information|accurate"
    r"|keep my (?:cv|résumé|resume|data|profile|details)|talent pool"
    r"|future (?:opportunities|positions|roles|vacancies)|opt.?in|newsletter"
    r"|subscribe|contact me|be contacted|communications|updates|informed",
    re.I,
)


def checkbox_answer(label: str, answers: dict | None) -> bool | None:
    """Tick (True), leave (False) or can't-say (None) for a checkbox label.

    Order: an explicit yes_no answer wins; a factual declaration with no
    explicit answer is None (the job is skipped rather than asserted); a
    consent / acknowledgement box is True. A box with no label text at all is
    treated as consent — a factual declaration always carries text."""
    text = (label or "").strip()
    if not text:
        return True
    explicit = _match(text, (answers or {}).get("yes_no", {}) or {})
    if explicit is not None:
        return bool(explicit)
    if _CHECKBOX_DECLARATION.search(text):
        return None
    if _CHECKBOX_CONSENT.search(text):
        return True
    return None


def dropdown_answer(label: str, options: list[str], answers: dict,
                    job_location: str | None = None) -> str | None:
    """Pick a dropdown option. Prefer a Yes/No match, else config default."""
    yn = yes_no_answer(label, answers, job_location)
    if yn is not None:
        want = POSITIVE_OPTIONS if yn else NEGATIVE_OPTIONS
        for opt in options:
            if opt.strip().lower() in want:
                return opt
    txt = text_answer(label, answers)
    if txt:
        for opt in options:
            if txt.lower() in opt.lower():
                return opt
    default = answers.get("dropdown_default") or ""
    if default:
        for opt in options:
            if default.lower() in opt.lower():
                return opt
    return None
