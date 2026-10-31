Yes—a custom Lean agent for Prove2Me is a strong use case for the Claude Agent SDK. For that goal, I would start with a small Python controller, the official Claude runtime, Lean-specific tools, and a Prove2Me API adapter.

Prove2Me already supports agents through an official workspace repository and documented API. Its documentation explicitly supports agents with filesystem and network access, so you would be building on an existing integration surface. prove2me/prove2me_workspace · GitHub

The key choice is whether you want an agent that produces useful Lean proofs, or a research system for experimenting with proof-search algorithms. I would build those somewhat differently.
1. The architecture I would choose
For an agent whose job is to solve Prove2Me missions, my proposed architecture is:
Component	Implementation I would choose	Responsibility
Controller	Your Python code	Select targets, allocate attempts, manage workspaces, save progress, and decide when to submit.
Proof-writing worker	Claude Agent SDK	Develop mathematical approaches, write Lean code, and repair failed attempts.
Lean interface	lean-lsp-mcp, plus local builds	Expose goals, diagnostics, declaration lookup, and proof checks.
Project environment	Lean and Mathlib pinned to the target	Make local results correspond to the platform’s environment.
Prove2Me adapter	A small typed HTTP client	Retrieve statements and dependencies, submit solutions, and track verdicts.
Experiment log	SQLite or structured JSON records	Preserve candidates, errors, versions, timings, and results for comparison.


The SDK supplies Claude Code’s existing agent loop and lets you attach custom tools through MCP. Your controller can therefore concentrate on theorem-proving workflow, while the official runtime handles the model/tool conversation. Claude Code Docs

