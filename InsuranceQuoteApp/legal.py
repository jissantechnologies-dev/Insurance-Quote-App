"""Public legal pages (Privacy Policy and Terms & Conditions).

These two pages must stay reachable without logging in: Meta's business
verification for bulk messaging (WhatsApp Business / SMS) checks that the
policy URLs load for an anonymous visitor.

Edit the COMPANY_* constants below to match the registered business
details before submitting the URLs to Meta.
"""

from html import escape

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title} - Gravity Insurance</title>
    <link rel="stylesheet" href="/style.css" />
</head>
<body>
    <div class="page-shell auth-shell legal-shell">
        <header class="hero auth-hero">
            <p class="eyebrow">Insurance Dashboard</p>
            <h1>Gravity Insurance</h1>
            <p class="subheading">{title}</p>
        </header>
        <div class="card auth-card legal-card">
            {content}
        </div>
    </div>
</body>
</html>"""

COMPANY_NAME = "Gravity Insurance"
COMPANY_LEGAL_NAME = "Gravity Insurance"
COMPANY_ADDRESS = "Kerala, India"
COMPANY_EMAIL = "support@gravityinsurance.in"
COMPANY_PHONE = "+91 00000 00000"
COMPANY_WEBSITE = "https://app.gravityinsurance.in"
LAST_UPDATED = "3 September 2026"

PRIVACY_PATH = "/privacy-policy"
TERMS_PATH = "/terms-and-conditions"


def _section(heading, *paragraphs):
        body = "".join(p if p.startswith("<") else f"<p>{p}</p>" for p in paragraphs)
        return f"<h3>{escape(heading)}</h3>{body}"


def _list(*items):
        return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def _contact_block():
        return (
                "<p><strong>" + escape(COMPANY_LEGAL_NAME) + "</strong><br />"
                + escape(COMPANY_ADDRESS) + "<br />"
                'Email: <a href="mailto:' + escape(COMPANY_EMAIL) + '">' + escape(COMPANY_EMAIL) + "</a><br />"
                "Phone: " + escape(COMPANY_PHONE) + "<br />"
                'Website: <a href="' + escape(COMPANY_WEBSITE) + '">' + escape(COMPANY_WEBSITE) + "</a></p>"
        )


def _footer_links(other_path, other_label):
        return (
                '<p class="auth-switch">'
                f'<a href="{other_path}">{escape(other_label)}</a> &nbsp;&middot;&nbsp; '
                '<a href="/login">Back to login</a></p>'
        )


def build_privacy_policy_content():
        company = escape(COMPANY_NAME)
        return (
                "<h2>Privacy Policy</h2>"
                f'<p class="auth-note">Last updated: {escape(LAST_UPDATED)}</p>'
                + _section(
                        "1. Who we are",
                        f"{company} is an insurance advisory and policy servicing business based in India. "
                        "This policy explains what personal information we collect from our customers and "
                        "prospective customers, how we use it, and the choices you have. It applies to this "
                        "application and to the messages we send you over SMS and WhatsApp.",
                )
                + _section(
                        "2. Information we collect",
                        "We collect only the information needed to prepare quotes, issue and renew policies, and keep you informed:",
                        _list(
                                "<strong>Contact details</strong> &mdash; name, mobile number, and email address.",
                                "<strong>Policy and vehicle details</strong> &mdash; vehicle registration number, "
                                "policy number, insurer, premium, and expiry date.",
                                "<strong>Documents you share with us</strong> &mdash; RC book, previous policy copy, "
                                "Aadhaar and PAN copies, uploaded by you or by our staff on your behalf so that the "
                                "insurer can process your policy.",
                                "<strong>Communication records</strong> &mdash; quotes, payment links, and renewal "
                                "reminders we have sent you, and their delivery status.",
                        ),
                        "We do not collect information from children, and we do not buy contact lists from third parties.",
                )
                + _section(
                        "3. How we use your information",
                        _list(
                                "To prepare and send insurance quotes and payment links you have asked for.",
                                "To issue, renew, and service your insurance policies with the insurer you choose.",
                                "To send you policy expiry and renewal reminders.",
                                "To respond to your questions and provide claim assistance.",
                                "To meet our legal, regulatory, and record-keeping obligations under Indian law.",
                        ),
                )
                + _section(
                        "4. SMS and WhatsApp messaging",
                        "We send transactional and service messages &mdash; quotes, payment links, policy documents, "
                        "and renewal reminders &mdash; over SMS and WhatsApp to the mobile number you gave us.",
                        _list(
                                "<strong>Consent.</strong> We message you only after you have shared your mobile number "
                                "with us for an insurance enquiry, policy, or renewal, or have otherwise opted in.",
                                "<strong>Opt out.</strong> You can stop these messages at any time by replying "
                                "<strong>STOP</strong> to any message, or by writing to us at "
                                f'<a href="mailto:{escape(COMPANY_EMAIL)}">{escape(COMPANY_EMAIL)}</a>. '
                                "We act on opt-out requests promptly.",
                                "<strong>Message content.</strong> We do not sell your number, and we do not use it "
                                "for unrelated third-party advertising.",
                                "Standard carrier message and data rates may apply.",
                        ),
                )
                + _section(
                        "5. Sharing your information",
                        "We share your information only with:",
                        _list(
                                "<strong>Insurance companies and their authorised intermediaries</strong>, to obtain "
                                "quotes and to issue or renew your policy.",
                                "<strong>Payment gateways</strong>, to process premium payments you initiate.",
                                "<strong>Messaging and hosting providers</strong> (including Meta Platforms for "
                                "WhatsApp messaging and our SMS provider) that deliver our messages and host this "
                                "application on our behalf.",
                                "<strong>Regulators, courts, and law enforcement</strong>, where we are required to do so by law.",
                        ),
                        "We never sell your personal information.",
                )
                + _section(
                        "6. Data retention",
                        "We keep your information for as long as you remain our customer and afterwards for the period "
                        "required by insurance and tax record-keeping rules in India. Documents you upload are deleted "
                        "on request once we no longer need them for an active policy or a legal obligation.",
                )
                + _section(
                        "7. Security",
                        "Access to this application is restricted to approved staff accounts protected by passwords, "
                        "and the site is served over HTTPS. Uploaded documents are stored outside the public web root "
                        "and are served only to signed-in staff. No system is perfectly secure, but we take reasonable "
                        "technical and organisational measures to protect your data.",
                )
                + _section(
                        "8. Your rights",
                        "You may ask us to give you a copy of the personal information we hold about you, correct it if "
                        "it is wrong, delete it where we are not required to keep it, or stop sending you messages. "
                        f'Write to <a href="mailto:{escape(COMPANY_EMAIL)}">{escape(COMPANY_EMAIL)}</a> and we will '
                        "respond within 30 days.",
                )
                + _section(
                        "9. Changes to this policy",
                        "If we change this policy we will update the date at the top of this page. Please check back "
                        "from time to time.",
                )
                + _section("10. Contact us", _contact_block())
                + _footer_links(TERMS_PATH, "Terms and Conditions")
        )


def build_terms_content():
        company = escape(COMPANY_NAME)
        return (
                "<h2>Terms and Conditions</h2>"
                f'<p class="auth-note">Last updated: {escape(LAST_UPDATED)}</p>'
                + _section(
                        "1. Acceptance of these terms",
                        "By using this application, or by receiving quotes, payment links, and reminders from "
                        f"{company}, you agree to these terms. If you do not agree, please stop using the service and "
                        "tell us to stop messaging you.",
                )
                + _section(
                        "2. About our service",
                        f"{company} helps customers compare, buy, and renew insurance policies. We act as an "
                        "intermediary: the insurance contract itself is between you and the insurance company that "
                        "issues your policy. Cover, exclusions, claim decisions, and refunds are governed by that "
                        "insurer's policy wording.",
                )
                + _section(
                        "3. Accounts",
                        "Accounts in this application are for authorised staff only. New registrations must be approved "
                        "by an administrator. You are responsible for keeping your password confidential and for all "
                        "activity under your account, and you must tell us immediately if you suspect misuse.",
                )
                + _section(
                        "4. Quotes and premiums",
                        "Quotes are indicative and are based on the details you provide. The final premium is set by the "
                        "insurer and may change if the vehicle, policy, or declared details differ. A quote is not a "
                        "confirmation of cover: cover begins only when the insurer accepts your proposal, the premium is "
                        "received, and the policy is issued.",
                )
                + _section(
                        "5. Payments",
                        "Premium payments are made through the payment link we send you, which is processed by the "
                        "insurer or by a licensed payment gateway. Never share your card details, CVV, OTP, or "
                        "passwords with anyone claiming to be from our team. Refunds and cancellations follow the "
                        "insurer's policy terms and IRDAI regulations.",
                )
                + _section(
                        "6. Messaging",
                        "By giving us your mobile number you agree to receive service messages from us over SMS and "
                        "WhatsApp about your enquiries, quotes, payments, policies, and renewals. You can opt out at "
                        "any time by replying <strong>STOP</strong> or by writing to "
                        f'<a href="mailto:{escape(COMPANY_EMAIL)}">{escape(COMPANY_EMAIL)}</a>. Opting out of service '
                        "messages may mean you miss renewal reminders.",
                )
                + _section(
                        "7. Your responsibilities",
                        _list(
                                "Give us accurate and complete information; insurers can reject a claim if declared details are wrong.",
                                "Upload only documents that belong to you or that you are authorised to share.",
                                "Do not misuse the service, attempt to gain unauthorised access, or interfere with its operation.",
                        ),
                )
                + _section(
                        "8. Intellectual property",
                        f"This application, its content, and its branding belong to {company} and may not be copied or "
                        "reused without our written permission.",
                )
                + _section(
                        "9. Limitation of liability",
                        "To the extent permitted by law, we are not liable for indirect or consequential losses, for "
                        "decisions taken by an insurer, or for service interruptions outside our reasonable control. "
                        "Nothing in these terms excludes liability that cannot be excluded by law.",
                )
                + _section(
                        "10. Governing law",
                        "These terms are governed by the laws of India, and the courts of Kerala, India have exclusive "
                        "jurisdiction over any dispute.",
                )
                + _section("11. Contact us", _contact_block())
                + _footer_links(PRIVACY_PATH, "Privacy Policy")
        )


def render_privacy_policy_page():
        return PAGE_TEMPLATE.format(
                title="Privacy Policy", content=build_privacy_policy_content()
        )


def render_terms_page():
        return PAGE_TEMPLATE.format(
                title="Terms and Conditions", content=build_terms_content()
        )
