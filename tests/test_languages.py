"""One customer reply to an AR notice per vocabulary language, as Outlook HTML and as plain text.

Hand-built from Outlook's reply-header layout, NOT captured from real mailboxes: they prove the
vocabulary is wired in, not that the words match every Outlook build. vocab.py marks a language
`verified` once a real thread in it has been checked.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from email_thread_mcp import vocab
from email_thread_mcp.thread import parse_email_thread

AR = ["ar@example.com"]
SENT_ISO = "2026-09-29T10:48:00"


@dataclass
class Case:
    lang: str
    labels: tuple[str, str, str, str]  # from, sent, to, subject
    sent: str
    greeting: str  # the customer greets us ...
    greeting_name: str
    closing: str  # ... and signs off
    signer: str
    our_greeting: str  # our quoted notice greets the customer
    our_greeting_name: str
    our_closing: str


CASES = [
    Case("en", ("From", "Sent", "To", "Subject"), "Tuesday, September 29, 2026 10:48 AM",
         "Dear Ms. Keller,", "Ms. Keller", "Kind regards,", "Peter Novak", "Dear Mr. Novak,", "Mr. Novak", "Kind regards,"),
    Case("de", ("Von", "Gesendet", "An", "Betreff"), "Dienstag, 29. September 2026 10:48",
         "Sehr geehrte Frau Keller,", "Frau Keller", "Mit freundlichen Grüßen", "Jonas Weber",
         "Sehr geehrter Herr Weber,", "Herr Weber", "Mit freundlichen Grüßen"),
    Case("fr", ("De", "Envoyé", "À", "Objet"), "mardi 29 septembre 2026 10:48",
         "Bonjour Madame Keller,", "Madame Keller", "Cordialement,", "Claire Dubois",
         "Bonjour Madame Dubois,", "Madame Dubois", "Cordialement,"),
    Case("nl", ("Van", "Verzonden", "Aan", "Onderwerp"), "dinsdag 29 september 2026 10:48",
         "Geachte mevrouw Keller,", "mevrouw Keller", "Met vriendelijke groet,", "Jan de Vries",
         "Geachte heer De Vries,", "heer De Vries", "Met vriendelijke groet,"),
    Case("it", ("Da", "Inviato", "A", "Oggetto"), "martedì 29 settembre 2026 10:48",
         "Gentile Sig.ra Keller,", "Sig.ra Keller", "Cordiali saluti,", "Marco Rossi",
         "Gentile Sig. Rossi,", "Sig. Rossi", "Cordiali saluti,"),
    Case("es", ("De", "Enviado", "Para", "Asunto"), "martes, 29 de septiembre de 2026 10:48",
         "Estimada Sra. Keller:", "Sra. Keller", "Saludos cordiales,", "Lucía García",
         "Estimada Sra. García:", "Sra. García", "Saludos cordiales,"),
    Case("pt", ("De", "Enviado", "Para", "Assunto"), "terça-feira, 29 de setembro de 2026 10:48",
         "Prezada Sra. Keller,", "Sra. Keller", "Atenciosamente,", "João Silva",
         "Prezado Sr. Silva,", "Sr. Silva", "Atenciosamente,"),
    Case("pl", ("Od", "Wysłano", "Do", "Temat"), "wtorek, 29 września 2026 10:48",
         "Szanowna Pani Keller,", "Pani Keller", "Z poważaniem", "Piotr Nowak",
         "Szanowny Panie Nowak,", "Panie Nowak", "Z poważaniem"),
    Case("cs", ("Od", "Odesláno", "Komu", "Předmět"), "úterý 29. září 2026 10:48",
         "Vážená paní Kellerová,", "paní Kellerová", "S pozdravem", "Jan Novák",
         "Vážený pane Nováku,", "pane Nováku", "S pozdravem"),
    Case("sv", ("Från", "Skickat", "Till", "Ämne"), "den 29 september 2026 10:48",
         "Hej Anna,", "Anna", "Med vänliga hälsningar", "Erik Larsson", "Hej Erik,", "Erik", "Med vänliga hälsningar"),
    Case("da", ("Fra", "Sendt", "Til", "Emne"), "29. september 2026 10:48",
         "Kære Anna,", "Anna", "Med venlig hilsen", "Mette Hansen", "Kære Mette,", "Mette", "Med venlig hilsen"),
    Case("no", ("Fra", "Sendt", "Til", "Emne"), "tirsdag 29. september 2026 10:48",
         "Hei Anna,", "Anna", "Med vennlig hilsen", "Ola Nordmann", "Hei Ola,", "Ola", "Med vennlig hilsen"),
    Case("fi", ("Lähettäjä", "Lähetetty", "Vastaanottaja", "Aihe"), "tiistai 29. syyskuuta 2026 10.48",
         "Hei Anna,", "Anna", "Ystävällisin terveisin", "Matti Virtanen", "Hei Matti,", "Matti", "Ystävällisin terveisin"),
]


def _outlook_html(c: Case) -> str:
    f, s, t, subj = c.labels
    return (
        f"<html><body><div>{c.greeting}</div><div>LATEST 9000002 15.10.2026</div><div>{c.closing}</div>"
        f'<div>{c.signer}</div><hr style="display:inline-block;width:98%"><div id="divRplyFwdMsg" dir="ltr">'
        f"<font><b>{f}:</b> AR Team &lt;ar@example.com&gt;<br><b>{s}:</b> {c.sent}<br>"
        f"<b>{t}:</b> {c.signer} &lt;c@customer.example&gt;<br><b>{subj}:</b> 9000002</font><div>&nbsp;</div></div>"
        f"<div>{c.our_greeting}</div><div>QUOTED 9000002</div><div>{c.our_closing}</div><div>Anna Keller</div></body></html>"
    )


def _plain(c: Case) -> str:
    f, s, t, subj = c.labels
    return (
        f"{c.greeting}\n\nLATEST 9000002 15.10.2026\n\n{c.closing}\n{c.signer}\n\n"
        f"{f}: AR Team <ar@example.com>\n{s}: {c.sent}\n{t}: {c.signer} <c@customer.example>\n{subj}: 9000002\n\n"
        f"{c.our_greeting}\n\nQUOTED 9000002\n\n{c.our_closing}\nAnna Keller\n"
    )


def test_every_vocabulary_language_has_a_case() -> None:
    assert sorted(c.lang for c in CASES) == sorted(vocab.LANGUAGES)


@pytest.mark.parametrize("render", [_outlook_html, _plain], ids=["outlook-html", "plain-text"])
@pytest.mark.parametrize("case", CASES, ids=[c.lang for c in CASES])
def test_language(case: Case, render) -> None:
    result = parse_email_thread(render(case), AR)
    info = result["parse_info"]
    assert info["quoted_messages"] == 1
    assert info["warnings"] == []

    latest = result["latest_message"]
    assert latest["text"] == f"{case.greeting}\nLATEST 9000002 15.10.2026"
    assert latest["greeting_name"] == case.greeting_name
    assert latest["signer_name"] == case.signer
    assert result["reply_to_name"] == case.signer

    (quoted,) = result["thread_context"]
    assert quoted["from"] == "AR Team <ar@example.com>"
    assert quoted["to"] == f"{case.signer} <c@customer.example>"
    assert quoted["subject"] == "9000002"
    assert quoted["sent_at"] == SENT_ISO
    assert quoted["role"] == "recipient"
    assert quoted["greeting_name"] == case.our_greeting_name
    assert quoted["signer_name"] == "Anna Keller"
    assert quoted["body"].startswith(case.our_greeting)


# ---- languages the vocabulary does not know ------------------------------------------------- #

TURKISH_PLAIN = """Merhaba Anna,