I would organize the workflow like this:
#chatgpt-mermaid-_r_1v8_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(237, 237, 237);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-_r_1v8_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-_r_1v8_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-_r_1v8_ .error-icon{fill:rgb(48, 48, 48);}#chatgpt-mermaid-_r_1v8_ .error-text{fill:rgb(237, 237, 237);stroke:rgb(237, 237, 237);}#chatgpt-mermaid-_r_1v8_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-_r_1v8_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-_r_1v8_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-_r_1v8_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-_r_1v8_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-_r_1v8_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-_r_1v8_ .marker{fill:rgb(175, 175, 175);stroke:rgb(175, 175, 175);}#chatgpt-mermaid-_r_1v8_ .marker.cross{stroke:rgb(175, 175, 175);}#chatgpt-mermaid-_r_1v8_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-_r_1v8_ p{margin:0;}#chatgpt-mermaid-_r_1v8_ .label{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";color:rgb(237, 237, 237);}#chatgpt-mermaid-_r_1v8_ .cluster-label text{fill:rgb(237, 237, 237);}#chatgpt-mermaid-_r_1v8_ .cluster-label span{color:rgb(237, 237, 237);}#chatgpt-mermaid-_r_1v8_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-_r_1v8_ .label text,#chatgpt-mermaid-_r_1v8_ span{fill:rgb(237, 237, 237);color:rgb(237, 237, 237);}#chatgpt-mermaid-_r_1v8_ .node rect,#chatgpt-mermaid-_r_1v8_ .node circle,#chatgpt-mermaid-_r_1v8_ .node ellipse,#chatgpt-mermaid-_r_1v8_ .node polygon,#chatgpt-mermaid-_r_1v8_ .node path{fill:rgb(19, 33, 54);stroke:rgb(31, 78, 148);stroke-width:1px;}#chatgpt-mermaid-_r_1v8_ .rough-node .label text,#chatgpt-mermaid-_r_1v8_ .node .label text,#chatgpt-mermaid-_r_1v8_ .image-shape .label,#chatgpt-mermaid-_r_1v8_ .icon-shape .label{text-anchor:middle;}#chatgpt-mermaid-_r_1v8_ .node .katex path{fill:#000;stroke:#000;stroke-width:1px;}#chatgpt-mermaid-_r_1v8_ .rough-node .label,#chatgpt-mermaid-_r_1v8_ .node .label,#chatgpt-mermaid-_r_1v8_ .image-shape .label,#chatgpt-mermaid-_r_1v8_ .icon-shape .label{text-align:center;}#chatgpt-mermaid-_r_1v8_ .node.clickable{cursor:pointer;}#chatgpt-mermaid-_r_1v8_ .root .anchor path{fill:rgb(175, 175, 175)!important;stroke-width:0;stroke:rgb(175, 175, 175);}#chatgpt-mermaid-_r_1v8_ .arrowheadPath{fill:rgb(175, 175, 175);}#chatgpt-mermaid-_r_1v8_ .edgePath .path{stroke:rgb(175, 175, 175);stroke-width:1px;}#chatgpt-mermaid-_r_1v8_ .flowchart-link{stroke:rgb(175, 175, 175);fill:none;}#chatgpt-mermaid-_r_1v8_ .edgeLabel{background-color:rgb(14, 14, 14);text-align:center;}#chatgpt-mermaid-_r_1v8_ .edgeLabel p{background-color:rgb(14, 14, 14);}#chatgpt-mermaid-_r_1v8_ .edgeLabel rect{opacity:0.5;background-color:rgb(14, 14, 14);fill:rgb(14, 14, 14);}#chatgpt-mermaid-_r_1v8_ .labelBkg{background-color:rgba(14, 14, 14, 0.5);}#chatgpt-mermaid-_r_1v8_ .cluster rect{fill:rgb(48, 48, 48);stroke:rgba(255, 255, 255, 0.15);stroke-width:1px;}#chatgpt-mermaid-_r_1v8_ .cluster text{fill:rgb(237, 237, 237);}#chatgpt-mermaid-_r_1v8_ .cluster span{color:rgb(237, 237, 237);}#chatgpt-mermaid-_r_1v8_ div.mermaidTooltip{position:absolute;text-align:center;max-width:200px;padding:2px;font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:12px;background:rgb(48, 48, 48);border:1px solid rgba(255, 255, 255, 0.15);border-radius:2px;pointer-events:none;z-index:100;}#chatgpt-mermaid-_r_1v8_ .flowchartTitleText{text-anchor:middle;font-size:18px;fill:rgb(237, 237, 237);}#chatgpt-mermaid-_r_1v8_ rect.text{fill:none;stroke-width:0;}#chatgpt-mermaid-_r_1v8_ .icon-shape,#chatgpt-mermaid-_r_1v8_ .image-shape{background-color:rgb(14, 14, 14);text-align:center;}#chatgpt-mermaid-_r_1v8_ .icon-shape p,#chatgpt-mermaid-_r_1v8_ .image-shape p{background-color:rgb(14, 14, 14);padding:2px;}#chatgpt-mermaid-_r_1v8_ .icon-shape .label rect,#chatgpt-mermaid-_r_1v8_ .image-shape .label rect{opacity:0.5;background-color:rgb(14, 14, 14);fill:rgb(14, 14, 14);}#chatgpt-mermaid-_r_1v8_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-_r_1v8_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-_r_1v8_ .node .neo-node{stroke:rgb(31, 78, 148);}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].node rect,#chatgpt-mermaid-_r_1v8_ [data-look="neo"].cluster rect,#chatgpt-mermaid-_r_1v8_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-_r_1v8_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-_r_1v8_-gradient);stroke-width:1px;}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].node .neo-line path{stroke:rgb(31, 78, 148);filter:none;}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-_r_1v8_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-_r_1v8_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-_r_1v8_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-_r_1v8_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-_r_1v8_ .node text{font-size:14px;font-weight:600;letter-spacing:normal;fill:rgb(232, 232, 232);}#chatgpt-mermaid-_r_1v8_ .edgeLabels text{font-size:13px;font-weight:600;letter-spacing:-0.08px;fill:rgb(232, 232, 232);}#chatgpt-mermaid-_r_1v8_ .node tspan[font-weight="normal"],#chatgpt-mermaid-_r_1v8_ .edgeLabels tspan[font-weight="normal"]{font-weight:600;}#chatgpt-mermaid-_r_1v8_ .edgeLabel .label rect{opacity:1;rx:13px;ry:13px;fill:rgb(19, 19, 19);stroke:rgb(47, 47, 47);stroke-width:1px;}#chatgpt-mermaid-_r_1v8_ .node rect,#chatgpt-mermaid-_r_1v8_ .node circle,#chatgpt-mermaid-_r_1v8_ .node ellipse,#chatgpt-mermaid-_r_1v8_ .node polygon,#chatgpt-mermaid-_r_1v8_ .node path{fill:rgb(24, 24, 24);stroke:rgba(255, 255, 255, 0.1);stroke-width:1px;}#chatgpt-mermaid-_r_1v8_ .node rect{rx:16px;ry:16px;}#chatgpt-mermaid-_r_1v8_ .mermaid-decision .label-container{fill:rgb(19, 19, 19);stroke:rgb(47, 47, 47);stroke-dasharray:2,2;}#chatgpt-mermaid-_r_1v8_ .edgePaths .flowchart-link{stroke:rgb(175, 175, 175);stroke-width:1px;stroke-linecap:round;stroke-linejoin:round;}#chatgpt-mermaid-_r_1v8_ .marker{fill:rgb(175, 175, 175);stroke:rgb(175, 175, 175);}#chatgpt-mermaid-_r_1v8_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}Target and pinned environmentClaude proposes or repairs aproofLocal Lean checksCheck target anddependenciesSubmit to Prove2MeRecord proof or reductionverdictErrors or remaining goalsCandidate passesProblem foundReady




