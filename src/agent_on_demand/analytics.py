import posthog


def capture(user, event: str, properties: dict | None = None) -> None:
    """Identify-then-capture inside a posthog context manager.

    Centralized so a future change to identification (e.g. adding org_id) lands
    in one place instead of every callsite.
    """
    with posthog.new_context():
        posthog.identify_context(str(user.id))
        posthog.capture(event, properties=properties)
