import pytest

from email_thread_mcp.thread import parse_email_thread, parse_sent


# ---- formats not present in the test data -------------------------------------------------- #

OUTLOOK_DESKTOP_PLAIN = """Hello Ms. Keller,

Payment for invoice 9000002 will be made on 15.10.2026.

Best regards
Peter Novak
Accounts Payable, Example Trading Ltd

From: AR Team <ar@example.com>
Sent: Monday, October 5, 2026 9:15 AM
To: Peter Novak <p.novak@customer.example>
Subject: Overdue invoice 9000002

Dear Mr. Novak,

Invoice 9000002 is overdue. Could you confirm the payment date?

Kind regards,
Anna Keller
"""

GMAIL_HTML = """<div dir="ltr">Hi Anna,<div><br></div><div>We never received invoice 9000003, please send a copy.</div>
<div><br></div><div>Thanks,</div><div>Lena Brandt</div></div><br><div class="gmail_quote"><div dir="ltr" class="gmail_attr">
On Mon, Oct 5, 2026 at 9:15 AM Anna Keller &lt;ar@example.com&gt; wrote:<br></div><blockquote class="gmail_quote">
<div dir="ltr">Dear Ms. Brandt,<br>Invoice 9000003 is overdue.<br>Kind regards,<br>Anna Keller</div></blockquote></div>"""

GERMAN_OUTLOOK = """<html><body><div>Sehr geehrte Frau Keller,</div><div>die Rechnung 9000004 wird am 20.10.2026 bezahlt.</div>
<div>Mit freundlichen Grüßen</div><div>Jonas Weber</div><hr>
<div><b>Von:</b> AR Team &lt;ar@example.com&gt;<br><b>Gesendet:</b> Montag, 5. Oktober 2026 09:15<br>
<b>An:</b> Jonas Weber &lt;j.weber@kunde.example&gt;<br><b>Betreff:</b> Offene Rechnung 9000004</div>
<div>Sehr geehrter Herr Weber,</div><div>bitte bestätigen Sie den Zahlungstermin.</div><div>Mit freundlichen Grüßen</div><div>Anna Keller</div></body></html>"""

QUOTED_PLAIN = """Thanks, the remittance is attached.

Regards,
Tom Fischer

On Mon, Oct 5, 2026 at 9:15 AM, AR Team <ar@example.com> wrote:
> Dear Mr. Fischer,
> please send the remittance advice for invoice 9000005.
> Kind regards,
> Anna Keller
"""

ORIGINAL_MESSAGE = """Please see the dispute details below.

Kind regards
Maria Rossi

-----Original Message-----
From: AR Team <ar@example.com>
Sent: Monday, October 5, 2026 9:15 AM
To: Maria Rossi <m.rossi@customer.example>
Subject: Invoice 9000006

Dear Ms. Rossi,
invoice 9000006 is overdue.
Anna Keller
"""


@pytest.mark.parametrize(
    "body, fmt, signer, greeting, history_signer",
    [
        (OUTLOOK_DESKTOP_PLAIN, "plain-text", "Peter Novak", "Ms. Keller", "Anna Keller"),
        (GMAIL_HTML, "gmail-html", "Lena Brandt", "Anna", "Anna Keller"),
        (GERMAN_OUTLOOK, "outlook-html", "Jonas Weber", "Frau Keller", "Anna Keller"),
        (QUOTED_PLAIN, "plain-text", "Tom Fischer", None, "Anna Keller"),
        (ORIGINAL_MESSAGE, "plain-text", "Maria Rossi", None, None),
    ],
    ids=["outlook-desktop-plain", "gmail-html", "german-outlook", "quoted-plain", "original-message"],
)
def test_other_formats(body: str, fmt: str, signer: str, greeting: str | None, history_signer: str | None) -> None:
    result = parse_email_thread(body, ["ar@example.com"])
    assert result["parse_info"]["format"] == fmt
    assert result["parse_info"]["quoted_messages"] == 1
    assert result["latest_message"]["signer_name"] == signer
    assert result["latest_message"]["greeting_name"] == greeting
    quoted = result["thread_context"][0]
    assert quoted["role"] == "recipient"
    assert quoted["signer_name"] == history_signer
    assert "overdue" in quoted["body"] or "remittance" in quoted["body"] or "Zahlungstermin" in quoted["body"]
    assert "9000" in result["latest_message"]["text"] or "remittance" in result["latest_message"]["text"] or "dispute" in result["latest_message"]["text"]