Your controller should own the durable state. For example, it should remember which theorem is being proved, which candidate was checked, and which exact file was submitted. Those facts should survive a model session ending or being restarted.
2. Give it proper Lean tools
For this application, I would spend effort on the Lean interface before building a sophisticated UI.

The community project lean-lsp-mcp already documents useful tools:
Tool	Why it matters
lean_goal	Shows the hypotheses and goal at a particular proof position.
lean_diagnostic_messages	Returns Lean’s actual errors, warnings, and informational messages.
lean_local_search	Finds declarations in the local project and libraries.
lean_hover_info / lean_declaration_file	Lets the agent inspect the precise type and definition of a candidate lemma.
lean_verify	Reports axiom dependencies and additional source warnings.


These are existing tool names, rather than interfaces you would have to invent. GitHub

The behavior I would aim for is:
1. Read the current goal.
2. Search for relevant declarations.
3. Inspect their exact signatures.
4. Try a proof step or a short proof.
5. Read Lean’s response.
6. Continue, revise the approach, or return to an earlier candidate.
My expectation is that reliable feedback and retrieval would make the agent more useful than adding elaborate prompting alone. That is an engineering hypothesis to measure, not a guarantee of higher proof success.

For library lookup, I would favor the locally pinned Mathlib checkout. A lemma found in current online documentation still needs to exist, with the same signature, in the target’s environment.
3. The Prove2Me-specific details matter
Preserve the exact target
The platform’s theorem endpoint exposes the formal statement, preamble, and Mathlib revision. I would store these as immutable task inputs and let the worker edit the proof and explicitly permitted helper material. GitHub

Prove2Me requires a top-level theorem solution whose type matches the target. It also prohibits importing the target itself and prohibits sorry in the submitted solution. These should be checks in your controller, rather than instructions that depend entirely on the model remembering them. GitHub

This prevents a familiar failure mode: an agent makes a difficult theorem compile by accidentally changing what it is proving.
Match the target’s environment
The local project should use the target’s Lean toolchain and exact Mathlib revision. Prove2Me also specifies autoImplicit false, which affects how undeclared variables in signatures are treated. Its environment endpoint supplies the supported toolchain/revision combinations. GitHub