LATEST 9000002 15.10.2026

Saygılarımla
Ali Yılmaz

Kimden: AR Team <ar@example.com>
Gönderildi: 29 Eylül 2026 Salı 10:48
Kime: Ali Yılmaz <c@customer.example>
Konu: 9000002

QUOTED 9000002
Anna Keller
"""

RUSSIAN_OUTLOOK = (
    "<html><body><div>Здравствуйте, Анна!</div><div>LATEST 9000002 15.10.2026</div><div>С уважением,</div>"
    '<div>Иван Петров</div><hr><div id="divRplyFwdMsg"><b>От:</b> AR Team &lt;ar@example.com&gt;<br>'
    "<b>Отправлено:</b> 29 сентября 2026 г. 10:48<br><b>Кому:</b> Иван Петров &lt;c@customer.example&gt;<br>"
    "<b>Тема:</b> 9000002</div><div>QUOTED 9000002</div><div>Anna Keller</div></body></html>"
)


@pytest.mark.parametrize("body", [TURKISH_PLAIN, RUSSIAN_OUTLOOK], ids=["turkish-plain", "russian-outlook"])
def test_unknown_language_still_splits(body: str) -> None:
    result = parse_email_thread(body, AR)
    assert result["parse_info"]["quoted_messages"] == 1
    assert result["latest_message"]["text"].startswith(("Merhaba", "Здравствуйте"))
    assert "QUOTED" not in result["latest_message"]["text"]
    (quoted,) = result["thread_context"]
    assert quoted["from"] == "AR Team <ar@example.com>"
    assert quoted["to"].endswith("<c@customer.example>")
    assert quoted["subject"] == "9000002"
    assert quoted["role"] == "recipient"
    assert quoted["body"].startswith("QUOTED")
    assert any("header labels not recognised" in w for w in result["parse_info"]["warnings"])


GMAIL_UNKNOWN_ATTRIBUTION = (
    '<div dir="ltr">Merhaba Anna,<br>LATEST 9000002 15.10.2026<br>Saygılarımla<br>Ali Yılmaz</div>'
    '<div class="gmail_quote"><div dir="ltr" class="gmail_attr">29.09.2026 10:48 tarihinde AR Team '
    '&lt;<a href="mailto:ar@example.com">ar@example.com</a>&gt; şunu yazdı:<br></div>'
    '<blockquote class="gmail_quote"><div dir="ltr">QUOTED 9000002<br>Anna Keller</div></blockquote></div>'
)

APPLE_NO_HEADER = (
    "<html><body><div>Hej Anna,</div><div>LATEST 9000002 15.10.2026</div><div>Mvh</div><div>Erik Larsson</div>"
    '<div><br><blockquote type="cite"><div>QUOTED 9000002</div><div>Anna Keller</div></blockquote></div></body></html>'
)


def test_gmail_attribution_in_unknown_language_is_split_by_the_html_marker() -> None:
    result = parse_email_thread(GMAIL_UNKNOWN_ATTRIBUTION, AR)
    assert result["parse_info"]["quoted_messages"] == 1
    assert "QUOTED" not in result["latest_message"]["text"]
    (quoted,) = result["thread_context"]
    assert quoted["from"] == "AR Team <ar@example.com>"
    assert quoted["sent_at"] == SENT_ISO
    assert quoted["role"] == "recipient"
    assert quoted["body"].startswith("QUOTED")


def test_html_marker_alone_splits_and_says_so() -> None:
    result = parse_email_thread(APPLE_NO_HEADER, AR)
    assert result["latest_message"]["text"] == "Hej Anna,\nLATEST 9000002 15.10.2026"
    (quoted,) = result["thread_context"]
    assert quoted["body"].startswith("QUOTED") and quoted["from"] is None
    assert any("HTML quote marker only" in w for w in result["parse_info"]["warnings"])


REMITTANCE_TABLE = """Hello,

please find our payment details:

Invoice: 9000002
Amount: EUR 425.00
Due date: 15.10.2026
Contact: ap@customer.example

Kind regards,
Peter Novak
"""


def test_a_label_value_table_in_the_body_is_not_a_quote_header() -> None:
    result = parse_email_thread(REMITTANCE_TABLE, AR)
    assert result["parse_info"]["quoted_messages"] == 0
    assert "Contact: ap@customer.example" in result["latest_message"]["text"]
