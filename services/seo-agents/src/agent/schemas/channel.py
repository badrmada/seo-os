from enum import Enum


class Channel(str, Enum):
    """What kind of content a run produces. Same analyze->draft->self_qa pipeline,
    three different prompts/checks (agent/prompts/builder.py, agent/graph/stages/)
    depending on which one is picked. The single source of truth for these three
    values — every other module imports this instead of repeating the literal
    string, so renaming or adding a channel means changing it here only.

    Subclasses `str` on purpose: a Channel member IS its string value (equality,
    hashing, dict lookups, and JSON serialization all just work), so existing code
    comparing `channel == "engagement_comment"` or indexing a plain-string-keyed
    dict with a Channel member keeps working unchanged.
    """

    SITE_ARTICLE = "site_article"  # long-form SEO article for your own site, GSC-keyword-driven
    EXTERNAL_ARTICLE = "external_article"  # article for anywhere that isn't your own site
    # (Medium, Substack, a partner blog, ...) — deliberately not one channel per
    # platform, since the system can't hardcode every platform that exists. Which
    # platform it's for is a free-text params.platform_name the caller supplies
    # (see agent/prompts/builder.py), not a separate Channel value.
    ENGAGEMENT_COMMENT = "engagement_comment"  # a short, genuine reply to an existing
    # post/thread (context_text), never keyword/GSC-driven