def test_single_message_without_quotes() -> None:
    result = parse_email_thread("Dear AR team,\n\nPlease send a statement.\n\nKind regards,\nEva Lund")
    assert result["parse_info"] == {
        "format": "plain-text-no-quotes",
        "quoted_messages": 0,
        "warnings": ["no quote marker found: whole body treated as the latest message"],
    }
    assert result["latest_message"]["text"] == "Dear AR team,\nPlease send a statement."
    assert result["latest_message"]["signer_name"] == "Eva Lund"


def test_a_sentence_starting_with_thank_you_is_not_a_signoff() -> None:
    result = parse_email_thread("Hi,\nThank you for your collaboration.\nWe will pay tomorrow.\nBest,\nAna Pires")
    assert result["latest_message"]["signer_name"] == "Ana Pires"
    assert "Thank you for your collaboration." in result["latest_message"]["text"]


def test_parse_sent_formats() -> None:
    assert parse_sent("Tuesday, September 29, 2026 10:48 AM") == "2026-09-29T10:48:00"
    assert parse_sent("Montag, 5. Oktober 2026 09:15") == "2026-10-05T09:15:00"
    assert parse_sent("yesterday") is None


# ---- Gmail web thread: hand-built from Gmail's markup, NOT captured from a real mailbox --------- #
# Each reply nests the earlier thread in another blockquote; the signature sits in gmail_signature
# after "-- "; addresses are mailto links; the attribution uses the UK date form. Replace with real
# captured threads (anonymised like outlook-2026-09-29) once available.

GMAIL_NESTED = (
    '<div dir="ltr"><div>Hi Anna,</div><div><br></div><div>Payment of EUR 425.00 for invoices '
    "9000010-9000013 is scheduled for 06.10.2026.</div><div><br></div><div>Kind regards,</div></div>"
    '<br clear="all"><div><br></div><span class="gmail_signature_prefix">-- </span><br>'
    '<div dir="ltr" class="gmail_signature" data-smartmail="gmail_signature"><div dir="ltr">Lena Brandt'
    "<br>Accounts Payable, Brandt Handel GmbH</div></div><br>"
    '<div class="gmail_quote gmail_quote_container"><div dir="ltr" class="gmail_attr">On Tue, 29 Sept 2026 '
    'at 11:01, AR Team &lt;<a href="mailto:ar@example.com">ar@example.com</a>&gt; wrote:<br></div>'
    '<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;border-left:1px solid '
    'rgb(204,204,204);padding-left:1ex"><div dir="ltr">Dear Ms. Brandt,<br><br>Attached is the account '
    "statement as requested.<br><br>Kind regards,<br>Anna Keller</div><br>"
    '<div class="gmail_quote"><div dir="ltr" class="gmail_attr">On Tue, 29 Sept 2026 at 10:50, Lena Brandt '
    '&lt;<a href="mailto:l.brandt@customer.example">l.brandt@customer.example</a>&gt; wrote:<br></div>'
    '<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;border-left:1px solid '
    'rgb(204,204,204);padding-left:1ex"><div dir="ltr">Dear Ms. Keller,<br><br>Could you send a full '
    "account statement?<br><br>Kind regards,<br>Lena Brandt</div></blockquote></div></blockquote></div>"
)


def _gmail() -> dict:
    return parse_email_thread(GMAIL_NESTED, ["ar@example.com"])


def test_gmail_nested_thread_is_split_oldest_first() -> None:
    result = _gmail()
    assert result["parse_info"]["format"] == "gmail-html"
    assert result["parse_info"]["quoted_messages"] == 2
    assert result["latest_message"]["text"] == (
        "Hi Anna,\nPayment of EUR 425.00 for invoices 9000010-9000013 is scheduled for 06.10.2026."
    )
    history = result["thread_context"]
    assert [m["signer_name"] for m in history] == ["Lena Brandt", "Anna Keller"]
    assert history[1]["role"] == "recipient"


@pytest.mark.xfail(strict=True, reason="TODO: read the signer from the gmail_signature block after '-- '")
def test_gmail_signer_is_read_from_the_gmail_signature() -> None:
    result = _gmail()
    assert result["latest_message"]["signer_name"] == "Lena Brandt"
    assert result["reply_to_name"] == "Lena Brandt"


@pytest.mark.xfail(strict=True, reason="TODO: an attribution line has no To:, so a non-AR From: is the customer")
def test_gmail_customer_message_is_sender() -> None:
    assert _gmail()["thread_context"][0]["role"] == "sender"


def test_gmail_attribution_date_is_parsed() -> None:
    assert [m["sent_at"] for m in _gmail()["thread_context"]] == ["2026-09-29T10:50:00", "2026-09-29T11:01:00"]


# ---- form of address and our signature ------------------------------------------------------ #

