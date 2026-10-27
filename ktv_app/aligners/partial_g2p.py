"""Keep convertible context around reported pronunciation failures, in source order."""


def convert_parts(text, convert, error_type, depth=0):
    """Return (word, omitted_text, reason) records without replacing displayed lyrics."""
    try:
        return [(word, None, None) for word in convert(text)]
    except error_type as error:
        tokens = [
            t for t in getattr(error, "unconverted_tokens", []) if t and t in text
        ]
        if not tokens or depth >= 16:
            return [(None, text, str(error))]
        # Partition at the earliest reported unknown, preserving source order,
        # including repeats; remaining phrases retain all available context.
        token = min(tokens, key=lambda t: text.find(t))
        at = text.find(token)
        result = []
        if text[:at].strip():
            result.extend(convert_parts(text[:at], convert, error_type, depth + 1))
        result.append((None, token, str(error)))
        if text[at + len(token) :].strip():
            result.extend(
                convert_parts(text[at + len(token) :], convert, error_type, depth + 1)
            )
        return result
