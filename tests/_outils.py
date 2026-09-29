"""Petits outils partagés par les scripts de test (importés depuis tests/)."""


def corps_texte(msg):
    """Corps texte d'un mail, simple ou multipart (le récap a aussi une part HTML)."""
    part = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    return part.get_payload(decode=True).decode("utf-8")
