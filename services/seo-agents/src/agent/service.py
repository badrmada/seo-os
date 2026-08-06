"""The channel-agnostic entry point: one request in, one result out.

    channels (CLI · HTTP API · queue worker · scheduler)   <- thin adapters
            |  RunRequest
    AgentService.execute()                                 <- you are here
            |
    AgentRunner.arun()                                     <- the pipeline

Everything a run needs doing *around* the pipeline — resolve the tenant's config,
build the tools, build a reporter, run, emit to the output sinks, keep the state
snapshots — used to live inline in the CLI's `run` command. That made the CLI the
only way to run the agent: an HTTP handler or a queue worker would have had to
copy it, and the copy would have drifted.

This layer owns that sequence once. Nothing here prints, exits, or reads
`sys.argv`: it returns a `RunResult` and lets the channel decide what to do with
it. The CLI is now one adapter among several — it calls `execute()` and prints
what comes back.

**Still out of scope:** the queue, the worker pool, the HTTP framework, the
scheduler. This makes the agent *callable* by them. Owning the transport is the
control plane's job.

Concurrency model (see docs/architecture.md): runs are async and share nothing.
`aexecute()` is the real entry point, so a server can run many tenants' requests
in one process with `asyncio.gather`; tools, reporter, and state are built per
request and belong to that request alone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

from state.memory_store import InMemoryStateStore

from .config.workspace import TenantWorkspace
from .managers.output_manager import PROCESS_STDERR, OutputManager
from .managers.run_manager import AgentRunner
from .observability import build_reporter

__all__ = ["RunRequest", "RunResult", "AgentService", "RunRequestError"]


class RunRequestError(ValueError):
    """A request that can't be run as asked — an unknown tenant, a config that
    won't load, a misconfigured sink. Raised (not returned as a failed run)
    because nothing was attempted: there is no run to report the failure *of*, and
    a channel needs to tell "your request was wrong" apart from "the run failed",
    which are a 4xx and a 200 respectively.
    """


@dataclass
class RunRequest:
    """One run, described. Every field is optional except a way to get a config.

    **Where the config comes from:** `tenant` (a name resolved in the workspace,
    what an API request carries) or `config` (an already-loaded AgentConfig).
    Exactly one. The CLI passes `config` because it has already opened the
    workspace to find `--input` and `--output`, and re-resolving it here would
    both duplicate the work and let two error messages exist for one failure.

    **Per-run overrides** apply to this run only and never mutate the tenant's
    stored config — a request is not a config edit. `input` is the run input
    documented in agent/schemas/io.py's AgentInput.
    """

    tenant: str = ""
    userdata: str = ""          # workspace root override; only meaningful with `tenant`
    config: object = None       # an AgentConfig, if the caller already has one
    input: dict = field(default_factory=dict)

    # --- per-run overrides ---
    # Which pipeline to run — "" uses the tenant config's own `agent_type`. This is
    # a request field rather than an AgentInput one because it selects *which
    # agent*, not what to write about: the input describes the job, this picks who
    # does it. An agent type the config has no pipeline for is a RunRequestError,
    # not a failed run — nothing was attempted.
    agent_type: str = ""
    verbose: int | None = None      # None = use the config's own setting
    verbose_format: str = ""        # "" = use the config's own setting
    quiet: bool = False             # force verbose off, whatever the config says
    output_sinks: list[dict] | None = None  # replaces the configured sinks entirely
    run_timeout_seconds: float | None = None

    # --- how the run is observed ---
    collect_events: bool = False    # keep the events on the RunResult
    on_event: Callable[[dict], None] | None = None  # ...and/or hand each one over live
    verbose_stream: object = None   # where a streaming reporter writes; None = stderr

    # --- which of the process's file descriptors this run may write to ---
    # The defaults are what a CLI wants; a server passing its own streams (or
    # None) is how "the library must not print into my process" is expressed. The
    # result and the failed sink names come back on RunResult either way, so
    # silencing these loses nothing a caller can't see.
    stdout: object = None                   # None = the process's stdout (JsonOutputSink)
    warn_stream: object = PROCESS_STDERR    # None = never warn to a file descriptor

    def __post_init__(self) -> None:
        if bool(self.tenant) == (self.config is not None):
            raise RunRequestError("a RunRequest needs exactly one of `tenant` or `config`")


@dataclass
class RunResult:
    """What a channel gets back. Returned, never printed.

    `run` is the full result documented in docs/output-schema.md — including the
    `phase="failed"` shape, since a failed *run* is a successful *request*.
    `events` is what the reporter recorded (empty unless the request asked for
    them). `failed_sinks` names the sinks that couldn't deliver, which is
    otherwise invisible to a caller that isn't watching stderr.
    """

    run: dict
    events: list[dict] = field(default_factory=list)
    failed_sinks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.run.get("phase") != "failed"

    @property
    def run_id(self) -> str:
        return self.run.get("run_id", "")


class AgentService:
    """Runs a `RunRequest`. Stateless — hold one, or build one per call.

    Nothing is cached between runs, deliberately: tools are rebuilt per request.
    A per-tenant client cache is the obvious throughput win and the obvious data
    race, and isn't worth the complexity until something measures it.
    """

    async def aexecute(self, request: RunRequest) -> RunResult:
        """The real entry point. Everything a run needs, in order, once.

        Never raises for a failed *run* — that comes back as `RunResult` with
        `run["phase"] == "failed"`, exactly as `AgentRunner.arun()` guarantees.
        It does raise `RunRequestError` for a request that couldn't be started at
        all (unknown tenant, unloadable config, misconfigured sink), because
        that's a different thing and a channel maps it differently.
        """
        config = self._resolve_config(request)
        if request.agent_type:
            self._select_agent_type(config, request.agent_type)
        if request.output_sinks is not None:
            # A one-off destination for this run, replacing whatever the tenant
            # configured. Applied to the loaded copy, never written back.
            config.output_sinks = request.output_sinks
        if request.run_timeout_seconds is not None:
            config.run_timeout_seconds = request.run_timeout_seconds

        reporter = self._build_reporter(request, config)

        # Sinks are built before the run, not after: a broken sink config should
        # fail now rather than once a full pipeline has spent real LLM calls.
        try:
            sinks = OutputManager(
                config, reporter=reporter,
                stdout=request.stdout,
                warn_stream=request.warn_stream,
            )
        except Exception as exc:  # noqa: BLE001 - a misconfigured sink is a request error
            raise RunRequestError(f"output sink configuration: {exc}") from exc

        store = InMemoryStateStore()
        run = await AgentRunner(config, reporter=reporter).arun(request.input, state_store=store)
        failed_sinks = await sinks.aemit(run)

        return RunResult(
            run=run,
            events=list(getattr(reporter, "events", ())),
            failed_sinks=failed_sinks,
        )

    def execute(self, request: RunRequest) -> RunResult:
        """Sync wrapper around aexecute(), for callers with no event loop of their
        own — the CLI, a script, a test. Not usable from inside a running loop;
        anything already async awaits aexecute() directly."""
        return asyncio.run(self.aexecute(request))

    # -- the pieces, each small enough to be worth naming ---------------------

    def _resolve_config(self, request: RunRequest):
        """A tenant name becomes a loaded config. The loader validates the
        prompt/analytics/traffic templates on the way through, so a template that
        can't render fails here rather than mid-run."""
        if request.config is not None:
            return request.config
        try:
            workspace = TenantWorkspace.open(request.tenant, root=request.userdata or None)
        except Exception as exc:  # noqa: BLE001 - unknown/invalid tenant name
            raise RunRequestError(str(exc)) from exc
        try:
            return workspace.load_config()
        except Exception as exc:  # noqa: BLE001 - any load failure is a request error
            raise RunRequestError(f"could not load {workspace.config_path}: {exc}") from exc

    def _select_agent_type(self, config, agent_type: str) -> None:
        """Apply a per-run `--agent`, on the loaded copy and never written back.

        An agent type the config has no pipeline for is raised as a
        RunRequestError rather than left to fail the run, for the same reason an
        unknown tenant is: nothing was attempted, and a channel needs to tell "your
        request was wrong" apart from "the run failed".
        """
        from .graph.pipeline import agent_types

        available = agent_types(config)
        if agent_type not in available:
            raise RunRequestError(
                f"unknown agent type {agent_type!r} (available: {', '.join(available)})"
            )
        config.agent_type = agent_type

    def _build_reporter(self, request: RunRequest, config):
        """Verbosity precedence, in one place: `quiet` wins over everything, then
        an explicit `verbose`, then the tenant config's own setting.

        `verbose` can't express "off" from a CLI count flag (0 is also "not
        passed"), which is exactly why `quiet` exists — otherwise a tenant with
        verbose enabled in config would have no way to silence a single run.
        """
        if request.quiet:
            level = 0
        elif request.verbose is not None:
            level = request.verbose or getattr(config, "verbose", 0)
        else:
            level = getattr(config, "verbose", 0)
        return build_reporter(
            level,
            request.verbose_format or getattr(config, "verbose_format", "text"),
            stream=request.verbose_stream,
            collect=request.collect_events,
            on_event=request.on_event,
        )
