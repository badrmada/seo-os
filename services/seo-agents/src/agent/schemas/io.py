import operator
from typing import Annotated, TypedDict

from .channel import Channel


class AgentInput(TypedDict, total=False):
    run_id: str              # Optional. Lets a caller supply their own correlation id
                              # instead of the auto-generated uuid4 (see
                              # agent/managers/run_manager.py's AgentRunner.run()).
    model: str                # Optional. Overrides llm_options.model for this run only —
                               # passed straight to ToolsManager.build_all() (see
                               # agent/managers/tools_manager.py's build_llm()).
    channel: Channel        # Optional. If given, always used as-is. If omitted:
                             # config.discovery_sources non-empty -> ChooseChannelStage
                             # decides it from what discover() finds (see
                             # agent/graph/stages/choose_channel.py); otherwise
                             # (the default) -> config.default_channel, same as
                             # every run before discovery existed.
    site_url: str             # Optional, and normally set once in tenant.json as
                               # config.site_url rather than per run. Given here it
                               # overrides that for this run only — the case where a
                               # caller drives several sites through one config.
                               # Vendor-neutral: a Search Console *property*
                               # identifier is not this, it is
                               # search_performance_options.gsc_domain (see
                               # tools/base.py's SearchPerformanceClient).
    seed_keyword: str          # optional fallback keyword/topic for site_article/external_article;
                                # AnalyzeStage uses it only if no striking-distance query is found
    context_text: str           # required for engagement_comment: the post/thread/question
                                 # the drafted comment replies to. May be omitted when channel is
                                 # left for ChooseChannelStage to decide and it lands on
                                 # engagement_comment — it then borrows one from the top
                                 # discovered opportunity instead.
    params: dict                # e.g. {"max_words": int, "tone": str, "platform_name": str};
                                 # read by DraftStage to shape the LLM prompt and by SelfQaStage
                                 # to check the draft. platform_name (external_article only) is a
                                 # free-text hint ("Medium", "Substack", ...) — see
                                 # agent/prompts/builder.py