OVERDUE_TABLE_REPLY = """<html><body><div>Dear Ms. Weiss,</div><div>Here is our position on each:</div>
<ul><li>9000011: Payment already sent on 02.10.2026.</li><li>9000012: We never received this invoice, please send a copy.</li></ul>
<div>Best regards,</div><div>Jan Berg</div><div>Accounts Payable, Example Parts AB</div>
<hr><div id="divRplyFwdMsg"><b>From:</b> AR Desk &lt;ar-desk@example.com&gt;<br><b>Sent:</b> Monday, October 5, 2026 9:15 AM<br>
<b>To:</b> Jan Berg &lt;j.berg@customer.example&gt;<br><b>Subject:</b> Overdue invoices</div>
<div>Dear Mr. Berg,</div><div>Please find below the open invoices on your account.</div>
<table><tr><td>Invoice No.</td><td>Amount (EUR)</td></tr><tr><td>9000011</td><td>100.00</td></tr><tr><td>9000012</td><td>100.00</td></tr></table>
<div>Yours sincerely,</div><div>Clara Weiss</div><div>Finance Department</div><div>Example Trading GmbH</div>
<div>+49 30 1234 5678</div><div>clara.weiss@example.com</div><div>www.example.com</div></body></html>"""


def test_reply_to_greeting_reuses_the_honorific_our_message_used() -> None:
    result = parse_email_thread(OVERDUE_TABLE_REPLY)
    assert result["reply_to_name"] == "Jan Berg"
    assert result["reply_to_greeting"] == "Mr. Berg"


def test_our_signature_is_the_block_of_the_person_the_customer_greets() -> None:
    assert parse_email_thread(OVERDUE_TABLE_REPLY)["our_signature"] == "Clara Weiss\nFinance Department\nExample Trading GmbH"


@pytest.mark.parametrize(
    "body, greeting, signature",
    [
        (OUTLOOK_DESKTOP_PLAIN, "Mr. Novak", "Anna Keller"),
        (GERMAN_OUTLOOK, "Herr Weber", "Anna Keller"),
        (QUOTED_PLAIN, "Mr. Fischer", None),  # the latest message greets nobody: ours is not identified
        (ORIGINAL_MESSAGE, "Ms. Rossi", None),  # our message has no closing line, so no signature block
    ],
    ids=["outlook-desktop-plain", "german-outlook", "quoted-plain", "original-message"],
)
def test_greeting_and_signature_on_other_formats(body: str, greeting: str, signature: str | None) -> None:
    result = parse_email_thread(body)
    assert result["reply_to_greeting"] == greeting
    assert result["our_signature"] == signature


def test_no_honorific_is_added_that_no_message_states() -> None:
    body = OVERDUE_TABLE_REPLY.replace("Dear Mr. Berg,", "Hello,")
    assert parse_email_thread(body)["reply_to_greeting"] == "Jan Berg"


def test_a_customer_message_is_never_ours() -> None:
    result = parse_email_thread(OVERDUE_TABLE_REPLY, ar_mailboxes=["j.berg@customer.example"])
    assert result["thread_context"][0]["role"] == "sender"
    assert result["our_signature"] is None
    assert result["reply_to_greeting"] == "Jan Berg"


def test_ar_mailboxes_identify_our_message_without_a_greeting() -> None:
    result = parse_email_thread(QUOTED_PLAIN, ar_mailboxes=["ar@example.com"])
    assert result["our_signature"] == "Anna Keller"


def test_our_signature_is_the_fullest_block_of_our_person() -> None:
    follow_up = """<html><body><div>Dear Ms. Weiss,</div><div>9000012 will be paid in our next cycle.</div>
<div>Best regards,</div><div>Jan Berg</div>
<hr><div id="divRplyFwdMsg"><b>From:</b> AR Desk &lt;ar-desk@example.com&gt;<br><b>Sent:</b> Wednesday, October 7, 2026 10:00 AM<br>
<b>To:</b> Jan Berg &lt;j.berg@customer.example&gt;<br><b>Subject:</b> RE: Overdue invoices</div>
<div>Dear Mr. Berg,</div><div>Could you confirm the payment date for 9000012?</div>
<div>Kind regards,</div><div>Clara Weiss</div>
<hr><div id="divRplyFwdMsg"><b>From:</b> Jan Berg &lt;j.berg@customer.example&gt;<br><b>Sent:</b> Tuesday, October 6, 2026 8:30 AM<br>
<b>To:</b> AR Desk &lt;ar-desk@example.com&gt;<br><b>Subject:</b> RE: Overdue invoices</div>"""
    body = follow_up + OVERDUE_TABLE_REPLY.removeprefix("<html><body>")
    result = parse_email_thread(body)
    assert [m["signer_name"] for m in result["thread_context"]][-1] == "Clara Weiss"
    assert result["our_signature"] == "Clara Weiss\nFinance Department\nExample Trading GmbH"
