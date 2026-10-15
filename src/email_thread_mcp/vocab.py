"""Per-language words the thread parser looks for: quote-header labels, separators, attribution verbs,
greetings, sign-offs and honorifics.

These words only label and enrich what the parser finds. Quote boundaries are also found by structure
(HTML quote markers, a `label: value` block with an address and a date), so a language missing here
still splits correctly; only names and header labels degrade, and parse_info.warnings says so.

Adding a language: add an entry below, then a case to tests/test_languages.py. Set `verified` once a
real thread in that language (anonymised like tests/fixtures/outlook-2026-09-29) has been checked.
Keys of HEADERS are the canonical header names the parser emits. `dateparser` is the language code
dateparser expects for that language's dates.
"""

from __future__ import annotations

from typing import TypedDict


class Language(TypedDict):
    verified: bool
    dateparser: str
    headers: dict[str, list[str]]
    separators: list[str]
    wrote: list[str]
    greetings: list[str]
    closings: list[str]
    honorifics: list[str]


LANGUAGES: dict[str, Language] = {
    "en": {
        "verified": True,  # tests/fixtures/outlook-2026-09-29
        "dateparser": "en",
        "headers": {"from": ["from"], "sent": ["sent", "date"], "to": ["to"], "cc": ["cc", "bcc"], "subject": ["subject"]},
        "separators": ["original message", "forwarded message"],
        "wrote": ["wrote"],
        "greetings": ["dear", "hi", "hello", "hey", "good morning", "good afternoon", "good evening"],
        "closings": [
            "kind regards", "best regards", "warm regards", "kindest regards", "regards", "best wishes", "best",
            "thanks", "thank you", "many thanks", "sincerely", "yours sincerely", "yours faithfully",
            "yours truly", "cheers",
        ],
        "honorifics": ["mr", "ms", "mrs", "miss", "dr"],
    },
    "de": {
        "verified": False,
        "dateparser": "de",
        "headers": {"from": ["von"], "sent": ["gesendet", "datum"], "to": ["an"], "cc": ["cc", "bcc"], "subject": ["betreff"]},
        "separators": ["ursprüngliche nachricht", "weitergeleitete nachricht"],
        "wrote": ["schrieb"],
        "greetings": ["hallo", "liebe", "lieber", "liebes", "sehr geehrte", "sehr geehrter", "guten tag", "guten morgen", "moin"],
        "closings": [
            "mit freundlichen grüßen", "mit freundlichen grüssen", "freundliche grüße", "freundliche grüsse",
            "viele grüße", "beste grüße", "liebe grüße", "herzliche grüße", "schöne grüße", "grüße", "gruß",
            "vielen dank", "danke", "mfg",
        ],
        "honorifics": ["herr", "frau", "hr", "fr"],
    },
    "fr": {
        "verified": False,
        "dateparser": "fr",
        "headers": {"from": ["de"], "sent": ["envoyé", "date"], "to": ["à", "a"], "cc": ["cc", "cci"], "subject": ["objet"]},
        "separators": ["message d'origine", "message transféré"],
        "wrote": ["a écrit"],
        "greetings": ["bonjour", "bonsoir", "cher", "chère", "salut"],
        "closings": [
            "cordialement", "bien cordialement", "bien à vous", "salutations", "meilleures salutations",
            "sincères salutations", "merci", "merci beaucoup", "bonne journée",
        ],
        "honorifics": ["m", "mme", "mlle", "monsieur", "madame"],
    },
    "nl": {
        "verified": False,
        "dateparser": "nl",
        "headers": {"from": ["van"], "sent": ["verzonden", "datum"], "to": ["aan"], "cc": ["cc", "bcc"], "subject": ["onderwerp"]},
        "separators": ["oorspronkelijk bericht", "doorgestuurd bericht"],
        "wrote": ["schreef"],
        "greetings": ["beste", "geachte", "hallo", "hoi", "goedemorgen", "goedemiddag", "dag"],
        "closings": [
            "met vriendelijke groet", "met vriendelijke groeten", "vriendelijke groet", "vriendelijke groeten",
            "hartelijke groet", "groeten", "groet", "mvg", "alvast bedankt", "bedankt", "dank u wel",
        ],
        "honorifics": ["dhr", "mevr", "heer", "mevrouw", "de heer"],
    },
    "it": {
        "verified": False,
        "dateparser": "it",
        "headers": {"from": ["da"], "sent": ["inviato", "data"], "to": ["a"], "cc": ["cc", "ccn"], "subject": ["oggetto"]},
        "separators": ["messaggio originale", "messaggio inoltrato"],
        "wrote": ["ha scritto"],
        "greetings": ["gentile", "gentilissimo", "gentilissima", "egregio", "caro", "cara", "buongiorno", "buonasera", "salve", "ciao"],
        "closings": ["cordiali saluti", "distinti saluti", "un saluto", "saluti", "cordialmente", "grazie mille", "grazie"],
        "honorifics": ["sig", "sig.ra", "dott", "dott.ssa", "signor", "signora"],
    },
    "es": {
        "verified": False,
        "dateparser": "es",
        "headers": {"from": ["de"], "sent": ["enviado", "fecha"], "to": ["para"], "cc": ["cc", "cco"], "subject": ["asunto"]},
        "separators": ["mensaje original", "mensaje reenviado"],
        "wrote": ["escribió"],
        "greetings": ["estimado", "estimada", "estimados", "hola", "buenos días", "buenas tardes", "apreciado", "apreciada"],
        "closings": ["saludos cordiales", "un saludo", "saludos", "atentamente", "cordialmente", "muchas gracias", "gracias"],
        "honorifics": ["sr", "sra", "srta", "don", "doña"],
    },
    "pt": {
        "verified": False,
        "dateparser": "pt",
        "headers": {"from": ["de"], "sent": ["enviado", "enviada", "data"], "to": ["para"], "cc": ["cc", "cco"], "subject": ["assunto"]},
        "separators": ["mensagem original", "mensagem encaminhada"],
        "wrote": ["escreveu"],
        "greetings": ["prezado", "prezada", "caro", "cara", "olá", "bom dia", "boa tarde", "exmo", "exma"],
        "closings": [
            "atenciosamente", "com os melhores cumprimentos", "melhores cumprimentos", "cumprimentos",
            "saudações", "obrigado", "obrigada", "abraço",
        ],
        "honorifics": ["sr", "sra", "dr", "dra"],
    },
    "pl": {
        "verified": False,
        "dateparser": "pl",
        "headers": {"from": ["od"], "sent": ["wysłano", "data"], "to": ["do"], "cc": ["dw", "udw"], "subject": ["temat"]},
        "separators": ["wiadomość oryginalna", "oryginalna wiadomość", "wiadomość przekazana dalej"],
        "wrote": ["napisał(a)", "napisał", "napisała"],
        "greetings": ["szanowny", "szanowna", "szanowni państwo", "dzień dobry", "witam", "cześć"],
        "closings": ["z poważaniem", "z wyrazami szacunku", "serdecznie pozdrawiam", "pozdrawiam", "pozdrowienia", "dziękuję"],
        "honorifics": ["pan", "pani", "panie"],
    },
    "cs": {
        "verified": False,
        "dateparser": "cs",
        "headers": {"from": ["od"], "sent": ["odesláno", "datum"], "to": ["komu"], "cc": ["kopie"], "subject": ["předmět"]},
        "separators": ["původní zpráva", "přeposlaná zpráva"],
        "wrote": ["napsal(a)", "napsal", "napsala"],
        "greetings": ["dobrý den", "vážený", "vážená", "vážení", "ahoj"],
        "closings": ["s přátelským pozdravem", "s pozdravem", "děkuji", "díky"],
        "honorifics": ["pan", "paní", "pane"],
    },
    "sv": {
        "verified": False,
        "dateparser": "sv",
        "headers": {"from": ["från"], "sent": ["skickat", "datum"], "to": ["till"], "cc": ["kopia"], "subject": ["ämne"]},
        "separators": ["ursprungligt meddelande", "vidarebefordrat meddelande"],
        "wrote": ["skrev"],
        "greetings": ["hej", "hejsan", "hallå", "god morgon", "bästa"],
        "closings": ["med vänliga hälsningar", "vänliga hälsningar", "bästa hälsningar", "hälsningar", "mvh", "tack på förhand", "tack"],
        "honorifics": [],
    },
    "da": {
        "verified": False,
        "dateparser": "da",
        "headers": {"from": ["fra"], "sent": ["sendt", "dato"], "to": ["til"], "cc": ["cc"], "subject": ["emne"]},
        "separators": ["oprindelig meddelelse", "videresendt meddelelse"],
        "wrote": ["skrev"],
        "greetings": ["hej", "kære", "goddag", "god morgen"],
        "closings": ["med venlig hilsen", "venlig hilsen", "bedste hilsner", "hilsen", "mvh", "mange tak", "tak"],
        "honorifics": [],
    },
    "no": {
        "verified": False,
        "dateparser": "nb",
        "headers": {"from": ["fra"], "sent": ["sendt", "dato"], "to": ["til"], "cc": ["kopi"], "subject": ["emne"]},
        "separators": ["opprinnelig melding", "videresendt melding"],
        "wrote": ["skrev"],
        "greetings": ["hei", "hallo", "kjære", "god morgen"],
        "closings": ["med vennlig hilsen", "vennlig hilsen", "hilsen", "mvh", "tusen takk", "takk"],
        "honorifics": [],
    },
    "fi": {
        "verified": False,
        "dateparser": "fi",
        "headers": {
            "from": ["lähettäjä"], "sent": ["lähetetty", "päivämäärä"], "to": ["vastaanottaja"], "cc": ["kopio"], "subject": ["aihe"],
        },
        "separators": ["alkuperäinen viesti", "välitetty viesti"],
        "wrote": ["kirjoitti"],
        "greetings": ["hei", "hyvä", "moi", "terve", "hyvää huomenta"],
        "closings": ["ystävällisin terveisin", "parhain terveisin", "terveisin", "kiitos paljon", "kiitos"],
        "honorifics": [],
    },
}


def all_words(field: str) -> list[str]:
    """Every word of one field across languages, longest first so regex alternation prefers it."""
    words = {w for lang in LANGUAGES.values() for w in lang[field]}  # type: ignore[literal-required]
    return sorted(words, key=lambda w: (-len(w), w))


def header_labels() -> dict[str, str]:
    """label (lowercase) -> canonical header name."""
    return {label: key for lang in LANGUAGES.values() for key, labels in lang["headers"].items() for label in labels}


def label_languages() -> dict[str, list[str]]:
    """Sent/date label -> dateparser codes of the languages using it (e.g. 'data' -> it, pt, pl)."""
    out: dict[str, list[str]] = {}
    for lang in LANGUAGES.values():
        for label in lang["headers"]["sent"]:
            out.setdefault(label, [])
            if lang["dateparser"] not in out[label]:
                out[label].append(lang["dateparser"])
    return out