class AgentState(TypedDict, total=False):
    # --- set once at run start (agent/managers/run_manager.py), untouched by graph nodes ---
    run_id: str             # correlates this run's output/logs; passed through unchanged
    agent_type: str          # constant "seo_content"; a seam for when other agent
                              # types exist and need to be routed on

    # --- mutated by graph nodes as the run progresses ---
    phase: str             # "discover"/"choose_channel" (only when
                            # config.discovery_sources is non-empty) -> "analyze" ->
                            # "draft" -> "done". Can also become "failed" mid-graph —
                            # written by DraftStage or SelfQaStage themselves when
                            # they hit something with no meaningful way to continue
                            # (an LLM call failing, a malformed draft) — the graph
                            # still runs to completion in that case (later nodes
                            # detect phase="failed" and pass it through unchanged,
                            # see agent/graph/stages/self_qa.py), it just doesn't
                            # reach "done". AgentRunner.run()'s own try/except (see
                            # agent/managers/run_manager.py) is the outermost
                            # fallback, for anything that reaches it without a stage
                            # having already turned it into phase="failed" (e.g. a
                            # bad-input validation error, before the graph even runs).
    input: AgentInput        # read-only after init; the request that started the run

    analyze_context: dict    # Only present when config.discovery_sources is non-empty
                             # (see agent/graph/pipeline.py) — AnalyzeContextStage's output
                             # ({analytics_summary, analytics_highlights, traffic_summary,
                             # signals, tool_errors}), a direct child of START run concurrently with
                             # the discover -> choose_channel chain. Single-writer (only
                             # AnalyzeContextStage sets it), so unlike discover_results this
                             # needs no Annotated merge reducer. AnalyzeStage reads it (once
                             # both branches join into the "analyze" node) instead of making
                             # its own analytics/traffic calls — see
                             # agent/graph/stages/analyze.py.

    discover_results: Annotated[list[dict], operator.add]
                             # Only touched when config.discovery_sources has more
                             # than one entry (see agent/graph/pipeline.py's
                             # _default_spec) — the "parallel_by_source" fan-out
                             # path for DiscoverStage. Each concurrently-run
                             # DiscoverSourceStage branch (agent/graph/stages/
                             # discover.py) returns a single-element list containing
                             # {"tool", "opportunities", "tool_errors"} for its
                             # source; the Annotated reducer concatenates every
                             # branch's contribution instead of one overwriting
                             # another. DiscoverJoinStage reads this, merges it into
                             # working exactly like the single-source sequential
                             # path does, and nothing downstream reads it again.
                             # Absent entirely on the sequential (0 or 1 source)
                             # path, same as discover/choose_channel being absent
                             # when discovery_sources is empty.

    working: dict           # scratch space between nodes, stripped from the final
                             # result before it's returned/printed — its
                             # opportunities/channel_decision/tool_errors keys are
                             # surfaced instead as the public `discovery` field (see
                             # agent/managers/run_manager.py's AgentRunner.run()).
                             # Keys, written in this order (discover/choose_channel
                             # only run when config.discovery_sources is non-empty).
                             # Every stage below wraps its own external calls and
                             # degrades (or, for DraftStage/SelfQaStage, fails
                             # cleanly) rather than raising — see
                             # agent/utils/tool_errors.py's record_tool_error, used
                             # by all three of DiscoverStage/AnalyzeStage/DraftStage:
                             #   DiscoverStage -> opportunities (list[Opportunity], merged
                             #                 across every configured source), tool_errors
                             #                 (list[ToolError], one per source that raised —
                             #                 see agent/schemas/opportunity.py)
                             #   ChooseChannelStage -> channel (the effective channel every
                             #                 later stage reads instead of input.channel),
                             #                 channel_decision ({chosen, reason, fallback}),
                             #                 and — only when it picked engagement_comment
                             #                 itself with no input.context_text — context_text
                             #   AnalyzeStage -> analytics_summary, analytics_highlights,
                             #                 traffic_summary, signals ({name: Signal} for every
                             #                 configured signal_sources entry that had something
                             #                 to report — see agent/schemas/signal.py; reaches
                             #                 the prompt keyed by name) — all four always, as
                             #                 growth context for DraftStage, each independently
                             #                 falling back to ""/[]/{} on its own client failure —
                             #                 folded in from state["analyze_context"] when
                             #                 AnalyzeContextStage ran concurrently with discovery,
                             #                 else collected here
                             #                 directly; for site_article/external_article also
                             #                 search_performance_rows, chosen_keyword, chosen_keyword_row;
                             #                 tool_errors gains one entry per client
                             #                 (search_performance/analytics/traffic) that failed
                             #   DraftStage  -> on success: draft (the parsed LLM JSON — shape
                             #                 depends on channel, see agent/graph/stages/draft.py).
                             #                 on failure: no draft key at all; tool_errors gains a
                             #                 "llm" entry instead, and phase/error are set (see above)
                             #   SelfQaStage -> reads working, writes nothing here on success
                             #                 (it writes the public `output` field instead); if
                             #                 draft is missing or phase is already "failed", passes
                             #                 through unchanged instead of reading working["draft"]

    output: dict      # None until SelfQaStage runs; then set to the final
                             # publishable artifact: {kind, title, content, format, metadata}
                             # (kind/metadata shape varies by channel, see
                             # agent/graph/stages/self_qa.py)

    discovery: dict          # Always present (empty/neutral shape when
                              # config.discovery_sources is empty): {"opportunities":
                              # list[Opportunity], "channel_decision": {chosen, reason,
                              # fallback} | None, "tool_errors": list[ToolError]} — what
                              # discovery found and how the channel got decided, so a
                              # caller can see *why* a run turned out the way it did,
                              # not just the final draft. Set by AgentRunner.run() from
                              # working before working is stripped.

    usage: dict             # {"tokens": int, "cost_usd": float}; DraftStage increments
                             # tokens after each LLM call, nothing else touches it

    error: str         # None on success. Set either by DraftStage/SelfQaStage
                              # themselves (a clear, specific message) when they hit
                              # something unrecoverable, or — for anything that
                              # reaches it first (bad input, an exception no stage
                              # already turned into phase="failed") — by
                              # AgentRunner.run()'s own outermost try/except
                              # (agent/managers/run_manager.py). Either way, always
                              # a plain str(exception)-shaped message, never a
                              # traceback.