I would give concurrent workers separate editable source trees. Each attempt should record its environment alongside its proof, so that “this worked yesterday” is reproducible.
Keep local checking separate from submission
The main API operations are straightforward:
- Retrieve the task: GET /api/v1/theorems/:theorem_id.
- Retrieve supported environments: GET /api/v1/environments.
- Submit a solution: POST /api/v1/verify.
- Poll its result: GET /api/v1/verify?submission_id=.... GitHub
Despite its name, /verify creates a submission. I would run the edit–check–repair loop locally and invoke that endpoint only when the controller has a candidate ready to contribute. GitHub
Distinguish a proof from a reduction
Prove2Me has two materially different successful outcomes:
- ACCEPTED: the proof is verified, including a reduction whose imported theorems are already proved.
- SKETCH_ACCEPTED: the reduction checks, but it still imports one or more open platform theorems. Those remain obligations. GitHub
Your agent should track completed proofs and accepted reductions separately. A reduction can be valuable progress, but reporting it as a completed proof would distort both the user experience and your experiments.

There is a subtle local-checking issue here: Prove2Me’s documented workspace mirrors imported platform theorem statements using by sorry placeholders. Consequently, a local build can establish that a reduction fits together without establishing that every imported result has been proved. The agent must track those dependencies and use the platform’s verdict to determine the contribution’s final status. GitHub

More generally, Lean’s documentation distinguishes checking a proof from checking that the statement means what was intended. Axiom inspection is useful, but it belongs alongside preservation of the target and trusted dependencies. lean-lang.org
4. When I would build a more independent harness
For research into proof search, I would add an explicit search controller.

That controller might represent a node as:
A particular proof state, the tactic sequence that reached it, its parent node, and the remaining search budget.

Your code would then decide which node to expand, how many candidate tactics to request, when to backtrack, and when to abandon a branch. This gives you a place to investigate beam search, best-first search, lemma selection, or different policies for allocating attempts.

The Lean community REPL provides programmatic environments and experimental tactic-mode proof states, including persistence facilities. One practical limitation is that reaching a finished tactic state does not automatically replace the original placeholder declaration: you still need to reconstruct the proof script and check the resulting theorem. GitHub

The choice I would make is:
Your objective	My recommendation
Produce useful proofs and formalizations	Claude Agent SDK with Lean tools and a small controller.
Research how to schedule and explore proof attempts	Your own search controller, Lean REPL workers, and a replaceable model worker.
Control every model request, context update, and tool-loop decision	A fully owned model interaction loop using an appropriate API/backend.


You can implement substantial search logic around an SDK worker. The boundary is that the SDK still embeds Claude Code’s internal agent loop; it does not give you the source-level control of having implemented that whole loop yourself. Claude Code Docs

For your stated application, I would begin with the SDK architecture and keep the model worker replaceable. That gives you a working prover while leaving room for research.
5. Subscription use and open source
As of 31 October 2026, Anthropic’s help page says that Agent SDK and claude -p usage signed in with a Claude plan draws from the subscription’s limits. That supports the personal-agent arrangement we have been discussing. Claude Help Center

The integration should keep Claude authentication in the official runtime. Anthropic’s published conditions require the binary to remain unmodified and users to authenticate with their own credentials when Claude Code is offered through another product. Claude Code Docs

You can publish your controller and Lean integration as open source. The Python SDK wrapper is MIT-licensed; the underlying Claude Code runtime remains governed by Anthropic’s proprietary terms. Your agent can be open source while depending on that runtime. GitHub
6. The first version I would build
I would start with a deliberately small, measurable system:
1. One selected mission and one worker.
2. Direct proof attempts first, before automating the publication of new decomposition trees.
3. A pinned local workspace with goal inspection, declaration search, and compilation.
4. Persistent attempt records containing the target, candidate source, diagnostics, elapsed time, and outcome.
5. Bounded attempts, with the ability to stop and resume.
6. A distinct submission operation that records the server’s result.
For a baseline, Prove2Me’s existing workspace can already be used with an ordinary coding agent. I would compare the custom agent against that setup on the same targets and budgets. Prove2Me

Then I would add independent candidate branches, better lemma retrieval, and dependency-aware scheduling when the recorded failures show that those features would help.

For your Prove2Me project, my first choice is Python + Claude Agent SDK + lean-lsp-mcp, with your own controller managing tasks and evidence. The most valuable custom work would be in proof-state feedback, target selection, reproducibility, and learning from failed attempts.
