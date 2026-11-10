---------------------------- MODULE ChapterPath ----------------------------
\* A small model of one chapter's pack on disk, and of the three readers that
\* answer questions about it:
\*
\*   service.py:708  ReadingService._chapter_path   -> the badge path
\*                   (delegates to store.py:245 chapter_route)
\*   server.py:458   ReadingServer._serve_depth     -> 200 or 404
\*   audit.py:201    _audit_chapter                 -> the audit report path
\*
\* Each action below is one call that a reviewer can find in the code:
\*
\*   LegacyBuild      submit(uid, n)     writes manifest.json + chapter.html
\*   ModelOnlyBuild   submitDepth(["model"], rebuild_model=True)
\*                    writes chapter_model.json, ledger.json and state.json
\*                    with status="model"; keeps an existing chapter.html on
\*                    disk but does not render a new one (service.py:1180-1435)
\*   ReadyBuild       submitDepth([...]) with a fresh skim and an HTML layer
\*                    renders chapter.html and writes status="ready"
\*   SourceEdit       the chapter source changes; depth_state returns "stale"
\*                    (store.py:215-236) without touching any file
\*   SourceRevert     the source is restored; the old state matches again
\*
\* The model abstracts only the bytes: shaMatch says whether state.source_sha256
\* equals the current source sha. It does not model jobs, queues or retries.
\* -----------------------------------------------------------------------------
EXTENDS Naturals

VARIABLES status, html, model, ledger, legacy, shaMatch

vars == <<status, html, model, ledger, legacy, shaMatch>>

Init ==
    /\ status = "none"
    /\ html = FALSE
    /\ model = FALSE
    /\ ledger = FALSE
    /\ legacy = FALSE
    /\ shaMatch = TRUE

LegacyBuild ==
    /\ ~legacy
    /\ legacy' = TRUE
    /\ UNCHANGED <<status, html, model, ledger, shaMatch>>

ModelOnlyBuild ==
    /\ status' = "model"
    /\ html' = html
    /\ model' = TRUE
    /\ ledger' = TRUE
    /\ legacy' = legacy
    /\ shaMatch' = TRUE

ReadyBuild ==
    /\ status' = "ready"
    /\ html' = TRUE
    /\ model' = TRUE
    /\ ledger' = TRUE
    /\ legacy' = legacy
    /\ shaMatch' = TRUE

SourceEdit ==
    /\ shaMatch
    /\ shaMatch' = FALSE
    /\ UNCHANGED <<status, html, model, ledger, legacy>>

SourceRevert ==
    /\ ~shaMatch
    /\ shaMatch' = TRUE
    /\ UNCHANGED <<status, html, model, ledger, legacy>>

Next ==
    \/ LegacyBuild
    \/ ModelOnlyBuild
    \/ ReadyBuild
    \/ SourceEdit
    \/ SourceRevert

Spec == Init /\ [][Next]_vars

\* store.py:215-236, with depth_state(uid, n, source_sha) called:
\*   stale wins over everything; "ready" without chapter.html is reported as
\*   "model"; anything else is "stale".
DepthState ==
    IF status = "none" THEN "missing"
    ELSE IF ~shaMatch THEN "stale"
    ELSE IF status = "model" THEN "model"
    ELSE IF html THEN "ready"
    ELSE "model"

\* store.py:244-262 chapter_route: one rule behind all three readers.
\* "ready" is the only state that promises a page (depth_state already folds
\* "ready without html" into "model"); a manifest is the fallback; the rest
\* is absent, with the reason carried separately.
ServicePath ==
    IF DepthState = "ready" THEN "depth"
    ELSE IF legacy THEN "legacy"
    ELSE "none"

\* server.py:458-478: serves the page only when the route is depth and the
\* file is there.
Readable == DepthState = "ready" /\ html

\* audit.py:201-228: the same chapter_route call, with "none" reported as
\* "missing". The rule is spelled out a second time so this spec can catch
\* the two call sites drifting apart; the code itself shares one function.
AuditPath ==
    IF DepthState = "ready" THEN "depth"
    ELSE IF legacy THEN "legacy"
    ELSE "missing"

\* The service and the audit spell the same absence differently.
NormalizedService ==
    IF ServicePath = "none" THEN "missing" ELSE ServicePath

\* P1: a chapter advertised as depth must have a depth page.
AdvertisedDepthIsReadable ==
    ServicePath = "depth" => Readable

\* P2: the badge and the audit report must name the same path.
AuditPathMatchesService ==
    NormalizedService = AuditPath

=============================================================================
